from __future__ import annotations

from collections import defaultdict
from typing import Any

SEVERITY_WEIGHTS = {"critical": 4.0, "high": 3.0, "medium": 2.0, "low": 1.0}
CATEGORY_WEIGHTS = {"completeness": 1.0, "validity": 1.0, "uniqueness": 1.0, "consistency": 1.0}


def calculate_score(selected_rules: list[dict[str, Any]], failures: list[dict[str, Any]], row_count: int) -> dict[str, Any] | None:
    if not selected_rules:
        return None

    failures_by_rule: dict[str, set[Any]] = defaultdict(set)
    for failure in failures:
        failures_by_rule[failure["rule_id"]].add(failure["row_index"])

    category_scores: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for rule in selected_rules:
        weight = SEVERITY_WEIGHTS.get(str(rule.get("severity", "medium")).lower(), 1.0)
        failed_records = len(failures_by_rule.get(rule["rule_id"], set()))
        pass_rate = max(0.0, 1.0 - failed_records / max(row_count, 1))
        category_scores.setdefault(rule["category"], []).append((pass_rate, weight))

    breakdown = {}
    weighted_categories = []
    for category, scores in sorted(category_scores.items()):
        severity_weight = sum(weight for _, weight in scores)
        category_score = sum(score * weight for score, weight in scores) / severity_weight
        breakdown[category] = round(category_score * 100, 2)
        weighted_categories.append((category_score, CATEGORY_WEIGHTS.get(category, 1.0)))

    overall = sum(score * weight for score, weight in weighted_categories) / sum(weight for _, weight in weighted_categories)
    return {
        "value": round(overall * 100, 2),
        "breakdown": breakdown,
        "formula": "For each selected rule: pass rate = 1 - distinct failed records / total records. Within each category, average rule pass rates weighted by severity (critical 4, high 3, medium 2, low 1). Overall score is the equally weighted average of represented categories.",
        "category_weights": CATEGORY_WEIGHTS,
        "severity_weights": SEVERITY_WEIGHTS,
    }
