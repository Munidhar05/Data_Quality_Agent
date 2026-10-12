from __future__ import annotations

from typing import Any

import pandas as pd


class MinimumTextLengthRule:
    def get_metadata(self) -> dict[str, Any]:
        return {
            "rule_id": "DQ-MINIMUM-TEXT-LENGTH",
            "version": "1.0",
            "name": "Minimum text length",
            "category": "validity",
            "description": "Check text values against a user-defined minimum character count.",
            "applies_to": "text-like columns and an explicitly approved minimum length",
            "predicate": "minimum_text_length",
            "severity": "low",
            "configuration": {
                "column": {"type": "column", "required": True, "description": "Text column"},
                "minimum_length": {"type": "integer", "required": True, "minimum": 0, "description": "Minimum characters"},
            },
        }

    def check_applicability(self, profile: dict[str, Any]) -> dict[str, Any]:
        columns = [item["name"] for item in profile["columns"] if item["statistics"].get("mean_length") is not None]
        return {
            "applicability": "REQUIRES_USER_INPUT" if columns else "NOT_APPLICABLE",
            "confidence": 0.72 if columns else 0.0,
            "columns": columns,
            "evidence": ["Text-like values were observed; choose a column and define the minimum length."] if columns else ["No text-like columns were observed."],
            "requires_user_input": bool(columns),
        }

    def generate_configuration(self, profile: dict[str, Any]) -> dict[str, Any]:
        applicable = self.check_applicability(profile)["columns"]
        return {"column": applicable[0] if applicable else "", "minimum_length": None}

    def validate(self, data: pd.DataFrame, configuration: dict[str, Any]) -> list[dict[str, Any]]:
        column = configuration.get("column")
        minimum_length = configuration.get("minimum_length")
        if column not in data.columns or not isinstance(minimum_length, int) or minimum_length < 0:
            raise ValueError("Choose a text column and an explicit non-negative minimum length.")
        rule = self.get_metadata()
        failures = []
        for index, value in data[column].items():
            if pd.isna(value) or len(str(value)) >= minimum_length:
                continue
            failures.append({
                "rule_id": rule["rule_id"],
                "rule_name": rule["name"],
                "column": column,
                "row_index": int(index) if isinstance(index, int) else str(index),
                "status": "FAIL",
                "reason": self.explain_failure({"expected_condition": f"at least {minimum_length} characters"}),
                "severity": rule["severity"],
                "actual_value": str(value),
                "expected_condition": f"at least {minimum_length} characters",
            })
        return failures

    def explain_failure(self, result: dict[str, Any]) -> str:
        return f"Expected {result['expected_condition']}."


RULE_PLUGINS = [MinimumTextLengthRule()]
