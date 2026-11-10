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
                return {"==": left == right, "!=": left != right, ">=": left >= right, "<=": left <= right, ">": left > right, "<": left < right}[operator]
            except TypeError:
                return False
        return bool(self._value(expression, agent, target, world, context))

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
