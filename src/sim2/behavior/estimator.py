from __future__ import annotations

from typing import Any, Callable


class BehaviorDurationEstimator:
    """Calculate BT duration from registered Actions and control flow."""

    def __init__(self, actions: dict[str, dict[str, Any]], behaviors: dict[str, dict[str, Any]], recipes: dict[str, dict[str, Any]], route_distance: Callable[[str | None, str | None], int]):
        self.actions = actions
        self.behaviors = behaviors
        self.recipes = recipes
        self.route_distance = route_distance

    def behavior_minutes(self, behavior_id: str, parameters: dict[str, Any] | None = None, origin: str | None = None) -> int:
        return max(1, self._nodes_minutes((self.behaviors.get(behavior_id) or {}).get("nodes", []), dict(parameters or {}), origin, set()))

    def _nodes_minutes(self, nodes: list[dict[str, Any]], parameters: dict[str, Any], origin: str | None, stack: set[str]) -> int:
        total = 0
        for node in nodes or []:
            node_type = node.get("type")
            if node_type == "action":
                action = self.actions.get(str(node.get("action_id")), {})
                values = self._resolve(dict(node.get("parameters") or {}), parameters)
                total += self._action_minutes(action, values, origin)
            elif node_type in {"selector", "parallel"}:
                children = node.get("children", []) or []
                durations = []
                for child in children:
                    if isinstance(child, str) and child not in stack:
                        durations.append(self._nodes_minutes((self.behaviors.get(child) or {}).get("nodes", []), parameters, origin, stack | {child}))
                    elif isinstance(child, dict):
                        durations.append(self._nodes_minutes([child], parameters, origin, stack))
                total += max(durations, default=0)
            elif node_type in {"sequence", "root"}:
                total += self._nodes_minutes(node.get("children", []) or [], parameters, origin, stack)
        return total

    def _action_minutes(self, action: dict[str, Any], parameters: dict[str, Any], origin: str | None) -> int:
        duration = action.get("duration") or {}
        if isinstance(duration.get("minutes"), (int, float)):
            return max(1, int(duration["minutes"]))
        mode = duration.get("mode")
        if mode == "route_distance":
            return max(1, self.route_distance(origin, parameters.get("location_id") or parameters.get("destination_id")))
        if mode == "recipe_duration":
            recipe = self.recipes.get(str(parameters.get("recipe_id")), {})
            return max(1, int(recipe.get("duration_minutes", duration.get("default_minutes", 1))))
        return max(1, int(duration.get("default_minutes", action.get("default_minutes", 1))))

    @staticmethod
    def _resolve(parameters: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        return {key: context.get(value.removeprefix("context."), value) if isinstance(value, str) and value.startswith("context.") else value for key, value in parameters.items()}
