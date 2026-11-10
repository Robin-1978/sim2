"""Configuration-backed state and need definitions.

The execution engine owns the shape of an active action, while this module
owns the meaning and bounds of world-defined numeric attributes.  A world can
therefore replace health/energy/satiety with different names and semantics.
"""

from __future__ import annotations

from typing import Any


class AttributeSchema:
    def __init__(self, definitions: list[dict[str, Any]] | None = None, rules: dict[str, Any] | None = None):
        self.definitions = [item for item in (definitions or []) if isinstance(item, dict) and item.get("id")]
        self.rules = rules or {}
        self.by_id = {str(item["id"]): item for item in self.definitions}

    def definition(self, name: str) -> dict[str, Any]:
        return dict(self.by_id.get(str(name), {}))

    def kind(self, name: str) -> str:
        return str(self.by_id.get(str(name), {}).get("kind") or "state")

    def minimum(self, name: str, fallback: float = 0.0) -> float:
        value = self.by_id.get(str(name), {}).get("min", fallback)
        return float(value) if isinstance(value, (int, float)) else float(fallback)

    def maximum(self, name: str, fallback: float = 100.0) -> float:
        value = self.by_id.get(str(name), {}).get("max", fallback)
        return float(value) if isinstance(value, (int, float)) else float(fallback)

    def default(self, name: str, fallback: float | None = None) -> Any:
        definition = self.by_id.get(str(name), {})
        if "default" in definition:
            return definition["default"]
        if "initial" in definition:
            return definition["initial"]
        return fallback

    def clamp(self, name: str, value: Any) -> Any:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return value
        minimum = self.minimum(name)
        maximum = self.maximum(name)
        return max(minimum, min(maximum, float(value)))

    def cost_mode(self, name: str) -> str | None:
        configured = self.by_id.get(str(name), {}).get("cost_mode")
        if configured in {"increase", "decrease"}:
            return str(configured)
        return None

    def state_defaults(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for item in self.definitions:
            if str(item.get("kind")) != "state":
                continue
            value = self.default(str(item["id"]), None)
            if value is not None:
                result[str(item["id"])] = value
        return result

    def need_defaults(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for item in self.definitions:
            if str(item.get("kind")) != "need":
                continue
            value = self.default(str(item["id"]), item.get("target", 0))
            if value is not None:
                result[str(item["id"])] = value
        return result

    def vital_attribute(self) -> str | None:
        configured = self.rules.get("vital_attribute")
        if configured and str(configured) in self.by_id:
            return str(configured)
        return next((str(item["id"]) for item in self.definitions if item.get("vital") is True), None)

    def capacity_attribute(self) -> str | None:
        configured = self.rules.get("capacity_attribute")
        if configured and str(configured) in self.by_id:
            return str(configured)
        return next((str(item["id"]) for item in self.definitions if item.get("capacity") is True), None)

    def values(self, state: dict[str, Any], needs: dict[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in self.definitions:
            name = str(item["id"])
            source = state if str(item.get("kind")) == "state" else needs
            if name not in source:
                continue
            result.append({
                "id": name,
                "label": str(item.get("label") or name),
                "unit": str(item.get("unit") or ""),
                "value": source[name],
                "meaning": str(item.get("prompt") or ""),
                "kind": str(item.get("kind") or "state"),
            })
        return result
