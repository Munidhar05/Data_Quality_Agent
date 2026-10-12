from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

DATA_ROOT = Path(__file__).resolve().parents[1] / "datasets"
UPLOAD_DIR = DATA_ROOT / "uploads"
RUN_DIR = DATA_ROOT / "runs"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class DatasetStorage(Protocol):
    def save_dataset(self, filename: str, content: bytes) -> dict[str, str]: ...

    def load_dataset(self, dataset_id: str) -> pd.DataFrame: ...

    def dataset_metadata(self, dataset_id: str, name: str | None = None) -> dict[str, Any]: ...

    def save_run(self, run: dict[str, Any], accepted: pd.DataFrame, rejected: pd.DataFrame) -> None: ...


class LocalFileStorage(DatasetStorage):
    def __init__(self) -> None:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        RUN_DIR.mkdir(parents=True, exist_ok=True)

    def save_dataset(self, filename: str, content: bytes) -> dict[str, str]:
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("This file exceeds the 50 MB MVP upload limit. Use a smaller extract or a future scalable execution adapter.")
        suffix = Path(filename).suffix.lower()
        if suffix not in {".csv", ".parquet", ".pq"}:
            raise ValueError("Choose a CSV or Parquet (.parquet or .pq) file.")
        dataset_id = uuid.uuid4().hex
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).stem).strip("._") or "dataset"
        destination = UPLOAD_DIR / f"{dataset_id}{suffix}"
        destination.write_bytes(content)
        original_filename = Path(filename.replace("\\", "/")).name
        metadata = {"dataset_id": dataset_id, "name": safe_name, "filename": original_filename}
        (UPLOAD_DIR / f"{dataset_id}.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        return {**metadata, "path": str(destination)}

    def load_dataset(self, dataset_id: str) -> pd.DataFrame:
        matches = [path for path in UPLOAD_DIR.glob(f"{dataset_id}.*") if path.suffix.lower() in {".csv", ".parquet", ".pq"}]
        if len(matches) != 1 or not re.fullmatch(r"[a-f0-9]{32}", dataset_id):
            raise FileNotFoundError("That dataset is no longer available. Upload it again to continue.")
        path = matches[0]
        try:
            if path.suffix.lower() == ".csv":
                return pd.read_csv(path, low_memory=False)
            return pd.read_parquet(path)
        except Exception as error:
            raise ValueError(f"The uploaded file could not be read as {path.suffix[1:].upper()}. Check that it is a valid, non-empty tabular file.") from error

    def dataset_metadata(self, dataset_id: str, name: str | None = None) -> dict[str, Any]:
        path = UPLOAD_DIR / f"{dataset_id}.json"
        if path.exists():
            metadata = json.loads(path.read_text(encoding="utf-8"))
            # Legacy uploads stored their internal ID as the visible filename.
            filename = metadata.get("filename", "")
            if Path(filename).stem == dataset_id and metadata.get("name") != dataset_id:
                metadata["filename"] = metadata["name"] + Path(filename).suffix
            return metadata
        matches = [path for path in UPLOAD_DIR.glob(f"{dataset_id}.*") if path.suffix.lower() in {".csv", ".parquet", ".pq"}]
        if not matches:
            raise FileNotFoundError("That dataset is no longer available. Upload it again to continue.")
        metadata = {"dataset_id": dataset_id, "name": name or matches[0].stem, "filename": matches[0].name}
        path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        return metadata

    def save_run(self, run: dict[str, Any], accepted: pd.DataFrame, rejected: pd.DataFrame) -> None:
        run_id = run["run_id"]
        accepted.to_csv(RUN_DIR / f"{run_id}_accepted.csv", index=False)
        rejected.to_csv(RUN_DIR / f"{run_id}_rejected.csv", index=False)
        (RUN_DIR / f"{run_id}.json").write_text(json.dumps(run, ensure_ascii=False, default=str, indent=2), encoding="utf-8")

    def list_runs(self) -> list[dict[str, Any]]:
        runs = []
        for path in RUN_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                runs.append({key: data[key] for key in ("run_id", "dataset_name", "validated_at", "score", "accepted_count", "rejected_count") if key in data})
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(runs, key=lambda item: item.get("validated_at", ""), reverse=True)

    def get_run(self, run_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise FileNotFoundError("That validation run was not found.")
        path = RUN_DIR / f"{run_id}.json"
        if not path.exists():
            raise FileNotFoundError("That validation run was not found.")
        return json.loads(path.read_text(encoding="utf-8"))

    def output_path(self, run_id: str, output: str) -> Path:
        if output not in {"accepted", "rejected"}:
            raise FileNotFoundError("Choose either the accepted or rejected output.")
        run = self.get_run(run_id)
        path = RUN_DIR / f"{run_id}_{output}.csv"
        if not path.exists():
            raise FileNotFoundError("The requested output file is unavailable.")
        return path
