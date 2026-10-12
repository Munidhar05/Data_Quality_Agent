from __future__ import annotations

from typing import Any, Protocol

import pandas as pd

from backend.rules import validate_rule


class ExecutionEngine(Protocol):
    """Execution boundary; alternative engines can implement the same contract."""

    def execute_rule(self, frame: pd.DataFrame, rule: dict[str, Any], configuration: dict[str, Any]) -> list[dict[str, Any]]: ...


class PandasEngine:
    def execute_rule(self, frame: pd.DataFrame, rule: dict[str, Any], configuration: dict[str, Any]) -> list[dict[str, Any]]:
        return validate_rule(frame, rule, configuration)
