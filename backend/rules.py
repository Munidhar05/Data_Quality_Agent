from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from backend.rule_registry import RULE_REGISTRY

OPERATORS = {"<": lambda left, right: left < right, "<=": lambda left, right: left <= right, "==": lambda left, right: left == right, "!=": lambda left, right: left != right, ">=": lambda left, right: left >= right, ">": lambda left, right: left > right}


def discover_rules() -> list[dict[str, Any]]:
    return RULE_REGISTRY.discover()


def _column(frame: pd.DataFrame, name: Any) -> bool:
    return isinstance(name, str) and name in frame.columns


def recommend_rules(frame: pd.DataFrame, profile: dict[str, Any]) -> list[dict[str, Any]]:
    columns = {item["name"]: item for item in profile["columns"]}
    recommendations = []
    for rule in discover_rules():
        plugin = RULE_REGISTRY.plugin_for(rule["rule_id"])
        if plugin:
            applicability = plugin.check_applicability(profile)
            recommendations.append({
                **rule,
                **applicability,
                "reason": rule["description"],
                "configuration_defaults": plugin.generate_configuration(profile),
            })
            continue
        predicate = rule["predicate"]
        selected_columns: list[str] = []
        confidence = 0.5
        status = "OPTIONAL"
        evidence: list[str] = []
        requires_input = False

        if predicate == "not_null":
            selected_columns = [name for name, item in columns.items() if item["null_count"] > 0]
            missing = sum(columns[name]["null_count"] for name in selected_columns)
            status = "RECOMMENDED" if selected_columns else "OPTIONAL"
            confidence = 0.99 if selected_columns else 0.55
            evidence.append(f"{missing} missing values across {len(selected_columns)} columns." if selected_columns else "No missing values were observed in the current data.")
        elif predicate == "unique":
            selected_columns = [name for name, item in columns.items() if any(candidate["type"] == "IDENTIFIER" and candidate["confidence"] >= 0.8 for candidate in item["semantic_candidates"])]
            status = "RECOMMENDED" if selected_columns else "OPTIONAL"
            confidence = max((next(candidate["confidence"] for candidate in columns[name]["semantic_candidates"] if candidate["type"] == "IDENTIFIER") for name in selected_columns), default=0.45)
            evidence.append(f"{len(selected_columns)} column(s) show high identifier-like distinctness." if selected_columns else "No column has strong identifier-like evidence; select columns only if uniqueness is a business requirement.")
        elif predicate == "unique_rows":
            duplicates = int(frame.duplicated().sum())
            status = "RECOMMENDED" if duplicates else "OPTIONAL"
            confidence = 0.99 if duplicates else 0.6
            evidence.append(f"{duplicates} complete duplicate row(s) observed.")
        elif predicate == "semantic_format":
            matching = [(name, candidate) for name, item in columns.items() for candidate in item["semantic_candidates"] if candidate["type"] == "EMAIL" and candidate["confidence"] >= 0.8]
            selected_columns = [name for name, _ in matching]
            status = "RECOMMENDED" if matching else "NOT_APPLICABLE"
            confidence = max((candidate["confidence"] for _, candidate in matching), default=0.0)
            evidence.extend(f"{name}: {candidate['evidence'][0]}" for name, candidate in matching)
        elif predicate == "numeric_bounds":
            selected_columns = [name for name, item in columns.items() if item["statistics"].get("mean") is not None]
            status = "REQUIRES_USER_INPUT" if selected_columns else "NOT_APPLICABLE"
            requires_input = bool(selected_columns)
            confidence = 0.9 if selected_columns else 0.0
            evidence.append("Choose a column and define the allowed minimum and/or maximum; no domain limits are assumed.")
        elif predicate == "allowed_values":
            selected_columns = [name for name, item in columns.items() if len(item["top_values"]) <= 30 and item["top_values"]]
            status = "REQUIRES_USER_INPUT" if selected_columns else "NOT_APPLICABLE"
            requires_input = bool(selected_columns)
            confidence = 0.7 if selected_columns else 0.0
            evidence.append("Observed categories are discoverable, but only you can confirm the approved domain.")
        elif predicate in {"in_reference", "column_comparison"}:
            status = "REQUIRES_USER_INPUT"
            requires_input = True
            confidence = 0.7
            evidence.append("Explicit reference values or a user-approved column relationship are required; neither is inferred.")
        elif predicate == "regex_match":
            selected_columns = [name for name, item in columns.items() if item["statistics"].get("mean_length") is not None]
            status = "REQUIRES_USER_INPUT" if selected_columns else "NOT_APPLICABLE"
            requires_input = bool(selected_columns)
            confidence = 0.65 if selected_columns else 0.0
            evidence.append("Provide an approved pattern before this rule can run.")
        elif predicate == "castable_type":
            selected_columns = [name for name, item in columns.items() if item["physical_type"] in {"object", "string", "str"}]
            status = "OPTIONAL" if selected_columns else "NOT_APPLICABLE"
            confidence = 0.55 if selected_columns else 0.0
            evidence.append("A target type must be selected explicitly; the source values remain unchanged.")

        recommendations.append({
            **rule,
            "applicability": status,
            "confidence": round(float(confidence), 2),
            "columns": selected_columns,
            "evidence": evidence,
            "requires_user_input": requires_input,
            "reason": rule["description"],
        })
    return recommendations


def parse_business_requirement(text: str, columns: list[str]) -> dict[str, Any]:
    requirement = text.strip()
    if not requirement:
        return {"status": "REQUIRES_USER_INPUT", "clarification": "Describe the condition you want to enforce."}

    mentioned = [name for name in columns if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", requirement, re.IGNORECASE)]
    mentioned.sort(key=lambda name: re.search(rf"(?<!\w){re.escape(name)}(?!\w)", requirement, re.IGNORECASE).start())
    if len(mentioned) > 2:
        return {"status": "REQUIRES_USER_INPUT", "clarification": "This requirement refers to several columns. Please rewrite it as one clear comparison or condition.", "candidate_columns": mentioned}

    normalized = requirement.casefold()
    comparison_terms = [
        (r"(?:greater than or equal to|at least)\s+(-?\d+(?:\.\d+)?)", ">="),
        (r"(?:less than or equal to|at most|no more than)\s+(-?\d+(?:\.\d+)?)", "<="),
        (r"(?:greater than|more than|exceed(?:s)?)\s+(zero|negative|-?\d+(?:\.\d+)?)", ">"),
        (r"(?:less than|below)\s+(-?\d+(?:\.\d+)?)", "<"),
        (r"(?:equal to|equals?)\s+(-?\d+(?:\.\d+)?)", "=="),
    ]
    if len(mentioned) == 1:
        for pattern, operator in comparison_terms:
            match = re.search(pattern, normalized)
            if match:
                raw_value = match.group(1)
                value: int | float = 0 if raw_value in {"zero", "negative"} else float(raw_value)
                if isinstance(value, float) and value.is_integer():
                    value = int(value)
                return {"status": "PARSED", "type": "comparison", "column": mentioned[0], "operator": operator, "value": value, "original_requirement": requirement}
        if re.search(r"(?:positive|greater than zero)", normalized):
            return {"status": "PARSED", "type": "comparison", "column": mentioned[0], "operator": ">", "value": 0, "original_requirement": requirement}

    if len(mentioned) == 2:
        relation = re.search(r"(?:before|not(?:\s+be)?\s+after|cannot(?:\s+be)?\s+after|earlier than|less than or equal to)", normalized)
        negated_before = re.search(r"not(?:\s+be)?\s+before|cannot(?:\s+be)?\s+before", normalized)
        if relation and not negated_before:
            left, right = mentioned
            operator = "<" if re.search(r"\b(?:before|earlier than)\b", normalized) else "<="
            return {"status": "PARSED", "type": "cross_column", "left": left, "operator": operator, "right": right, "original_requirement": requirement}

    if not mentioned:
        return {"status": "REQUIRES_USER_INPUT", "clarification": "I could not match a column name in this requirement. Which dataset column should it apply to?", "candidate_columns": columns}
    return {"status": "REQUIRES_USER_INPUT", "clarification": "I found a column, but this condition is ambiguous. Specify the exact comparison, threshold, approved values, or reference data.", "candidate_columns": mentioned}


def _failure(rule: dict[str, Any], column: str | None, index: Any, actual: Any, expected: str) -> dict[str, Any]:
    return {"rule_id": rule["rule_id"], "rule_name": rule["name"], "column": column, "row_index": int(index) if isinstance(index, (int,)) else str(index), "status": "FAIL", "reason": f"Expected {expected}.", "severity": rule["severity"], "actual_value": None if pd.isna(actual) else str(actual), "expected_condition": expected}


def validate_rule(frame: pd.DataFrame, rule: dict[str, Any], configuration: dict[str, Any]) -> list[dict[str, Any]]:
    plugin = RULE_REGISTRY.plugin_for(rule["rule_id"])
    if plugin:
        return plugin.validate(frame, configuration)
    predicate = rule["predicate"]
    failures: list[dict[str, Any]] = []
    if predicate == "not_null":
        columns = configuration.get("columns", list(frame.columns))
        for name in columns:
            if not _column(frame, name):
                raise ValueError(f"Unknown column: {name}")
            failures.extend(_failure(rule, name, index, None, "a non-missing value") for index in frame.index[frame[name].isna()])
    elif predicate == "unique":
        columns = configuration.get("columns", [])
        if not columns or any(not _column(frame, name) for name in columns):
            raise ValueError("Select one or more existing columns for uniqueness.")
        duplicated = frame.duplicated(subset=columns, keep="first")
        for index in frame.index[duplicated]:
            failures.append(_failure(rule, ", ".join(columns), index, frame.loc[index, columns].to_dict(), "a unique value combination"))
    elif predicate == "unique_rows":
        duplicated = frame.duplicated(keep="first")
        for index in frame.index[duplicated]:
            failures.append(_failure(rule, None, index, "duplicate row", "a unique complete row"))
    elif predicate == "semantic_format":
        name = configuration.get("column")
        if not _column(frame, name):
            raise ValueError("Select an existing column for format validation.")
        invalid = frame[name].notna() & ~frame[name].astype(str).str.strip().str.match(EMAIL_PATTERN)
        for index in frame.index[invalid]:
            failures.append(_failure(rule, name, index, frame.at[index, name], "an email-shaped value, based on observed evidence"))
    elif predicate == "numeric_bounds":
        name = configuration.get("column")
        if not _column(frame, name) or ("minimum" not in configuration and "maximum" not in configuration):
            raise ValueError("Select a column and at least one explicit numeric bound.")
        values = pd.to_numeric(frame[name], errors="coerce")
        invalid = frame[name].notna() & values.isna()
        expected_parts = []
        if "minimum" in configuration:
            invalid |= values < float(configuration["minimum"])
            expected_parts.append(f">= {configuration['minimum']}")
        if "maximum" in configuration:
            invalid |= values > float(configuration["maximum"])
            expected_parts.append(f"<= {configuration['maximum']}")
        for index in frame.index[invalid]:
            failures.append(_failure(rule, name, index, frame.at[index, name], " and ".join(expected_parts)))
    elif predicate in {"allowed_values", "in_reference"}:
        name = configuration.get("column")
        allowed = configuration.get("values", configuration.get("reference_values", []))
        if not _column(frame, name) or not allowed:
            raise ValueError("Select an existing column and provide an explicit non-empty approved value list.")
        accepted = {str(value) for value in allowed}
        invalid = frame[name].notna() & ~frame[name].astype(str).isin(accepted)
        for index in frame.index[invalid]:
            failures.append(_failure(rule, name, index, frame.at[index, name], f"one of {sorted(accepted)}"))
    elif predicate == "column_comparison":
        left, right, operator = configuration.get("left"), configuration.get("right"), configuration.get("operator")
        if not _column(frame, left) or not _column(frame, right) or operator not in OPERATORS:
            raise ValueError("Select two existing columns and a supported comparison operator.")
        comparable = frame[left].notna() & frame[right].notna()
        try:
            valid = OPERATORS[operator](frame[left], frame[right])
        except TypeError as error:
            raise ValueError("The selected columns cannot be compared because their values have incompatible types.") from error
        for index in frame.index[comparable & ~valid]:
            failures.append(_failure(rule, f"{left}, {right}", index, {left: frame.at[index, left], right: frame.at[index, right]}, f"{left} {operator} {right}"))
    elif predicate == "regex_match":
        name, pattern = configuration.get("column"), configuration.get("pattern")
        if not _column(frame, name) or not pattern:
            raise ValueError("Select an existing column and provide a regular expression.")
        try:
            invalid = frame[name].notna() & ~frame[name].astype(str).str.match(pattern)
        except re.error as error:
            raise ValueError(f"The supplied regular expression is invalid: {error}") from error
        for index in frame.index[invalid]:
            failures.append(_failure(rule, name, index, frame.at[index, name], f"a value matching /{pattern}/"))
    elif predicate == "castable_type":
        name, target = configuration.get("column"), configuration.get("target_type")
        if not _column(frame, name) or target not in {"number", "date", "boolean"}:
            raise ValueError("Select an existing column and a supported target type.")
        values = frame[name].dropna()
        if target == "number":
            invalid_mask = pd.to_numeric(values, errors="coerce").isna()
        elif target == "date":
            invalid_mask = pd.to_datetime(values, errors="coerce", format="mixed", utc=True).isna()
        else:
            invalid_mask = ~values.astype(str).str.casefold().isin({"true", "false", "1", "0", "yes", "no"})
        for index in values.index[invalid_mask]:
            failures.append(_failure(rule, name, index, frame.at[index, name], f"a value convertible to {target}"))
    else:
        raise ValueError(f"No validation implementation is registered for predicate '{predicate}'.")
    return failures
