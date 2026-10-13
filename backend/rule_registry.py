from __future__ import annotations

import json
from importlib import import_module, metadata
from pathlib import Path
from pkgutil import iter_modules
from typing import Any, Protocol

REGISTRY_PATH = Path(__file__).with_name("rule_registry.json")


class RulePlugin(Protocol):
    def get_metadata(self) -> dict[str, Any]: ...

    def check_applicability(self, profile: dict[str, Any]) -> dict[str, Any]: ...

    def generate_configuration(self, profile: dict[str, Any]) -> dict[str, Any]: ...

    def validate(self, data: Any, configuration: dict[str, Any]) -> list[dict[str, Any]]: ...

    def explain_failure(self, result: dict[str, Any]) -> str: ...


class RuleRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, RulePlugin] = {}

    def _discover_plugins(self) -> list[RulePlugin]:
        plugins: list[RulePlugin] = []
        package = import_module("backend.rule_plugins")
        for module_info in iter_modules(package.__path__, f"{package.__name__}."):
            module = import_module(module_info.name)
            plugins.extend(getattr(module, "RULE_PLUGINS", []))
        try:
            entry_points = metadata.entry_points(group="feuji_dq.rules")
        except TypeError:
            entry_points = metadata.entry_points().get("feuji_dq.rules", [])
        for entry_point in entry_points:
            loaded = entry_point.load()
            plugins.append(loaded() if isinstance(loaded, type) else loaded)
        for plugin in plugins:
            for method in ("get_metadata", "check_applicability", "generate_configuration", "validate", "explain_failure"):
                if not callable(getattr(plugin, method, None)):
                    raise TypeError(f"Rule plugin {plugin!r} does not implement {method}().")
        return plugins

    def discover(self) -> list[dict[str, Any]]:
        definitions = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        plugins_by_id = {}
        for plugin in self._discover_plugins():
            rule = plugin.get_metadata()
            if not rule.get("rule_id") or not rule.get("predicate"):
                raise ValueError("Each rule plugin must declare a rule_id and predicate in its metadata.")
            if any(item["rule_id"] == rule["rule_id"] for item in definitions):
                raise ValueError(f"Rule ID {rule['rule_id']} is already defined in the rule registry.")
            if rule["rule_id"] in plugins_by_id:
                raise ValueError(f"Rule plugin ID {rule['rule_id']} is registered more than once.")
            plugins_by_id[rule["rule_id"]] = plugin
            definitions.append(rule)
        self._plugins = plugins_by_id
        return definitions

    def plugin_for(self, rule_id: str) -> RulePlugin | None:
        if not self._plugins:
            self.discover()
        return self._plugins.get(rule_id)


RULE_REGISTRY = RuleRegistry()
