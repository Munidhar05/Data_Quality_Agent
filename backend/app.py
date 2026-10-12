from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow direct startup from the backend directory: py app.py.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from backend.business_documents import extract_document, parse_batch
from backend.auth import router as auth_router, current_user
from backend.admin import router as admin_router, login_router as admin_login_router
from backend.execution import PandasEngine
from backend.llm import metadata_for_llm, provider_from_environment, validate_ai_recommendations
from backend.profiler import profile_frame
from backend.rules import OPERATORS, discover_rules, parse_business_requirement, recommend_rules
from backend.scoring import calculate_score
from backend.storage import LocalFileStorage, MAX_UPLOAD_BYTES, UPLOAD_DIR

APP_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(APP_ROOT / ".env")
FRONTEND_ROOT = APP_ROOT / "frontend"
ENGINE_VERSION = "0.1.0"
storage = LocalFileStorage()
execution_engine = PandasEngine()
llm_provider = provider_from_environment()
SEMANTIC_TYPES = {"UNKNOWN", "IDENTIFIER", "EMAIL", "PHONE", "NAME", "DATE", "TIMESTAMP", "CURRENCY", "AMOUNT", "PERCENTAGE", "STATUS", "CATEGORY", "ADDRESS", "COUNTRY", "STATE", "POSTAL_CODE", "BOOLEAN", "FOREIGN_KEY", "DESCRIPTION", "NUMERIC_MEASURE"}
app = FastAPI(title="Feuji Data Quality Agent", version=ENGINE_VERSION, description="Metadata-driven, human-approved data quality validation.")
app.mount("/static", StaticFiles(directory=FRONTEND_ROOT), name="static")
app.include_router(auth_router)
app.include_router(admin_login_router)
app.include_router(admin_router)
@app.middleware("http")
async def require_login(request, call_next):
    if request.url.path.startswith("/api/") and not request.url.path.startswith(("/api/auth/", "/api/admin/")) and request.url.path != "/api/health":
        if not current_user(request):
            return JSONResponse({"detail": "Please log in to continue."}, status_code=401)
    if request.url.path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Invalid request origin."}, status_code=403)
    return await call_next(request)



class BusinessRuleRequest(BaseModel):
    requirement: str = Field(min_length=1, max_length=1000)


class BusinessBatchRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100000)


class SemanticRequest(BaseModel):
    column: str
    semantic_type: str
    explanation: str = Field(default="", max_length=500)


class RuleSelection(BaseModel):
    rule_id: str
    configuration: dict[str, Any] = Field(default_factory=dict)


class RunRequest(BaseModel):
    dataset_id: str
    selected_rules: list[RuleSelection] = Field(default_factory=list)
    business_rules: list[dict[str, Any]] = Field(default_factory=list)
    approved: bool = False
    accepted_name: str | None = None
    rejected_name: str | None = None


@app.get("/", include_in_schema=False)
@app.get("/login", include_in_schema=False)
@app.get("/signup", include_in_schema=False)
@app.get("/upload-file", include_in_schema=False)
@app.get("/choose-checks", include_in_schema=False)
@app.get("/business-rules", include_in_schema=False)
@app.get("/results", include_in_schema=False)
@app.get("/file-details", include_in_schema=False)
@app.get("/history", include_in_schema=False)
@app.get("/available-checks", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND_ROOT / "index.html")


@app.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    return FileResponse(FRONTEND_ROOT / "admin.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "engine_version": ENGINE_VERSION, "llm": {"configured": llm_provider.configured, "mode": "metadata_only" if llm_provider.configured else "local_metadata_only", "message": "The configured provider receives structured profile metadata only; validation remains local and deterministic." if llm_provider.configured else "No LLM provider is configured. Recommendations use deterministic profiling evidence; no dataset content is sent to an external service."}}


def _profile_with_overrides(metadata: dict[str, Any], frame: pd.DataFrame) -> dict[str, Any]:
    profile = profile_frame(frame)
    overrides = metadata.get("semantic_overrides", {})
    for column in profile["columns"]:
        override = overrides.get(column["name"])
        if not override:
            continue
        declared = {"type": override["semantic_type"], "confidence": 1.0, "evidence": [f"User-declared meaning: {override['explanation'] or override['semantic_type']}."]}
        candidates = [candidate for candidate in column["semantic_candidates"] if candidate["type"] != override["semantic_type"]]
        column["semantic_candidates"] = [declared, *candidates]
    return profile


def _recommendations(frame: pd.DataFrame, profile: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    recommendations = recommend_rules(frame, profile)
    if not llm_provider.configured:
        return recommendations, "NOT_CONFIGURED"
    provider_response = llm_provider.recommend(metadata_for_llm(profile, discover_rules()))
    if provider_response.get("status") != "OK":
        return recommendations, str(provider_response.get("status", "UNAVAILABLE"))
    advisory = validate_ai_recommendations(provider_response, profile, discover_rules())
    for recommendation in recommendations:
        if recommendation["rule_id"] in advisory:
            recommendation["ai_assessment"] = advisory[recommendation["rule_id"]]
    return recommendations, "OK"


def _dataset_or_404(dataset_id: str) -> tuple[dict[str, Any], pd.DataFrame]:
    try:
        metadata = storage.dataset_metadata(dataset_id)
        frame = storage.load_dataset(dataset_id)
        return metadata, frame
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/datasets")
async def upload_dataset(file: UploadFile = File(...)) -> dict[str, Any]:
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="This file exceeds the 50 MB MVP upload limit.")
    try:
        metadata = storage.save_dataset(file.filename or "dataset.csv", content)
        frame = storage.load_dataset(metadata["dataset_id"])
        if frame.empty or len(frame.columns) == 0:
            raise ValueError("The file contains no tabular records or columns. Upload a CSV or Parquet file with a header and at least one record.")
        metadata = storage.dataset_metadata(metadata["dataset_id"], metadata["name"])
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    profile = _profile_with_overrides(metadata, frame)
    recommendations, _ = _recommendations(frame, profile)
    return {"dataset": metadata, "profile": profile, "recommendations": recommendations, "warnings": []}


@app.get("/api/datasets/{dataset_id}/profile")
def get_profile(dataset_id: str) -> dict[str, Any]:
    metadata, frame = _dataset_or_404(dataset_id)
    profile = _profile_with_overrides(metadata, frame)
    recommendations, _ = _recommendations(frame, profile)
    return {"dataset": metadata, "profile": profile, "recommendations": recommendations}


@app.get("/api/rules")
def get_rules() -> list[dict[str, Any]]:
    return discover_rules()


@app.post("/api/datasets/{dataset_id}/business-rules/parse")
def parse_rule(dataset_id: str, body: BusinessRuleRequest) -> dict[str, Any]:
    _, frame = _dataset_or_404(dataset_id)
    return parse_business_requirement(body.requirement, [str(column) for column in frame.columns])


def _business_batch(dataset_id: str, text: str) -> dict[str, Any]:
    metadata, frame = _dataset_or_404(dataset_id)
    profile = _profile_with_overrides(metadata, frame)
    try:
        return parse_batch(text, [str(column) for column in frame.columns], metadata_for_llm(profile, []), llm_provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/datasets/{dataset_id}/business-rules/batch")
def parse_business_batch(dataset_id: str, body: BusinessBatchRequest) -> dict[str, Any]:
    return _business_batch(dataset_id, body.text)


@app.post("/api/datasets/{dataset_id}/business-rules/document")
async def parse_business_document(dataset_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    from starlette.concurrency import run_in_threadpool
    _dataset_or_404(dataset_id)
    try:
        content = await file.read(10 * 1024 * 1024 + 1)
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Choose a document up to 10 MB.")
        text = await run_in_threadpool(extract_document, file.filename or "rules.txt", content)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc) if isinstance(exc, ValueError) else "Unable to read this document. Check the format or paste your rules into the text area.") from exc
    finally:
        await file.close()
    return await run_in_threadpool(_business_batch, dataset_id, text)


@app.post("/api/datasets/{dataset_id}/semantics")
def save_semantic(dataset_id: str, body: SemanticRequest) -> dict[str, Any]:
    metadata, frame = _dataset_or_404(dataset_id)
    if body.column not in frame.columns:
        raise HTTPException(status_code=422, detail="That column is not present in the uploaded dataset.")
    if body.semantic_type not in SEMANTIC_TYPES:
        raise HTTPException(status_code=422, detail="Choose one of the supported semantic classifications.")
    metadata.setdefault("semantic_overrides", {})[body.column] = {"semantic_type": body.semantic_type, "explanation": body.explanation.strip()}
    path = UPLOAD_DIR / f"{dataset_id}.json"
    path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    profile = _profile_with_overrides(metadata, frame)
    recommendations, _ = _recommendations(frame, profile)
    return {"dataset": metadata, "profile": profile, "recommendations": recommendations}


def _validate_business_rule(frame: pd.DataFrame, rule: dict[str, Any], ordinal: int) -> list[dict[str, Any]]:
    rule_id = f"BUSINESS-{ordinal:03d}"
    left_name = rule.get("column") if rule.get("type") == "comparison" else rule.get("left")
    right_name = rule.get("right") if rule.get("type") == "cross_column" else None
    operator = rule.get("operator")
    if left_name not in frame.columns or (right_name is not None and right_name not in frame.columns) or operator not in OPERATORS:
        raise ValueError("A business rule refers to a missing column or unsupported operator. Parse and review it again before running.")

    left_values = frame[left_name]
    right_values: Any = rule.get("value") if rule.get("type") == "comparison" else frame[right_name]
    if rule.get("type") == "cross_column":
        numeric_left = pd.to_numeric(left_values, errors="coerce")
        numeric_right = pd.to_numeric(right_values, errors="coerce")
        numeric_overlap = (numeric_left.notna() & numeric_right.notna()).sum()
        if numeric_overlap:
            left_values, right_values = numeric_left, numeric_right
        else:
            date_left = pd.to_datetime(left_values, errors="coerce", format="mixed", utc=True)
            date_right = pd.to_datetime(right_values, errors="coerce", format="mixed", utc=True)
            if (date_left.notna() & date_right.notna()).any():
                left_values, right_values = date_left, date_right
            else:
                raise ValueError("These columns cannot be compared as numbers or dates. Confirm their compatible types first.")
    elif pd.api.types.is_numeric_dtype(left_values.dtype):
        left_values = pd.to_numeric(left_values, errors="coerce")
    else:
        parsed_dates = pd.to_datetime(left_values, errors="coerce", format="mixed", utc=True)
        if parsed_dates.notna().sum() > 0 and not pd.api.types.is_numeric_dtype(left_values.dtype):
            left_values = parsed_dates
        else:
            raise ValueError("This comparison requires a numeric or date-like column. Its meaning cannot be safely inferred for this rule.")

    comparable = left_values.notna() if rule.get("type") == "comparison" else left_values.notna() & right_values.notna()
    try:
        valid = OPERATORS[operator](left_values, right_values)
    except (TypeError, ValueError) as error:
        raise ValueError("This business rule cannot compare the selected values. Check their types and operator.") from error

    failures = []
    for index in frame.index[comparable & ~valid]:
        actual = {str(left_name): frame.at[index, left_name]}
        if right_name:
            actual[str(right_name)] = frame.at[index, right_name]
        else:
            actual["expected_value"] = rule["value"]
        failures.append({"rule_id": rule_id, "rule_name": "Business requirement", "column": str(left_name) if not right_name else f"{left_name}, {right_name}", "row_index": int(index) if isinstance(index, int) else str(index), "status": "FAIL", "reason": f"Requirement: {rule.get('original_requirement', 'user-approved comparison')}.", "severity": "high", "actual_value": str(actual), "expected_condition": f"{left_name} {operator} {right_name if right_name else rule['value']}"})
    return failures


@app.post("/api/runs")
def create_run(request: RunRequest) -> dict[str, Any]:
    if not request.approved:
        raise HTTPException(status_code=400, detail="Review and approve the selected rules before running validation.")
    if not request.selected_rules and not request.business_rules:
        raise HTTPException(status_code=400, detail="Select at least one rule or approved business requirement.")

    metadata, frame = _dataset_or_404(request.dataset_id)
    rules_by_id = {rule["rule_id"]: rule for rule in discover_rules()}
    selected = []
    failures = []
    check_results = []
    try:
        for selection in request.selected_rules:
            rule = rules_by_id.get(selection.rule_id)
            if rule is None:
                raise ValueError(f"Rule '{selection.rule_id}' is not available in the current registry.")
            selected.append({**rule, "configuration": selection.configuration})
            rule_failures = execution_engine.execute_rule(frame, rule, selection.configuration)
            failures.extend(rule_failures)
            check_results.append({"rule_id": rule["rule_id"], "name": rule["name"], "status": "FAIL" if rule_failures else "PASS", "failure_count": len(rule_failures)})
        for ordinal, business_rule in enumerate(request.business_rules, 1):
            if business_rule.get("status") != "PARSED" or business_rule.get("type") not in {"comparison", "cross_column"}:
                raise ValueError("Only reviewed, structured comparison business rules can run. Clarify ambiguous requirements first.")
            business_failures = _validate_business_rule(frame, business_rule, ordinal)
            failures.extend(business_failures)
            check_results.append({"rule_id": f"BUSINESS-{ordinal:03d}", "name": "Business requirement", "status": "FAIL" if business_failures else "PASS", "failure_count": len(business_failures)})
            selected.append({"rule_id": f"BUSINESS-{ordinal:03d}", "version": "1.0", "name": "Business requirement", "category": "consistency", "severity": "high", "configuration": business_rule, "predicate": "user_approved_comparison"})
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    by_row: dict[Any, list[dict[str, Any]]] = {}
    for failure in failures:
        by_row.setdefault(failure["row_index"], []).append(failure)
    result = frame.copy()
    row_failures = [by_row.get(int(index) if isinstance(index, int) else str(index), []) for index in frame.index]
    result["dq_status"] = ["REJECTED" if issues else "ACCEPTED" for issues in row_failures]
    result["failed_rules"] = [[issue["rule_id"] for issue in issues] for issues in row_failures]
    result["failure_reasons"] = [[issue["reason"] for issue in issues] for issues in row_failures]
    result["run_id"] = uuid.uuid4().hex
    result["validated_at"] = datetime.now(timezone.utc).isoformat()
    rejected_mask = result["dq_status"] == "REJECTED"
    accepted_frame, rejected_frame = result.loc[~rejected_mask].copy(), result.loc[rejected_mask].copy()
    score = calculate_score(selected, failures, len(frame))
    accepted_name = request.accepted_name or f"{metadata['name']}_accepted"
    rejected_name = request.rejected_name or f"{metadata['name']}_rejected"
    if len(accepted_name) > 100 or len(rejected_name) > 100:
        raise HTTPException(status_code=422, detail="Output names must be 100 characters or fewer.")
    run = {
        "run_id": result["run_id"].iloc[0] if len(result) else uuid.uuid4().hex,
        "dataset_id": request.dataset_id,
        "dataset_name": metadata["name"],
        "dataset_filename": metadata.get("filename", metadata["name"]),
        "dataset_profile": _profile_with_overrides(metadata, frame),
        "profile_version": "1.0",
        "engine_version": ENGINE_VERSION,
        "validated_at": result["validated_at"].iloc[0] if len(result) else datetime.now(timezone.utc).isoformat(),
        "rules_considered": discover_rules(),
        "rules_selected": selected,
        "check_results": check_results,
        "business_rules": request.business_rules,
        "user_changes": {"selected_rule_ids": [item["rule_id"] for item in selected], "accepted_name": accepted_name, "rejected_name": rejected_name},
        "approval": {"approved": request.approved, "approved_at": datetime.now(timezone.utc).isoformat()},
        "score": score,
        "row_count": len(frame),
        "accepted_count": len(accepted_frame),
        "rejected_count": len(rejected_frame),
        "failure_count": len(failures),
        "failures": failures[:5000],
        "failure_categories": {category: sum(item["category"] == category for item in selected) for category in sorted({item["category"] for item in selected})},
        "outputs": {"accepted": accepted_name, "rejected": rejected_name},
        "warnings": ["Failure detail is capped at 5,000 entries in the audit report; accepted/rejected outputs include per-record failure reasons."] if len(failures) > 5000 else [],
    }
    storage.save_run(run, accepted_frame, rejected_frame)
    return run


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    return storage.list_runs()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    try:
        return storage.get_run(run_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/api/runs/{run_id}/download/{output}")
def download_output(run_id: str, output: str) -> FileResponse:
    try:
        run = storage.get_run(run_id)
        path = storage.output_path(run_id, output)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    filename = f"{run['outputs'][output]}.csv"
    return FileResponse(path, media_type="text/csv", filename=filename)


@app.get("/api/datasets/{dataset_id}/rows")
def get_rows(dataset_id: str, offset: int = 0, limit: int = 50, search: str = "", sort_by: str = "", sort_direction: str = "asc") -> dict[str, Any]:
    metadata, frame = _dataset_or_404(dataset_id)
    if search:
        mask = frame.astype(str).apply(lambda column: column.str.contains(search, case=False, regex=False, na=False)).any(axis=1)
        frame = frame.loc[mask]
    total_count = len(frame)
    if sort_by:
        if sort_by not in frame.columns:
            raise HTTPException(status_code=422, detail="Choose an existing dataset column for sorting.")
        if sort_direction not in {"asc", "desc"}:
            raise HTTPException(status_code=422, detail="Sort direction must be asc or desc.")
        try:
            frame = frame.sort_values(sort_by, ascending=sort_direction == "asc", na_position="last", kind="stable")
        except TypeError:
            frame = frame.assign(**{sort_by: frame[sort_by].astype(str)}).sort_values(sort_by, ascending=sort_direction == "asc", kind="stable")
    limit = max(1, min(limit, 100))
    frame = frame.iloc[max(offset, 0):max(offset, 0) + limit]
    safe_frame = json.loads(frame.to_json(orient="records", date_format="iso", default_handler=str))
    return {"dataset": metadata["name"], "columns": [str(column) for column in frame.columns], "rows": safe_frame, "offset": offset, "limit": limit, "total_count": total_count, "sort_by": sort_by or None, "sort_direction": sort_direction}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app:app", host="127.0.0.1", port=8080, reload=True, reload_dirs=[str(APP_ROOT)])
