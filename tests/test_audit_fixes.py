import io
import json
import pandas as pd
import pytest
from fastapi.testclient import TestClient
import backend.app as api
import backend.storage as storage


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    for name in ("UPLOAD_DIR", "RUN_DIR"):
        path = tmp_path / name.lower()
        path.mkdir()
        monkeypatch.setattr(storage, name, path)
    monkeypatch.setattr(api, "UPLOAD_DIR", storage.UPLOAD_DIR)
    monkeypatch.setattr(api.llm_provider, "configured", False)


def account(email):
    client = TestClient(api.app)
    data = {"first_name": "Audit", "last_name": "Tester", "company": "Testing", "phone": "9876543210", "email": email, "password": "audit-test-password"}
    assert client.post("/api/auth/signup", json=data).status_code == 201
    assert client.post("/api/auth/login", json={"email": email, "password": data["password"]}).status_code == 200
    return client


def upload(client, data=b"id,amount\n001,10.00\n002,-1.00\n", name="Audit File.csv"):
    response = client.post("/api/datasets", files={"file": (name, data, "text/csv")})
    assert response.status_code == 200, response.text
    return response.json()["dataset"]["dataset_id"]


def test_accounts_cannot_read_change_validate_or_reference_each_others_files(isolated_storage):
    first = account("first@example.com")
    second = account("second@example.com")
    dataset = upload(first)
    response = first.post("/api/runs", json={"dataset_id": dataset, "approved": True, "selected_rules": [{"rule_id": "DQ-CATALOG-005", "configuration": {}}]})
    assert response.status_code == 200, response.text
    run = response.json()["run_id"]
    assert first.get("/api/runs").json()[0]["dataset_filename"] == "Audit File.csv"
    assert second.get("/api/runs").json() == []
    for path in (f"/api/datasets/{dataset}/profile", f"/api/datasets/{dataset}/rows", f"/api/runs/{run}", f"/api/runs/{run}/download/accepted", f"/api/runs/{run}/download/rejected"):
        assert second.get(path).status_code == 404, path
        assert first.get(path).status_code == 200, path
    assert second.post(f"/api/datasets/{dataset}/semantics", json={"column": "id", "semantic_type": "IDENTIFIER"}).status_code == 404
    assert second.post(f"/api/datasets/{dataset}/business-rules/batch", json={"text": "amount must be greater than 0"}).status_code == 404
    assert second.post(f"/api/datasets/{dataset}/business-rules/document", files={"file": ("rules.txt", b"amount must be greater than 0", "text/plain")}).status_code == 404
    assert second.post("/api/runs", json={"dataset_id": dataset, "approved": True, "selected_rules": [{"rule_id": "DQ-CATALOG-005", "configuration": {}}]}).status_code == 404
    own = upload(second)
    reference = second.post("/api/runs", json={"dataset_id": own, "approved": True, "selected_rules": [{"rule_id": "DQ-CATALOG-010", "configuration": {"parameters": {"column": "id", "reference_column": "id", "reference_dataset_id": dataset}}}]})
    assert reference.status_code == 422
    assert first.get("/api/runs").json()[0]["run_id"] == run


def test_csv_original_values_survive_explorer_and_both_outputs(isolated_storage):
    client = account("values@example.com")
    dataset = upload(client, b"id,amount,region,description\n001,10.00,NA,null\n002,-1.00,NULL,NaN\n003,,NA,\n")
    rows = client.get(f"/api/datasets/{dataset}/rows").json()["rows"]
    assert rows[0] == {"id": "001", "amount": "10.00", "region": "NA", "description": "null"}
    parsed = client.post(f"/api/datasets/{dataset}/business-rules/batch", json={"text": "amount must be greater than 0"}).json()
    response = client.post("/api/runs", json={"dataset_id": dataset, "approved": True, "business_rules": parsed["rules"], "selected_rules": [{"rule_id": "DQ-CATALOG-004", "configuration": {"parameters": {"columns": ["amount"]}}}]})
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["accepted_count"] == 1 and run["rejected_count"] == 2
    accepted = pd.read_csv(io.StringIO(client.get(f"/api/runs/{run['run_id']}/download/accepted").text), dtype=str, keep_default_na=False)
    rejected = pd.read_csv(io.StringIO(client.get(f"/api/runs/{run['run_id']}/download/rejected").text), dtype=str, keep_default_na=False)
    assert accepted.iloc[0]["id"] == "001" and accepted.iloc[0]["amount"] == "10.00"
    assert accepted.iloc[0]["region"] == "NA" and accepted.iloc[0]["description"] == "null"
    assert rejected["id"].tolist() == ["002", "003"]
    assert rejected["amount"].tolist() == ["-1.00", ""]


def test_unowned_legacy_files_fail_closed_without_modification(isolated_storage):
    legacy = storage.LocalFileStorage().save_dataset("legacy.csv", b"id\n1\n")
    client = account("legacy@example.com")
    assert client.get(f"/api/datasets/{legacy['dataset_id']}/profile").status_code == 404
    assert json.loads((storage.UPLOAD_DIR / (legacy["dataset_id"] + ".json")).read_text())["owner_id"] is None


def test_admin_is_unavailable_until_a_password_is_configured(monkeypatch):
    monkeypatch.delenv("DQ_ADMIN_PASSWORD", raising=False)
    client = TestClient(api.app)
    assert client.post("/api/admin/login", json={"password": "admin"}).status_code == 503
    monkeypatch.setenv("DQ_ADMIN_PASSWORD", "configured-audit-secret")
    assert client.post("/api/admin/login", json={"password": "admin"}).status_code == 401
    assert client.post("/api/admin/login", json={"password": "configured-audit-secret"}).status_code == 200


def test_numeric_sort_preserves_original_text_and_one_sided_bounds_work(isolated_storage):
    client = account("bounds@example.com")
    dataset = upload(client, b"id,amount\n001,10.00\n002,2.00\n003,-1.00\n")
    rows = client.get(f"/api/datasets/{dataset}/rows", params={"sort_by": "amount"}).json()["rows"]
    assert [row["amount"] for row in rows] == ["-1.00", "2.00", "10.00"]
    for parameters in ({"column": "amount", "minimum": 0}, {"column": "amount", "minimum": 0, "maximum": None}):
        response = client.post("/api/runs", json={"dataset_id": dataset, "approved": True, "selected_rules": [{"rule_id": "DQ-CATALOG-007", "configuration": {"parameters": parameters}}]})
        assert response.status_code == 200, response.text
        assert response.json()["rejected_count"] == 1


def test_result_metadata_does_not_overwrite_input_columns(isolated_storage):
    client = account("metadata@example.com")
    dataset = upload(client, b"id,dq_status,dq_status_2,run_id\n001,source,another,original\n")
    response = client.post("/api/runs", json={"dataset_id": dataset, "approved": True, "selected_rules": [{"rule_id": "DQ-CATALOG-005", "configuration": {}}]})
    assert response.status_code == 200, response.text
    run = response.json()
    output = pd.read_csv(io.StringIO(client.get(f"/api/runs/{run['run_id']}/download/accepted").text), dtype=str)
    assert output.iloc[0]["dq_status"] == "source"
    assert output.iloc[0]["dq_status_2"] == "another"
    assert output.iloc[0]["run_id"] == "original"
    assert output.iloc[0][run["output_metadata_columns"]["dq_status"]] == "ACCEPTED"


@pytest.mark.parametrize("extension", [".xlsx", ".json", ".xml", ".avro"])
def test_added_formats_validate_and_export_original_values(isolated_storage, extension):
    from test_file_inputs import fixture_content
    client = account("formats@example.com")
    dataset = upload(client, fixture_content(extension), "Source" + extension)
    response = client.post("/api/runs", json={"dataset_id": dataset, "approved": True, "selected_rules": [{"rule_id": "DQ-CATALOG-007", "configuration": {"parameters": {"column": "amount", "minimum": 0}}}]})
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["accepted_count"] == 1 and run["rejected_count"] == 1
    for output, expected in (("accepted", "001"), ("rejected", "002")):
        frame = pd.read_csv(io.StringIO(client.get(f"/api/runs/{run['run_id']}/download/{output}").text), dtype=str, keep_default_na=False)
        assert frame.iloc[0]["id"] == expected
    assert client.get("/api/runs").json()[0]["dataset_filename"] == "Source" + extension
