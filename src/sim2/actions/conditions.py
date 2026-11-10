from __future__ import annotations

import re
from typing import Any

import yaml


class ConditionEngine:
    """Evaluate the small declarative predicate language used by definitions.

    Package authors write readable predicates such as ``actor.can_explore`` or
    ``destination.reachable``.  The engine never evaluates Python or SQL; an
    unknown predicate is false and is returned to Platform as a failed check.
    """

    _comparison = re.compile(r"^\s*([\w.]+)\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*$")

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
        if expression.startswith("(") and expression.endswith(")"):
            expression = expression[1:-1].strip()
        for operator in (" or ", " and "):
            if operator in expression:
                parts = [part.strip() for part in expression.split(operator)]
                values = [self.evaluate(part, agent=agent, target=target, world=world, context=context) for part in parts]
                return any(values) if operator.strip() == "or" else all(values)
        if expression.startswith("not "):
            return not self.evaluate(expression[4:], agent=agent, target=target, world=world, context=context)
        for operator in (" not_in ", " in "):
            if operator in expression:
                left_path, right_text = expression.split(operator, 1)
                left = self._value(left_path.strip(), agent, target, world, context)
                right = self._literal(right_text)
                if left is None:
                    # An absent membership value is still outside an
                    # explicitly listed set.  This lets declarative access
                    # rules distinguish “no faction/permission” from a
                    # matching member without story-specific code.
                    return operator.strip() == "not_in"
                try:
                    return left not in right if operator.strip() == "not_in" else left in right
                except TypeError:
                    return False
        comparison = self._comparison.match(expression)
        if comparison:
            left = self._value(comparison.group(1), agent, target, world, context)
            right_text = comparison.group(3).strip()
            right = self._value(right_text, agent, target, world, context) if right_text.startswith("context.") else self._literal(right_text)
            if left is None:
                return False
            try:
                operator = comparison.group(2)
                return self._compare(left, operator, right)
            except TypeError:
                return False
        return bool(self._value(expression, agent, target, world, context))

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
        if expression.startswith("(") and expression.endswith(")"):
            expression = expression[1:-1].strip()
        for operator in (" or ", " and "):
            if operator in expression:
                parts = [part.strip() for part in expression.split(operator)]
                values = [self.evaluate_known(part, agent=agent, target=target, world=world, context=context) for part in parts]
                if any(value is None for value in values):
                    return None
                return any(values) if operator.strip() == "or" else all(values)
        if expression.startswith("not "):
            value = self.evaluate_known(expression[4:], agent=agent, target=target, world=world, context=context)
            return None if value is None else not value
        for operator in (" not_in ", " in "):
            if operator in expression:
                left_path, right_text = expression.split(operator, 1)
                if not self._known_path(left_path.strip()):
                    return None
                left = self._value(left_path.strip(), agent, target, world, context)
                right = self._literal(right_text)
                try:
                    return left not in right if operator.strip() == "not_in" else left in right
                except TypeError:
                    return False
        comparison = self._comparison.match(expression)
        if comparison:
            left_path = comparison.group(1)
            if not self._known_path(left_path):
                return None
            left = self._value(left_path, agent, target, world, context)
            right_text = comparison.group(3).strip()
            if right_text.startswith("context."):
                if not self._known_path(right_text):
                    return None
                right = self._value(right_text, agent, target, world, context)
            else:
                right = self._literal(right_text)
            if left is None:
                return False
            try:
                operator = comparison.group(2)
                return self._compare(left, operator, right)
            except TypeError:
                return False
        if not self._known_path(expression):
            return None
        return bool(self._value(expression, agent, target, world, context))

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
            "actor.permission.", "actor.can_", "actor.is_", "world.",
            "context.", "event.recent.",
        )):
            return True
        return path in {
            "actor.alive", "actor.incapacitated", "actor.role", "actor.mobile",
            "actor.faction_id", "actor.at_location", "actor.exhausted", "actor.stunned",
            "inventory.available", "inventory.item_available", "actor.inventory_item_available",
            "route.known", "destination.reachable", "area.unknown", "area.not_fully_mapped",
            "object.available", "research.new_fact_available", "secret.source_available",
            "target.available", "target.visible", "location.has_food", "location.resource_node",
            "location.health_service", "location.safe", "location.reachable", "station.available",
            "patient.available", "patient.in_clinic", "counterparty.available", "resource.available",
            "inputs.available", "recipe.available", "medicine.available", "communication.channel_open",
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
            food_items = context.get("food_items", []) or []
            item_id = context.get("item_id") or target.get("item_id")
            if item_id:
                return str(item_id) in {str(item) for item in food_items} and float(inventory.get(str(item_id), 0) or 0) >= 1
            return any(float(inventory.get(str(item), 0) or 0) >= 1 for item in food_items)
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
            object_location = obj_state.get("location_id")
            current_location = state.get("location_id")
            return bool(obj and object_location == current_location and not obj_state.get("recovered", False))
        if path == "research.new_fact_available":
            source_id = target.get("source_id") or context.get("source_id")
            return bool(source_id) and str(source_id) not in {str(item) for item in context.get("researched_sources", [])}
        if path == "secret.source_available":
            return any(str(item).startswith("secret:") for item in agent.get("knowledge_seed", []))
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
        if path == "location.has_food":
            location = context.get("current_location", {}) or {}
            tags = self.location_tag_groups.get("food", {"food"})
            return bool(set(location.get("tags", []) or []) & tags) or bool(context.get("food_available", False))
        if path == "location.resource_node":
            location = context.get("current_location", {}) or {}
            tags = self.location_tag_groups.get("resource_node", {"food", "resource", "water", "mine", "forest"})
            return bool(set(location.get("tags", []) or []) & tags)
        if path.startswith("location.tag."):
            location = context.get("current_location", {}) or {}
            return path.removeprefix("location.tag.") in set(location.get("tags", []) or [])
        if path == "location.health_service":
            location = context.get("current_location", {}) or {}
            tags = self.location_tag_groups.get("health_service", {"health", "clinic"})
            return bool(set(location.get("tags", []) or []) & tags)
        if path == "station.available":
            required = set(context.get("station_tags", []) or [])
            current = set((context.get("current_location", {}) or {}).get("tags", []) or [])
            return not required or bool(required & current)
        if path in {"patient.available", "counterparty.available"}:
            return bool(context.get(path, target.get("available", False)))
        if path in {"location.safe", "destination.safe_or_defensible", "target.available", "patient.in_clinic", "source.accessible", "resource.available", "inputs.available", "recipe.available", "medicine.available", "communication.channel_open", "location.reachable"}:
            if path == "location.safe":
                location = context.get("current_location", {})
                safe_tags = self.location_tag_groups.get("safe", {"safe"})
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

    @staticmethod
    def _literal(value: str) -> Any:
        value = value.strip().strip("\"")
        if value.startswith("[") or value.startswith("{"):
            try:
                return yaml.safe_load(value)
            except yaml.YAMLError:
                return value
        if value.lower() in {"true", "false"}:
            return value.lower() == "true"
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return value
