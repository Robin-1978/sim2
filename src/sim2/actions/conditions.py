from __future__ import annotations

import re
from typing import Any

from ..dsl.expressions import (
    BinaryExpression,
    Expression,
    ExpressionSyntaxError,
    LiteralExpression,
    ReferenceExpression,
    UnaryExpression,
    parse_expression,
)


_UNKNOWN = object()


class ConditionEngine:
    """Evaluate the small declarative predicate language used by definitions.

    Package authors write readable predicates such as ``actor.can_explore`` or
    ``destination.reachable``.  The engine never evaluates Python or SQL; an
    unknown predicate is false and is returned to Platform as a failed check.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        config = config or {}
        self.vital_attribute = str(config.get("vital_attribute")) if config.get("vital_attribute") else None
        self.capacity_attribute = str(config.get("capacity_attribute")) if config.get("capacity_attribute") else None
        self.capability_aliases = {
            str(key): [str(value)] if isinstance(value, str) else [str(item) for item in (value or [])]
            for key, value in (config.get("capability_aliases", {}) or {}).items()
        }
        groups = config.get("location_tag_groups", {}) or {}
        self.location_tag_groups = {
            str(key): {str(item) for item in (value if isinstance(value, list) else [value])}
            for key, value in groups.items()
        }
        self.public_locations_are_safe = bool(config.get("public_locations_are_safe", True))

    def evaluate(self, expression: str, *, agent: dict[str, Any], target: dict[str, Any] | None = None, world: dict[str, Any] | None = None, context: dict[str, Any] | None = None) -> bool:
        expression = str(expression or "").strip()
        if not expression:
            return True
        try:
            tree = parse_expression(expression)
            value = self._evaluate_tree(tree, agent, target, world, context)
        except (ExpressionSyntaxError, TypeError, ValueError):
            return False
        return bool(value) if value is not None else False

    def evaluate_known(self, expression: str, *, agent: dict[str, Any], target: dict[str, Any] | None = None, world: dict[str, Any] | None = None, context: dict[str, Any] | None = None) -> bool | None:
        """Evaluate a predicate when it is part of the declarative language.

        Action result fields historically contain both machine predicates
        (for example ``actor.state.energy >= 80``) and human result labels
        (for example ``dialogue.completed``).  The normal evaluator must keep
        unknown labels false for entry checks.  Completion checks need a
        tri-state result so a label can remain an outcome contract without
        being mistaken for a failed machine predicate.  ``None`` means the
        expression is not machine-evaluable with the available projection.
        """

        expression = str(expression or "").strip()
        if not expression:
            return True
        try:
            tree = parse_expression(expression)
            value = self._evaluate_tree(tree, agent, target, world, context, known=True)
        except (ExpressionSyntaxError, TypeError, ValueError):
            return None
        return None if value is _UNKNOWN else bool(value)

    def _evaluate_tree(
        self,
        tree: Expression,
        agent: dict[str, Any],
        target: dict[str, Any] | None,
        world: dict[str, Any] | None,
        context: dict[str, Any] | None,
        *,
        known: bool = False,
    ) -> Any:
        if isinstance(tree, LiteralExpression):
            if isinstance(tree.value, list):
                return [
                    self._evaluate_tree(item, agent, target, world, context, known=known)
                    if isinstance(item, Expression) else item
                    for item in tree.value
                ]
            return tree.value
        if isinstance(tree, ReferenceExpression):
            if known and not self._known_path(tree.path):
                return _UNKNOWN
            return self._value(tree.path, agent, target, world, context)
        if isinstance(tree, UnaryExpression):
            value = self._evaluate_tree(tree.operand, agent, target, world, context, known=known)
            if value is _UNKNOWN:
                return _UNKNOWN
            if tree.operator == "not":
                return not bool(value)
            if tree.operator == "+":
                return +value
            if tree.operator == "-":
                return -value
            return value
        if not isinstance(tree, BinaryExpression):
            return _UNKNOWN if known else None
        if tree.operator == "and":
            left = self._evaluate_tree(tree.left, agent, target, world, context, known=known)
            if left is not _UNKNOWN and not bool(left):
                return False
            right = self._evaluate_tree(tree.right, agent, target, world, context, known=known)
            if right is not _UNKNOWN and not bool(right):
                return False
            return _UNKNOWN if known and (left is _UNKNOWN or right is _UNKNOWN) else True
        if tree.operator == "or":
            left = self._evaluate_tree(tree.left, agent, target, world, context, known=known)
            if left is not _UNKNOWN and bool(left):
                return True
            right = self._evaluate_tree(tree.right, agent, target, world, context, known=known)
            if right is not _UNKNOWN and bool(right):
                return True
            return _UNKNOWN if known and (left is _UNKNOWN or right is _UNKNOWN) else False
        left = self._evaluate_tree(tree.left, agent, target, world, context, known=known)
        right = self._evaluate_tree(tree.right, agent, target, world, context, known=known)
        if left is _UNKNOWN or right is _UNKNOWN:
            return _UNKNOWN
        if left is None and tree.operator in {"in", "not_in"}:
            return tree.operator == "not_in"
        try:
            if tree.operator == "in":
                return left in right
            if tree.operator == "not_in":
                return left not in right
            if tree.operator == "+":
                return left + right
            if tree.operator == "-":
                return left - right
            if tree.operator == "*":
                return left * right
            if tree.operator == "/":
                return left / right if right != 0 else 0
            return self._compare(left, tree.operator, right)
        except (TypeError, KeyError):
            return False

    @staticmethod
    def _compare(left: Any, operator: str, right: Any) -> bool:
        """Compare numeric configuration values without binary-edge errors."""

        if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
            epsilon = 1e-9 * max(1.0, abs(float(left)), abs(float(right)))
            if operator == "==":
                return abs(float(left) - float(right)) <= epsilon
            if operator == "!=":
                return abs(float(left) - float(right)) > epsilon
            if operator == ">=":
                return float(left) + epsilon >= float(right)
            if operator == "<=":
                return float(left) - epsilon <= float(right)
            if operator == ">":
                return float(left) > float(right) + epsilon
            if operator == "<":
                return float(left) < float(right) - epsilon
        return {"==": left == right, "!=": left != right, ">=": left >= right, "<=": left <= right, ">": left > right, "<": left < right}[operator]

    @staticmethod
    def _known_path(path: str) -> bool:
        """Return whether a path belongs to the generic world projection."""

        path = str(path or "").strip()
        if path in {"true", "false"} or path.startswith(('"', "'", "[", "{")):
            return True
        try:
            float(path)
            return True
        except ValueError:
            pass
        if path.startswith((
            "actor.need.", "actor.state.", "actor.inventory.",
            "actor.permission.", "actor.knowledge.", "actor.can_", "actor.is_", "world.",
            "context.", "event.recent.",
        )):
            return True
        return path in {
            "actor.alive", "actor.incapacitated", "actor.role", "actor.mobile",
            "actor.faction_id", "actor.at_location", "actor.exhausted", "actor.stunned",
            "inventory.available", "inventory.item_available", "actor.inventory_item_available",
            "route.known", "destination.reachable", "area.unknown", "area.not_fully_mapped",
            "object.available", "research.new_fact_available", "secret.source_available",
            "target.available", "target.visible", "location.resource_node",
            "location.health_service", "location.safe", "location.reachable", "station.available",
            "patient.available", "patient.in_clinic", "counterparty.available", "resource.available",
            "inputs.available", "recipe.available", "medicine.available", "communication.channel_open",
            "institution.allocation_possible",
        } or path.startswith("location.tag.")

    def _value(self, path: str, agent: dict[str, Any], target: dict[str, Any] | None, world: dict[str, Any] | None, context: dict[str, Any] | None) -> Any:
        target = target or {}
        world = world or {}
        context = context or {}
        state = agent.get("initial_state", {})
        if path == "actor.alive":
            if self.vital_attribute:
                return float(state.get(self.vital_attribute, 0) or 0) > 0
            return str(state.get("status", "alive")).casefold() not in {"dead", "unconscious", "incapacitated"}
        if path == "actor.incapacitated":
            vital_zero = self.vital_attribute and float(state.get(self.vital_attribute, 0) or 0) <= 0
            return bool(vital_zero or "incapacitated" in state.get("conditions", []) or str(state.get("status", "")).casefold() in {"dead", "unconscious"})
        if path == "actor.role":
            return agent.get("role")
        if path.startswith("actor.need."):
            return (agent.get("needs", {}) or {}).get(path.removeprefix("actor.need."), state.get(path.removeprefix("actor.need.")))
        if path.startswith("actor.state."):
            return state.get(path.removeprefix("actor.state."))
        if path.startswith("actor.inventory."):
            return (agent.get("inventory", {}) or {}).get(path.removeprefix("actor.inventory."), 0)
        if path == "actor.mobile":
            return self._value("actor.alive", agent, target, world, context)
        if path == "inventory.available":
            inventory = agent.get("inventory", {}) or {}
            item_id = context.get("item_id") or target.get("item_id")
            if item_id:
                return float(inventory.get(str(item_id), 0) or 0) >= 1
            candidates = context.get("inventory_items", []) or []
            return any(float(inventory.get(str(item), 0) or 0) >= 1 for item in candidates)
        if path in {"inventory.item_available", "actor.inventory_item_available"}:
            inventory = agent.get("inventory", {}) or {}
            item_id = context.get("item_id") or target.get("item_id") or context.get("entity_id") or target.get("entity_id")
            return bool(item_id) and float(inventory.get(str(item_id), 0) or 0) >= 1
        if path.startswith("actor.can_"):
            capability = path.removeprefix("actor.can_")
            candidates = self.capability_aliases.get(capability, [capability])
            value = max((agent.get("capabilities", {}).get(item, 0) for item in candidates), default=0)
            return float(value or 0) > 0
        if path == "actor.faction_id":
            factions = agent.get("faction_ids", [])
            return factions[0] if factions else None
        if path.startswith("actor.permission."):
            permissions = agent.get("permissions", {})
            return permissions.get(path.removeprefix("actor.permission."), False)
        if path.startswith("actor.knowledge."):
            wanted = path.removeprefix("actor.knowledge.")
            normalized = lambda value: re.sub(r"[^\w]+", "_", str(value), flags=re.UNICODE).strip("_").casefold()
            return normalized(wanted) in {normalized(item) for item in (context.get("known_fact_keys", []) or [])}
        if path.startswith("actor.is_"):
            role = str(agent.get("role", "")).lower()
            expected = path.removeprefix("actor.is_").replace("_", " ")
            return expected in role.replace("_", " ") or float((agent.get("capabilities", {}) or {}).get(expected.replace(" ", "_"), 0) or 0) > 0
        if path == "actor.at_location":
            return bool(state.get("location_id"))
        if path == "route.known":
            destination = target.get("location_id") or context.get("location_id") or context.get("destination_id")
            if not destination:
                return True
            known_destinations = context.get("known_route_destinations")
            if known_destinations is not None:
                return str(destination) in {str(item) for item in known_destinations}
            location = context.get("current_location", {})
            return destination in (location.get("routes", []) if isinstance(location, dict) else []) or destination == state.get("location_id")
        if path == "destination.reachable":
            return self._value("route.known", agent, target, world, context)
        if path in {"area.unknown", "area.not_fully_mapped"}:
            mapped = set(context.get("mapped_areas", []))
            area_id = target.get("area_id") or context.get("area_id")
            return not area_id or area_id not in mapped
        if path == "object.available":
            obj = context.get("current_object", {}) or {}
            obj_state = obj.get("initial_state", {}) if isinstance(obj, dict) else {}
            object_location = obj_state.get("location_id") or (obj.get("location_id") if isinstance(obj, dict) else None)
            current_location = state.get("location_id")
            recovered = obj_state.get("recovered", obj.get("recovered", False) if isinstance(obj, dict) else False)
            return bool(obj and object_location == current_location and not recovered)
        if path == "research.new_fact_available":
            source_id = target.get("source_id") or context.get("source_id")
            if not source_id:
                return False
            known = {str(item) for item in (context.get("known_fact_keys", []) or [])}
            discoveries = context.get("research_discoveries") or []
            if isinstance(discoveries, dict):
                discoveries = [discoveries]
            # A source can expose multiple stages.  A completed first stage
            # must not make later, independently configured questions
            # disappear from the executable catalog.
            if discoveries:
                return any(
                    str(item.get("id") or "") not in known
                    and str(item.get("status") or "active").casefold() not in {"obsolete", "disabled"}
                    for item in discoveries
                    if isinstance(item, dict)
                )
            return str(source_id) not in {str(item) for item in context.get("researched_sources", [])}
        if path == "source.accessible":
            source = context.get("source", {}) or {}
            if not isinstance(source, dict) or not source:
                return False
            source_state = source.get("initial_state", {}) if isinstance(source.get("initial_state"), dict) else {}
            source_location = source_state.get("location_id") or source.get("location_id")
            if source_location and str(source_location) != str(state.get("location_id") or ""):
                return False
            return not bool(source_state.get("destroyed", source.get("destroyed", False)))
        if path == "secret.source_available":
            seeded = {
                str(item.get("id") or "") if isinstance(item, dict) else str(item)
                for item in (agent.get("knowledge_seed", []) or [])
            }
            known = {str(item) for item in (context.get("known_fact_keys", []) or [])}
            return any(item.startswith("secret:") for item in seeded | known)
        if path in {"actor.exhausted", "actor.stunned"}:
            capacity_zero = self.capacity_attribute and float(state.get(self.capacity_attribute, 0) or 0) <= 0
            return bool(capacity_zero or path.removeprefix("actor.") in state.get("conditions", []))
        if path.startswith("target."):
            if path == "target.available":
                # A target-dependent action is not executable until Platform
                # resolves an actual target. Treating an omitted target as
                # available makes an apparently valid BT fail at execution.
                return bool(target.get("available", context.get("target.available", False)))
            if path == "target.visible":
                return bool(target.get("visible", context.get("target.visible", False)))
            return self._path_value(target, path.removeprefix("target."))
        if path.startswith("world."):
            return self._path_value(world, path.removeprefix("world."))
        if path.startswith("context."):
            return self._path_value(context, path.removeprefix("context."))
        if path.startswith("event.recent."):
            event_type = path.removeprefix("event.recent.")
            return event_type in {str(item) for item in (context.get("recent_event_types", []) or [])}
        if path == "location.resource_node":
            location = context.get("current_location", {}) or {}
            tags = self.location_tag_groups.get("resource_node", set())
            return bool(set(location.get("tags", []) or []) & tags)
        if path.startswith("location.tag."):
            location = context.get("current_location", {}) or {}
            return path.removeprefix("location.tag.") in set(location.get("tags", []) or [])
        if path == "location.health_service":
            location = context.get("current_location", {}) or {}
            tags = self.location_tag_groups.get("health_service", set())
            return bool(set(location.get("tags", []) or []) & tags)
        if path == "station.available":
            required = set(context.get("station_tags", []) or [])
            current = set((context.get("current_location", {}) or {}).get("tags", []) or [])
            return not required or bool(required & current)
        if path in {"patient.available", "counterparty.available"}:
            return bool(context.get(path, target.get("available", False)))
        if path == "institution.allocation_possible":
            return bool(context.get("institution.allocation_possible", False))
        if path in {"location.safe", "destination.safe_or_defensible", "target.available", "patient.in_clinic", "resource.available", "inputs.available", "recipe.available", "medicine.available", "communication.channel_open", "location.reachable"}:
            if path == "location.safe":
                location = context.get("current_location", {})
                safe_tags = self.location_tag_groups.get("safe", set())
                return bool(location.get("safe", False) or (self.public_locations_are_safe and location.get("public", False) is True) or set(location.get("tags", []) or []) & safe_tags)
            return bool(context.get(path, True))
        return self._path_value(context, path)

    @staticmethod
    def _path_value(value: Any, path: str) -> Any:
        current = value
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current
