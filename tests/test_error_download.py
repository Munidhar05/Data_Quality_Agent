import io
import json
from uuid import uuid4

import pandas as pd
import pytest
import backend.storage as module


def test_error_export_includes_failures_beyond_audit_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "RUN_DIR", tmp_path)
    storage = module.LocalFileStorage()
    failures = [{"status": "FAIL", "row_index": i, "reason": 'Bad value, "quoted"', "actual_value": "line1\nline2"} for i in range(5003)]
    run = {"run_id": uuid4().hex, "failure_count": len(failures), "failures": failures[:5000]}
    storage.save_run(run, pd.DataFrame(), pd.DataFrame(), failures=failures)
    output = pd.read_csv(storage.output_path(run["run_id"], "errors"), keep_default_na=False)
    assert len(output) == 5003
    assert output.iloc[-1]["Rule / row"].endswith("Row 5002")
    assert output.iloc[0]["Reason"] == failures[0]["reason"]
    assert output.iloc[0]["Observed value"] == failures[0]["actual_value"]


def test_legacy_error_export_and_empty_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "RUN_DIR", tmp_path)
    storage = module.LocalFileStorage()
    run = {"run_id": uuid4().hex, "failure_count": 0, "failures": []}
    storage.save_run(run, pd.DataFrame(), pd.DataFrame())
    output = pd.read_csv(storage.output_path(run["run_id"], "errors"))
    assert output.empty and "Reason" in output.columns
    run["run_id"] = uuid4().hex
    run["failure_count"] = 5001
    storage.save_run(run, pd.DataFrame(), pd.DataFrame())
    with pytest.raises(FileNotFoundError, match="Run validation again"):
        storage.output_path(run["run_id"], "errors")


def test_existing_raw_error_csv_is_converted_without_losing_capped_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "RUN_DIR", tmp_path)
    storage = module.LocalFileStorage()
    failures = [{"status":"FAIL", "rule_name":"Business requirement", "row_index":i, "column":"quantity", "reason":"Invalid quantity", "expected_condition":"quantity > 0", "actual_value":"-2"} for i in range(5001)]
    run = {"run_id":uuid4().hex, "failure_count":5001, "failures":failures[:5000]}
    storage.save_run(run, pd.DataFrame(), pd.DataFrame())
    pd.DataFrame(failures).to_csv(tmp_path / f"{run['run_id']}_errors.csv", index=False)
    output = pd.read_csv(storage.output_path(run["run_id"], "errors"), keep_default_na=False)
    assert list(output.columns) == ["Status", "Rule / row", "Column", "Reason", "Observed value"]
    assert len(output) == 5001
    assert output.iloc[0]["Rule / row"] == "Business requirement\nRow 0"
    assert output.iloc[0]["Reason"] == "Invalid quantity\nquantity > 0"
