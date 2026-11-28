from __future__ import annotations

from typing import Any, Callable

from ..world.action_tools import best_time_tool
from .plan import wait_minutes


class BehaviorDurationEstimator:
    """Calculate BT duration from registered Actions and control flow."""

    def __init__(
        self,
        actions: dict[str, dict[str, Any]],
        behaviors: dict[str, dict[str, Any]],
        recipes: dict[str, dict[str, Any]],
        route_distance: Callable[[str | None, str | None], int],
        *,
        capabilities: dict[str, Any] | None = None,
        inventory: dict[str, Any] | None = None,
        capability_modifiers: dict[str, Any] | None = None,
        travel_speed: Callable[[dict[str, Any]], float] | None = None,
        phase_duration: Callable[[str, int], int | None] | None = None,
    ):
        self.actions = actions
        self.behaviors = behaviors
        self.recipes = recipes
        self.route_distance = route_distance
        self.capabilities = capabilities or {}
        self.inventory = inventory or {}
        self.capability_modifiers = capability_modifiers or {}
        self.travel_speed = travel_speed
        self.phase_duration = phase_duration

    def behavior_minutes(self, behavior_id: str, parameters: dict[str, Any] | None = None, origin: str | None = None) -> int:
        behavior = self.behaviors.get(behavior_id) or {}
        no_op_minutes = max(0, int(behavior.get("no_op_travel_minutes", 1) or 0))
        return max(1, self._nodes_minutes(behavior.get("nodes", []), dict(parameters or {}), origin, {behavior_id}, no_op_minutes))

    def _nodes_minutes(self, nodes: list[dict[str, Any]], parameters: dict[str, Any], origin: str | None, stack: set[str], no_op_minutes: int = 1, elapsed_minutes: int = 0) -> int:
        total = 0
        for node in nodes or []:
            if isinstance(node, str):
                node = {"type": "behavior", "behavior_id": node}
            node_type = node.get("type")
            if node_type == "action":
                action = self.actions.get(str(node.get("action_id")), {})
                values = self._resolve(dict(node.get("parameters") or {}), parameters)
                total += self._action_minutes(action, values, origin, no_op_minutes, elapsed_minutes=elapsed_minutes + total)
            elif node_type == "wait":
                total += wait_minutes(node)
            elif node_type in {"behavior", "subtree"}:
                nested_id = str(node.get("behavior_id"))
                if nested_id not in stack:
                    nested = self.behaviors.get(nested_id) or {}
                    total += self._nodes_minutes(nested.get("nodes", []), parameters, origin, stack | {nested_id}, no_op_minutes, elapsed_minutes + total)
            elif node_type in {"selector", "parallel"}:
                children = node.get("children", []) or []
                total += max(
                    (self._nodes_minutes([child], parameters, origin, stack, no_op_minutes, elapsed_minutes + total) for child in children),
                    default=0,
                )
            elif node_type in {"sequence", "root"}:
                total += self._nodes_minutes(node.get("children", []) or [], parameters, origin, stack, no_op_minutes, elapsed_minutes + total)
        return total

    def _action_minutes(self, action: dict[str, Any], parameters: dict[str, Any], origin: str | None, no_op_minutes: int = 1, *, elapsed_minutes: int = 0, use_tools: bool = True) -> int:
        duration = action.get("duration") or {}
        if duration.get("mode") == "until_day_phase" and self.phase_duration is not None:
            minutes = self.phase_duration(str(duration.get("phase") or ""), elapsed_minutes)
            if minutes is not None:
                return minutes
        if isinstance(duration.get("minutes"), (int, float)):
            base = float(duration["minutes"])
        else:
            mode = duration.get("mode")
            if mode == "route_distance":
                base = float(max(0, self.route_distance(origin, parameters.get("location_id") or parameters.get("destination_id"))))
                if base == 0:
                    return max(0, int(no_op_minutes))
                if self.travel_speed is not None:
                    try:
                        speed = float(self.travel_speed(parameters))
                    except (TypeError, ValueError):
                        speed = 1.0
                    if speed > 0:
                        base /= speed
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
        tool = best_time_tool(action, parameters, self.inventory) if use_tools else None
        if tool:
            multiplier *= tool[1]
        return max(1, int(round(base * multiplier)))

    @staticmethod
    def _resolve(parameters: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        return {key: context.get(value.removeprefix("context."), value) if isinstance(value, str) and value.startswith("context.") else value for key, value in parameters.items()}
