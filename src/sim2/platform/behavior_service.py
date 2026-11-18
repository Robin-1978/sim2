from __future__ import annotations

import json
from typing import Any

from ..actions.capacity import check_action_capacity
from ..behavior.estimator import BehaviorDurationEstimator
from ..world.context import configured_context_values
from ..world.navigation import movement_speed, reachable_locations, route_minutes
from ..world.time import world_time_view
from .services import PlatformService


class BehaviorExecutionService(PlatformService):
    """Action parameter resolution, progress and interruption."""

    def behavior_condition_context(self, agent: dict[str, Any], parameters: dict[str, Any], target: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build the small declarative context used to recheck BT entry rules."""

        state = agent.get("initial_state", {}) or {}
        location_id = state.get("location_id")
        location = {}
        if location_id:
            try:
                location = self.store.repositories.entities.get_entity(str(location_id))
            except KeyError:
                location = {}
        resolved_target, _ = self.targets.resolve(agent, {**parameters, **(target or {})})
        participants = self.targets.visible_agents(agent)
        rules = self.store.repositories.metadata.get("rules", {}) or {}
        conflict_types = {str(item) for item in (rules.get("conflict_relation_types", ["conflict"]) or [])}
        participant_ids = {str(item.get("id")) for item in participants}
        conflict_available = False
        for relation in self.store.repositories.entities.get_related_relationships(agent["id"], limit=50):
            if str(relation.get("person")) not in participant_ids or str(relation.get("relation")) not in conflict_types:
                continue
            try:
                conflict_available = float(relation.get("relation_strength", relation.get("value", 0)) or 0) < 0
            except (TypeError, ValueError):
                conflict_available = False
            if conflict_available:
                break
        result = {
            "location_id": location_id,
            "destination_id": parameters.get("location_id") or parameters.get("destination_id"),
            "area_id": parameters.get("area_id"),
            "item_id": parameters.get("item_id"),
            "entity_id": parameters.get("entity_id"),
            "object_id": parameters.get("object_id"),
            "inventory": agent.get("inventory", {}) or {},
            "current_location": location,
            "current_object": self.entity_for_context(parameters.get("object_id")),
            "mapped_areas": self.store.repositories.memory.explored_areas(agent["id"]),
            "researched_sources": self.store.repositories.memory.researched_sources(agent["id"]),
            "known_fact_keys": [item.get("fact_key") for item in self.store.repositories.memory.list_knowledge_facts(agent["id"], limit=1000) if item.get("fact_key")],
            "target": resolved_target or {},
            "target.visible": bool((resolved_target or {}).get("visible", False)),
            "target.available": bool((resolved_target or {}).get("available", False)),
            "known_route_destinations": reachable_locations(self.store, location_id),
            "recent_event_types": self._context.services["evolution"].recent_event_types(),
            "day_phase": world_time_view(self.store).get("day_phase"),
            "conflict": {"available": conflict_available},
        }
        result.update(configured_context_values(self.store.repositories.metadata.get("rules", {}) or {}, agent))
        return result

    def entity_for_context(self, entity_id: Any) -> dict[str, Any]:
        """Return a configured entity for a declarative condition context."""

        if not entity_id:
            return {}
        try:
            return self.store.repositories.entities.get_entity(str(entity_id))
        except KeyError:
            return {}

    def compile_behavior_actions(self, behavior_id: str, seen: set[str] | None = None) -> list[dict[str, Any]]:
        seen = set(seen or set())
        if behavior_id in seen:
            return []
        seen.add(behavior_id)
        definitions = {item["id"]: item for item in self.store.repositories.entities.get_definitions("bt_def")}
        behavior = definitions.get(behavior_id)
        if not behavior:
            return []

        def visit(nodes: list[Any], parallel_group: str | None = None) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            for raw in nodes or []:
                node = raw if isinstance(raw, dict) else {"type": "behavior", "behavior_id": raw}
                node_type = node.get("type")
                if node_type == "action" and node.get("action_id"):
                    action = next((item for item in self.store.repositories.entities.get_definitions("action_def") if item.get("id") == node["action_id"]), {})
                    result.append({"action_id": node["action_id"], "action_version": int(action.get("definition_version", action.get("version", 1)) or 1), "parameters": dict(node.get("parameters") or {}), "parallel_group": parallel_group})
                elif node_type in {"behavior", "subtree"} and node.get("behavior_id"):
                    nested = self.compile_behavior_actions(str(node["behavior_id"]), seen)
                    if parallel_group:
                        for item in nested:
                            item["parallel_group"] = parallel_group
                    result.extend(nested)
                elif node_type in {"sequence", "parallel"}:
                    group = parallel_group
                    if node_type == "parallel":
                        group = f"parallel:{behavior_id}:{len(result)}"
                    result.extend(visit(node.get("children", []), group))
                elif node_type == "selector":
                    # Selector branch conditions are evaluated by the fuller
                    # condition engine in M4.  Until then, choose the first
                    # configured branch deterministically and record that
                    # choice in the BT instance.
                    children = node.get("children", [])
                    if children:
                        result.extend(visit([children[0]], parallel_group))
                # condition/wait nodes do not consume an Action minute.
            return result

        return visit(behavior.get("nodes", []))

    @staticmethod
    def execution_snapshot(agent: dict[str, Any]) -> dict[str, Any]:
        """Return a small, JSON-safe state projection for outcome review."""

        state = dict(agent.get("initial_state") or {})
        execution_fields = {
            "location_id", "status", "active_bt_instance", "active_behavior_id", "active_action",
            "active_action_instance", "active_action_parameters", "active_action_reservation",
            "active_parallel_actions", "active_parameters", "remaining_minutes", "action_progress",
            "active_action_definition", "active_action_definition_version", "active_action_duration",
            "active_action_noop", "active_action_index", "conditions", "injuries", "current_goal_id",
            "last_action_result", "last_platform_result", "last_outcome",
        }
        attributes = {key: value for key, value in state.items() if key not in execution_fields}
        return {
            "location": state.get("location_id"),
            "attributes": attributes,
            "needs": dict(agent.get("needs") or {}),
            "inventory": dict(agent.get("inventory") or {}),
            "capabilities": dict(agent.get("capabilities") or {}),
        }

    @staticmethod
    def execution_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        """Describe only changed values so the next Agent sees consequences."""

        delta: dict[str, Any] = {}
        if before.get("location") != after.get("location"):
            delta["location"] = {"before": before.get("location"), "after": after.get("location")}
        old_attributes = before.get("attributes") or {}
        new_attributes = after.get("attributes") or {}
        attribute_changes = {
            key: {"before": old_attributes.get(key), "after": new_attributes.get(key)}
            for key in sorted(set(old_attributes) | set(new_attributes))
            if old_attributes.get(key) != new_attributes.get(key)
        }
        if attribute_changes:
            delta["attributes"] = attribute_changes
        for group in ("needs", "inventory", "capabilities"):
            old = before.get(group) or {}
            new = after.get(group) or {}
            changes = {}
            for key in sorted(set(old) | set(new)):
                if old.get(key) != new.get(key):
                    changes[key] = {"before": old.get(key, 0), "after": new.get(key, 0)}
            if changes:
                delta[group] = changes
        return delta

    @staticmethod
    def _resolve_parameters(parameters: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        context = dict(state)
        # Intent parameters are the caller's selected targets and override the
        # actor's current state when a definition uses context.* references.
        context.update(state.get("active_parameters") or {})
        # Derived context is still declarative: the BT names a source, and
        # Platform obtains its configured location.  The Agent never needs to
        # invent a second hidden location parameter.
        def resolve(value: Any) -> Any:
            if isinstance(value, str) and value.startswith("context."):
                return context.get(value.removeprefix("context."))
            if isinstance(value, dict):
                return {key: resolve(item) for key, item in value.items()}
            if isinstance(value, list):
                return [resolve(item) for item in value]
            return value

        return {key: resolve(value) for key, value in parameters.items()}

    def resolve_activity_parameters(self, parameters: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        context = dict(state)
        context.update(state.get("active_parameters") or {})
        source_id = context.get("source_id")
        if source_id:
            try:
                source = self.store.repositories.entities.get_entity(str(source_id))
            except KeyError:
                source = {}
            source_state = source.get("initial_state") if isinstance(source.get("initial_state"), dict) else {}
            context.setdefault("source_location_id", source_state.get("location_id") or source.get("location_id"))

        return self._resolve_parameters(parameters, context)

    def route_distance(self, origin: str | None, destination: str | None) -> int:
        """Return baseline minutes for a weighted route.

        Actor-specific speed is applied by ``BehaviorDurationEstimator`` so
        the same route can cost different characters different time.
        """

        return route_minutes(self.store, origin, destination)

    def action_duration(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], origin_location: str | None = None) -> int:
        estimator = BehaviorDurationEstimator(
            {item["id"]: item for item in self.store.repositories.entities.get_definitions("action_def")},
            {},
            {item["id"]: item for item in self.store.repositories.entities.get_definitions("recipe_def")},
            self.route_distance,
            capabilities=agent.get("capabilities", {}),
            capability_modifiers=(self.store.repositories.metadata.get("rules", {}) or {}).get("capability_modifiers", {}),
            travel_speed=lambda values: movement_speed(self.store, agent, values),
        )
        return estimator._action_minutes(action, parameters, origin_location)

    def apply_action_parameter_defaults(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        """Resolve declarative defaults without making the LLM fill hidden plumbing."""

        result = dict(parameters)
        configured_context = configured_context_values(self.store.repositories.metadata.get("rules", {}) or {}, agent)
        for key, default in (action.get("parameter_defaults", {}) or {}).items():
            value = result.get(key)
            if value not in (None, "") and not (isinstance(value, str) and value.startswith("context.")):
                continue
            if isinstance(default, dict) and str(default.get("resolver") or "") == "context_value":
                result[key] = configured_context.get(str(default.get("key") or key), value)
            elif default == "first_available_resource":
                location_id = (agent.get("initial_state") or {}).get("location_id")
                sources = self.store.repositories.entities.entities_at_location(location_id) if location_id else []
                rules = self.store.repositories.metadata.get("rules", {}) or {}
                entity_types = {str(item) for item in (rules.get("resource_entity_types", ["resource", "material"]) or [])}
                protected_key = str(rules.get("resource_protected_state", "protected"))
                source = next((item for item in sources if item.get("entity_type") in entity_types and not (item.get("initial_state") or {}).get(protected_key) and self.entity_quantity(item) > 0), None)
                result[key] = source.get("id") if source else value
            elif isinstance(default, str) and default in configured_context:
                # A package may name a configured context value directly;
                # it is resolved through the generic context projection.
                result[key] = configured_context.get(default, value)
            elif value in (None, "") or (isinstance(value, str) and value.startswith("context.")):
                result[key] = default
        return result

    @staticmethod
    def entity_quantity(entity: dict[str, Any]) -> float:
        state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
        value = entity.get("amount")
        if value is None:
            value = state.get("quantity", state.get("amount", 0))
        return float(value or 0)

    def reserve_action_costs(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], *, commit: bool = True) -> tuple[bool, str, list[dict[str, Any]]]:
        return self.resources.reserve(agent, action, parameters, commit=commit)

    def check_action_capacity(self, agent: dict[str, Any], action: dict[str, Any], duration: int) -> tuple[bool, str]:
        return check_action_capacity(agent, action, duration, self.store.repositories.metadata.get("attributes", []) or [])

    def apply_action_progress(self, agent: dict[str, Any], action: dict[str, Any]) -> None:
        state = dict(agent.get("initial_state") or {})
        needs = dict(agent.get("needs") or {})
        for key, value in (action.get("costs", {}).get("per_minute", {}) or {}).items():
            if not isinstance(value, (int, float)) or key == "time":
                continue
            if key not in state and key not in needs:
                continue
            numeric = float(value)
            mode = self.attribute_schema.cost_mode(str(key))
            if mode is None:
                continue
            target = state if key in state or key not in needs else needs
            current = float(target.get(key, self.attribute_schema.default(str(key), 0)) or 0)
            changed = current + numeric if mode == "increase" else current - numeric
            target[key] = self.attribute_schema.clamp(str(key), changed)
            if target is state:
                state[key] = target[key]
            else:
                needs[key] = target[key]
        agent["needs"] = needs
        agent["initial_state"] = state
        for effect in action.get("progress_effects", []) or []:
            self.effects.apply(agent, effect)

    def interrupt_behavior(self, actor_id: str, reason: str, *, hard: bool = True) -> dict[str, Any]:
        agent = self.store.repositories.entities.get_agent(actor_id)
        state = dict(agent.get("initial_state") or {})
        execution_id = state.get("active_bt_instance")
        if not execution_id:
            return {"status": "ignored", "message": "The actor has no active behavior."}
        minute = int(self.store.repositories.metadata.get("world_minute", 0))
        status = "cancelled" if hard else "paused"
        with self.store.transaction():
            bt_row = self.store.connection.execute("SELECT behavior_id,data_json FROM bt_instance WHERE execution_id=?", (execution_id,)).fetchone()
            bt_data = json.loads(bt_row["data_json"]) if bt_row is not None else {}
            before = dict(bt_data.get("before") or {})
            if hard:
                reservations = list(state.get("active_action_reservation") or [])
                for item in (state.get("active_parallel_actions") or {}).values():
                    reservations.extend(item.get("reservation", []) or [])
                self.resources.release(agent, reservations)
            outcome = {
                "status": status,
                "result": reason,
                "expected": dict(bt_data.get("expected") or {}),
                "actual": self.execution_snapshot(agent),
                "delta": self.execution_delta(before, self.execution_snapshot(agent)) if before else {},
                "decision": dict(bt_data.get("decision") or {}),
            }
            action_instance_id = state.get("active_action_instance")
            if action_instance_id:
                action_row = self.store.connection.execute("SELECT elapsed_minutes,remaining_minutes,data_json FROM action_instance WHERE action_instance_id=?", (action_instance_id,)).fetchone()
                if action_row is not None:
                    self.store.repositories.activity.update_action_instance(
                        action_instance_id,
                        status="cancelled" if hard else "paused",
                        elapsed_minutes=action_row["elapsed_minutes"],
                        remaining_minutes=action_row["remaining_minutes"],
                        data=json.loads(action_row["data_json"]),
                        updated_minute=minute,
                    )
            for entry in (state.get("active_parallel_actions") or {}).values():
                action_row = self.store.connection.execute("SELECT elapsed_minutes,remaining_minutes,data_json FROM action_instance WHERE action_instance_id=?", (entry.get("action_instance_id"),)).fetchone()
                if action_row is not None and action_row["remaining_minutes"] > 0:
                    self.store.repositories.activity.update_action_instance(entry["action_instance_id"], status="cancelled" if hard else "paused", elapsed_minutes=action_row["elapsed_minutes"], remaining_minutes=action_row["remaining_minutes"], data=json.loads(action_row["data_json"]), updated_minute=minute)
            state["status"] = "idle" if hard else "paused"
            state["last_platform_result"] = {"status": status, "reason": reason, "outcome": outcome}
            state["last_outcome"] = outcome
            if hard:
                state["active_bt_instance"] = None
                state["active_behavior_id"] = None
                state["active_action"] = None
                state["active_action_instance"] = None
                state["active_parallel_actions"] = {}
                state["remaining_minutes"] = 0
                state.pop("active_resource_withdrawal", None)
                state.pop("active_resource_authorization", None)
            agent["initial_state"] = state
            self.store.repositories.entities.update_agent(actor_id, agent)
            bt_data["outcome"] = outcome
            bt_data["result"] = reason
            self.store.repositories.activity.update_bt_instance(execution_id, status=status, current_node=int(state.get("active_action_index", 0)), data=bt_data, updated_minute=minute)
            self._context.services["events"].append("behavior.interrupted", actor_id, minute, {"execution_id": execution_id, "reason": reason, "hard": hard, "outcome": outcome})
        return {"status": status, "execution_id": execution_id, "reason": reason}

    def resume_behavior(self, actor_id: str) -> dict[str, Any]:
        agent = self.store.repositories.entities.get_agent(actor_id)
        state = dict(agent.get("initial_state") or {})
        execution_id = state.get("active_bt_instance")
        if not execution_id or state.get("status") != "paused":
            return {"status": "ignored", "message": "The actor has no paused behavior."}
        minute = int(self.store.repositories.metadata.get("world_minute", 0))
        with self.store.transaction():
            state["status"] = "executing"
            agent["initial_state"] = state
            self.store.repositories.entities.update_agent(actor_id, agent)
            self.store.connection.execute("UPDATE action_instance SET status='running' WHERE execution_id=? AND status='paused'", (execution_id,))
            self.store.connection.execute("UPDATE bt_instance SET status='executing',updated_minute=? WHERE execution_id=?", (minute, execution_id))
            self._context.services["events"].append("behavior.resumed", actor_id, minute, {"execution_id": execution_id})
        return {"status": "resumed", "execution_id": execution_id}
