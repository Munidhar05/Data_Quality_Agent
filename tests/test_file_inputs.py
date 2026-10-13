import io
import json

import pandas as pd
import pytest

import backend.storage as storage
from backend.file_inputs import convert_file


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(storage, "RUN_DIR", tmp_path / "runs")
    return storage.LocalFileStorage()


def fixture_content(suffix):
    frame = pd.DataFrame({"id": ["001", "002"], "amount": ["10.00", "-1.00"]})
    stream = io.BytesIO()
    if suffix in {".csv", ".tsv", ".psv", ".txt"}:
        return frame.to_csv(index=False, sep={".tsv": "\t", ".psv": "|", ".txt": ";"}.get(suffix, ",")).encode()
    if suffix in {".xlsx", ".xlsm", ".ods"}:
        with pd.ExcelWriter(stream, engine="odf" if suffix == ".ods" else "openpyxl") as book:
            frame.to_excel(book, sheet_name="Records", index=False)
            pd.DataFrame({"ignored": [1]}).to_excel(book, sheet_name="Other", index=False)
        return stream.getvalue()
    if suffix in {".json", ".jsonl", ".ndjson"}:
        records = frame.to_dict(orient="records")
        return (json.dumps(records) if suffix == ".json" else "\n".join(json.dumps(row) for row in records)).encode()
    if suffix == ".xml":
        return b"<records><record><id>001</id><amount>10.00</amount></record><record><id>002</id><amount>-1.00</amount></record></records>"
    if suffix in {".feather", ".arrow"}:
        frame.to_feather(stream)
    elif suffix == ".orc":
        frame.to_orc(stream)
    elif suffix in {".parquet", ".pq"}:
        frame.to_parquet(stream)
    elif suffix == ".avro":
        from fastavro import writer
        schema = {"type": "record", "name": "Input", "fields": [{"name": "id", "type": "string"}, {"name": "amount", "type": "string"}]}
        writer(stream, schema, frame.to_dict(orient="records"))
    return stream.getvalue()


@pytest.mark.parametrize("suffix", [".csv", ".parquet", ".pq", ".tsv", ".txt", ".psv", ".xlsx", ".xlsm", ".ods", ".json", ".jsonl", ".ndjson", ".xml", ".feather", ".arrow", ".orc", ".avro"])
def test_file_import_profile_values_and_reload(files, suffix):
    info = files.save_dataset("Original File" + suffix, fixture_content(suffix))
    assert info["filename"] == "Original File" + suffix
    assert files.load_dataset(info["dataset_id"], original=True).to_dict(orient="list") == {"id": ["001", "002"], "amount": ["10.00", "-1.00"]}
    assert files.load_dataset(info["dataset_id"])["id"].tolist() == ["001", "002"]
    if suffix in {".xlsx", ".xlsm", ".ods"}:
        assert "Records" in info["import_notes"][0]
        assert "first worksheet" in info["import_notes"][0]


def test_nested_json_and_literal_null_tokens(files):
    content = json.dumps({"records": [{"id": "001", "label": "NA", "details": {"active": False}, "tags": ["a", "b"]}]}).encode()
    info = files.save_dataset("data.json", content)
    frame = files.load_dataset(info["dataset_id"], original=True)
    assert frame.iloc[0]["label"] == "NA"
    assert frame.iloc[0]["details.active"] == "False"
    assert json.loads(frame.iloc[0]["tags"]) == ["a", "b"]


@pytest.mark.parametrize("name,content", [
    ("bad.json", b"[1,2]"), ("empty.json", b"[]"),
    ("bad.jsonl", b'{"id":1}\ninvalid'), ("bad.xlsx", b"not a workbook"),
    ("bad.xml", b"<root><row><id>1</id><id>2</id></row></root>"),
    ("nested.xml", b"<root><row><detail><id>1</id></detail></row></root>"),
    ("unsafe.xml", b'<!DOCTYPE x [<!ENTITY e "secret">]><root><row><id>&e;</id></row></root>'),
    ("bad.txt", b"not delimited"),
])
def test_invalid_import_leaves_no_saved_dataset(files, name, content):
    with pytest.raises(ValueError):
        files.save_dataset(name, content)
    assert list(storage.UPLOAD_DIR.iterdir()) == []


def test_sparse_json_does_not_round_large_numeric_identifiers(files):
    info = files.save_dataset("large.json", b'[{"id":1234567890123456789,"amount":1},{"amount":2}]')
    frame = files.load_dataset(info["dataset_id"], original=True)
    assert frame["id"].tolist() == ["1234567890123456789", ""]
