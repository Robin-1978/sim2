from __future__ import annotations

import copy
import json
from typing import Any
from uuid import uuid4

from ..world.context import action_condition_context, calendar_context, conflict_context
from ..world.definition_compiler import bind_legacy_inventory_effect, condition_syntax_error, effect_payload_error
from ..world.execution_recipes import execution_recipe, pin_execution_recipe
from ..world.lifecycle import trim_unlocked_behaviors, unlocked_behavior_limit
from ..actions.institutions import allocation_preflight
from ..actions.catalogs import resolve_catalog_entry
from ..actions.pacing import SOCIAL_OUTCOME_DELIVERED, generated_action_unavailable
from ..evolution.memory import update_role_memory_status


class ActivityRuntimeMixin:
    """Long-running Action/BT execution and outcome projection.

    The mixin deliberately depends on Platform services (store, effects,
    targets, rules, resources) but owns no world-specific behavior.  Keeping
    this lifecycle separate makes the coordinator readable and testable.
    """

    def _action_definition(self, action_info: dict[str, Any]) -> dict[str, Any] | None:
        if "definition_snapshot" in action_info:
            return action_info["definition_snapshot"]
        return next(
            (item for item in self.store.repositories.entities.get_definitions("action_def") if item.get("id") == action_info.get("action_id")),
            None,
        )

    def _behavior_definition(self, bt_data: dict[str, Any], behavior_id: str) -> dict[str, Any]:
        if "behavior_snapshot" in bt_data:
            return bt_data["behavior_snapshot"]
        return next((item for item in self.store.repositories.entities.get_definitions("bt_def") if item.get("id") == behavior_id), {})

    def _start_action(
        self, agent: dict[str, Any], execution_id: str, action_info: dict[str, Any], bt_data: dict[str, Any], minute: int,
    ) -> tuple[bool, str]:
        action_id = action_info.get("action_id")
        action = self._action_definition(action_info)
        if not action:
            return False, f"action is not registered: {action_id}"
        if reason := generated_action_unavailable(self.store, action, agent, minute):
            return False, reason
        state = dict(agent.get("initial_state") or {})
        parameters = self._context.services["execution"].resolve_activity_parameters(dict(action_info.get("parameters") or {}), state)
        parameters = self._context.services["execution"].apply_action_parameter_defaults(agent, action, parameters)
        action = pin_execution_recipe(self.store, action, parameters, bt_data)
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
        condition_context = self._action_condition_context(agent, action, parameters, resolved_target, minute)
        condition_target = {**parameters, **(resolved_target or {})}
        world = self.store.repositories.metadata.get("world_properties", {}) or {}
        denial = self.rule_compiler.denial(
            action=action,
            agent=agent,
            target=condition_target,
            context=condition_context,
            world=world,
        )
        if denial:
            return False, denial["message"]
        for predicate in action.get("preconditions", []) or []:
            if not self.conditions.evaluate(str(predicate), agent=agent, target=condition_target, context=condition_context, world=world):
                return False, f"precondition failed: {predicate}"
        location_ok, location_reason = self._resource_effect_location(agent, action, parameters, state)
        if not location_ok:
            return False, location_reason
        duration = self._context.services["execution"].action_duration(agent, action, parameters, state.get("location_id"))
        if already_at_destination:
            behavior = self._behavior_definition(bt_data, str(state.get("active_behavior_id") or ""))
            if "no_op_travel_minutes" in behavior:
                duration = max(0, int(behavior.get("no_op_travel_minutes", 1) or 0))
        if already_at_destination:
            # Completing an already reached destination consumes this tick,
            # but it does not charge movement costs or reserve resources.
            reservation = []
        else:
            capacity_ok, capacity_reason = self._context.services["execution"].check_action_capacity(agent, action, duration)
            if not capacity_ok:
                return False, capacity_reason
            allowed, reason, reservation = self._context.services["execution"].reserve_action_costs(agent, action, parameters)
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
        self.store.repositories.activity.put_action_instance(action_instance_id, execution_id, agent["id"], action_id, "running", 0, duration, {"parameters": parameters, "target": resolved_target, "reservation": reservation, "no_op": already_at_destination, "definition_version": int(action.get("definition_version", action.get("version", 1)) or 1), "definition_snapshot": copy.deepcopy(action)}, minute, minute)
        self._context.services["events"].append("action.started", agent["id"], minute, {"execution_id": execution_id, "action_id": action_id, "duration_minutes": duration, "no_op": already_at_destination})
        return True, ""

    def _resource_effect_location(
        self,
        agent: dict[str, Any],
        action: dict[str, Any],
        parameters: dict[str, Any],
        state: dict[str, Any],
    ) -> tuple[bool, str]:
        """Every stock change requires the actor at the stock's location.

        ``resource_transfer`` deposits a carried item, while
        ``resource_withdrawal`` and ``resource_take`` remove stock.  All three
        mutate one located world stock, so the same generic location gate
        applies regardless of the stock name or how a Behavior binds it.
        """

        actor_location = str(state.get("location_id") or "")
        stock_effect_types = {"resource_transfer", "resource_withdrawal", "resource_take"}
        for effect in action.get("effects", []) or []:
            if not isinstance(effect, dict):
                continue
            if str(effect.get("type") or effect.get("op") or "") not in stock_effect_types:
                continue
            reference = effect.get("resource_id") or effect.get("resource")
            if isinstance(reference, str) and reference.startswith("context."):
                reference = parameters.get(reference.removeprefix("context."))
            if not reference:
                continue
            resource_id = str(self.targets.canonical_id(str(reference)) or reference)
            try:
                resource = self.store.repositories.entities.get_entity(resource_id)
            except KeyError:
                continue
            resource_state = resource.get("initial_state") if isinstance(resource.get("initial_state"), dict) else {}
            resource_location = str(resource_state.get("location_id") or resource.get("location_id") or "")
            if resource_location and resource_location != actor_location:
                return False, "the actor is not at the resource location"
        return True, ""

    def _prepare_parallel_action(self, agent: dict[str, Any], action_info: dict[str, Any], bt_data: dict[str, Any], minute: int) -> tuple[dict[str, Any] | None, str]:
        action = self._action_definition(action_info)
        if not action:
            return None, f"action is not registered: {action_info.get('action_id')}"
        if reason := generated_action_unavailable(self.store, action, agent, minute):
            return None, reason
        state = dict(agent.get("initial_state") or {})
        parameters = self._context.services["execution"].resolve_activity_parameters(dict(action_info.get("parameters") or {}), state)
        parameters = self._context.services["execution"].apply_action_parameter_defaults(agent, action, parameters)
        action = pin_execution_recipe(self.store, action, parameters, bt_data)
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
        location_ok, location_reason = self._resource_effect_location(agent, action, parameters, state)
        if not location_ok:
            return None, location_reason
        context = self._action_condition_context(agent, action, parameters, target, minute)
        condition_target = {**parameters, **(target or {})}
        world = self.store.repositories.metadata.get("world_properties", {}) or {}
        denial = self.rule_compiler.denial(
            action=action,
            agent=agent,
            target=condition_target,
            context=context,
            world=world,
        )
        if denial:
            return None, denial["message"]
        for predicate in action.get("preconditions", []) or []:
            if not self.conditions.evaluate(str(predicate), agent=agent, target=condition_target, context=context, world=world):
                return None, f"precondition failed: {predicate}"
        duration = self._context.services["execution"].action_duration(agent, action, parameters, state.get("location_id"))
        if already_at_destination:
            reservation = []
        else:
            capacity_ok, capacity_reason = self._context.services["execution"].check_action_capacity(agent, action, duration)
            if not capacity_ok:
                return None, capacity_reason
            probe = copy.deepcopy(agent)
            allowed, reason, reservation = self._context.services["execution"].reserve_action_costs(probe, action, parameters, commit=False)
            if not allowed:
                return None, reason
        return {"action": action, "parameters": parameters, "target": target, "duration": duration, "reservation": reservation, "no_op": already_at_destination}, ""

    def _institution_action_context(self, action: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        """Expose declarative institution capacity to the condition engine."""

        for effect in action.get("effects", []) or []:
            if not isinstance(effect, dict) or str(effect.get("type") or effect.get("op") or "") != "institution_allocation":
                continue
            def resolve(value: Any) -> Any:
                if isinstance(value, str) and value.startswith("context."):
                    return parameters.get(value.removeprefix("context."), value)
                return value
            institution = resolve(effect.get("institution"))
            pool_entity = resolve(effect.get("pool_entity"))
            subjects = resolve(effect.get("subjects"))
            if isinstance(subjects, str):
                subjects = [subjects]
            check = allocation_preflight(self.store, institution, pool_entity, subjects if isinstance(subjects, list) else None)
            return {
                "institution.allocation_possible": bool(check.get("possible")),
                "institution.allocation_available": check.get("available", 0.0),
                "institution.allocation_required": check.get("required", 0.0),
            }
        return {}

    def _progress_parallel_group(self, agent: dict[str, Any], execution_id: str, behavior_id: str, bt_data: dict[str, Any], index: int, minute: int) -> bool:
        state = dict(agent.get("initial_state") or {})
        actions = bt_data["actions"]
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
                info, error = self._prepare_parallel_action(agent, item, bt_data, minute)
                if error:
                    return self._fail_behavior_step(agent, execution_id, behavior_id, actions[index], error, minute)
                prepared.append((item_index, item, info or {}))
            error = self._reserve_parallel_group(agent, [info for _, _, info in prepared])
            if error:
                return self._fail_behavior_step(agent, execution_id, behavior_id, actions[index], error, minute)
            active = {}
            for item_index, item, info in prepared:
                action = info["action"]
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
                self.store.repositories.activity.put_action_instance(action_instance_id, execution_id, agent["id"], action_id, "running", 0, info["duration"], {"parameters": info["parameters"], "target": info["target"], "reservation": info.get("reservation", []), "parallel_group": group, "definition_version": int(action.get("definition_version", action.get("version", 1)) or 1), "definition_snapshot": copy.deepcopy(action)}, minute, minute)
                self._context.services["events"].append("action.started", agent["id"], minute, {"execution_id": execution_id, "action_id": action_id, "duration_minutes": info["duration"], "parallel_group": group})
            state["active_parallel_actions"] = active
            state["active_action"] = "parallel"
            state["active_action_index"] = index
            agent["initial_state"] = state
        completed_count = 0
        for item_index, entry in list(active.items()):
            if int(entry["remaining"]) == 0:
                completed_count += 1
                continue
            action = self._action_definition(entry)
            if not action:
                self._fail_behavior_step(agent, execution_id, behavior_id, actions[index], f"action is not registered: {entry['action_id']}", minute)
                return False
            action = pin_execution_recipe(self.store, action, entry["parameters"], bt_data)
            if not entry.get("no_op"):
                self._context.services["execution"].apply_action_progress(agent, action)
            remaining = max(0, int(entry["remaining"]) - 1)
            elapsed = int(entry["duration"]) - remaining
            self.store.repositories.activity.update_action_instance(entry["action_instance_id"], status="succeeded" if remaining == 0 else "running", elapsed_minutes=elapsed, remaining_minutes=remaining, data={"parameters": entry["parameters"], "target": entry["target"], "reservation": entry.get("reservation", []), "parallel_group": group, "no_op": bool(entry.get("no_op")), "definition_version": int(entry.get("definition_version", action.get("definition_version", action.get("version", 1))) or 1), "definition_snapshot": entry.get("definition_snapshot") or copy.deepcopy(action)}, updated_minute=minute)
            if remaining == 0:
                failure = self._action_failure_reason(agent, action, entry["parameters"], entry.get("target"), minute)
                if failure:
                    self._fail_behavior_step(agent, execution_id, behavior_id, actions[index], failure, minute)
                    return False
                success_failure, information_result = self._complete_action_effects(
                    agent,
                    action,
                    entry["parameters"],
                    minute,
                    target=entry.get("target"),
                    reservations=entry.get("reservation") or [],
                )
                if success_failure:
                    self._fail_behavior_step(agent, execution_id, behavior_id, actions[index], success_failure, minute)
                    return False
                action_result = str((information_result or {}).get("result") or action.get("success", {}).get("result", "completed"))
                state = dict(agent.get("initial_state") or {})
                state["last_action_result"] = action_result
                agent["initial_state"] = state
                self._context.services["events"].append("action.completed", agent["id"], minute, {"execution_id": execution_id, "action_id": entry["action_id"], "parallel_group": group, "result": action_result})
                completed_count += 1
            entry["remaining"] = remaining
        remaining_values = [int(entry["remaining"]) for entry in active.values()]
        state = dict(agent.get("initial_state") or {})
        state["active_parallel_actions"] = active
        state["remaining_minutes"] = max(remaining_values or [0])
        state["action_progress"] = round(min(1 - int(entry["remaining"]) / max(1, int(entry["duration"])) for entry in active.values()), 4) if active else 1.0
        if completed_count == len(active):
            self._clear_active_action(state)
            state["action_progress"] = 1.0
            agent["initial_state"] = state
            self._continue_behavior_after_step(agent, execution_id, behavior_id, actions, index + len(group_items), minute)
        else:
            agent["initial_state"] = state
            self.store.repositories.activity.update_bt_instance(execution_id, status="executing", current_node=index, updated_minute=minute)
            self.store.repositories.entities.update_agent(agent["id"], agent)
        return False

    def _reserve_parallel_group(self, agent: dict[str, Any], prepared: list[dict[str, Any]]) -> str:
        """Reserve all concurrent costs before any Action in the group starts."""

        savepoint = f"parallel_reserve_{uuid4().hex}"
        self.store.connection.execute(f"SAVEPOINT {savepoint}")
        staged_agent = copy.deepcopy(agent)
        for info in prepared:
            if info["no_op"]:
                continue
            allowed, reason, reservation = self.resources.reserve(staged_agent, info["action"], info["parameters"])
            if not allowed:
                self.store.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.store.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                return reason
            info["reservation"] = reservation
        self.store.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        agent["inventory"] = staged_agent.get("inventory") or {}
        return ""

    def progress_agent(self, agent: dict[str, Any], minute: int) -> None:
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
            self.store.repositories.entities.update_agent(agent["id"], agent)
            return
        bt_data = json.loads(bt_rows["data_json"])
        actions = bt_data.get("actions", [])
        index = int(state.get("active_action_index", bt_rows["current_node"] or 0))
        while True:
            index = self._advance_behavior_controls(agent, execution_id, behavior_id, actions, index, minute)
            if index is None:
                return
            step = actions[index]
            if step.get("parallel_group"):
                if not self._progress_parallel_group(agent, execution_id, behavior_id, bt_data, index, minute):
                    return
            elif not self._progress_single_action(agent, execution_id, behavior_id, bt_data, index, minute):
                return
            index = agent["initial_state"]["active_action_index"]

    def _advance_behavior_controls(
        self, agent: dict[str, Any], execution_id: str, behavior_id: str,
        actions: list[dict[str, Any]], index: int, minute: int,
    ) -> int | None:
        state = agent["initial_state"]
        while index < len(actions):
            state["active_action_index"] = index
            step = actions[index]
            if step.get("type") == "jump":
                index = step["to"]
            elif step.get("type") == "condition":
                expression = str(step.get("expression") or "").strip()
                parameters = state.get("active_parameters") or {}
                target, _ = self.targets.resolve(agent, parameters)
                context = self._action_condition_context(agent, {}, parameters, target, minute)
                context.update(conflict_context(self.store, agent["id"], self.targets.visible_agents(agent)))
                if expression and self.conditions.evaluate(
                    expression, agent=agent, target={**parameters, **(target or {})},
                    context=context, world=self.store.repositories.metadata.get("world_properties", {}) or {},
                ):
                    index += 1
                elif self._fail_behavior_step(agent, execution_id, behavior_id, step, f"condition failed: {expression}", minute):
                    state = agent["initial_state"]
                    index = state["active_action_index"]
                else:
                    return None
            else:
                return index
        state["active_action_index"] = index
        self._finish_behavior(agent, execution_id, behavior_id, "completed", "behavior_completed", minute)
        return None

    def _continue_behavior_after_step(
        self, agent: dict[str, Any], execution_id: str, behavior_id: str,
        actions: list[dict[str, Any]], index: int, minute: int,
    ) -> None:
        index = self._advance_behavior_controls(agent, execution_id, behavior_id, actions, index, minute)
        if index is not None:
            self.store.repositories.activity.update_bt_instance(
                execution_id, status="executing", current_node=index, updated_minute=minute,
            )
            self.store.repositories.entities.update_agent(agent["id"], agent)

    @staticmethod
    def _clear_active_action(state: dict[str, Any]) -> None:
        state.update({
            "active_action": None, "active_action_instance": None, "active_parallel_actions": {},
            "active_action_duration": 0, "active_action_parameters": {}, "active_action_reservation": [],
            "active_action_noop": False, "active_action_definition_version": 0, "active_action_definition": None,
            "remaining_minutes": 0,
        })

    def _fail_behavior_step(
        self, agent: dict[str, Any], execution_id: str, behavior_id: str,
        step: dict[str, Any], reason: str, minute: int,
    ) -> bool:
        fallback = step.get("on_failure")
        if fallback is None:
            self._finish_behavior(agent, execution_id, behavior_id, "failed", reason, minute)
            return False
        self._fail_active_actions(agent, minute)
        state = agent["initial_state"]
        self._clear_active_action(state)
        state.update({"active_action_index": fallback, "action_progress": 0.0, "last_action_result": None})
        self.store.repositories.activity.update_bt_instance(
            execution_id, status="executing", current_node=fallback, updated_minute=minute,
        )
        self.store.repositories.entities.update_agent(agent["id"], agent)
        return True

    def _progress_single_action(
        self, agent: dict[str, Any], execution_id: str, behavior_id: str,
        bt_data: dict[str, Any], index: int, minute: int,
    ) -> bool:
        actions = bt_data["actions"]
        step = actions[index]
        state = agent["initial_state"]
        if not state.get("active_action"):
            started, reason = self._start_action(agent, execution_id, step, bt_data, minute)
            if not started:
                return self._fail_behavior_step(agent, execution_id, behavior_id, step, reason, minute)
            state = agent["initial_state"]
        action_id = state.get("active_action")
        # An already-running step may hold an older snapshot than its legacy plan.
        action = state.get("active_action_definition") or self._action_definition(step)
        if not action:
            return self._fail_behavior_step(agent, execution_id, behavior_id, step, "action_missing", minute)
        state = dict(agent.get("initial_state") or {})
        action = pin_execution_recipe(self.store, action, state.get("active_action_parameters") or {}, bt_data)
        # An idempotent destination Action still completes at this tick, but
        # its configured movement costs do not apply when already there.
        if not state.get("active_action_noop"):
            self._context.services["execution"].apply_action_progress(agent, action)
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
            self.store.repositories.activity.update_action_instance(action_instance_id, status="succeeded" if remaining == 0 else "running", elapsed_minutes=duration - remaining, remaining_minutes=remaining, data={"parameters": state.get("active_action_parameters", {}), "reservation": state.get("active_action_reservation", []), "no_op": bool(state.get("active_action_noop")), "definition_version": int(state.get("active_action_definition_version", action.get("definition_version", action.get("version", 1))) or 1), "definition_snapshot": copy.deepcopy(action)}, updated_minute=minute)
        if remaining > 0:
            self.store.repositories.activity.update_bt_instance(execution_id, status="executing", current_node=index, updated_minute=minute)
            self.store.repositories.entities.update_agent(agent["id"], agent)
            return False
        action_parameters = state.get("active_action_parameters", {})
        completed_noop = bool(state.get("active_action_noop"))
        failure = self._action_failure_reason(agent, action, action_parameters, self.targets.resolve(agent, action_parameters)[0], minute)
        if failure:
            self._fail_behavior_step(agent, execution_id, behavior_id, step, failure, minute)
            return False
        resolved_target = self.targets.resolve(agent, action_parameters)[0]
        success_failure, information_result = self._complete_action_effects(
            agent,
            action,
            action_parameters,
            minute,
            target=resolved_target,
            reservations=state.get("active_action_reservation") or [],
        )
        if success_failure:
            self._fail_behavior_step(agent, execution_id, behavior_id, step, success_failure, minute)
            return False
        self._record_declared_capability_evidence(agent, action, action_parameters, minute)
        action_result = str((information_result or {}).get("result") or action.get("success", {}).get("result", "completed"))
        state = dict(agent.get("initial_state") or {})
        state["last_action_result"] = action_result
        agent["initial_state"] = state
        self._context.services["events"].append("action.completed", agent["id"], minute, {"execution_id": execution_id, "action_id": action_id, "result": action_result})
        # Effects may update location, health, inventory or needs; retain that
        # projection before clearing the completed action fields.
        state = dict(agent.get("initial_state") or {})
        self._clear_active_action(state)
        state["active_action_index"] = index + 1
        state["action_progress"] = 1.0
        agent["initial_state"] = state
        if completed_noop:
            self.store.repositories.entities.update_agent(agent["id"], agent)
            return True
        self._continue_behavior_after_step(agent, execution_id, behavior_id, actions, index + 1, minute)
        return False

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
            recipe = execution_recipe(self.store, action, recipe_id)
            for effect in (recipe or {}).get("effects", []) or []:
                if isinstance(effect, dict) and str(effect.get("type") or effect.get("op") or "") in {"capability.add_xp", "capability_xp"} and effect.get("capability"):
                    explicit.add(str(effect["capability"]))

        owned = {str(item) for item in (agent.get("capabilities") or {})}
        action_id = str(action.get("id") or "")
        for definition in self.store.repositories.entities.get_definitions("capability_def"):
            capability = str(definition.get("id") or "")
            uses = {str(item) for item in (definition.get("uses") or [])}
            if not capability or capability not in owned or capability in explicit or action_id not in uses:
                continue
            self._context.services["events"].append(
                "capability.evidence",
                agent["id"],
                minute,
                {"capability": capability, "source": action_id, "xp_rule": definition.get("xp_rule"), "requires_agent_review": True},
            )

    def _apply_success_effects(
        self,
        agent: dict[str, Any],
        action: dict[str, Any],
        parameters: dict[str, Any],
        minute: int,
        *,
        target: dict[str, Any] | None = None,
        reservations: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        errors: list[str] = []
        location_ok, location_reason = self._resource_effect_location(
            agent, action, parameters, agent.get("initial_state") or {}
        )
        if not location_ok:
            return [location_reason]
        effects = list(action.get("effects", []) or [])
        for effect in effects:
            effect = bind_legacy_inventory_effect(effect, action.get("parameters") or {}, parameters)
            if error := effect_payload_error(effect):
                return [*errors, error]
            resolved_effect = {
                key: parameters.get(value.removeprefix("context."), value) if isinstance(value, str) and value.startswith("context.") else value
                for key, value in effect.items()
            }
            effect_target = target or parameters
            receiver = agent
            if effect.get("target") == "target" and target and target.get("id"):
                try:
                    receiver = self.store.repositories.entities.get_agent(str(target["id"]))
                except KeyError:
                    receiver = agent
            if effect.get("formula") and parameters.get("weapon_id"):
                combat = agent.get("combat_stats", {}) or {}
                weapon_damage = (combat.get("weapon_damage", {}) or {}).get(str(parameters["weapon_id"]), 0)
                effect_target = {**effect_target, "weapon": {"damage": weapon_damage}}
            effect_type = str(effect.get("type") or effect.get("op") or "")
            if effect_type == "relationship_delta":
                related = target or effect_target
                explicit = resolved_effect.get("target_id") or resolved_effect.get("target")
                if explicit and explicit not in ("actor", "target"):
                    related_id = self.targets.canonical_id(explicit)
                    if not related_id or related_id == agent["id"] or (target and target.get("id") != related_id):
                        errors.append(f"relationship_delta target is invalid or conflicts with action target: {explicit}")
                        continue
                    related = {"id": related_id}
                self.apply_relationship_effect(agent, related, resolved_effect, minute)
            elif effect_type == "public_event":
                event_type = str(effect["event"])

                def _resolve_public_value(value: Any) -> Any:
                    if isinstance(value, str) and value.startswith("context."):
                        return parameters.get(value.removeprefix("context."), value)
                    if isinstance(value, dict):
                        return {key: _resolve_public_value(item) for key, item in value.items()}
                    if isinstance(value, list):
                        return [_resolve_public_value(item) for item in value]
                    return value

                payload = {
                    **_resolve_public_value(dict(effect.get("data") or {}) if isinstance(effect.get("data"), dict) else {}),
                    "action_id": action.get("id"),
                    "parameters": parameters,
                }
                summary = str(effect.get("summary") or "").strip()
                if summary:
                    payload["summary"] = summary
                self._context.services["events"].publish(event_type, agent["id"], minute, payload, wake="configured")
            elif effect_type == "world_property_delta":
                # A living-world Action may bind the target property as a
                # parameter (for example a generated case slot).  Resolve it
                # through the same context binding used by inventory and
                # resource effects instead of creating a literal
                # ``context.case_slot`` property.
                property_id = str(resolved_effect.get("property") or resolved_effect.get("world_property") or "")
                if property_id:
                    property_id = self._context.services["tick"].world_property_key(property_id)
                    properties = dict(self.store.repositories.metadata.get("world_properties", {}) or {})
                    before_value = properties.get(property_id)
                    value = float(before_value or 0) + float(resolved_effect.get("delta", resolved_effect.get("value", 0)) or 0)
                    if isinstance(resolved_effect.get("minimum"), (int, float)):
                        value = max(float(resolved_effect["minimum"]), value)
                    if isinstance(resolved_effect.get("maximum"), (int, float)):
                        value = min(float(resolved_effect["maximum"]), value)
                    value = self._context.services["tick"].set_world_property(property_id, value)["after"]
                    self._context.services["events"].append(
                        "world.property_changed", agent["id"], minute,
                        {"property": property_id, "before": before_value, "after": value, "delta": value - float(before_value or 0), "source": action.get("id")},
                    )
            elif effect_type == "world_property_set":
                property_id = str(resolved_effect.get("property") or resolved_effect.get("world_property") or "")
                if property_id:
                    property_id = self._context.services["tick"].world_property_key(property_id)
                    properties = dict(self.store.repositories.metadata.get("world_properties", {}) or {})
                    before_value = properties.get(property_id)
                    value = resolved_effect.get("value")
                    if "value_from" in resolved_effect:
                        value = parameters.get(str(resolved_effect.get("value_from") or ""))
                    if isinstance(value, str) and value.startswith("context."):
                        value = parameters.get(value.removeprefix("context."), value)
                    value = self._context.services["tick"].set_world_property(property_id, value)["after"]
                    self._context.services["events"].append(
                        "world.property_changed", agent["id"], minute,
                        {"property": property_id, "before": before_value, "after": value, "delta": None, "source": action.get("id"), "mode": "set"},
                    )
            elif effect_type == "world_resource_delta":
                # Public work may deliberately move a shared stock (for
                # example, a daily rationing decision).  This is distinct from
                # ``resource_transfer``: no carried item is reserved because
                # the world, not one inventory, is the source or sink.
                resource_ref = resolved_effect.get("resource_id") or resolved_effect.get("resource")
                resource_id = str(self.targets.canonical_id(str(resource_ref or "")) or resource_ref or "")
                delta = float(resolved_effect.get("delta", resolved_effect.get("value", 0)) or 0)
                current = self.resources._world_resource(resource_id) if resource_id else None
                if not resource_id or current is None:
                    errors.append("world_resource_delta target must be an existing world resource")
                    continue
                target_amount = float(current.get("amount", 0) or 0) + delta
                if isinstance(resolved_effect.get("minimum"), (int, float)):
                    target_amount = max(float(resolved_effect["minimum"]), target_amount)
                if isinstance(resolved_effect.get("maximum"), (int, float)):
                    target_amount = min(float(resolved_effect["maximum"]), target_amount)
                caps = ((self.store.repositories.metadata.get("rules", {}) or {}).get("resource_caps", {}) or {})
                try:
                    resource_entity = self.store.repositories.entities.get_entity(resource_id)
                except KeyError:
                    resource_entity = {}
                cap = caps.get(resource_id) or caps.get(str(resource_entity.get("name") or "")) or {}
                if isinstance(cap, dict):
                    if isinstance(cap.get("min"), (int, float)):
                        target_amount = max(float(cap["min"]), target_amount)
                    if isinstance(cap.get("max"), (int, float)):
                        target_amount = min(float(cap["max"]), target_amount)
                self.resources.change_for_behavior(
                    agent,
                    resource_id,
                    target_amount - float(current.get("amount", 0) or 0),
                )
            elif effect_type == "resource_withdrawal":
                # Two-phase shared-resource withdrawal.  The admission
                # transaction already recorded resource.withdrawal.started;
                # this completion transaction owns the single stock change and
                # its completed fact.  A later completion failure never erases
                # the committed start.
                resource_ref = resolved_effect.get("resource_id") or resolved_effect.get("resource")
                resource_id = str(self.targets.canonical_id(str(resource_ref or "")) or resource_ref or "")
                amount = float(resolved_effect.get("amount", resolved_effect.get("quantity", 1)) or 0)
                current = self.resources._world_resource(resource_id) if resource_id else None
                output_item = str(resolved_effect.get("output_item") or resolved_effect.get("inventory_item") or "")
                only_if_missing = bool(resolved_effect.get("only_if_missing"))
                inventory = dict(agent.get("inventory") or {})
                if only_if_missing and output_item and float(inventory.get(output_item, 0) or 0) >= amount:
                    # A reusable step such as "take a repair kit from the
                    # commons rack" becomes idempotent: an actor who already
                    # carries one can still run the combined behavior without
                    # stripping the shared stock, even when the rack is empty.
                    continue
                if not resource_id or current is None:
                    errors.append("resource_withdrawal target must be an existing world resource")
                elif amount <= 0:
                    errors.append("resource_withdrawal amount must be positive")
                elif float(current.get("amount", 0) or 0) < amount:
                    errors.append("the shared resource has insufficient stock")
                else:
                    before = float(current.get("amount", 0) or 0)
                    self.resources.change_for_behavior(agent, resource_id, -amount)
                    after = before - amount
                    if output_item:
                        inventory[output_item] = float(inventory.get(output_item, 0) or 0) + amount
                        agent["inventory"] = inventory
                    state = agent.get("initial_state") or {}
                    self._context.services["events"].append(
                        "resource.withdrawal.completed",
                        agent["id"],
                        minute,
                        {
                            "intent_id": str(state.get("active_bt_instance") or ""),
                            "actor": str(agent["id"]),
                            "target": resource_id,
                            "authorization": state.get("active_resource_authorization"),
                            "amount": amount,
                            "before": before,
                            "after": after,
                            "minute": int(minute),
                        },
                    )
            elif effect_type == "resource_take":
                # One unauthorized take commits its stock change, violation
                # record, witness set and social consequences together.  The
                # caller's savepoint owns rollback on any failure.
                errors.extend(
                    self._context.services["contested"].complete_take(agent, resolved_effect, minute)
                )
            elif effect_type == "catalog_action":
                catalog = str(resolved_effect.get("catalog") or "")
                entry_value = resolved_effect.get("entry") or parameters.get("entry_id") or parameters.get("entry")
                entry = resolve_catalog_entry(self.store, catalog, entry_value)
                if entry is None:
                    errors.append(f"catalog_action has no entry {entry_value!r} in {catalog or 'an unspecified catalog'}")
                else:
                    state = agent.get("initial_state") or {}
                    location_id = str(state.get("location_id") or "")
                    entry_location = str(entry.get("location") or "")
                    if entry_location and entry_location != location_id:
                        errors.append(f"catalog entry requires location {entry_location}")
                    condition_context = self._action_condition_context(agent, action, parameters, target, minute)
                    for predicate in entry.get("requires") or []:
                        if not self.conditions.evaluate(
                            str(predicate),
                            agent=agent,
                            target=target or {},
                            context=condition_context,
                            world=self.store.repositories.metadata.get("world_properties", {}) or {},
                        ):
                            errors.append(f"catalog entry requirement not met: {predicate}")
                            break
                    if not errors:
                        synthetic = {
                            "id": action.get("id"),
                            "costs": entry.get("costs") or {},
                            "effects": entry.get("effects") or [],
                        }
                        accepted, reason, reservations = self.resources.reserve(agent, synthetic, parameters, commit=True)
                        if not accepted:
                            errors.append(reason or "catalog entry costs are unavailable")
                        else:
                            nested_errors = self._apply_success_effects(
                                agent, synthetic, parameters, minute, target=target, reservations=reservations,
                            )
                            errors.extend(nested_errors)
                            if not nested_errors:
                                event_type = str(entry.get("event_type") or "").strip()
                                if event_type:
                                    payload = dict(entry.get("event_data") or {})
                                    payload.update({
                                        "entry_id": str(entry.get("id") or ""),
                                        "entry_label": str(entry.get("label") or ""),
                                        "action_id": str(action.get("id") or ""),
                                    })
                                    self._context.services["events"].publish(event_type, agent["id"], minute, payload, wake="configured")
            elif effect_type == "resource_transfer":
                # ResourceLedger has already reserved/removed any carried
                # source item before success effects run.  This primitive
                # transfers that conserved amount into an existing world
                # resource entity; it cannot mint a new stock.
                resource_ref = resolved_effect.get("resource_id") or resolved_effect.get("resource")
                resource_id = str(self.targets.canonical_id(str(resource_ref or "")) or resource_ref or "")
                quantity_parameter = resolved_effect.get("quantity_parameter")
                quantity_value = parameters.get(str(quantity_parameter)) if quantity_parameter else resolved_effect.get("quantity", resolved_effect.get("amount", 0))
                quantity = float(quantity_value or 0)
                if quantity <= 0:
                    errors.append("resource_transfer quantity must be positive")
                else:
                    carried_reservations = [
                        item for item in (reservations or [])
                        if str(item.get("scope") or "") == "agent" and float(item.get("amount", 0) or 0) > 0
                    ]
                    source_hint = resolved_effect.get("source_item")
                    source_parameter = resolved_effect.get("source_item_parameter")
                    if source_parameter:
                        source_hint = parameters.get(str(source_parameter))
                    if isinstance(source_hint, str) and source_hint.startswith("context."):
                        source_hint = parameters.get(source_hint.removeprefix("context."), source_hint)
                    source_keys = {str(item.get("key")) for item in carried_reservations}
                    if source_hint:
                        source_keys &= {str(source_hint)}
                    if len(source_keys) != 1:
                        errors.append("resource_transfer must identify exactly one reserved carried item")
                    else:
                        reserved_quantity = sum(
                            float(item.get("amount", 0) or 0)
                            for item in carried_reservations
                            if str(item.get("key")) in source_keys
                        )
                        if abs(quantity - reserved_quantity) > 1e-9:
                            errors.append(
                                "resource_transfer quantity must equal the reserved carried item amount"
                            )
                    try:
                        resource = self.store.repositories.entities.get_entity(resource_id)
                    except KeyError:
                        resource = None
                    if errors:
                        continue
                    if not resource or str(resource.get("entity_type") or "") not in {"resource", "material"}:
                        errors.append("resource_transfer target must be an existing resource or material")
                    else:
                        caps = ((self.store.repositories.metadata.get("rules", {}) or {}).get("resource_caps", {}) or {})
                        cap = caps.get(resource_id) or caps.get(str(resource.get("name") or "")) or {}
                        resource_state = resource.get("initial_state") if isinstance(resource.get("initial_state"), dict) else {}
                        current = float(resource_state.get("quantity", resource_state.get("amount", 0)) or 0)
                        maximum = cap.get("max") if isinstance(cap, dict) else None
                        if isinstance(maximum, (int, float)) and current + quantity > float(maximum):
                            errors.append("the shared resource is at capacity")
                        else:
                            self.resources.change_for_behavior(agent, resource_id, quantity)
            elif effect_type == "entity_state_set":
                if error := self._set_entity_state(resolved_effect, agent["id"], action.get("id"), minute):
                    errors.append(error)
            elif effect_type == "institution_allocation":
                allocation = self._context.services["social"].apply_institution_allocation(
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
                    allocation_event_id = self._context.services["events"].publish(
                        "institution.allocation_completed", agent["id"], minute, allocation_event,
                        wake="configured",
                    )
                else:
                    self._context.services["events"].append("institution.allocation_failed", agent["id"], minute, allocation)
                    errors.append(str(allocation.get("message") or allocation.get("failed_check") or "institution allocation failed"))
            elif effect_type not in {"capability_xp", "capability.add_xp"}:
                outcome = self.effects.apply(receiver, resolved_effect, target=effect_target, formula_agent=agent)
                if outcome.get("error"):
                    errors.append(str(outcome["error"]))
            if receiver is not agent:
                self.store.repositories.entities.update_agent(receiver["id"], receiver)
            if effect.get("type") in {"capability_xp", "capability.add_xp"} or effect.get("op") == "capability.add_xp":
                capability = effect.get("capability")
                if capability:
                    self._context.services["events"].append("capability.evidence", agent["id"], minute, {"capability": str(capability), "source": str(action.get("id", "action")), "configured_delta": float(effect.get("value", effect.get("delta", 0)) or 0), "requires_agent_review": True})
        for effect in action.get("capability_effects", []) or []:
            capability = effect.get("capability")
            if capability:
                self._context.services["events"].append("capability.evidence", agent["id"], minute, {"capability": str(capability), "source": str(action.get("id", "action")), "configured_delta": float(effect.get("value", 0) or 0), "requires_agent_review": True})
        recipe_id = parameters.get("recipe_id")
        if not recipe_id:
            return errors
        recipe = execution_recipe(self.store, action, recipe_id)
        if not recipe:
            return errors
        for output in recipe.get("outputs", []) or []:
            item = str(output.get("item"))
            agent.setdefault("inventory", {})[item] = agent["inventory"].get(item, 0) + output.get("quantity", 0)
        for effect in recipe.get("effects", []) or []:
            if error := effect_payload_error(effect, "recipe.effects"):
                return [*errors, error]
            effect_type = str(effect.get("type") or effect.get("op") or "")
            if effect_type == "entity_state_set":
                if error := self._set_entity_state(effect, agent["id"], f"recipe:{recipe_id}", minute):
                    errors.append(error)
            elif effect_type in {"capability.add_xp", "capability_xp"}:
                capability = effect.get("capability")
                if capability:
                    self._context.services["events"].append("capability.evidence", agent["id"], minute, {"capability": str(capability), "source": f"recipe:{recipe_id}", "configured_delta": float(effect.get("value", 0) or 0), "requires_agent_review": True})
        return errors

    def _set_entity_state(
        self, effect: dict[str, Any], actor_id: str, action_id: str | None, minute: int,
    ) -> str | None:
        entity_ref = effect.get("entity_id") or effect.get("target_entity")
        state_key = str(effect.get("state") or effect.get("property") or "")
        if not state_key:
            return "entity_state_set requires a state field"
        entity = self.targets.named_entity(entity_ref)
        if entity is None:
            return f"entity_state_set target does not exist or is ambiguous: {entity_ref}"
        state = dict(entity.get("initial_state") or {})
        before = state.get(state_key)
        state[state_key] = effect["value"]
        entity["initial_state"] = state
        self.store.repositories.entities.put_entity(
            entity["id"], entity["entity_type"], entity["name"],
            {key: value for key, value in entity.items() if key not in {"id", "entity_type", "name"}},
        )
        self._context.services["events"].append(
            "entity.state_changed", actor_id, minute,
            {"entity_id": entity["id"], "state": state_key, "before": before, "after": state[state_key], "action_id": action_id},
        )
        return None

    def _action_condition_context(
        self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any],
        target: dict[str, Any] | None, minute: int,
    ) -> dict[str, Any]:
        context = action_condition_context(
            self.store, agent, parameters, target, canonical_id=self.targets.canonical_id,
            recent_event_types=self._context.services["evolution"].recent_event_types(), minute=minute,
        )
        context.update(self._institution_action_context(action, parameters))
        if (
            action.get("duration", {}).get("mode") == "recipe_duration"
            or "recipe_inputs" in (action.get("costs", {}).get("reserve", {}) or {})
        ):
            recipe = execution_recipe(self.store, action, parameters.get("recipe_id")) or {}
            context["station_tags"] = list(recipe.get("station_tags") or [])
        return context

    def _action_failure_reason(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], target: dict[str, Any] | None, minute: int) -> str | None:
        condition = str((action.get("failure") or {}).get("condition") or "").strip()
        if not condition:
            return None
        if error := condition_syntax_error(condition):
            return f"invalid failure condition: {error}"
        context = self._action_condition_context(agent, action, parameters, target, minute)
        if self.conditions.evaluate(condition, agent=agent, target={**parameters, **(target or {})}, context=context, world=self.store.repositories.metadata.get("world_properties", {}) or {}):
            return str((action.get("failure") or {}).get("result") or condition)
        return None

    def _action_success_reason(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], target: dict[str, Any] | None, minute: int) -> str | None:
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
        context = self._action_condition_context(agent, action, parameters, target, minute)
        verdict = self.conditions.evaluate_known(
            condition,
            agent=agent,
            target={**parameters, **(target or {})},
            context=context,
            world=self.store.repositories.metadata.get("world_properties", {}) or {},
            allow_outcome_label=True,
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
        reservations: list[dict[str, Any]] | None = None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Apply one Action's effects atomically with its postcondition.

        A configured success condition is part of the commit contract.  If a
        machine-checkable postcondition is false, neither the effect nor its
        information/result event may remain in SQLite.  This prevents a
        failed Action from publishing a success story or leaving a partial
        world mutation behind.
        """

        # Another resident can finish the shared outcome while this work is in flight.
        if reason := generated_action_unavailable(self.store, action, agent, minute):
            return reason, None
        original_agent = copy.deepcopy(agent)
        savepoint = f"action_effects_{uuid4().hex}"
        self.store.connection.execute(f"SAVEPOINT {savepoint}")
        try:
            effect_errors = self._apply_success_effects(
                agent,
                action,
                parameters,
                minute,
                target=target,
                reservations=reservations,
            )
            if effect_errors:
                self.store.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.store.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                agent.clear()
                agent.update(original_agent)
                return "; ".join(dict.fromkeys(effect_errors)), None
            failure = self._action_success_reason(agent, action, parameters, target, minute)
            if failure:
                self.store.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.store.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                agent.clear()
                agent.update(original_agent)
                return failure, None
            information_result = self._record_information_result(agent, action, parameters, minute)
            self.store.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            return None, information_result
        except Exception as exc:
            self.store.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.store.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            agent.clear()
            agent.update(original_agent)
            # A withdrawal is a two-phase transaction.  The stock change and
            # its completed fact roll back together, while the already
            # committed started fact remains.  Convert the failure into a
            # normal failed outcome so the failed fact and the actor's failed
            # terminal state can commit.
            if any(
                isinstance(effect, dict)
                and str(effect.get("type") or effect.get("op") or "") == "resource_withdrawal"
                for effect in (action.get("effects") or [])
            ):
                return f"resource.withdrawal.failed: {exc}", None
            if any(
                isinstance(effect, dict)
                and str(effect.get("type") or effect.get("op") or "") == "resource_take"
                for effect in (action.get("effects") or [])
            ):
                return f"resource.take.failed: {exc}", None
            raise

    def apply_relationship_effect(self, agent: dict[str, Any], target: dict[str, Any], effect: dict[str, Any], minute: int) -> None:
        target_id = str(target.get("id") or target.get("entity_id") or "") if isinstance(target, dict) else ""
        if not target_id or target_id == agent["id"]:
            return
        relation_type = str(effect.get("relation") or "related")
        delta = float(effect.get("delta", effect.get("value", 0)) or 0)
        current = next((item for item in self.store.repositories.entities.list_relationships() if item.get("from") == agent["id"] and item.get("to") == target_id and item.get("type") == relation_type), None)
        data = dict(current or {})
        for key in ("from", "to", "type"):
            data.pop(key, None)
        before = float(data.get("relation_strength", 0) or 0)
        data["relation_strength"] = max(-1.0, min(1.0, before + delta))
        data["last_changed_minute"] = int(minute)
        description = str(effect.get("description") or "").strip()
        if description:
            data["description"] = description
        self.store.repositories.entities.put_relationship(agent["id"], target_id, relation_type, data)
        self._context.services["events"].append("relationship.changed", agent["id"], minute, {
            "target_id": target_id, "relation": relation_type, "delta": delta,
            "actual_delta": data["relation_strength"] - before, "relationship_created": current is None,
            "relation_strength": data["relation_strength"], **({"description": description} if description else {}),
        })

    def _advance_information_progress(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], minute: int) -> dict[str, Any] | None:
        definition = action.get("progress") or {}
        if not isinstance(definition, dict) or not definition.get("key"):
            return None
        source_id = str(parameters.get("source_id") or "")
        discoveries = self.store.repositories.memory.research_discoveries(source_id) if source_id else []
        source = {}
        if source_id:
            try:
                source = self.store.repositories.entities.get_entity(source_id)
            except KeyError:
                source = {}
        source_state = source.get("initial_state") if isinstance(source.get("initial_state"), dict) else {}
        # Research effort belongs to the source definition, not to the
        # generic Action.  A source can require a different number of
        # completed work units without creating another Action or BT.
        source_required = source.get("research_required")
        if source_required is None:
            source_required = source_state.get("research_required")
        if source_required is None:
            source_required = (self.store.repositories.metadata.get("rules", {}) or {}).get("research_default_required")
        try:
            source_required = max(1.0, float(source_required)) if source_required is not None else None
        except (TypeError, ValueError):
            source_required = None
        known_fact_keys = {
            str(item.get("fact_key"))
            for item in self.store.repositories.memory.list_knowledge_facts(agent["id"], limit=1000)
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
        owner = str(self.store.repositories.metadata.get("world_id", "world")) if scope == "world" else str(agent["id"])
        required_value = (discovery or {}).get("required")
        if required_value is None:
            required_value = source_required
        if required_value is None:
            required_value = 1
        try:
            required = max(1.0, float(required_value))
        except (TypeError, ValueError):
            required = 1.0
        progress = self.store.repositories.activity.advance_progress(owner, key, float(definition.get("amount", 1) or 1), required, minute, {"source_action": action.get("id"), "source_agent": agent["id"], "stage_id": (discovery or {}).get("id")})
        event_type = "research.completed" if progress["completed"] else "research.progressed"
        event_id = self._context.services["events"].append(event_type, agent["id"], minute, {"progress_key": key, "progress": progress, "source_action": action.get("id"), "parameters": parameters, "source_id": source_id, "stage_id": (discovery or {}).get("id"), "discovery_status": (discovery or {}).get("status", "active")})
        if progress["completed"]:
            self.store.repositories.memory.update_research_gap(
                source_id,
                stage_id=(discovery or {}).get("id"),
                completed=True,
                progress=progress,
                fact=str((discovery or {}).get("fact") or ""),
                event_id=event_id,
                world_minute=minute,
            )
        # Partial work is retained in SQLite for the next attempt. Story
        # feedback is a completion boundary, so an incomplete sample does not
        # pause the world or wake the Story Agent without a new fact.
        if progress["completed"]:
            wake = definition.get("wake")
            wake_spec: str | dict[str, Any] = "story_source" if wake is None else {"wake": wake}
            self._context.services["events"].wake(event_id, event_type, agent["id"], minute, wake=wake_spec)
        return {**progress, "discovery": discovery, "stage_id": (discovery or {}).get("id"), "progress_key": key}

    def _record_information_result(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], minute: int) -> dict[str, Any]:
        """Persist information produced by configured information channels."""

        information = action.get("information", {}) or {}
        configured_event = information.get("event_type")
        result: dict[str, Any] = {}
        area_id = parameters.get("area_id") or information.get("area_parameter") and parameters.get(information["area_parameter"])
        source_id = parameters.get("source_id") or information.get("source_parameter") and parameters.get(information["source_parameter"])
        target_id = parameters.get("target_id")
        topic = parameters.get("topic")
        message = str(parameters.get("message") or "").strip()
        if area_id:
            area_id = str(area_id)
            authored_exploration = configured_event == "exploration.completed"
            if authored_exploration:
                configured_event = None
            if area_id not in self.store.repositories.memory.explored_areas(agent["id"]):
                result_id = self.store.repositories.activity.put_exploration_result(agent["id"], area_id, minute, {"source": action.get("id"), "parameters": parameters})
                area = self.store.repositories.entities.get_entity(area_id) if area_id else {}
                discoveries = list(area.get("discoveries", []) or []) if isinstance(area, dict) else []
                discoveries = [{"id": f"explored:{area_id}", "fact": f"Area {area_id} was explored."}, *discoveries]
                for discovery in discoveries:
                    discovery_id = str(discovery.get("id") or f"explored:{area_id}")
                    fact = str(discovery.get("fact") or f"Area {area_id} was explored.")
                    self.store.repositories.memory.merge_knowledge_fact(agent["id"], discovery_id, {"fact": fact, "source_type": "exploration", "source_id": result_id, "behavior": action.get("id"), "confidence": 0.8, "observed_at": minute})
                    unlocks = discovery.get("unlocks", []) if isinstance(discovery, dict) else []
                    for unlock in unlocks or []:
                        behavior_id = str(unlock.get("behavior_id") if isinstance(unlock, dict) else unlock)
                        if not behavior_id:
                            continue
                        registered = any(item.get("id") == behavior_id for item in self.store.repositories.entities.get_definitions("bt_def"))
                        if not registered:
                            continue
                        unlocked = list(agent.get("unlocked_bt_ids", []) or [])
                        if behavior_id not in unlocked:
                            unlocked.append(behavior_id)
                            agent["unlocked_bt_ids"] = trim_unlocked_behaviors(
                                {**agent, "unlocked_bt_ids": unlocked},
                                unlocked_behavior_limit(self.store.repositories.metadata.get("rules", {}) or {}),
                            )
                            self._context.services["events"].append("behavior.unlocked", agent["id"], minute, {"behavior_id": behavior_id, "discovery_id": discovery_id, "source": "exploration"})
                payload = {"area_id": area_id, "result_id": result_id, "discoveries": [str(item.get("id")) for item in discoveries]}
                wake: str | dict[str, Any] = "story_source"
                if authored_exploration:
                    payload = {**(information.get("data") or {}), **payload, "action_id": action.get("id"), "parameters": parameters}
                    if information.get("summary"):
                        payload["summary"] = str(information["summary"])
                    if information.get("configured") or information.get("wake"):
                        wake = information
                event_id = self._context.services["events"].publish("exploration.completed", agent["id"], minute, payload, wake=wake)
                result = {"result": "exploration_completed", "event_id": event_id}
        if source_id:
            source_id = str(source_id)
            progress = self._advance_information_progress(agent, action, parameters, minute)
            if progress is not None and not progress["completed"]:
                result = {"result": "research_progressed", "progress": progress}
            else:
                discovery = (progress or {}).get("discovery") if isinstance(progress, dict) else None
                if discovery is None:
                    candidates = [item for item in self.store.repositories.memory.research_discoveries(source_id) if str(item.get("id") or "") not in {str(row.get("fact_key")) for row in self.store.repositories.memory.list_knowledge_facts(agent["id"], limit=1000)}]
                    discovery = candidates[0] if candidates else None
                if discovery is not None or source_id not in self.store.repositories.memory.researched_sources(agent["id"]):
                    source = self.store.repositories.entities.get_entity(source_id) if source_id else {}
                    discovery = discovery or {}
                    fact_key = str(discovery.get("id") or f"researched:{source_id}")
                    fact = str(discovery.get("fact") or f"Source {source_id} was researched.")
                    self.store.repositories.memory.merge_knowledge_fact(agent["id"], fact_key, {"fact": fact, "source_type": "research", "source_id": source_id, "behavior": action.get("id"), "confidence": 0.8, "observed_at": minute, "status": str(discovery.get("status") or "active"), "supersedes": discovery.get("supersedes", [])})
                    # A configured progress action already emitted the
                    # completion event; without progress keep the legacy
                    # single-attempt event contract.
                    if progress is None:
                        event_id = self._context.services["events"].publish("research.completed", agent["id"], minute, {"source_id": source_id, "fact": fact}, wake="story_source")
                        self.store.repositories.memory.update_research_gap(
                            source_id,
                            completed=True,
                            fact=fact,
                            event_id=event_id,
                            world_minute=minute,
                        )
                        result = {"result": "research_completed", "event_id": event_id, "progress": progress}
                    elif progress is not None:
                        result = {"result": "research_completed", "progress": progress}
        object_id = parameters.get("object_id") or information.get("object_parameter") and parameters.get(information["object_parameter"])
        # An information channel is an observation by default. Recovery is a
        # stronger world fact and must be explicitly declared by the Action;
        # otherwise an inspection would emit ``object.recovered`` on every
        # repeat and falsely wake Story with the same evidence.
        if object_id and information.get("record_object_discovery", False):
            object_id = str(object_id)
            try:
                discovered_object = self.store.repositories.entities.get_entity(object_id)
            except KeyError:
                discovered_object = {}
            discovery = (discovered_object.get("recovery_discovery") or {}) if isinstance(discovered_object, dict) else {}
            fact_key = str(discovery.get("id") or f"recovered:{object_id}")
            fact = str(discovery.get("fact") or f"Object {object_id} was recovered.")
            self.store.repositories.memory.merge_knowledge_fact(
                agent["id"], fact_key,
                {"fact": fact, "source_type": "recovery", "source_id": object_id, "object_id": object_id, "behavior": action.get("id"), "confidence": 0.9, "observed_at": minute},
            )
            event_id = self._context.services["events"].publish("object.recovered", agent["id"], minute, {"object_id": object_id, "fact": fact}, wake="story_source")
            result = {"result": "object_recovered", "event_id": event_id}
        if target_id and topic:
            target_id = str(target_id)
            try:
                listener = self.store.repositories.entities.get_agent(target_id)
            except KeyError:
                listener = None
            testimony = self._context.services["social"].testimony_metadata(agent, listener) if listener else {}
            dialogue_id = self.store.repositories.social.put_dialogue_exchange(agent["id"], target_id if listener else None, str(topic), minute, {"source": action.get("id"), "message": message, "parameters": parameters})
            self.store.repositories.memory.put_memory(agent["id"], "dialogue", {"topic": str(topic), "message": message, "target_id": target_id, "world_minute": minute, "dialogue_id": dialogue_id, "claim_status": "observed", "source_type": "own_statement"})
            if listener:
                self.store.repositories.memory.put_memory(target_id, "testimony", {"topic": str(topic), "message": message, "speaker_id": agent["id"], "world_minute": minute, "dialogue_id": dialogue_id, "claim_status": "reported", "source_type": "testimony", **testimony})
                self._context.services["social"].apply_configured_relationship_effect(agent, listener, "dialogue.completed", minute)
                self.store.repositories.entities.update_agent(listener["id"], listener)
            event_id = self._context.services["events"].append("dialogue.completed", agent["id"], minute, {"target_id": target_id, "topic": str(topic), "message": message, "dialogue_id": dialogue_id, **testimony})
            result = {"result": "dialogue_recorded", "event_id": event_id}
        observed: dict[str, Any] = {}
        observed_id = parameters.get("object_id") or parameters.get("entity_id") or parameters.get("item_id") or parameters.get("source_id") or parameters.get("area_id")
        if observed_id:
            try:
                entity = self.store.repositories.entities.get_entity(str(observed_id))
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
            event_id = self._context.services["events"].publish(str(configured_event), agent["id"], minute, event_data, wake=information)
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
            self.store.repositories.memory.merge_knowledge_fact(agent["id"], fact_key, {"fact": str(fact), "source_type": str(information.get("source_type") or "action"), "source_id": str(action.get("id", "action")), "behavior": str(action.get("id", "action")), "confidence": float(information.get("confidence", 0.8)), "observed_at": minute})
            result.setdefault("result", str(information.get("result") or "information_recorded"))
        return result

    def _institution_period_key(self, period: Any, minute: int) -> str:
        if isinstance(period, (int, float)) and not isinstance(period, bool) and float(period) > 0:
            size = max(1, int(period))
            return f"block:{int(minute) // size}"
        calendar = (self.store.repositories.metadata.get("setting", {}) or {}).get("calendar", {}) or {}
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
        event_types = selector.get("event_types", [])
        if isinstance(event_types, str):
            event_types = [event_types]
        if event_types and str(event_type) not in {str(item) for item in event_types}:
            return False
        behavior_ids = selector.get("behavior_ids", [])
        if isinstance(behavior_ids, str):
            behavior_ids = [behavior_ids]
        if behavior_ids and str(payload.get("behavior_id", "")) not in {str(item) for item in behavior_ids}:
            return False
        action_ids = selector.get("action_ids", [])
        if isinstance(action_ids, str):
            action_ids = [action_ids]
        if action_ids and str(payload.get("action_id", "")) not in {str(item) for item in action_ids}:
            return False
        return True

    def _record_active_institution_entries(self, event_id: int, event_type: str, actor_id: str, minute: int, payload: dict[str, Any]) -> None:
        """Apply active, declarative ledger selectors to one verified event."""

        for institution in self.store.repositories.social.list_institutions(status="active", limit=200):
            ledger = institution.get("ledger") if isinstance(institution.get("ledger"), dict) else {}
            selectors = ledger.get("selectors", [])
            if not isinstance(selectors, list):
                continue
            # Multiple selectors often describe the same evidence in two
            # equivalent ways (for example an outcome event and its behavior
            # envelope). One verified event/category must contribute once;
            # distinct configured categories remain independent.
            matched_categories: set[str] = set()
            for raw_selector in selectors:
                if not isinstance(raw_selector, dict) or not self._institution_selector_matches(raw_selector, event_type, payload):
                    continue
                category = str(raw_selector.get("category") or "general").strip()
                amount = float(raw_selector.get("points", raw_selector.get("amount", 1)) or 0)
                if not category or amount <= 0:
                    continue
                if category in matched_categories:
                    continue
                matched_categories.add(category)
                period = self._institution_period_key(ledger.get("period", "world"), minute)
                entry_id = self.store.repositories.social.append_institution_ledger_entry(
                    str(institution["institution_id"]), actor_id, period, category, amount,
                    int(event_id), "verified", {"event_type": event_type, "source": payload.get("behavior_id") or payload.get("action_id") or event_type}, minute,
                )
                if entry_id is not None:
                    ledger_event_id = self._context.services["events"].append(
                        "institution.ledger_entry", actor_id, minute,
                        {"institution": {"name": institution.get("name"), "category": category}, "period": period, "amount": amount, "source_event_id": int(event_id), "status": "verified"},
                    )
                    # The package decides whether a ledger accumulation is
                    # enough to wake God or Story. Event groups apply their
                    # own evidence count and cooldown; this call does not
                    # hard-code any institution or quota semantics.
                    self._context.services["events"].wake(ledger_event_id, "institution.ledger_entry", actor_id, minute, wake="configured")

    def abort_active_behavior(self, agent: dict[str, Any], reason: str, minute: int) -> bool:
        """Fail the behavior the character is executing, if any.

        Used when the character can no longer act (for example on
        incapacitation): the in-flight reservations are released and the
        outcome is recorded as a failure instead of leaving a behavior that
        no tick will ever progress.  Returns whether a behavior was active.
        """

        state = agent.get("initial_state") or {}
        execution_id = state.get("active_bt_instance")
        behavior_id = state.get("active_behavior_id")
        if not execution_id or not behavior_id:
            return False
        self._finish_behavior(agent, str(execution_id), str(behavior_id), "failed", reason, minute)
        return True

    def _fail_active_actions(self, agent: dict[str, Any], minute: int) -> None:
        state = agent.get("initial_state") or {}
        reservations = list(state.get("active_action_reservation") or [])
        action_ids = [state["active_action_instance"]] if state.get("active_action_instance") else []
        for entry in (state.get("active_parallel_actions") or {}).values():
            if int(entry["remaining"]) > 0:
                reservations.extend(entry.get("reservation") or [])
                action_ids.append(entry["action_instance_id"])
        self.resources.release(agent, reservations)
        for action_id in action_ids:
            row = self.store.connection.execute(
                "SELECT elapsed_minutes,remaining_minutes,data_json FROM action_instance WHERE action_instance_id=?", (action_id,),
            ).fetchone()
            if row is not None:
                self.store.repositories.activity.update_action_instance(
                    action_id, status="failed", elapsed_minutes=row["elapsed_minutes"],
                    remaining_minutes=row["remaining_minutes"], data=json.loads(row["data_json"]), updated_minute=minute,
                )

    def _finish_behavior(self, agent: dict[str, Any], execution_id: str, behavior_id: str, status: str, result: str, minute: int) -> None:
        state = dict(agent.get("initial_state") or {})
        bt_row = self.store.connection.execute("SELECT data_json FROM bt_instance WHERE execution_id=?", (execution_id,)).fetchone()
        bt_data = json.loads(bt_row["data_json"]) if bt_row is not None else {}
        before = dict(bt_data.get("before") or {})
        behavior_definition = self._behavior_definition(bt_data, behavior_id)
        if status == "completed" and result == "behavior_completed":
            result = str(state.get("last_action_result") or result)
        outcome_effects = behavior_definition.get("outcome_effects") or []
        if status == "completed" and outcome_effects:
            failure, _ = self._complete_action_effects(
                agent, {"id": f"behavior:{behavior_id}", "effects": outcome_effects}, {}, minute,
            )
            if failure:
                status, result = "failed", failure
            state = dict(agent.get("initial_state") or {})
        if status != "completed":
            self._fail_active_actions(agent, minute)
        after = self._context.services["execution"].execution_snapshot(agent)
        outcome = {
            "status": status,
            "result": result,
            "expected": dict(bt_data.get("expected") or {}),
            "actual": after,
            "delta": self._context.services["execution"].execution_delta(before, after) if before else {},
            "decision": dict(bt_data.get("decision") or {}),
        }
        rule_followups: list[dict[str, Any]] = []
        if status == "completed":
            location_id = (agent.get("initial_state") or {}).get("location_id")
            context = {
                "location_id": location_id,
                "current_location": self._context.services["execution"].entity_for_context(location_id),
                "behavior_id": behavior_id,
                **calendar_context(self.store, minute),
                "recent_event_types": self._context.services["evolution"].recent_event_types(),
            }
            world = self._context.services["evolution"].world_trigger_context()
            for raw_rule in self.rule_compiler.all_rules():
                behavior = behavior_definition
                if not self.rule_compiler.behavior_matches(raw_rule, behavior):
                    continue
                if not self.rule_compiler.matches(raw_rule, agent=agent, target={}, context=context, world=world):
                    continue
                compiled = self.rule_compiler.compile(raw_rule)
                followup = {
                    "rule": compiled["name"],
                    "rule_id": raw_rule.get("id") or compiled["name"],
                    "execution_id": execution_id,
                    "behavior_id": behavior_id,
                    "result": result,
                    "expected_effects": list(raw_rule.get("expected_effects") or []),
                    "observed_delta": outcome.get("delta") or {},
                }
                rule_followups.append(followup)
                self._context.services["events"].append("world.rule.followed", agent["id"], minute, followup)
        if rule_followups:
            outcome["rule_followups"] = rule_followups
        if status == "completed":
            for configured in behavior_definition.get("outcome_events", []) or []:
                if not isinstance(configured, dict):
                    continue
                event_type = str(configured.get("event_type") or "").strip()
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
                for existing in self.store.repositories.events.recent_events(limit=32):
                    if (
                        str(existing.get("event_type") or "") == event_type
                        and str(existing.get("actor_id") or "") == str(agent["id"])
                        and int(existing.get("world_minute", -1) or -1) == int(minute)
                        and str(existing.get("behavior_id") or "") == behavior_id
                        and str(existing.get("execution_id") or "") == execution_id
                    ):
                        duplicate_event_id = int(existing["event_id"])
                        break
                event_id = duplicate_event_id or self._context.services["events"].append(event_type, agent["id"], minute, payload)
                # Publish every validated outcome to configured consumer
                # policies. The event itself remains the source of truth;
                # event groups decide whether Story/God receive it directly,
                # after a threshold, or not at all.
                self._context.services["events"].wake(
                    event_id, event_type, agent["id"], minute,
                    wake={"configured": True, "followups": configured},
                )
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
                self.store.repositories.memory.merge_knowledge_fact(agent["id"], fact_key, {"fact": str(configured["fact"]), "source_type": str(configured.get("source_type") or "behavior"), "source_id": behavior_id, "behavior": behavior_id, "confidence": float(configured.get("confidence", 0.8)), "observed_at": minute})
        state["status"] = "idle" if status == "completed" else "failed"
        blocked_behaviors = dict(state.get("blocked_behaviors") or {})
        cooling_down = result.startswith("cooldown until")
        if status == "completed" or cooling_down or result == SOCIAL_OUTCOME_DELIVERED:
            blocked_behaviors.pop(str(behavior_id), None)
        else:
            blocked_behaviors[str(behavior_id)] = {
                "result": str(result),
                "failed_at": int(minute),
                "failed_world_revision": int(self.store.repositories.metadata.get("world_revision", 0) or 0),
            }
        state["blocked_behaviors"] = blocked_behaviors
        assignments = list(agent.get("behavior_assignments", []) or [])
        assignment_changed = False
        for assignment in assignments:
            if not isinstance(assignment, dict) or str(assignment.get("behavior_id")) != str(behavior_id):
                continue
            if cooling_down or str(assignment.get("status") or "assigned") != "assigned":
                continue
            assignment.update({
                "status": "completed" if status == "completed" else "failed",
                "completed_minute": int(minute),
                "result": str(result),
            })
            assignment_changed = True
        if assignment_changed:
            agent["behavior_assignments"] = assignments[-8:]
        withdrawal = state.get("active_resource_withdrawal") if isinstance(state.get("active_resource_withdrawal"), dict) else None
        withdrawal_authorization = state.get("active_resource_authorization")
        if status != "completed" and withdrawal:
            self._context.services["events"].append(
                "resource.withdrawal.failed",
                agent["id"],
                minute,
                {
                    "intent_id": str(execution_id),
                    "actor": str(agent["id"]),
                    "target": str(withdrawal.get("resource_id") or ""),
                    "reason": str(result),
                    "authorization": withdrawal_authorization,
                    "minute": int(minute),
                },
            )
        take = state.get("active_resource_take") if isinstance(state.get("active_resource_take"), dict) else None
        if status != "completed" and take:
            self._context.services["events"].append(
                "resource.take.failed",
                agent["id"],
                minute,
                {
                    "intent_id": str(execution_id),
                    "actor": str(agent["id"]),
                    "target": str(take.get("resource_id") or ""),
                    "reason": str(result),
                    "minute": int(minute),
                },
            )
        state.pop("active_resource_withdrawal", None)
        state.pop("active_resource_authorization", None)
        state.pop("active_resource_take", None)
        state["active_bt_instance"] = None
        state["active_behavior_id"] = None
        self._clear_active_action(state)
        state["last_platform_result"] = {"status": status, "behavior_ref": f"behavior:{behavior_id}", "result": result, "outcome": outcome}
        state["last_outcome"] = outcome
        agent["initial_state"] = state
        self.store.repositories.entities.update_agent(agent["id"], agent)
        bt_data["outcome"] = outcome
        bt_data["result"] = result
        self.store.repositories.activity.update_bt_instance(execution_id, status=status, current_node=int(state.get("active_action_index", 0)), data=bt_data, updated_minute=minute)
        self._context.services["events"].append(
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
        behavior_event_id = self._context.services["events"].append(f"behavior.{status}", agent["id"], minute, {"execution_id": execution_id, "behavior_id": behavior_id, "result": result, "outcome": outcome})
        if status == "completed":
            self._record_active_institution_entries(behavior_event_id, "behavior.completed", agent["id"], minute, {"execution_id": execution_id, "behavior_id": behavior_id, "result": result, "outcome": outcome})
            self._complete_story_beats_for_actor(agent["id"], behavior_id, minute, behavior_event_id, result)
        else:
            self._block_story_beats_for_failed_followup(agent["id"], behavior_id, minute, behavior_event_id, result)

    def _block_story_beats_for_failed_followup(
        self,
        actor_id: str,
        behavior_id: str,
        minute: int,
        source_event_id: int,
        result: str,
    ) -> None:
        """Stop a failed story goal from forcing the same action forever.

        A failed institution settlement or other follow-up is evidence that the
        current continuation is blocked.  Close that branch as blocked, clear
        its copied goal, and wake Story with the real failure so it can choose
        a different executable continuation.  This is generic lifecycle logic;
        the world package still decides what the replacement is.
        """

        try:
            state = dict(self.store.repositories.metadata.get("story_state", {}) or {})
            agent = self.store.repositories.entities.get_agent(actor_id)
        except (KeyError, TypeError):
            return
        story_root = self.store.repositories.metadata.get("story", {}) or {}
        story_root = story_root.get("story", story_root) if isinstance(story_root, dict) else {}
        definitions = {str(item.get("id")): item for item in self.store.repositories.entities.get_definitions("bt_def")}
        beats = {str(item.get("id")): item for item in (story_root.get("beats", []) or []) if isinstance(item, dict)}
        beats.update({str(item.get("id")): item for item in (state.get("emergent_beats", []) or []) if isinstance(item, dict) and item.get("id")})

        def matches(declaration: Any) -> bool:
            key = "".join(char for char in str(declaration or "").casefold() if char.isalnum())
            if not key:
                return False
            definition = definitions.get(str(behavior_id), {})
            candidates = {str(behavior_id)} | {
                str(definition.get(field) or "")
                for field in ("agent_label", "summary", "description")
            }
            return any(key == "".join(char for char in candidate.casefold() if char.isalnum()) for candidate in candidates if candidate)

        changed = False
        cleared_goals: set[str] = set()
        for beat_id, entry in list(state.items()):
            if beat_id == "emergent_beats" or not isinstance(entry, dict) or entry.get("status") != "active":
                continue
            audience = {str(item) for item in (entry.get("audience", []) or [])}
            if str(actor_id) not in audience:
                continue
            beat = beats.get(str(beat_id), {})
            followups = beat.get("followup_behaviors")
            if followups is not None and not any(matches(item) for item in (followups if isinstance(followups, list) else [followups])):
                continue
            entry = dict(entry)
            entry.update({"status": "blocked", "blocked_minute": int(minute), "blocked_by_behavior": str(behavior_id), "blocked_result": str(result)})
            state[beat_id] = entry
            update_role_memory_status(
                self.store,
                "story",
                beat_id=str(beat_id),
                status="blocked",
                world_minute=minute,
                behavior_id=behavior_id,
            )
            changed = True
            goal_hint = str(beat.get("goal_hint") or "").strip()
            if goal_hint:
                cleared_goals.add(goal_hint)
            self._context.services["events"].append("story.beat.blocked", actor_id, minute, {"beat_id": beat_id, "behavior_id": behavior_id, "result": result})
        if not changed:
            return
        if cleared_goals:
            agent["story_goals"] = [item for item in (agent.get("story_goals", []) or []) if str(item) not in cleared_goals]
            self.store.repositories.entities.update_agent(actor_id, agent)
        self.store.repositories.metadata.set("story_state", state)
        self._context.services["events"].wake(source_event_id, "behavior.failed", actor_id, minute, wake="story_source")

    def _complete_story_beats_for_actor(
        self, actor_id: str, behavior_id: str, minute: int, behavior_event_id: int | None = None, result: str = "",
    ) -> None:
        """Close active audience beats after a matching follow-up behavior.

        Beats are one-shot narrative threads.  When a beat declares
        ``followup_behaviors``, only those behaviors close it, so a chapter
        stays open until its dramatically relevant action happens.  Beats
        without a declared followup keep the legacy close-on-any-behavior
        rule.  Either way the story catalog cannot fill with stale threads
        and Story gets a verifiable close-out.
        """

        try:
            state = dict(self.store.repositories.metadata.get("story_state", {}) or {})
        except Exception:
            return
        story_root = self.store.repositories.metadata.get("story", {}) or {}
        story_root = story_root.get("story", story_root) if isinstance(story_root, dict) else {}
        package_beats = {str(item.get("id")): item for item in (story_root.get("beats", []) or []) if isinstance(item, dict)}
        for item in state.get("emergent_beats", []) or []:
            if isinstance(item, dict) and item.get("id"):
                package_beats.setdefault(str(item["id"]), item)
        definitions = {str(item.get("id")): item for item in self.store.repositories.entities.get_definitions("bt_def")}

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
                agent_name = str(self.store.repositories.entities.get_agent(actor_id).get("name", actor_id))
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
            update_role_memory_status(
                self.store,
                "story",
                beat_id=str(beat_id),
                status="completed",
                world_minute=minute,
                behavior_id=behavior_id,
            )
            changed = True
            self._context.services["events"].publish("story.beat.completed", actor_id, minute, {
                "beat_id": beat_id, "behavior_id": behavior_id,
                "chapter_title": package_beats.get(str(beat_id), {}).get("agent_label") or entry.get("summary", ""),
                "behavior_label": definitions.get(behavior_id, {}).get("agent_label", behavior_id),
                "behavior_event_id": behavior_event_id, "result": result,
            }, wake="configured")
        if changed:
            self.store.repositories.metadata.set("story_state", state)

