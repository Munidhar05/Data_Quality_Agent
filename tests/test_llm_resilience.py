import json
from types import SimpleNamespace

import httpx
import pytest

from backend.llm import OpenAICompatibleProvider, validate_ai_recommendations


@pytest.mark.parametrize("response", [
    {"recommendations": None},
    {"recommendations": {}},
    {"recommendations": [{"rule_id": []}]},
    {"recommendations": [{"rule_id": "r", "applicability": []}]},
    {"recommendations": [{"rule_id": "r", "applicability": "OPTIONAL", "confidence": 0.5, "columns": [{}]}]},
    {"recommendations": [{"rule_id": "r", "applicability": "OPTIONAL", "confidence": True, "columns": [], "evidence": [], "reason": "test"}]},
])
def test_malformed_ai_items_do_not_break_check_selection(response):
    assert validate_ai_recommendations(response, {"columns": [{"name": "amount"}]}, [{"rule_id": "r"}]) == {}


@pytest.mark.parametrize("failure", ["timeout", "http", "invalid_json", "wrong_shape"])
def test_provider_failure_keeps_deterministic_checks_available(monkeypatch, failure):
    import backend.llm as module

    def post(*args, **kwargs):
        if failure == "timeout":
            raise httpx.ReadTimeout("provider timed out")
        if failure == "http":
            request = httpx.Request("POST", "https://example.test/chat/completions")
            raise httpx.HTTPStatusError("unavailable", request=request, response=httpx.Response(503, request=request))
        content = "broken JSON" if failure == "invalid_json" else json.dumps({"recommendations": {}})
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(module.httpx, "post", post)
    result = OpenAICompatibleProvider("test", "https://example.test", "test").recommend({})
    assert result["status"] == "UNAVAILABLE"
    assert "Deterministic checks remain available" in result["message"]
