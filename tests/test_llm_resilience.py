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


def test_gemini_environment(monkeypatch):
    from backend.llm import GeminiProvider, provider_from_environment
    monkeypatch.setenv("DQ_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert isinstance(provider_from_environment(), GeminiProvider)
    monkeypatch.setenv("DQ_LLM_PROVIDER", "offline")
    assert not provider_from_environment().configured


@pytest.mark.parametrize("failure", [False, True])
def test_gemini_response_and_failure(monkeypatch, failure):
    from backend.llm import GeminiProvider
    def post(url, **kwargs):
        assert kwargs["headers"]["x-goog-api-key"] == "test-key"
        assert "test-key" not in url
        if failure:
            raise httpx.ReadTimeout("timeout")
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps({"recommendations": []})}]}}]})
    monkeypatch.setattr("backend.llm.httpx.post", post)
    assert GeminiProvider("test-key").recommend({})["status"] == ("UNAVAILABLE" if failure else "OK")


@pytest.mark.parametrize("mode", ["valid", "invalid", "timeout", "empty"])
def test_ai_drives_recommendations_with_local_fallback(monkeypatch, mode):
    import pandas as pd
    import backend.app as api
    from backend.profiler import profile_frame
    frame = pd.DataFrame({"amount": [1, 2]})
    profile = profile_frame(frame)
    rule_id = api.discover_rules()[0]["rule_id"]
    item = {"rule_id": rule_id, "applicability": "OPTIONAL", "confidence": 0.77,
            "columns": ["amount"], "reason": "AI supplied reason", "evidence": ["Profile evidence"],
            "requires_user_input": False}
    if mode == "invalid":
        item["columns"] = ["invented"]
    def recommend(metadata):
        if mode == "timeout":
            raise httpx.ReadTimeout("timeout")
        return {"status": "OK", "recommendations": [] if mode == "empty" else [item]}
    monkeypatch.setattr(api, "llm_provider", SimpleNamespace(configured=True, recommend=recommend))
    result, status = api._recommendations(frame, profile)
    selected = next(entry for entry in result if entry["rule_id"] == rule_id)
    if mode == "valid":
        assert selected["recommendation_source"] == "AI"
        assert selected["reason"] == "AI supplied reason"
        assert selected["confidence"] == 0.77
        assert status == "PARTIAL"
    else:
        assert all(entry["recommendation_source"] == "RULE_BASED" for entry in result)
        assert status in {"UNAVAILABLE", "INVALID_RESPONSE"}


@pytest.mark.parametrize("mode", ["success", "failure", "unsupported"])
def test_business_chat_compiles_or_falls_back(monkeypatch, mode):
    import backend.app as api
    def chat(text, columns, previous=None):
        assert columns == ["age"]
        assert previous == "age should be adult"
        if mode == "failure":
            raise RuntimeError("provider failure")
        return "age must be at least 18" if mode == "success" else "invented_column must be positive"
    monkeypatch.setattr(api, "llm_provider", SimpleNamespace(configured=True, business_chat=chat))
    result = api._interpret_business_text("age must be at least 21", ["age"], {}, previous="age should be adult")
    assert result["rules"]
    assert result["rules"][0]["interpretation_source"] == ("AI" if mode == "success" else "RULE_BASED")
    assert (result["mode"] == "ai_chat") == (mode == "success")


@pytest.mark.parametrize("previous", [None, "age should be adult"])
def test_google_sdk_chat_history(monkeypatch, previous):
    from google import genai
    from backend.llm import GeminiProvider
    recorded = {}
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "test-key"
            self.chats = self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def create(self, **kwargs):
            recorded.update(kwargs)
            return self
        def send_message(self, message):
            assert message == "age must be at least 18"
            return SimpleNamespace(text=json.dumps({"requirements": message}))
    monkeypatch.setattr(genai, "Client", Client)
    result = GeminiProvider("test-key").business_chat("age must be at least 18", ["age"], previous)
    assert result == "age must be at least 18"
    assert len(recorded["history"]) == (2 if previous else 0)
    if previous:
        assert recorded["history"][0].parts[0].text == previous
