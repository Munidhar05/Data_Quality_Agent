from __future__ import annotations

import io
import json

import pandas as pd
from fastapi.testclient import TestClient

import backend.app as api
import backend.storage as storage_module
from backend.profiler import profile_frame
from backend.rules import discover_rules, parse_business_requirement, recommend_rules, validate_rule
from backend.scoring import calculate_score
from backend.llm import metadata_for_llm, validate_ai_recommendations



def authenticated_client():
    client = TestClient(api.app)
    from uuid import uuid4
    email = f"test-{uuid4().hex}@example.com"
    client.post("/api/auth/signup", json={"first_name":"Test", "last_name":"User", "company":"Testing", "phone":"9876543210", "email":email, "password":"test-password"})
    assert client.post("/api/auth/login", json={"email":email, "password":"test-password"}).status_code == 200
    return client

def test_profiler_adapts_to_five_structurally_different_datasets() -> None:
    datasets = [
        pd.DataFrame({"measure_a": [1, 2, 3], "measure_b": [10.2, 11.4, 9.8], "measure_c": [4, 5, 6]}),
        pd.DataFrame({"group": ["north", "south", "north"], "flag": [True, False, True]}),
        pd.DataFrame({"observed_at": pd.to_datetime(["2025-01-01", "2025-01-02"]), "quantity": [2, 0]}),
        pd.DataFrame({"free_text": ["short", "longer explanatory text", None], "code_value": ["A-1", "B-2", "B-2"]}),
        pd.DataFrame({"first": [1, None, 1], "second": ["x", "y", "x"], "third": ["p", "q", "r"]}),
    ]
    for frame in datasets:
        profile = profile_frame(frame)
        assert profile["row_count"] == len(frame)
        assert profile["column_count"] == len(frame.columns)
        assert [column["name"] for column in profile["columns"]] == list(frame.columns)
        assert all("semantic_candidates" in column for column in profile["columns"])
        assert all("statistics" in column for column in profile["columns"])


def test_date_inference_requires_date_shape_not_just_parseable_identifier() -> None:
    profile = profile_frame(pd.DataFrame({"unfamiliar_code": ["T-7001", "T-7002", "T-7003"]}))
    candidates = profile["columns"][0]["semantic_candidates"]
    assert all(candidate["type"] != "DATE" for candidate in candidates)
    assert any(candidate["type"] == "IDENTIFIER" for candidate in candidates)

    date_profile = profile_frame(pd.DataFrame({"unlabeled_values": ["2025-05-12", "2025-05-13", "2025-05-14"]}))
    date_candidates = date_profile["columns"][0]["semantic_candidates"]
    assert any(candidate["type"] == "DATE" for candidate in date_candidates)
    assert all(candidate["type"] != "PHONE" for candidate in date_candidates)
    assert date_profile["columns"][0]["statistics"]["min_date"].startswith("2025-05-12")

    structured_ids = profile_frame(pd.DataFrame({"unknown_key": ["C-100", "C-101", "C-102", "C-102"]}))
    assert any(candidate["type"] == "IDENTIFIER" for candidate in structured_ids["columns"][0]["semantic_candidates"])


def test_registry_rules_are_metadata_driven_and_score_is_explainable() -> None:
    rules = discover_rules()
    frame = pd.DataFrame({"opaque_value": ["X7", "Y9", "Z2"]})
    profile = profile_frame(frame)
    recommendations = recommend_rules(frame, profile)
    assert {rule["rule_id"] for rule in recommendations} == {rule["rule_id"] for rule in rules}
    score = calculate_score([{"rule_id": "DQ-COMPLETENESS", "category": "completeness", "severity": "high"}], [], len(frame))
    assert score["value"] == 100.0
    assert "severity" in score["formula"]


def test_business_parser_asks_instead_of_guessing() -> None:
    columns = ["observed_amount", "opened_on", "closed_on"]
    ambiguous = parse_business_requirement("amount should be valid", columns)
    assert ambiguous["status"] == "REQUIRES_USER_INPUT"
    parsed = parse_business_requirement("observed_amount must be greater than zero", columns)
    assert parsed["status"] == "PARSED"
    assert parsed["column"] == "observed_amount"
    assert parsed["operator"] == ">" and parsed["value"] == 0
    cross_column = parse_business_requirement("opened_on should not be after closed_on", columns)
    assert cross_column["status"] == "PARSED"
    assert cross_column["left"] == "opened_on" and cross_column["right"] == "closed_on"
    assert cross_column["operator"] == "<="
    strict_cross_column = parse_business_requirement("closed_on must be before opened_on", columns)
    assert strict_cross_column["left"] == "closed_on" and strict_cross_column["right"] == "opened_on"
    assert strict_cross_column["operator"] == "<"
    negated_before = parse_business_requirement("opened_on should not be before closed_on", columns)
    assert negated_before["status"] == "REQUIRES_USER_INPUT"


def test_selected_rules_validate_deterministically_and_count_failed_records() -> None:
    frame = pd.DataFrame({"reading": [1, -3, None]})
    rule = next(rule for rule in discover_rules() if rule["rule_id"] == "DQ-NUMERIC-BOUNDS")
    failures = validate_rule(frame, rule, {"column": "reading", "minimum": 0})
    assert len(failures) == 1
    assert failures[0]["row_index"] == 1
    score = calculate_score([{**rule, "configuration": {}}], failures, len(frame))
    assert score["value"] == 66.67


def test_rule_plugin_is_discovered_recommended_configured_and_executed() -> None:
    frame = pd.DataFrame({"unmapped_text": ["sufficient", "go"]})
    profile = profile_frame(frame)
    rules = discover_rules()
    rule = next(item for item in rules if item["rule_id"] == "DQ-MINIMUM-TEXT-LENGTH")
    recommendation = next(item for item in recommend_rules(frame, profile) if item["rule_id"] == rule["rule_id"])
    assert recommendation["applicability"] == "REQUIRES_USER_INPUT"
    assert recommendation["configuration_defaults"]["column"] == "unmapped_text"
    failures = validate_rule(frame, rule, {"column": "unmapped_text", "minimum_length": 3})
    assert len(failures) == 1
    assert failures[0]["rule_id"] == rule["rule_id"]


def test_llm_metadata_excludes_values_and_rejects_unknown_columns() -> None:
    frame = pd.DataFrame({"sensitive_field": ["private-sample-a", "private-sample-b"]})
    profile = profile_frame(frame)
    rules = discover_rules()
    payload = metadata_for_llm(profile, rules)
    serialized = json.dumps(payload)
    assert "private-sample-a" not in serialized
    assert "top_values" not in serialized
    response = {"recommendations": [{"rule_id": rules[0]["rule_id"], "applicability": "RECOMMENDED", "confidence": 0.9, "columns": ["invented_column"], "reason": "Guess", "evidence": [], "requires_user_input": False}]}
    assert validate_ai_recommendations(response, profile, rules) == {}


def test_api_requires_approval_and_writes_dynamic_accepted_rejected_outputs(tmp_path, monkeypatch) -> None:
    upload_dir = tmp_path / "uploads"
    run_dir = tmp_path / "runs"
    upload_dir.mkdir()
    run_dir.mkdir()
    monkeypatch.setattr(storage_module, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(storage_module, "RUN_DIR", run_dir)
    monkeypatch.setattr(api, "UPLOAD_DIR", upload_dir)

    client = authenticated_client()
    csv_content = "artifact,reading" + chr(10) + "R1,5" + chr(10) + "R2,-3" + chr(10) + "R3," + chr(10)
    uploaded = client.post("/api/datasets", files={"file": ("arbitrary_export.csv", csv_content, "text/csv")})
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    dataset_id = body["dataset"]["dataset_id"]
    assert body["profile"]["column_count"] == 2
    rows = client.get(f"/api/datasets/{dataset_id}/rows", params={"offset": 0, "limit": 1, "sort_by": "artifact", "sort_direction": "desc"}).json()
    assert rows["total_count"] == 3
    assert rows["rows"][0]["artifact"] == "R3"

    ambiguous = client.post(f"/api/datasets/{dataset_id}/business-rules/parse", json={"requirement": "reading should be valid"})
    assert ambiguous.json()["status"] == "NOT_APPLIED"
    parsed = client.post(f"/api/datasets/{dataset_id}/business-rules/parse", json={"requirement": "reading must be greater than zero"}).json()
    assert parsed["status"] == "PARSED"

    semantic = client.post(f"/api/datasets/{dataset_id}/semantics", json={"column": "artifact", "semantic_type": "IDENTIFIER", "explanation": "User says this is an external key"})
    assert semantic.status_code == 200, semantic.text
    uniqueness = next(rule for rule in semantic.json()["recommendations"] if rule["rule_id"] == "DQ-UNIQUENESS")
    assert uniqueness["applicability"] == "RECOMMENDED"
    assert "artifact" in uniqueness["columns"]

    selection = {"dataset_id": dataset_id, "selected_rules": [{"rule_id": "DQ-COMPLETENESS", "configuration": {"columns": ["reading"]}}, {"rule_id": "DQ-NUMERIC-BOUNDS", "configuration": {"column": "reading", "minimum": 0}}]}
    not_approved = client.post("/api/runs", json=selection)
    assert not_approved.status_code == 400
    executed = client.post("/api/runs", json={**selection, "business_rules": [parsed], "approved": True})
    assert executed.status_code == 200, executed.text
    run = executed.json()
    assert run["accepted_count"] == 1
    assert run["rejected_count"] == 2
    assert run["score"]["value"] < 100
    assert run["approval"]["approved"] is True
    assert run["outputs"]["accepted"] == "arbitrary_export_accepted"
    accepted = pd.read_csv(run_dir / f"{run['run_id']}_accepted.csv")
    rejected = pd.read_csv(run_dir / f"{run['run_id']}_rejected.csv")
    assert {"artifact", "reading", "dq_status", "failed_rules", "failure_reasons", "run_id", "validated_at"}.issubset(accepted.columns)
    assert len(accepted) == 1 and len(rejected) == 2
    download = client.get(f"/api/runs/{run['run_id']}/download/rejected")
    assert download.status_code == 200
    assert "arbitrary_export_rejected.csv" in download.headers["content-disposition"]
    errors_download = client.get(f"/api/runs/{run['run_id']}/download/errors")
    assert errors_download.status_code == 200
    errors = pd.read_csv(io.StringIO(errors_download.text), keep_default_na=False)
    assert len(errors) == run["failure_count"]
    assert list(errors.columns) == ["Status", "Rule / row", "Column", "Reason", "Observed value"]
    assert "arbitrary_export_errors.csv" in errors_download.headers["content-disposition"]
    pdf_download = client.get(f"/api/runs/{run['run_id']}/errors.pdf")
    assert pdf_download.status_code == 200
    assert pdf_download.headers["content-type"] == "application/pdf"
    from pypdf import PdfReader
    pdf = PdfReader(io.BytesIO(pdf_download.content))
    text = "\n".join(page.extract_text() for page in pdf.pages)
    assert "REASON" in text and "OBSERVED VALUE" in text and "Business requirement" in text




def test_api_accepts_parquet_without_schema_assumptions(tmp_path, monkeypatch) -> None:
    upload_dir = tmp_path / "uploads"
    run_dir = tmp_path / "runs"
    upload_dir.mkdir()
    run_dir.mkdir()
    monkeypatch.setattr(storage_module, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(storage_module, "RUN_DIR", run_dir)
    buffer = io.BytesIO()
    pd.DataFrame({"unusual metric": [1.5, 2.5], "free label": ["a", "b"]}).to_parquet(buffer, index=False)
    response = authenticated_client().post("/api/datasets", files={"file": ("unfamiliar.parquet", buffer.getvalue(), "application/octet-stream")})
    assert response.status_code == 200, response.text
    assert response.json()["profile"]["row_count"] == 2
    assert {column["name"] for column in response.json()["profile"]["columns"]} == {"unusual metric", "free label"}


def test_discovered_plugin_runs_through_approved_api_workflow(tmp_path, monkeypatch) -> None:
    upload_dir = tmp_path / "uploads"
    run_dir = tmp_path / "runs"
    upload_dir.mkdir()
    run_dir.mkdir()
    monkeypatch.setattr(storage_module, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(storage_module, "RUN_DIR", run_dir)
    client = authenticated_client()
    upload = client.post("/api/datasets", files={"file": ("free_text.csv", "arbitrary_note" + chr(10) + "x" + chr(10) + "longer" + chr(10), "text/csv")})
    assert upload.status_code == 200, upload.text
    dataset_id = upload.json()["dataset"]["dataset_id"]
    run = client.post("/api/runs", json={
        "dataset_id": dataset_id,
        "selected_rules": [{"rule_id": "DQ-MINIMUM-TEXT-LENGTH", "configuration": {"column": "arbitrary_note", "minimum_length": 3}}],
        "approved": True,
    })
    assert run.status_code == 200, run.text
    assert run.json()["rules_selected"][0]["version"] == "1.0"
    assert run.json()["accepted_count"] == 1
    assert run.json()["rejected_count"] == 1
