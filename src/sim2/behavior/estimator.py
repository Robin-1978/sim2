from __future__ import annotations

from typing import Any, Callable


class BehaviorDurationEstimator:
    """Calculate BT duration from registered Actions and control flow."""

    def __init__(self, actions: dict[str, dict[str, Any]], behaviors: dict[str, dict[str, Any]], recipes: dict[str, dict[str, Any]], route_distance: Callable[[str | None, str | None], int], *, capabilities: dict[str, Any] | None = None, capability_modifiers: dict[str, Any] | None = None):
        self.actions = actions
        self.behaviors = behaviors
        self.recipes = recipes
        self.route_distance = route_distance
        self.capabilities = capabilities or {}
        self.capability_modifiers = capability_modifiers or {}

    def behavior_minutes(self, behavior_id: str, parameters: dict[str, Any] | None = None, origin: str | None = None) -> int:
        behavior = self.behaviors.get(behavior_id) or {}
        no_op_minutes = max(0, int(behavior.get("no_op_travel_minutes", 1) or 0))
        return max(1, self._nodes_minutes(behavior.get("nodes", []), dict(parameters or {}), origin, set(), no_op_minutes))

    def _nodes_minutes(self, nodes: list[dict[str, Any]], parameters: dict[str, Any], origin: str | None, stack: set[str], no_op_minutes: int = 1) -> int:
        total = 0
        for node in nodes or []:
            node_type = node.get("type")
            if node_type == "action":
                action = self.actions.get(str(node.get("action_id")), {})
                values = self._resolve(dict(node.get("parameters") or {}), parameters)
                total += self._action_minutes(action, values, origin, no_op_minutes)
            elif node_type in {"selector", "parallel"}:
                children = node.get("children", []) or []
                durations = []
                for child in children:
                    if isinstance(child, str) and child not in stack:
                        durations.append(self._nodes_minutes((self.behaviors.get(child) or {}).get("nodes", []), parameters, origin, stack | {child}, no_op_minutes))
                    elif isinstance(child, dict):
                        durations.append(self._nodes_minutes([child], parameters, origin, stack, no_op_minutes))
                total += max(durations, default=0)
            elif node_type in {"sequence", "root"}:
                total += self._nodes_minutes(node.get("children", []) or [], parameters, origin, stack, no_op_minutes)
        return total

    def _action_minutes(self, action: dict[str, Any], parameters: dict[str, Any], origin: str | None, no_op_minutes: int = 1) -> int:
        duration = action.get("duration") or {}
        if isinstance(duration.get("minutes"), (int, float)):
            base = float(duration["minutes"])
        else:
            mode = duration.get("mode")
            if mode == "route_distance":
                base = float(max(0, self.route_distance(origin, parameters.get("location_id") or parameters.get("destination_id"))))
                if base == 0:
                    return max(0, int(no_op_minutes))
            elif mode == "recipe_duration":
                recipe = self.recipes.get(str(parameters.get("recipe_id")), {})
                base = float(max(1, int(recipe.get("duration_minutes", duration.get("default_minutes", 1)))))
            else:
                base = float(max(1, int(duration.get("default_minutes", action.get("default_minutes", 1)))))
        multiplier = 1.0
        for capability, actions in (self.capability_modifiers or {}).items():
            spec = (actions or {}).get(str(action.get("id", ""))) if isinstance(actions, dict) else None
            curve = spec.get("time_multiplier") if isinstance(spec, dict) else None
            if not isinstance(curve, dict):
                continue
            value = float(self.capabilities.get(str(capability), 0) or 0)
            current = float(curve.get("base", 1.0) or 1.0) + float(curve.get("per_value", 0) or 0) * value
            if isinstance(curve.get("min"), (int, float)):
                current = max(float(curve["min"]), current)
            if isinstance(curve.get("max"), (int, float)):
                current = min(float(curve["max"]), current)
            multiplier *= max(0.05, current)
        return max(1, int(round(base * multiplier)))

    @staticmethod
    def _resolve(parameters: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        return {key: context.get(value.removeprefix("context."), value) if isinstance(value, str) and value.startswith("context.") else value for key, value in parameters.items()}
