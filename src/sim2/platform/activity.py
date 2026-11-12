from __future__ import annotations

import copy
import json
from typing import Any
from uuid import uuid4

from ..world.navigation import reachable_locations
from ..world.time import world_time_view
from ..world.context import configured_context_values


class ActivityRuntimeMixin:
    """Long-running Action/BT execution and outcome projection.

    The mixin deliberately depends on Platform services (store, effects,
    targets, rules, resources) but owns no world-specific behavior.  Keeping
    this lifecycle separate makes the coordinator readable and testable.
    """

    def _start_action(self, agent: dict[str, Any], execution_id: str, action_info: dict[str, Any], minute: int) -> tuple[bool, str]:
        action_defs = {item["id"]: item for item in self.store.get_definitions("action_def")}
        action_id = action_info.get("action_id")
        action = action_defs.get(action_id)
        if not action:
            return False, f"action is not registered: {action_id}"
        state = dict(agent.get("initial_state") or {})
        parameters = self._resolve_parameters(dict(action_info.get("parameters") or {}), state)
        parameters = self._apply_action_parameter_defaults(agent, action, parameters)
        already_at_destination = bool(
            action.get("requires_destination")
            and action.get("completion", {}).get("if_already_at_destination")
            and parameters.get("location_id") == state.get("location_id")
        )
        resolved_target, target_error = self.targets.resolve(agent, parameters)
        if target_error:
            return False, target_error
        if resolved_target:
            for key in self.targets.TARGET_KEYS:
                if parameters.get(key):
                    parameters[key] = resolved_target["id"]
        current_location = {}
        location_id = state.get("location_id")
        if location_id:
            try:
                current_location = self.store.get_entity(location_id)
            except KeyError:
                current_location = {}
        condition_context = {
            "location_id": location_id,
            "destination_id": parameters.get("location_id") or parameters.get("destination_id"),
            "area_id": parameters.get("area_id"),
            "source_id": parameters.get("source_id"),
            "item_id": parameters.get("item_id"),
            "object_id": parameters.get("object_id"),
            "inventory": agent.get("inventory", {}) or {},
            "station_tags": [],
            "current_location": current_location,
            "current_object": self._entity_for_context(parameters.get("object_id")),
            "source": self._entity_for_context(parameters.get("source_id")),
            "research_discoveries": self.store.research_discoveries(str(parameters.get("source_id"))) if parameters.get("source_id") else [],
            "mapped_areas": self.store.explored_areas(agent["id"]),
            "researched_sources": self.store.researched_sources(agent["id"]),
            "known_fact_keys": [
                item.get("fact_key")
                for item in self.store.list_knowledge_facts(agent["id"], limit=1000)
                if item.get("fact_key")
            ],
            "target": resolved_target or {},
            "target.visible": (resolved_target or {}).get("visible", False),
            "target.available": (resolved_target or {}).get("available", False),
            "known_route_destinations": reachable_locations(self.store, location_id),
            "recent_event_types": self._recent_event_types(),
            "day_phase": world_time_view(self.store).get("day_phase"),
        }
        condition_context.update(configured_context_values(self.store.get_meta("rules", {}) or {}, agent))
        if (
            action.get("duration", {}).get("mode") == "recipe_duration"
            or "recipe_inputs" in (action.get("costs", {}).get("reserve", {}) or {})
        ):
            recipe = next((item for item in self.store.get_definitions("recipe_def") if item.get("id") == parameters.get("recipe_id")), None)
            condition_context["station_tags"] = list((recipe or {}).get("station_tags", []) or [])
        denial = self.rule_compiler.denial(
            action=action,
            agent=agent,
            target={**parameters, **(resolved_target or {})},
            context=condition_context,
            world=self.store.get_meta("world_properties", {}) or {},
        )
        if denial:
            return False, denial["message"]
        for predicate in action.get("preconditions", []) or []:
            condition_target = {**parameters, **(resolved_target or {})}
            if not self.conditions.evaluate(str(predicate), agent=agent, target=condition_target, context=condition_context, world=self.store.get_meta("world_properties", {}) or {}):
                return False, f"precondition failed: {predicate}"
        duration = self._action_duration(agent, action, parameters, state.get("location_id"))
        if already_at_destination:
            # Completing an already reached destination consumes this tick,
            # but it does not charge movement costs or reserve resources.
            reservation = []
        else:
            capacity_ok, capacity_reason = self._check_action_capacity(agent, action, duration)
            if not capacity_ok:
                return False, capacity_reason
            allowed, reason, reservation = self._reserve_action_costs(agent, action, parameters)
            if not allowed:
                return False, reason
        action_instance_id = f"{execution_id}:{state.get('active_action_index', 0)}"
        state["active_action"] = action_id
        state["active_action_instance"] = action_instance_id
        state["action_progress"] = 0.0
        state["active_action_duration"] = duration
        state["remaining_minutes"] = duration
        state["active_action_parameters"] = parameters
        state["active_action_reservation"] = reservation
        state["active_action_noop"] = already_at_destination
        # A running activity is bound to the definition that was validated at
        # start time.  Later God/schema changes affect new activities only;
        # they cannot silently change the cost, duration, or effects of work
        # already in progress.
        state["active_action_definition_version"] = int(action.get("definition_version", action.get("version", 1)) or 1)
        state["active_action_definition"] = copy.deepcopy(action)
        agent["initial_state"] = state
        self.store.put_action_instance(action_instance_id, execution_id, agent["id"], action_id, "running", 0, duration, {"parameters": parameters, "target": resolved_target, "reservation": reservation, "no_op": already_at_destination, "definition_version": int(action.get("definition_version", action.get("version", 1)) or 1), "definition_snapshot": copy.deepcopy(action)}, minute, minute)
        self.store.append_event("action.started", agent["id"], minute, {"execution_id": execution_id, "action_id": action_id, "duration_minutes": duration, "no_op": already_at_destination})
        return True, ""

    def _prepare_parallel_action(self, agent: dict[str, Any], action_info: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        action_defs = {item["id"]: item for item in self.store.get_definitions("action_def")}
        action = action_defs.get(action_info.get("action_id"))
        if not action:
            return None, f"action is not registered: {action_info.get('action_id')}"
        state = dict(agent.get("initial_state") or {})
        parameters = self._resolve_parameters(dict(action_info.get("parameters") or {}), state)
        parameters = self._apply_action_parameter_defaults(agent, action, parameters)
        already_at_destination = bool(
            action.get("requires_destination")
            and action.get("completion", {}).get("if_already_at_destination")
            and parameters.get("location_id") == state.get("location_id")
        )
        target, target_error = self.targets.resolve(agent, parameters)
        if target_error:
            return None, target_error
        if target:
            for key in self.targets.TARGET_KEYS:
                if parameters.get(key):
                    parameters[key] = target["id"]
        current_location = {}
        if state.get("location_id"):
            try:
                current_location = self.store.get_entity(state["location_id"])
            except KeyError:
                pass
        context = {
            "location_id": state.get("location_id"),
            "destination_id": parameters.get("location_id") or parameters.get("destination_id"),
            "area_id": parameters.get("area_id"),
            "source_id": parameters.get("source_id"),
            "item_id": parameters.get("item_id"),
            "object_id": parameters.get("object_id"),
            "inventory": agent.get("inventory", {}) or {},
            "station_tags": [],
            "current_location": current_location,
            "current_object": self._entity_for_context(parameters.get("object_id")),
            "source": self._entity_for_context(parameters.get("source_id")),
            "mapped_areas": self.store.explored_areas(agent["id"]),
            "researched_sources": self.store.researched_sources(agent["id"]),
            "known_fact_keys": [
                item.get("fact_key")
                for item in self.store.list_knowledge_facts(agent["id"], limit=1000)
                if item.get("fact_key")
            ],
            "target": target or {},
            "target.visible": (target or {}).get("visible", False),
            "target.available": (target or {}).get("available", False),
            "known_route_destinations": reachable_locations(self.store, state.get("location_id")),
            "recent_event_types": self._recent_event_types(),
            "day_phase": world_time_view(self.store).get("day_phase"),
        }
        context.update(configured_context_values(self.store.get_meta("rules", {}) or {}, agent))
        if (
            action.get("duration", {}).get("mode") == "recipe_duration"
            or "recipe_inputs" in (action.get("costs", {}).get("reserve", {}) or {})
        ):
            recipe = next((item for item in self.store.get_definitions("recipe_def") if item.get("id") == parameters.get("recipe_id")), None)
            context["station_tags"] = list((recipe or {}).get("station_tags", []) or [])
        condition_target = {**parameters, **(target or {})}
        denial = self.rule_compiler.denial(
            action=action,
            agent=agent,
            target=condition_target,
            context=context,
            world=self.store.get_meta("world_properties", {}) or {},
        )
        if denial:
            return None, denial["message"]
        for predicate in action.get("preconditions", []) or []:
            if not self.conditions.evaluate(str(predicate), agent=agent, target=condition_target, context=context, world=self.store.get_meta("world_properties", {}) or {}):
                return None, f"precondition failed: {predicate}"
        duration = self._action_duration(agent, action, parameters, state.get("location_id"))
        if already_at_destination:
            reservation = []
        else:
            capacity_ok, capacity_reason = self._check_action_capacity(agent, action, duration)
            if not capacity_ok:
                return None, capacity_reason
            probe = copy.deepcopy(agent)
            allowed, reason, reservation = self._reserve_action_costs(probe, action, parameters, commit=False)
            if not allowed:
                return None, reason
        return {"action": action, "parameters": parameters, "target": target, "duration": duration, "reservation": reservation, "no_op": already_at_destination}, ""

    def _progress_parallel_group(self, agent: dict[str, Any], execution_id: str, behavior_id: str, actions: list[dict[str, Any]], index: int, minute: int) -> None:
        state = dict(agent.get("initial_state") or {})
        group = actions[index].get("parallel_group")
        group_items: list[tuple[int, dict[str, Any]]] = []
        for item_index in range(index, len(actions)):
            if actions[item_index].get("parallel_group") != group:
                break
            group_items.append((item_index, actions[item_index]))
        active = state.get("active_parallel_actions")
        if not active:
            prepared: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
            for item_index, item in group_items:
                info, error = self._prepare_parallel_action(agent, item)
                if error:
                    self._finish_behavior(agent, execution_id, behavior_id, "failed", error, minute)
                    return
                prepared.append((item_index, item, info or {}))
            for item_index, item, info in prepared:
                action = info["action"]
                if not info.get("no_op"):
                    self._reserve_action_costs(agent, action, info["parameters"])
            active = {}
            for item_index, item, info in prepared:
                action_id = item["action_id"]
                action_instance_id = f"{execution_id}:parallel:{item_index}"
                active[str(item_index)] = {
                    "action_id": action_id,
                    "parameters": info["parameters"],
                    "target": info["target"],
                    "duration": info["duration"],
                    "remaining": info["duration"],
                    "action_instance_id": action_instance_id,
                    "reservation": info.get("reservation", []),
                    "no_op": bool(info.get("no_op")),
                    "definition_version": int(action.get("definition_version", action.get("version", 1)) or 1),
                    "definition_snapshot": copy.deepcopy(action),
                }
                self.store.put_action_instance(action_instance_id, execution_id, agent["id"], action_id, "running", 0, info["duration"], {"parameters": info["parameters"], "target": info["target"], "reservation": info.get("reservation", []), "parallel_group": group, "definition_version": int(action.get("definition_version", action.get("version", 1)) or 1), "definition_snapshot": copy.deepcopy(action)}, minute, minute)
                self.store.append_event("action.started", agent["id"], minute, {"execution_id": execution_id, "action_id": action_id, "duration_minutes": info["duration"], "parallel_group": group})
            state["active_parallel_actions"] = active
            state["active_action"] = "parallel"
            state["active_action_index"] = index
            agent["initial_state"] = state
        action_defs = {item["id"]: item for item in self.store.get_definitions("action_def")}
        completed_count = 0
        for item_index, entry in list(active.items()):
            if int(entry["remaining"]) == 0:
                completed_count += 1
                continue
            action = entry.get("definition_snapshot") or action_defs.get(entry["action_id"])
            if not action:
                self._finish_behavior(agent, execution_id, behavior_id, "failed", f"action is not registered: {entry['action_id']}", minute)
                return
            if not entry.get("no_op"):
                self._apply_action_progress(agent, action)
            entry["remaining"] = max(0, int(entry["remaining"]) - 1)
            elapsed = int(entry["duration"]) - int(entry["remaining"])
            self.store.update_action_instance(entry["action_instance_id"], status="succeeded" if entry["remaining"] == 0 else "running", elapsed_minutes=elapsed, remaining_minutes=entry["remaining"], data={"parameters": entry["parameters"], "target": entry["target"], "reservation": entry.get("reservation", []), "parallel_group": group, "no_op": bool(entry.get("no_op")), "definition_version": int(entry.get("definition_version", action.get("definition_version", action.get("version", 1))) or 1), "definition_snapshot": entry.get("definition_snapshot") or copy.deepcopy(action)}, updated_minute=minute)
            if entry["remaining"] == 0:
                failure = self._action_failure_reason(agent, action, entry["parameters"], entry.get("target"))
                if failure:
                    self._finish_behavior(agent, execution_id, behavior_id, "failed", failure, minute)
                    return
                success_failure, information_result = self._complete_action_effects(
                    agent, action, entry["parameters"], minute, target=entry.get("target")
                )
                if success_failure:
                    self._finish_behavior(agent, execution_id, behavior_id, "failed", success_failure, minute)
                    return
                action_result = str((information_result or {}).get("result") or action.get("success", {}).get("result", "completed"))
                state = dict(agent.get("initial_state") or {})
                state["last_action_result"] = action_result
                agent["initial_state"] = state
                self.store.append_event("action.completed", agent["id"], minute, {"execution_id": execution_id, "action_id": entry["action_id"], "parallel_group": group, "result": action_result})
                completed_count += 1
        remaining_values = [int(entry["remaining"]) for entry in active.values()]
        state = dict(agent.get("initial_state") or {})
        state["active_parallel_actions"] = active
        state["remaining_minutes"] = max(remaining_values or [0])
        state["action_progress"] = round(min(1 - int(entry["remaining"]) / max(1, int(entry["duration"])) for entry in active.values()), 4) if active else 1.0
        if completed_count == len(active):
            state["active_parallel_actions"] = {}
            state["active_action"] = None
            state["active_action_index"] = index + len(group_items)
            state["remaining_minutes"] = 0
            state["action_progress"] = 1.0
            agent["initial_state"] = state
            bt_data = json.loads(self.store.connection.execute("SELECT data_json FROM bt_instance WHERE execution_id=?", (execution_id,)).fetchone()["data_json"])
            self.store.update_bt_instance(execution_id, status="executing", current_node=index + len(group_items), data=bt_data, updated_minute=minute)
            if index + len(group_items) >= len(actions):
                self._finish_behavior(agent, execution_id, behavior_id, "completed", "behavior_completed", minute)
            else:
                self.store.update_agent(agent["id"], agent)
        else:
            agent["initial_state"] = state
            self.store.update_agent(agent["id"], agent)

    def _progress_agent(self, agent: dict[str, Any], minute: int) -> None:
        state = dict(agent.get("initial_state") or {})
        if state.get("status") not in {"executing", "paused"} or state.get("status") == "paused":
            return
        execution_id = state.get("active_bt_instance")
        behavior_id = state.get("active_behavior_id")
        if not execution_id or not behavior_id:
            return
        bt_rows = self.store.connection.execute("SELECT data_json,current_node FROM bt_instance WHERE execution_id=?", (execution_id,)).fetchone()
        if bt_rows is None:
            state["status"] = "failed"
            state["last_platform_result"] = {"status": "failed", "message": "BT instance is missing."}
            agent["initial_state"] = state
            self.store.update_agent(agent["id"], agent)
            return
        bt_data = json.loads(bt_rows["data_json"])
        actions = bt_data.get("actions", [])
        index = int(state.get("active_action_index", bt_rows["current_node"] or 0))
        if index >= len(actions):
            self._finish_behavior(agent, execution_id, behavior_id, "completed", "behavior_completed", minute)
            return
        if actions[index].get("parallel_group"):
            self._progress_parallel_group(agent, execution_id, behavior_id, actions, index, minute)
            return
        if not state.get("active_action"):
            started, reason = self._start_action(agent, execution_id, actions[index], minute)
            if not started:
                self._finish_behavior(agent, execution_id, behavior_id, "failed", reason, minute)
                return
            state = dict(agent.get("initial_state") or {})
        action_id = state.get("active_action")
        # Prefer the immutable definition snapshot captured by _start_action.
        # The registry may now contain a newer version proposed by God.
        action = state.get("active_action_definition") or next((item for item in self.store.get_definitions("action_def") if item.get("id") == action_id), None)
        if not action:
            self._finish_behavior(agent, execution_id, behavior_id, "failed", "action_missing", minute)
            return
        state = dict(agent.get("initial_state") or {})
        # An idempotent destination Action still completes at this tick, but
        # its configured movement costs do not apply when already there.
        if not state.get("active_action_noop"):
            self._apply_action_progress(agent, action)
            # The progress pipeline mutates the agent projection.  Reload it
            # before advancing the action cursor; otherwise the old cursor
            # snapshot would silently overwrite every per-minute cost.
            state = dict(agent.get("initial_state") or {})
        remaining = max(0, int(state.get("remaining_minutes", 1)) - 1)
        duration = max(1, int(state.get("active_action_duration", state.get("remaining_minutes", 1))))
        state["remaining_minutes"] = remaining
        state["action_progress"] = round(1 - remaining / duration, 4)
        agent["initial_state"] = state
        action_instance_id = state.get("active_action_instance")
        if action_instance_id:
            self.store.update_action_instance(action_instance_id, status="succeeded" if remaining == 0 else "running", elapsed_minutes=duration - remaining, remaining_minutes=remaining, data={"parameters": state.get("active_action_parameters", {}), "reservation": state.get("active_action_reservation", []), "no_op": bool(state.get("active_action_noop")), "definition_version": int(state.get("active_action_definition_version", action.get("definition_version", action.get("version", 1))) or 1), "definition_snapshot": copy.deepcopy(action)}, updated_minute=minute)
        if remaining > 0:
            self.store.update_bt_instance(execution_id, status="executing", current_node=index, data=bt_data, updated_minute=minute)
            self.store.update_agent(agent["id"], agent)
            return
        action_parameters = state.get("active_action_parameters", {})
        failure = self._action_failure_reason(agent, action, action_parameters, self.targets.resolve(agent, action_parameters)[0])
        if failure:
            self._finish_behavior(agent, execution_id, behavior_id, "failed", failure, minute)
            return
        resolved_target = self.targets.resolve(agent, action_parameters)[0]
        success_failure, information_result = self._complete_action_effects(
            agent, action, action_parameters, minute, target=resolved_target
        )
        if success_failure:
            self._finish_behavior(agent, execution_id, behavior_id, "failed", success_failure, minute)
            return
        self._record_declared_capability_evidence(agent, action, action_parameters, minute)
        action_result = str((information_result or {}).get("result") or action.get("success", {}).get("result", "completed"))
        state = dict(agent.get("initial_state") or {})
        state["last_action_result"] = action_result
        agent["initial_state"] = state
        self.store.append_event("action.completed", agent["id"], minute, {"execution_id": execution_id, "action_id": action_id, "result": action_result})
        # Effects may update location, health, inventory or needs; retain that
        # projection before clearing the completed action fields.
        state = dict(agent.get("initial_state") or {})
        state["active_action_index"] = index + 1
        state["active_action"] = None
        state["active_action_instance"] = None
        state["active_action_duration"] = 0
        state["active_action_parameters"] = {}
        state["active_action_reservation"] = []
        state["active_action_noop"] = False
        state["active_action_duration"] = 0
        state["active_action_definition_version"] = 0
        state["active_action_definition"] = None
        state["action_progress"] = 1.0
        agent["initial_state"] = state
        self.store.update_bt_instance(execution_id, status="executing", current_node=index + 1, data=bt_data, updated_minute=minute)
        if index + 1 >= len(actions):
            self._finish_behavior(agent, execution_id, behavior_id, "completed", "behavior_completed", minute)
        else:
            self.store.update_agent(agent["id"], agent)

    def _record_declared_capability_evidence(
        self,
        agent: dict[str, Any],
        action: dict[str, Any],
        parameters: dict[str, Any],
        minute: int,
    ) -> None:
        """Record only capability evidence declared for the completed Action.

        Recipe effects may name a narrower capability than the generic craft
        Action, so those explicit declarations take precedence. Other Actions
        use the imported Capability ``uses`` catalog. This records evidence;
        Reflection still decides whether to request a bounded level update.
        """

        explicit: set[str] = set()
        for effect in list(action.get("effects") or []) + list(action.get("capability_effects") or []):
            if not isinstance(effect, dict):
                continue
            if str(effect.get("type") or effect.get("op") or "") in {"capability_xp", "capability.add_xp"} and effect.get("capability"):
                explicit.add(str(effect["capability"]))
        recipe_id = parameters.get("recipe_id")
        if recipe_id:
            recipe = next((item for item in self.store.get_definitions("recipe_def") if item.get("id") == recipe_id), None)
            for effect in (recipe or {}).get("effects", []) or []:
                if isinstance(effect, dict) and str(effect.get("type") or effect.get("op") or "") == "capability.add_xp" and effect.get("capability"):
                    explicit.add(str(effect["capability"]))

        owned = {str(item) for item in (agent.get("capabilities") or {})}
        action_id = str(action.get("id") or "")
        for definition in self.store.get_definitions("capability_def"):
            capability = str(definition.get("id") or "")
            uses = {str(item) for item in (definition.get("uses") or [])}
            if not capability or capability not in owned or capability in explicit or action_id not in uses:
                continue
            self.store.append_event(
                "capability.evidence",
                agent["id"],
                minute,
                {"capability": capability, "source": action_id, "xp_rule": definition.get("xp_rule"), "requires_agent_review": True},
            )

    def _apply_success_effects(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], minute: int, *, target: dict[str, Any] | None = None) -> list[str]:
        errors: list[str] = []
        effects = list(action.get("effects", []) or [])
        for effect in effects:
            resolved_effect = {
                key: parameters.get(value.removeprefix("context."), value) if isinstance(value, str) and value.startswith("context.") else value
                for key, value in effect.items()
            }
            effect_target = target or parameters
            receiver = agent
            if effect.get("target") == "target" and target and target.get("id"):
                try:
                    receiver = self.store.get_agent(str(target["id"]))
                except KeyError:
                    receiver = agent
            if effect.get("formula") and parameters.get("weapon_id"):
                combat = agent.get("combat_stats", {}) or {}
                weapon_damage = (combat.get("weapon_damage", {}) or {}).get(str(parameters["weapon_id"]), 0)
                effect_target = {**effect_target, "weapon": {"damage": weapon_damage}}
            effect_type = str(effect.get("type") or effect.get("op") or "")
            if effect_type == "relationship_delta":
                self._apply_relationship_effect(agent, target or effect_target, effect, minute)
            elif effect_type == "public_event":
                event_type = str(effect.get("event") or "public.event")
                self.store.append_event(event_type, agent["id"], minute, {"action_id": action.get("id"), "parameters": parameters})
            elif effect_type == "world_property_delta":
                property_id = str(effect.get("property") or effect.get("world_property") or "")
                if property_id:
                    properties = dict(self.store.get_meta("world_properties", {}) or {})
                    before_value = properties.get(property_id)
                    value = float(before_value or 0) + float(effect.get("delta", effect.get("value", 0)) or 0)
                    if isinstance(effect.get("minimum"), (int, float)):
                        value = max(float(effect["minimum"]), value)
                    if isinstance(effect.get("maximum"), (int, float)):
                        value = min(float(effect["maximum"]), value)
                    self._set_world_property(property_id, value)
                    self.store.append_event(
                        "world.property_changed", agent["id"], minute,
                        {"property": property_id, "before": before_value, "after": value, "delta": value - float(before_value or 0), "source": action.get("id")},
                    )
            elif effect_type == "entity_state_set":
                entity_id = resolved_effect.get("entity_id") or resolved_effect.get("target_entity")
                state_key = resolved_effect.get("state") or resolved_effect.get("property")
                if entity_id and state_key:
                    try:
                        entity = self.store.get_entity(str(entity_id))
                    except KeyError:
                        entity = None
                    if entity is not None:
                        entity_state = dict(entity.get("initial_state") or {})
                        before_value = entity_state.get(str(state_key))
                        entity_state[str(state_key)] = resolved_effect.get("value")
                        entity["initial_state"] = entity_state
                        self.store.put_entity(
                            entity["id"], entity["entity_type"], entity["name"],
                            {key: value for key, value in entity.items() if key not in {"id", "entity_type", "name"}},
                        )
                        self.store.append_event(
                            "entity.state_changed", agent["id"], minute,
                            {"entity_id": entity["id"], "state": str(state_key), "before": before_value, "after": entity_state[str(state_key)], "action_id": action.get("id")},
                        )
            elif effect_type == "institution_allocation":
                allocation = self.apply_institution_allocation(
                    str(resolved_effect.get("institution") or ""),
                    str(resolved_effect.get("pool_entity") or ""),
                    str(resolved_effect.get("item") or ""),
                    str(resolved_effect.get("period") or "world"),
                    agent["id"],
                    minute,
                    list(resolved_effect.get("subjects") or []),
                )
                if allocation.get("status") == "accepted":
                    for row in allocation.get("allocations", []) or []:
                        if str(row.get("subject")) == str(agent.get("id")):
                            inventory = dict(agent.get("inventory") or {})
                            key = str(resolved_effect.get("item") or "")
                            inventory[key] = float(inventory.get(key, 0) or 0) + float(row.get("total", 0) or 0)
                            agent["inventory"] = inventory
                    allocation_event = dict(allocation)
                    allocation_event.update({
                        "behavior_id": str(agent.get("initial_state", {}).get("active_behavior_id") or ""),
                        "execution_id": str(agent.get("initial_state", {}).get("active_bt_instance") or ""),
                        "action_id": str(action.get("id") or ""),
                    })
                    allocation_event_id = self.store.append_event("institution.allocation_completed", agent["id"], minute, allocation_event)
                    self._wake_configured_evolution_consumers(
                        allocation_event_id, "institution.allocation_completed", agent["id"], minute,
                    )
                else:
                    self.store.append_event("institution.allocation_failed", agent["id"], minute, allocation)
                    errors.append(str(allocation.get("message") or allocation.get("failed_check") or "institution allocation failed"))
            else:
                self.effects.apply(receiver, resolved_effect, target=effect_target, formula_agent=agent)
            if receiver is not agent:
                self.store.update_agent(receiver["id"], receiver)
            if effect.get("type") in {"capability_xp", "capability.add_xp"} or effect.get("op") == "capability.add_xp":
                capability = effect.get("capability")
                if capability:
                    self.store.append_event("capability.evidence", agent["id"], minute, {"capability": str(capability), "source": str(action.get("id", "action")), "configured_delta": float(effect.get("value", effect.get("delta", 0)) or 0), "requires_agent_review": True})
        for effect in action.get("capability_effects", []) or []:
            capability = effect.get("capability")
            if capability:
                self.store.append_event("capability.evidence", agent["id"], minute, {"capability": str(capability), "source": str(action.get("id", "action")), "configured_delta": float(effect.get("value", 0) or 0), "requires_agent_review": True})
        recipe_id = parameters.get("recipe_id")
        if not recipe_id:
            return errors
        recipe = next((item for item in self.store.get_definitions("recipe_def") if item.get("id") == recipe_id), None)
        if not recipe:
            return errors
        for output in recipe.get("outputs", []) or []:
            item = str(output.get("item"))
            agent.setdefault("inventory", {})[item] = agent["inventory"].get(item, 0) + output.get("quantity", 0)
        for effect in recipe.get("effects", []) or []:
            effect_type = str(effect.get("type") or effect.get("op") or "")
            if effect_type == "entity_state_set":
                entity_id = effect.get("entity_id") or effect.get("target_entity")
                state_key = effect.get("state") or effect.get("property")
                if entity_id and state_key:
                    try:
                        entity = self.store.get_entity(str(entity_id))
                    except KeyError:
                        entity = None
                    if entity is not None:
                        entity_state = dict(entity.get("initial_state") or {})
                        before_value = entity_state.get(str(state_key))
                        entity_state[str(state_key)] = effect.get("value")
                        entity["initial_state"] = entity_state
                        self.store.put_entity(
                            entity["id"], entity["entity_type"], entity["name"],
                            {key: value for key, value in entity.items() if key not in {"id", "entity_type", "name"}},
                        )
                        self.store.append_event(
                            "entity.state_changed", agent["id"], minute,
                            {"entity_id": entity["id"], "state": str(state_key), "before": before_value, "after": entity_state[str(state_key)], "action_id": f"recipe:{recipe_id}"},
                        )
            elif effect_type == "capability.add_xp":
                capability = effect.get("capability")
                if capability:
                    self.store.append_event("capability.evidence", agent["id"], minute, {"capability": str(capability), "source": f"recipe:{recipe_id}", "configured_delta": float(effect.get("value", 0) or 0), "requires_agent_review": True})
        return errors

    def _action_condition_context(self, agent: dict[str, Any], parameters: dict[str, Any], target: dict[str, Any] | None) -> dict[str, Any]:
        state = agent.get("initial_state", {}) or {}
        location = {}
        location_id = state.get("location_id")
        if location_id:
            try:
                location = self.store.get_entity(str(location_id))
            except KeyError:
                location = {}
        source = {}
        source_id = parameters.get("source_id")
        if source_id:
            try:
                source = self.store.get_entity(str(source_id))
            except KeyError:
                source = {}
        return {
            "location_id": location_id,
            "destination_id": parameters.get("location_id") or parameters.get("destination_id"),
            "source_id": source_id,
            "item_id": parameters.get("item_id"),
            "current_location": location,
            "location": location,
            "source": source,
            "research_discoveries": self.store.research_discoveries(str(source_id)) if source_id else [],
            "known_fact_keys": [
                item.get("fact_key")
                for item in self.store.list_knowledge_facts(agent["id"], limit=1000)
                if item.get("fact_key")
            ],
            "station": location,
            "target": target or {},
            "danger": {"detected": "danger" in (state.get("conditions") or [])},
            "hostile_contact": "hostile_contact" in (state.get("conditions") or []),
            "target.refused": "target_refused" in (state.get("conditions") or []),
            "known_route_destinations": reachable_locations(self.store, location_id),
        }

    def _action_failure_reason(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], target: dict[str, Any] | None) -> str | None:
        condition = str((action.get("failure") or {}).get("condition") or "").strip()
        if not condition:
            return None
        context = self._action_condition_context(agent, parameters, target)
        if self.conditions.evaluate(condition, agent=agent, target=target or {}, context=context, world=self.store.get_meta("world_properties", {}) or {}):
            return str((action.get("failure") or {}).get("result") or condition)
        return None

    def _action_success_reason(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], target: dict[str, Any] | None) -> str | None:
        """Return a failure result when a machine-checkable postcondition fails.

        Configured result labels remain descriptive contracts.  Only predicates
        understood by the generic ConditionEngine are enforced here; unknown
        labels are satisfied by the configured effect/information pipeline and
        retain their normal result text.
        """

        success = action.get("success") or {}
        condition = str(success.get("condition") or "").strip()
        if not condition:
            return None
        context = self._action_condition_context(agent, parameters, target)
        verdict = self.conditions.evaluate_known(
            condition,
            agent=agent,
            target=target or {},
            context=context,
            world=self.store.get_meta("world_properties", {}) or {},
        )
        if verdict is False:
            return str(success.get("failure_result") or f"success condition failed: {condition}")
        return None

    def _complete_action_effects(
        self,
        agent: dict[str, Any],
        action: dict[str, Any],
        parameters: dict[str, Any],
        minute: int,
        *,
        target: dict[str, Any] | None = None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Apply one Action's effects atomically with its postcondition.

        A configured success condition is part of the commit contract.  If a
        machine-checkable postcondition is false, neither the effect nor its
        information/result event may remain in SQLite.  This prevents a
        failed Action from publishing a success story or leaving a partial
        world mutation behind.
        """

        original_agent = copy.deepcopy(agent)
        savepoint = f"action_effects_{uuid4().hex}"
        self.store.connection.execute(f"SAVEPOINT {savepoint}")
        try:
            effect_errors = self._apply_success_effects(agent, action, parameters, minute, target=target)
            if effect_errors:
                self.store.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.store.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                agent.clear()
                agent.update(original_agent)
                return "; ".join(dict.fromkeys(effect_errors)), None
            failure = self._action_success_reason(agent, action, parameters, target)
            if failure:
                self.store.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.store.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                agent.clear()
                agent.update(original_agent)
                return failure, None
            information_result = self._record_information_result(agent, action, parameters, minute)
            self.store.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            return None, information_result
        except Exception:
            self.store.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.store.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            agent.clear()
            agent.update(original_agent)
            raise

    def _apply_relationship_effect(self, agent: dict[str, Any], target: dict[str, Any], effect: dict[str, Any], minute: int) -> None:
        target_id = str(target.get("id") or target.get("entity_id") or "") if isinstance(target, dict) else ""
        if not target_id or target_id == agent["id"]:
            return
        relation_type = str(effect.get("relation") or "related")
        delta = float(effect.get("delta", effect.get("value", 0)) or 0)
        current = next((item for item in self.store.list_relationships() if item.get("from") == agent["id"] and item.get("to") == target_id and item.get("type") == relation_type), None)
        data = dict(current or {})
        for key in ("from", "to", "type"):
            data.pop(key, None)
        data["relation_strength"] = max(-1.0, min(1.0, float(data.get("relation_strength", 0) or 0) + delta))
        self.store.put_relationship(agent["id"], target_id, relation_type, data)
        self.store.append_event("relationship.changed", agent["id"], minute, {"target_id": target_id, "relation": relation_type, "delta": delta, "relation_strength": data["relation_strength"]})

    def _queue_story_task(self, event_id: int, event_type: str, actor_id: str, minute: int) -> None:
        """Create a same-boundary Story Agent barrier for a new discovery."""

        self.store.put_evolution_task(
            f"story:event:{event_id}", "story", "story", f"story:{event_type}", minute, 0,
            data={"source_event_id": int(event_id), "source_event_type": event_type, "source_actor_id": actor_id},
        )

    def _queue_event_consumer_task(self, consumer: str, event_id: int, event_type: str, actor_id: str | None, minute: int, *, task_type: str | None = None, explicit: bool = False) -> str | None:
        role, resolved_actor = self._resolve_evolution_consumer(consumer, actor_id)
        if role in {"agent_decision", "agent_reflection"} and resolved_actor:
            task_id = f"{role}:event:{int(event_id)}:{resolved_actor}"
            self.store.put_evolution_task(
                task_id,
                role,
                resolved_actor,
                str(task_type or f"event:{event_type}"),
                int(minute),
                0,
                status="ready",
                data={
                    "source_events": [{"event_id": int(event_id), "event_type": str(event_type), "actor_id": actor_id}],
                    "source_event_id": int(event_id),
                    "source_event_type": str(event_type),
                    "source_actor_id": actor_id,
                },
            )
            return task_id
        return self.event_wakes.queue(role, event_id, event_type, resolved_actor, minute, task_type=task_type, explicit=explicit)

    def _queue_configured_followups(self, configured: dict[str, Any], event_id: int, event_type: str, actor_id: str, minute: int) -> None:
        """Wake configured result consumers without knowing story-specific IDs."""

        for role in configured.get("wake", configured.get("wake_roles", [])) or []:
            role = str(role)
            if not role:
                continue
            self._queue_event_consumer_task(
                role,
                event_id,
                event_type,
                actor_id,
                minute,
                task_type=f"event:{event_type}",
                explicit=True,
            )

    def _advance_information_progress(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], minute: int) -> dict[str, Any] | None:
        definition = action.get("progress") or {}
        if not isinstance(definition, dict) or not definition.get("key"):
            return None
        source_id = str(parameters.get("source_id") or "")
        discoveries = self.store.research_discoveries(source_id) if source_id else []
        known_fact_keys = {
            str(item.get("fact_key"))
            for item in self.store.list_knowledge_facts(agent["id"], limit=1000)
            if item.get("fact_key")
        }
        pending = [
            item for item in discoveries
            if str(item.get("id") or "") not in known_fact_keys
            and str(item.get("status") or "active").casefold() not in {"obsolete", "disabled"}
        ]
        discovery = pending[0] if pending else None
        key = str(definition["key"])
        for name, value in parameters.items():
            key = key.replace("{" + str(name) + "}", str(value))
        # Preserve the original key for one-stage legacy sources.  Multi-stage
        # sources receive an independent progress record per question, so a
        # completed stage never consumes the entire source.
        if discovery and len(discoveries) > 1:
            key = f"{key}:{discovery['id']}"
        scope = str(definition.get("scope", "agent"))
        owner = str(self.store.get_meta("world_id", "world")) if scope == "world" else str(agent["id"])
        required = float((discovery or {}).get("required", definition.get("required", 1)) or 1)
        progress = self.store.advance_progress(owner, key, float(definition.get("amount", 1) or 1), required, minute, {"source_action": action.get("id"), "source_agent": agent["id"], "stage_id": (discovery or {}).get("id")})
        event_type = "research.completed" if progress["completed"] else "research.progressed"
        event_id = self.store.append_event(event_type, agent["id"], minute, {"progress_key": key, "progress": progress, "source_action": action.get("id"), "parameters": parameters, "source_id": source_id, "stage_id": (discovery or {}).get("id"), "discovery_status": (discovery or {}).get("status", "active")})
        # Partial work is retained in SQLite for the next attempt. Story
        # feedback is a completion boundary, so an incomplete sample does not
        # pause the world or wake the Story Agent without a new fact.
        if progress["completed"]:
            wake = definition.get("wake")
            if wake is None:
                self._queue_story_task(event_id, event_type, agent["id"], minute)
            else:
                self._queue_configured_followups({"wake": wake}, event_id, event_type, agent["id"], minute)
        return {**progress, "discovery": discovery, "stage_id": (discovery or {}).get("id"), "progress_key": key}

    def _record_information_result(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], minute: int) -> dict[str, Any]:
        """Persist information produced by configured information channels."""

        information = action.get("information", {}) or {}
        result: dict[str, Any] = {}
        area_id = parameters.get("area_id") or information.get("area_parameter") and parameters.get(information["area_parameter"])
        source_id = parameters.get("source_id") or information.get("source_parameter") and parameters.get(information["source_parameter"])
        target_id = parameters.get("target_id")
        topic = parameters.get("topic")
        message = str(parameters.get("message") or "").strip()
        if area_id:
            area_id = str(area_id)
            if area_id not in self.store.explored_areas(agent["id"]):
                result_id = self.store.put_exploration_result(agent["id"], area_id, minute, {"source": action.get("id"), "parameters": parameters})
                area = self.store.get_entity(area_id) if area_id else {}
                discoveries = list(area.get("discoveries", []) or []) if isinstance(area, dict) else []
                discoveries = [{"id": f"explored:{area_id}", "fact": f"Area {area_id} was explored."}, *discoveries]
                for discovery in discoveries:
                    discovery_id = str(discovery.get("id") or f"explored:{area_id}")
                    fact = str(discovery.get("fact") or f"Area {area_id} was explored.")
                    self.store.merge_knowledge_fact(agent["id"], discovery_id, {"fact": fact, "source_type": "exploration", "source_id": result_id, "behavior": action.get("id"), "confidence": 0.8, "observed_at": minute})
                    unlocks = discovery.get("unlocks", []) if isinstance(discovery, dict) else []
                    for unlock in unlocks or []:
                        behavior_id = str(unlock.get("behavior_id") if isinstance(unlock, dict) else unlock)
                        if not behavior_id:
                            continue
                        registered = any(item.get("id") == behavior_id for item in self.store.get_definitions("bt_def"))
                        if not registered:
                            continue
                        unlocked = list(agent.get("unlocked_bt_ids", []) or [])
                        if behavior_id not in unlocked:
                            unlocked.append(behavior_id)
                            agent["unlocked_bt_ids"] = unlocked
                            self.store.append_event("behavior.unlocked", agent["id"], minute, {"behavior_id": behavior_id, "discovery_id": discovery_id, "source": "exploration"})
                event_id = self.store.append_event("exploration.completed", agent["id"], minute, {"area_id": area_id, "result_id": result_id, "discoveries": [str(item.get("id")) for item in discoveries]})
                self._queue_story_task(event_id, "exploration.completed", agent["id"], minute)
                result = {"result": "exploration_completed", "event_id": event_id}
        if source_id:
            source_id = str(source_id)
            progress = self._advance_information_progress(agent, action, parameters, minute)
            if progress is None or progress["completed"]:
                discovery = (progress or {}).get("discovery") if isinstance(progress, dict) else None
                if discovery is None:
                    candidates = [item for item in self.store.research_discoveries(source_id) if str(item.get("id") or "") not in {str(row.get("fact_key")) for row in self.store.list_knowledge_facts(agent["id"], limit=1000)}]
                    discovery = candidates[0] if candidates else None
                if discovery is not None or source_id not in self.store.researched_sources(agent["id"]):
                    source = self.store.get_entity(source_id) if source_id else {}
                    discovery = discovery or ((source.get("research_discovery") or {}) if isinstance(source, dict) else {})
                    fact_key = str(discovery.get("id") or f"researched:{source_id}")
                    fact = str(discovery.get("fact") or f"Source {source_id} was researched.")
                    self.store.merge_knowledge_fact(agent["id"], fact_key, {"fact": fact, "source_type": "research", "source_id": source_id, "behavior": action.get("id"), "confidence": 0.8, "observed_at": minute, "status": str(discovery.get("status") or "active"), "supersedes": discovery.get("supersedes", [])})
                    # A configured progress action already emitted the
                    # completion event; without progress keep the legacy
                    # single-attempt event contract.
                    if progress is None:
                        event_id = self.store.append_event("research.completed", agent["id"], minute, {"source_id": source_id, "fact": fact})
                        self._queue_story_task(event_id, "research.completed", agent["id"], minute)
                        result = {"result": "research_completed", "event_id": event_id, "progress": progress}
                    elif progress is not None:
                        result = {"result": "research_progressed", "progress": progress}
        object_id = parameters.get("object_id") or information.get("object_parameter") and parameters.get(information["object_parameter"])
        if object_id and information.get("record_object_discovery", True):
            object_id = str(object_id)
            try:
                discovered_object = self.store.get_entity(object_id)
            except KeyError:
                discovered_object = {}
            discovery = (discovered_object.get("recovery_discovery") or {}) if isinstance(discovered_object, dict) else {}
            fact_key = str(discovery.get("id") or f"recovered:{object_id}")
            fact = str(discovery.get("fact") or f"Object {object_id} was recovered.")
            self.store.merge_knowledge_fact(
                agent["id"], fact_key,
                {"fact": fact, "source_type": "recovery", "source_id": object_id, "object_id": object_id, "behavior": action.get("id"), "confidence": 0.9, "observed_at": minute},
            )
            event_id = self.store.append_event("object.recovered", agent["id"], minute, {"object_id": object_id, "fact": fact})
            self._queue_story_task(event_id, "object.recovered", agent["id"], minute)
            result = {"result": "object_recovered", "event_id": event_id}
        if target_id and topic:
            target_id = str(target_id)
            try:
                listener = self.store.get_agent(target_id)
            except KeyError:
                listener = None
            testimony = self._testimony_metadata(agent, listener) if listener else {}
            dialogue_id = self.store.put_dialogue_exchange(agent["id"], target_id if listener else None, str(topic), minute, {"source": action.get("id"), "message": message, "parameters": parameters})
            self.store.put_memory(agent["id"], "dialogue", {"topic": str(topic), "message": message, "target_id": target_id, "world_minute": minute, "dialogue_id": dialogue_id, "claim_status": "observed", "source_type": "own_statement"})
            if listener:
                self.store.put_memory(target_id, "testimony", {"topic": str(topic), "message": message, "speaker_id": agent["id"], "world_minute": minute, "dialogue_id": dialogue_id, "claim_status": "reported", "source_type": "testimony", **testimony})
                self._apply_configured_relationship_effect(agent, listener, "dialogue.completed", minute)
                self.store.update_agent(listener["id"], listener)
            event_id = self.store.append_event("dialogue.completed", agent["id"], minute, {"target_id": target_id, "topic": str(topic), "message": message, "dialogue_id": dialogue_id, **testimony})
            result = {"result": "dialogue_recorded", "event_id": event_id}
        configured_event = information.get("event_type") or information.get("event")
        observed: dict[str, Any] = {}
        observed_id = parameters.get("object_id") or parameters.get("entity_id") or parameters.get("item_id") or parameters.get("source_id") or parameters.get("area_id")
        if observed_id:
            try:
                entity = self.store.get_entity(str(observed_id))
            except KeyError:
                entity = {}
            if entity:
                entity_state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
                amount = entity.get("amount", entity_state.get("quantity", entity_state.get("amount")))
                observed = {
                    "name": entity.get("name", observed_id),
                    "entity_type": entity.get("entity_type"),
                    "amount": amount,
                    "location": entity_state.get("location_id") or entity.get("location_id"),
                    "status": {key: value for key, value in entity_state.items() if key in {"recovered", "created", "readable", "durability"}},
                }
        if configured_event:
            event_data = dict(information.get("data") or {})
            event_data.update({"action_id": action.get("id"), "parameters": parameters})
            if observed:
                event_data["observed"] = observed
            if information.get("summary"):
                event_data["summary"] = str(information["summary"])
            if information.get("trend") is True:
                event_data["trend"] = True
            if "story" in {str(item) for item in (information.get("wake") or [])}:
                event_data["story_source"] = True
            event_id = self.store.append_event(str(configured_event), agent["id"], minute, event_data)
            self._queue_configured_followups(information, event_id, str(configured_event), agent["id"], minute)
            result.setdefault("event_id", event_id)
            result.setdefault("result", str(information.get("result") or "information_recorded"))
        fact_template = information.get("fact_template")
        if fact_template:
            template_values = {"id": observed_id or "", **observed}
            try:
                fact = str(fact_template).format_map(template_values)
            except (KeyError, ValueError):
                fact = str(fact_template)
        else:
            fact = information.get("fact") or information.get("knowledge_fact")
        if fact:
            fact_key = str(information.get("fact_key") or f"{action.get('id', 'action')}:{parameters.get('object_id') or parameters.get('source_id') or parameters.get('area_id') or 'result'}")
            self.store.merge_knowledge_fact(agent["id"], fact_key, {"fact": str(fact), "source_type": str(information.get("source_type") or "action"), "source_id": str(action.get("id", "action")), "behavior": str(action.get("id", "action")), "confidence": float(information.get("confidence", 0.8)), "observed_at": minute})
            result.setdefault("result", str(information.get("result") or "information_recorded"))
        return result

    def _institution_period_key(self, period: Any, minute: int) -> str:
        if isinstance(period, (int, float)) and not isinstance(period, bool) and float(period) > 0:
            size = max(1, int(period))
            return f"block:{int(minute) // size}"
        calendar = (self.store.get_meta("setting", {}) or {}).get("calendar", {}) or {}
        key = str(period or "world").casefold()
        sizes = {
            "day": int(calendar.get("minutes_per_day", 1440) or 1440),
            "week": int(calendar.get("minutes_per_week", 10080) or 10080),
            "month": int(calendar.get("minutes_per_month", 43200) or 43200),
        }
        if key in sizes:
            return f"{key}:{int(minute) // max(1, sizes[key])}"
        return f"{key}:{int(minute)}"

    @staticmethod
    def _institution_selector_matches(selector: dict[str, Any], event_type: str, payload: dict[str, Any]) -> bool:
        event_types = selector.get("event_types", selector.get("events", []))
        if isinstance(event_types, str):
            event_types = [event_types]
        if event_types and str(event_type) not in {str(item) for item in event_types}:
            return False
        behavior_ids = selector.get("behavior_ids", selector.get("behaviors", []))
        if isinstance(behavior_ids, str):
            behavior_ids = [behavior_ids]
        if behavior_ids and str(payload.get("behavior_id", "")) not in {str(item) for item in behavior_ids}:
            return False
        action_ids = selector.get("action_ids", selector.get("actions", []))
        if isinstance(action_ids, str):
            action_ids = [action_ids]
        if action_ids and str(payload.get("action_id", "")) not in {str(item) for item in action_ids}:
            return False
        return True

    def _record_active_institution_entries(self, event_id: int, event_type: str, actor_id: str, minute: int, payload: dict[str, Any]) -> None:
        """Apply active, declarative ledger selectors to one verified event."""

        for institution in self.store.list_institutions(status="active", limit=200):
            ledger = institution.get("ledger") if isinstance(institution.get("ledger"), dict) else {}
            selectors = ledger.get("selectors", ledger.get("evidence", []))
            if not isinstance(selectors, list):
                continue
            for raw_selector in selectors:
                if not isinstance(raw_selector, dict) or not self._institution_selector_matches(raw_selector, event_type, payload):
                    continue
                category = str(raw_selector.get("category") or "general").strip()
                amount = float(raw_selector.get("points", raw_selector.get("amount", 1)) or 0)
                if not category or amount <= 0:
                    continue
                period = self._institution_period_key(ledger.get("period", "world"), minute)
                entry_id = self.store.append_institution_ledger_entry(
                    str(institution["institution_id"]), actor_id, period, category, amount,
                    int(event_id), "verified", {"event_type": event_type, "source": payload.get("behavior_id") or payload.get("action_id") or event_type}, minute,
                )
                if entry_id is not None:
                    ledger_event_id = self.store.append_event(
                        "institution.ledger_entry", actor_id, minute,
                        {"institution": {"name": institution.get("name"), "category": category}, "period": period, "amount": amount, "source_event_id": int(event_id), "status": "verified"},
                    )
                    # The package decides whether a ledger accumulation is
                    # enough to wake God or Story. Event groups apply their
                    # own evidence count and cooldown; this call does not
                    # hard-code any institution or quota semantics.
                    self._wake_configured_evolution_consumers(
                        ledger_event_id, "institution.ledger_entry", actor_id, minute,
                    )

    def _finish_behavior(self, agent: dict[str, Any], execution_id: str, behavior_id: str, status: str, result: str, minute: int) -> None:
        state = dict(agent.get("initial_state") or {})
        bt_row = self.store.connection.execute("SELECT data_json FROM bt_instance WHERE execution_id=?", (execution_id,)).fetchone()
        bt_data = json.loads(bt_row["data_json"]) if bt_row is not None else {}
        before = dict(bt_data.get("before") or {})
        behavior_definition = next((item for item in self.store.get_definitions("bt_def") if item.get("id") == behavior_id), {})
        if status == "completed" and result == "behavior_completed":
            result = str(state.get("last_action_result") or result)
        if status != "completed":
            reservations = list(state.get("active_action_reservation") or [])
            for item in (state.get("active_parallel_actions") or {}).values():
                reservations.extend(item.get("reservation", []) or [])
            self.resources.release(agent, reservations)
        if status == "completed":
            # BT-level effects are declarative too.  Running them through the
            # same Platform effect pipeline ensures governance, patrol and
            # other composite behaviors can change the world as well as the
            # actor without introducing world-specific branches here.
            self._apply_success_effects(agent, {"id": f"behavior:{behavior_id}", "effects": behavior_definition.get("outcome_effects", []) or []}, {}, minute)
        after = self._execution_snapshot(agent)
        outcome = {
            "status": status,
            "result": result,
            "expected": dict(bt_data.get("expected") or {}),
            "actual": after,
            "delta": self._execution_delta(before, after) if before else {},
            "decision": dict(bt_data.get("decision") or {}),
        }
        rule_followups: list[dict[str, Any]] = []
        if status == "completed":
            location_id = (agent.get("initial_state") or {}).get("location_id")
            context = {
                "location_id": location_id,
                "current_location": self._entity_for_context(location_id),
                "behavior_id": behavior_id,
                "recent_event_types": self._recent_event_types(),
            }
            world = self._world_trigger_context()
            for raw_rule in self.rule_compiler.all_rules():
                behavior = behavior_definition
                if not self.rule_compiler.behavior_matches(raw_rule, behavior):
                    continue
                if not self.rule_compiler.matches(raw_rule, agent=agent, target={}, context=context, world=world):
                    continue
                compiled = self.rule_compiler.compile(raw_rule)
                followup = {
                    "rule": compiled["name"],
                    "behavior_id": behavior_id,
                    "result": result,
                    "expected_effects": list(raw_rule.get("expected_effects") or []),
                    "observed_delta": outcome.get("delta") or {},
                }
                rule_followups.append(followup)
                self.store.append_event("world.rule.followed", agent["id"], minute, followup)
        if rule_followups:
            outcome["rule_followups"] = rule_followups
        action_instance_id = state.get("active_action_instance")
        if action_instance_id and status != "completed":
            action_row = self.store.connection.execute("SELECT elapsed_minutes,remaining_minutes,data_json FROM action_instance WHERE action_instance_id=?", (action_instance_id,)).fetchone()
            if action_row is not None:
                self.store.update_action_instance(
                    action_instance_id,
                    status="failed",
                    elapsed_minutes=action_row["elapsed_minutes"],
                    remaining_minutes=action_row["remaining_minutes"],
                    data=json.loads(action_row["data_json"]),
                    updated_minute=minute,
                )
        if status != "completed":
            for entry in (state.get("active_parallel_actions") or {}).values():
                action_row = self.store.connection.execute("SELECT elapsed_minutes,remaining_minutes,data_json FROM action_instance WHERE action_instance_id=?", (entry.get("action_instance_id"),)).fetchone()
                if action_row is not None and action_row["remaining_minutes"] > 0:
                    self.store.update_action_instance(entry["action_instance_id"], status="failed", elapsed_minutes=action_row["elapsed_minutes"], remaining_minutes=action_row["remaining_minutes"], data=json.loads(action_row["data_json"]), updated_minute=minute)
        if status == "completed":
            for configured in behavior_definition.get("outcome_events", []) or []:
                if not isinstance(configured, dict):
                    continue
                event_type = str(configured.get("event_type") or configured.get("event") or "").strip()
                if not event_type:
                    continue
                payload = dict(configured.get("data") or {})
                payload.update({"behavior_id": behavior_id, "result": result, "outcome": outcome})
                if configured.get("summary"):
                    payload["summary"] = str(configured["summary"])
                if configured.get("trend") is True:
                    payload["trend"] = True
                if "story" in {str(item) for item in (configured.get("wake") or [])}:
                    payload["story_source"] = True
                payload.setdefault("execution_id", execution_id)
                duplicate_event_id = None
                for existing in self.store.recent_events(limit=32):
                    if (
                        str(existing.get("event_type") or "") == event_type
                        and str(existing.get("actor_id") or "") == str(agent["id"])
                        and int(existing.get("world_minute", -1) or -1) == int(minute)
                        and str(existing.get("behavior_id") or "") == behavior_id
                        and str(existing.get("execution_id") or "") == execution_id
                    ):
                        duplicate_event_id = int(existing["event_id"])
                        break
                event_id = duplicate_event_id or self.store.append_event(event_type, agent["id"], minute, payload)
                self._queue_configured_followups(configured, event_id, event_type, agent["id"], minute)
                # An institution may account for the verified outcome event
                # emitted by a behavior (for example, a research or public
                # inspection result) instead of the generic behavior.completed
                # envelope.  Feed every configured result through the same
                # declarative selector path; no world-specific event name is
                # required and INSERT OR IGNORE keeps retries idempotent.
                self._record_active_institution_entries(
                    event_id, event_type, agent["id"], minute, payload,
                )
            for configured in behavior_definition.get("outcome_facts", []) or []:
                if not isinstance(configured, dict) or not configured.get("fact"):
                    continue
                fact_key = str(configured.get("fact_key") or f"behavior:{behavior_id}:{configured.get('id', 'outcome')}")
                self.store.merge_knowledge_fact(agent["id"], fact_key, {"fact": str(configured["fact"]), "source_type": str(configured.get("source_type") or "behavior"), "source_id": behavior_id, "behavior": behavior_id, "confidence": float(configured.get("confidence", 0.8)), "observed_at": minute})
        state["status"] = "idle" if status == "completed" else "failed"
        state["active_bt_instance"] = None
        state["active_behavior_id"] = None
        state["active_action"] = None
        state["active_action_instance"] = None
        state["active_parallel_actions"] = {}
        state["remaining_minutes"] = 0
        state["last_platform_result"] = {"status": status, "behavior_ref": f"behavior:{behavior_id}", "result": result, "outcome": outcome}
        state["last_outcome"] = outcome
        agent["initial_state"] = state
        self.store.update_agent(agent["id"], agent)
        bt_data["outcome"] = outcome
        bt_data["result"] = result
        self.store.update_bt_instance(execution_id, status=status, current_node=int(state.get("active_action_index", 0)), data=bt_data, updated_minute=minute)
        self.store.append_event(
            "outcome.evaluated",
            agent["id"],
            minute,
            {
                "execution_id": execution_id,
                "behavior_id": behavior_id,
                "status": status,
                "expected": outcome.get("expected", {}),
                "actual": outcome.get("actual", {}),
                "delta": outcome.get("delta", {}),
                "result": result,
                "causation_id": execution_id,
                "correlation_id": execution_id,
            },
        )
        behavior_event_id = self.store.append_event(f"behavior.{status}", agent["id"], minute, {"execution_id": execution_id, "behavior_id": behavior_id, "result": result, "outcome": outcome})
        if status == "completed":
            self._record_active_institution_entries(behavior_event_id, "behavior.completed", agent["id"], minute, {"execution_id": execution_id, "behavior_id": behavior_id, "result": result, "outcome": outcome})
            self._complete_story_beats_for_actor(agent["id"], behavior_id, minute)

    def _complete_story_beats_for_actor(self, actor_id: str, behavior_id: str, minute: int) -> None:
        """Close active audience beats after a matching follow-up behavior.

        Beats are one-shot narrative threads.  When a beat declares
        ``followup_behaviors``, only those behaviors close it, so a chapter
        stays open until its dramatically relevant action happens.  Beats
        without a declared followup keep the legacy close-on-any-behavior
        rule.  Either way the story catalog cannot fill with stale threads
        and Story gets a verifiable close-out.
        """

        try:
            state = dict(self.store.get_meta("story_state", {}) or {})
        except Exception:
            return
        story_root = self.store.get_meta("story", {}) or {}
        story_root = story_root.get("story", story_root) if isinstance(story_root, dict) else {}
        package_beats = {str(item.get("id")): item for item in (story_root.get("beats", []) or []) if isinstance(item, dict)}
        for item in state.get("emergent_beats", []) or []:
            if isinstance(item, dict) and item.get("id"):
                package_beats.setdefault(str(item["id"]), item)
        definitions = {str(item.get("id")): item for item in self.store.get_definitions("bt_def")}

        def declared_followups(beat_id: str) -> list[str] | None:
            beat = package_beats.get(str(beat_id))
            if not isinstance(beat, dict):
                return None
            raw = beat.get("followup_behaviors")
            if raw is None:
                # Emergent Story beats are open chapters.  An unrelated
                # maintenance action must not close the chapter before its
                # grounded goal is attempted. Configured package beats keep
                # the historical close-on-any-behavior behavior when they do
                # not declare a follow-up.
                if str(beat_id).startswith("emergent:"):
                    return None
                return None
            return [str(item) for item in (raw if isinstance(raw, list) else [raw])]

        def behavior_matches(behavior_id: str, declaration: str) -> bool:
            key = "".join(char for char in str(declaration or "").casefold() if char.isalnum())
            if not key:
                return False
            candidates = {str(behavior_id)}
            definition = definitions.get(str(behavior_id), {})
            candidates.update(str(definition.get(field) or "") for field in ("agent_label", "summary", "description"))
            return any(key == "".join(char for char in str(candidate).casefold() if char.isalnum()) for candidate in candidates if candidate)

        changed = False
        for beat_id, entry in list(state.items()):
            if beat_id == "emergent_beats" or not isinstance(entry, dict):
                continue
            if entry.get("status") != "active":
                continue
            audience = [str(item) for item in (entry.get("audience", []) or [])]
            try:
                agent_name = str(self.store.get_agent(actor_id).get("name", actor_id))
            except KeyError:
                agent_name = str(actor_id)
            if str(actor_id) not in audience and agent_name not in audience:
                continue
            followups = declared_followups(beat_id)
            if str(beat_id).startswith("emergent:") and followups is None:
                continue
            if followups and not any(behavior_matches(behavior_id, item) for item in followups):
                continue
            entry = dict(entry)
            entry["status"] = "completed"
            entry["completed_minute"] = int(minute)
            entry["completed_by_behavior"] = str(behavior_id)
            state[beat_id] = entry
            changed = True
            self.store.append_event("story.beat.completed", actor_id, minute, {"beat_id": beat_id, "behavior_id": behavior_id})
        if changed:
            self.store.set_meta("story_state", state)

