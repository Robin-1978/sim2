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
                    return False
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
            return float(state.get("health", 100)) > 0
        if path == "actor.incapacitated":
            return float(state.get("health", 100)) <= 0 or "incapacitated" in state.get("conditions", [])
        if path == "actor.energy":
            return float(state.get("energy", 100))
        if path == "actor.health":
            return float(state.get("health", 100))
        if path == "actor.location":
            return state.get("location_id")
        if path == "actor.satiety":
            return float((agent.get("needs", {}) or {}).get("satiety", state.get("satiety", 0)))
        if path == "actor.mobile":
            return self._value("actor.alive", agent, target, world, context)
        if path == "actor.has_food":
            inventory = agent.get("inventory", {}) or {}
            food_items = context.get("food_items", []) or []
            item_id = context.get("item_id") or target.get("item_id")
            if item_id:
                return str(item_id) in {str(item) for item in food_items} and float(inventory.get(str(item_id), 0) or 0) >= 1
            return any(float(inventory.get(str(item), 0) or 0) >= 1 for item in food_items)
        if path.startswith("actor.can_"):
            capability = path.removeprefix("actor.can_")
            aliases = {"explore": "exploration", "speak": "conversation", "communicate": "conversation", "craft": "cooking", "attack": "combat", "defend": "defense", "gather": "gather", "research": "research", "inspect": "investigation", "investigate": "investigation", "inspect_granary": "accounting", "negotiate": "conversation", "sound_alarm": "alarm", "mediate": "conversation"}
            if capability == "craft":
                value = max((agent.get("capabilities", {}).get(item, 0) for item in ("cooking", "herbalism", "forging")), default=0)
            else:
                value = agent.get("capabilities", {}).get(aliases.get(capability, capability), 0)
            if not value and capability == "inspect":
                value = max((agent.get("capabilities", {}).get(item, 0) for item in ("investigation", "lore", "accounting", "governance")), default=0)
            if not value and capability == "inspect_granary":
                value = max((agent.get("capabilities", {}).get(item, 0) for item in ("accounting", "governance")), default=0)
            if not value and capability == "gather":
                value = max((agent.get("capabilities", {}).get(item, 0) for item in ("gather", "farming", "hunting", "foraging")), default=0)
            if not value and capability == "investigate":
                value = max((agent.get("capabilities", {}).get(item, 0) for item in ("investigation", "tracking", "lore", "rumor_assessment")), default=0)
            return float(value or 0) > 0
        if path == "actor.faction_id":
            factions = agent.get("faction_ids", [])
            return factions[0] if factions else None
        if path.startswith("actor.permission."):
            permissions = agent.get("permissions", {})
            return permissions.get(path.removeprefix("actor.permission."), False)
        if path == "actor.food_withdrawal":
            return bool((agent.get("permissions", {}) or {}).get("food_withdrawal", False))
        if path.startswith("actor.is_"):
            role = str(agent.get("role", "")).lower()
            expected = path.removeprefix("actor.is_").replace("_", " ")
            role_aliases = {
                "guard": ("guard", "watch", "patrol"),
                "healer": ("healer", "healing", "herbalist"),
                "steward": ("steward", "council", "leader"),
                "granary keeper": ("granary_keeper", "granary", "ration"),
                "blacksmith": ("blacksmith", "smith"),
            }
            aliases = role_aliases.get(expected, (expected,))
            return any(alias in role for alias in aliases) or any(any(alias in str(key).lower() for alias in aliases) and float(value or 0) > 0 for key, value in agent.get("capabilities", {}).items())
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
            return float(state.get("energy", 100)) <= 0 or path.removeprefix("actor.") in state.get("conditions", [])
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
        if path == "location.has_food":
            location = context.get("current_location", {}) or {}
            return "food" in (location.get("tags", []) or []) or bool(context.get("food_available", False))
        if path == "location.resource_node":
            location = context.get("current_location", {}) or {}
            return bool(set(location.get("tags", []) or []) & {"food", "resource", "water", "mine", "forest"})
        if path == "location.food_store":
            location = context.get("current_location", {}) or {}
            tags = set(location.get("tags", []) or [])
            return {"food", "resource"}.issubset(tags)
        if path == "location.market":
            location = context.get("current_location", {}) or {}
            return "market" in set(location.get("tags", []) or [])
        if path == "location.health_service":
            location = context.get("current_location", {}) or {}
            return bool(set(location.get("tags", []) or []) & {"health", "clinic"})
        if path == "station.available":
            required = set(context.get("station_tags", []) or [])
            current = set((context.get("current_location", {}) or {}).get("tags", []) or [])
            return not required or bool(required & current)
        if path in {"patient.available", "counterparty.available"}:
            return bool(context.get(path, target.get("available", False)))
        if path in {"location.safe", "destination.safe_or_defensible", "target.available", "patient.in_clinic", "source.accessible", "resource.available", "inputs.available", "recipe.available", "medicine.available", "communication.channel_open", "location.reachable"}:
            if path == "location.safe":
                location = context.get("current_location", {})
                return bool(location.get("safe", False) or location.get("public", False) is True or "safe" in location.get("tags", []))
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
