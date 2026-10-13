from __future__ import annotations

import json
import os
from typing import Any, Protocol

import httpx


class LLMProvider(Protocol):
    def recommend(self, structured_metadata: dict[str, Any]) -> dict[str, Any]: ...


class DisabledLLMProvider:
    """Explicit offline mode; never sends source data or invents a recommendation."""

    configured = False

    def recommend(self, structured_metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            "configured": False,
            "status": "NOT_CONFIGURED",
            "message": "No LLM provider is configured. Review deterministic profile evidence and rule-registry recommendations instead.",
            "metadata_keys_available": sorted(structured_metadata),
        }


class OpenAICompatibleProvider:
    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.configured = True

    def recommend(self, structured_metadata: dict[str, Any]) -> dict[str, Any]:
        prompt = (
            "Review the supplied dataset profile and rule metadata. Return one JSON object exactly shaped as "
            '{"recommendations":[{"rule_id":"...","applicability":"RECOMMENDED|OPTIONAL|NOT_APPLICABLE|REQUIRES_USER_INPUT|CANNOT_EVALUATE",'
            '"confidence":0.0,"columns":["..."],"reason":"...","evidence":["..."],"requires_user_input":false}]} . '
            "Use only the supplied rule IDs and column names. Do not invent business rules, thresholds, reference data, or values. "
            "These are advisory explanations, never validation results; never state PASS or FAIL."
        )
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(structured_metadata, ensure_ascii=True, default=str)}],
                },
                timeout=12.0,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            decoded = json.loads(content)
            if not isinstance(decoded, dict) or not isinstance(decoded.get("recommendations"), list):
                raise ValueError("The model response did not match the required structured format.")
            return {"configured": True, "status": "OK", "recommendations": decoded["recommendations"]}
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            return {"configured": True, "status": "UNAVAILABLE", "message": "The AI provider did not return a valid structured recommendation. Deterministic checks remain available."}


def provider_from_environment() -> LLMProvider:
    api_key = os.getenv("DQ_LLM_API_KEY", "").strip()
    if not api_key:
        return DisabledLLMProvider()
    return OpenAICompatibleProvider(
        api_key=api_key,
        base_url=os.getenv("DQ_LLM_BASE_URL", "https://api.openai.com/v1"),
        model=os.getenv("DQ_LLM_MODEL", "gpt-4o-mini"),
    )


def metadata_for_llm(profile: dict[str, Any], rules: list[dict[str, Any]]) -> dict[str, Any]:
    columns = []
    for column in profile.get("columns", []):
        statistics = {key: value for key, value in column.get("statistics", {}).items() if key not in {"min", "max", "min_date", "max_date"}}
        columns.append({
            "name": column["name"],
            "physical_type": column["physical_type"],
            "null_count": column["null_count"],
            "null_percentage": column["null_percentage"],
            "distinct_count": column["distinct_count"],
            "uniqueness_percentage": column["uniqueness_percentage"],
            "semantic_candidates": column["semantic_candidates"],
            "statistics": statistics,
        })
    rule_metadata = [{key: rule[key] for key in ("rule_id", "name", "category", "description", "applies_to", "severity", "configuration")} for rule in rules]
    return {"row_count": profile["row_count"], "column_count": profile["column_count"], "missing_values": profile["missing_values"], "duplicate_row_count": profile["duplicate_row_count"], "columns": columns, "rules": rule_metadata}


def validate_ai_recommendations(response: dict[str, Any], profile: dict[str, Any], rules: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    known_rules = {rule["rule_id"] for rule in rules}
    known_columns = {column["name"] for column in profile.get("columns", [])}
    valid_statuses = {"RECOMMENDED", "OPTIONAL", "NOT_APPLICABLE", "REQUIRES_USER_INPUT", "CANNOT_EVALUATE"}
    validated = {}
    items = response.get("recommendations", [])
    if not isinstance(items, list):
        return validated
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("rule_id"), str) or item["rule_id"] not in known_rules:
            continue
        if not isinstance(item.get("applicability"), str) or item["applicability"] not in valid_statuses:
            continue
        confidence = item.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            continue
        columns = item.get("columns")
        evidence = item.get("evidence")
        if not isinstance(columns, list) or any(not isinstance(column, str) or column not in known_columns for column in columns):
            continue
        if not isinstance(evidence, list) or not isinstance(item.get("reason"), str):
            continue
        validated[item["rule_id"]] = {
            "applicability": item["applicability"],
            "confidence": round(float(confidence), 2),
            "columns": columns,
            "reason": item["reason"][:500],
            "evidence": [str(value)[:300] for value in evidence[:5]],
            "requires_user_input": bool(item.get("requires_user_input", False)),
        }
    return validated
