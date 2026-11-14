from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..actions.capacity import check_action_capacity
from ..behavior.estimator import BehaviorDurationEstimator
from ..behavior.recovery import failure_block_recovered
from ..intents.models import BehaviorIntent
from ..world.context import configured_context_values
from ..world.navigation import (
    configured_location_tags,
    nearest_location_with_tag,
    nearest_resource_node,
    nearest_safe_location,
    reachable_locations,
)
from ..world.time import world_time_view
from ..world.rules import RuleCompiler
from ..world.schema import AttributeSchema
from ..actions.conditions import ConditionEngine
from .services import PlatformService


class BehaviorIntentService(PlatformService):
    """Behavior intent validation and activity admission."""

    def _reject_intent(self, intent_id: str, actor_id: str, intent_type: str, data: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        world_minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, intent_type, data, world_minute)
            self.store.finish_intent(intent_id, "rejected", result)
        return result

    def _validate_behavior_parameters(self, behavior_id: str, parameters: dict[str, Any]) -> list[str]:
        bt = next((item for item in self.store.get_definitions("bt_def") if item.get("id") == behavior_id), None)
        if not bt:
            return ["behavior is not registered"]
        action_defs = {item["id"]: item for item in self.store.get_definitions("action_def")}
        allowed: set[str] = set()
        for node in self.view._iter_behavior_actions(behavior_id):
            action = action_defs.get(node.get("action_id"), {})
            allowed.update((action.get("parameters") or {}).keys())
            allowed.update((node.get("parameters") or {}).keys())
        errors = [f"unknown behavior parameter: {key}" for key in parameters if key not in allowed]
        type_checks = {
            "string": lambda value: isinstance(value, str),
            "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
            "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
            "boolean": lambda value: isinstance(value, bool),
            "object": lambda value: isinstance(value, dict),
            "array": lambda value: isinstance(value, list),
        }
        for name, raw_spec in (bt.get("agent_parameters", {}) or {}).items():
            spec = raw_spec if isinstance(raw_spec, dict) else {"type": str(raw_spec)}
            value = parameters.get(str(name))
            has_default = str(name) in (bt.get("parameter_defaults", {}) or {})
            if spec.get("required") and value in (None, "", [], {}) and not has_default:
                errors.append(f"missing required behavior parameter: {name}")
                continue
            if value is None:
                continue
            expected = str(spec.get("type", ""))
            if expected in type_checks and not type_checks[expected](value):
                errors.append(f"behavior parameter {name} must be {expected}")
            max_length = spec.get("max_length", spec.get("maxLength"))
            if isinstance(value, str) and isinstance(max_length, int) and len(value) > max_length:
                errors.append(f"behavior parameter {name} is too long")
        return errors

    def submit_behavior(self, intent: BehaviorIntent) -> dict[str, Any]:
        previous = self.store.get_intent(intent.intent_id)
        if previous is not None:
            return previous["result"] or {"status": previous["status"], "intent_id": intent.intent_id}
        try:
            agent = self.store.get_agent(intent.actor_id)
        except KeyError:
            return {"status": "rejected", "message": "The actor does not exist.", "failed_check": "actor", "retryable": False}
        behavior_id = intent.behavior_ref.removeprefix("behavior:")
        normalized_parameters = dict(intent.parameters or {})
        if "entity_id" in normalized_parameters and "target_id" not in normalized_parameters:
            normalized_parameters["target_id"] = normalized_parameters["entity_id"]
        normalized_parameters.pop("entity_id", None)
        for target_alias in ("entity_id", "id"):
            if target_alias in intent.target and "target_id" not in normalized_parameters:
                normalized_parameters["target_id"] = intent.target[target_alias]
        for alias in ("destination", "destination_id", "location"):
            if alias in normalized_parameters and "location_id" not in normalized_parameters:
                normalized_parameters["location_id"] = normalized_parameters[alias]
            normalized_parameters.pop(alias, None)
            if alias in intent.target and "location_id" not in normalized_parameters:
                normalized_parameters["location_id"] = intent.target[alias]
        # Models use the visible names supplied by AgentView.  Normalize every
        # entity reference only after target aliases have been folded into the
        # registered Action parameters.  Doing this earlier left values copied
        # from ``target.location`` as display text (for example ``粮仓``), while
        # navigation compares stable entity IDs (``granary``).  The behavior
        # was accepted from a valid view and then failed one tick later.
        for key in ("target_id", "patient_id", "protected_target_id", "counterparty_id", "listener_id", "location_id", "source_id", "object_id", "area_id"):
            if normalized_parameters.get(key):
                canonical = self.targets.canonical_id(normalized_parameters[key])
                if canonical:
                    normalized_parameters[key] = canonical
        intent_data = {
            "actor_id": intent.actor_id,
            "behavior_ref": intent.behavior_ref,
            "target": intent.target,
            "parameters": normalized_parameters,
            "reason": intent.reason,
            "confidence": intent.confidence,
            "observed_world_revision": intent.observed_world_revision,
            "observed_agent_revision": intent.observed_agent_revision,
        }
        available_defs = {x["id"]: x for x in self.store.get_definitions("bt_def")}
        if behavior_id not in available_defs:
            return self._reject_intent(intent.intent_id, intent.actor_id, intent.intent_type, intent_data, {
                "status": "rejected", "message": "The selected behavior is not registered.", "failed_check": "behavior_ref", "retryable": True,
            })
        # A package may provide an opening source as a default while the same
        # BT remains reusable for every later source created by Story/God.
        # Defaults are declarative BT metadata; the generic runtime does not
        # know any source name.
        bt = available_defs[behavior_id]
        for key, value in (bt.get("parameter_defaults", {}) or {}).items():
            if normalized_parameters.get(str(key)) in (None, "", [], {}):
                normalized_parameters[str(key)] = value
        allowed = (
            set(agent.get("initial_bt_ids", []))
            | set(agent.get("unlocked_bt_ids", []))
            | set((self.store.get_meta("rules", {}) or {}).get("fallback_behavior_ids", []))
        )
        if behavior_id not in allowed:
            return self._reject_intent(intent.intent_id, intent.actor_id, intent.intent_type, intent_data, {
                "status": "rejected", "message": "This behavior is not available to this character.", "failed_check": "capability", "retryable": True,
            })
        parameter_errors = self._validate_behavior_parameters(behavior_id, normalized_parameters)
        if parameter_errors:
            return self._reject_intent(intent.intent_id, intent.actor_id, intent.intent_type, intent_data, {
                "status": "rejected", "message": "Behavior parameters must match the registered Action schema.", "failed_check": "parameters", "errors": parameter_errors, "retryable": True,
            })
        state = dict(agent.get("initial_state", {}))
        if state.get("status") in {"executing", "paused"}:
            return self._reject_intent(intent.intent_id, intent.actor_id, intent.intent_type, intent_data, {
                "status": "rejected", "message": "The actor already has an active behavior; wait for its result or an interrupt.", "failed_check": "active_behavior", "retryable": False,
            })
        blocked = (state.get("blocked_behaviors") or {}).get(str(behavior_id)) if isinstance(state, dict) else None
        if isinstance(blocked, dict) and not failure_block_recovered(self.store, blocked):
            return self._reject_intent(intent.intent_id, intent.actor_id, intent.intent_type, intent_data, {
                "status": "rejected",
                "message": "This behavior is blocked after its last failed result; wait for a configured recovery event.",
                "failed_check": "failed_behavior_blocked",
                "retryable": True,
                "failure_result": blocked.get("result"),
            })
        bt = available_defs[behavior_id]
        runtime_parameters = dict(normalized_parameters)
        target_keys = self.view._target_parameter_keys(bt, available_defs)
        visible_participants = self.targets.visible_agents(agent)
        supplied_target = any(runtime_parameters.get(key) for key in target_keys)
        if supplied_target:
            visible_ids = {str(item.get("id")) for item in visible_participants}
            hidden_targets = [
                (key, str(runtime_parameters.get(key)))
                for key in target_keys
                if runtime_parameters.get(key) and str(runtime_parameters.get(key)) not in visible_ids
            ]
            if hidden_targets:
                return self._reject_intent(intent.intent_id, intent.actor_id, intent.intent_type, intent_data, {
                    "status": "rejected", "message": "Choose one exact visible participant from the current target list.", "failed_check": "target_visibility", "retryable": True,
                    "available_targets": [str(item.get("name", "participant")) for item in visible_participants],
                })
        if target_keys and not supplied_target:
            visible_name = next((intent.target.get(key) for key in ("name", "entity", "participant", "target") if isinstance(intent.target, dict) and intent.target.get(key)), None)
            if visible_name:
                canonical = self.targets.canonical_id(visible_name)
                if canonical is None or not any(str(item.get("id")) == str(canonical) for item in visible_participants):
                    return self._reject_intent(intent.intent_id, intent.actor_id, intent.intent_type, intent_data, {
                        "status": "rejected", "message": "Choose one exact visible participant from the current target list.", "failed_check": "target_choice", "retryable": True,
                        "available_targets": [str(item.get("name", "participant")) for item in visible_participants],
                    })
                runtime_parameters[next(iter(target_keys))] = canonical
                normalized_parameters[next(iter(target_keys))] = canonical
            elif len(visible_participants) > 1:
                return self._reject_intent(intent.intent_id, intent.actor_id, intent.intent_type, intent_data, {
                    "status": "rejected", "message": "This behavior requires one explicit visible participant.", "failed_check": "target_required", "retryable": True,
                    "available_targets": [str(item.get("name", "participant")) for item in visible_participants],
                })
            elif len(visible_participants) == 1:
                runtime_parameters[next(iter(target_keys))] = visible_participants[0]["id"]
        for parameter, resolver in (bt.get("parameter_bindings", {}) or {}).items():
            resolver = str(resolver)
            if resolver == "nearest_safe_location":
                value = nearest_safe_location(self.store, state.get("location_id"))
            elif resolver in {"nearest_resource_node", "nearest_resource_tag"}:
                resource = nearest_resource_node(self.store, state.get("location_id"))
                value = (resource or {}).get("location_id" if resolver == "nearest_resource_node" else "resource_tag")
            elif resolver == "nearest_health_service":
                value = nearest_location_with_tag(self.store, state.get("location_id"), configured_location_tags(self.store, "health_service", {"health", "clinic"}))
            else:
                value = None
            if value:
                runtime_parameters.setdefault(str(parameter), value)
        # Resolve derived research travel once for the whole BT.  The
        # estimator and the Action runtime must see the same source location;
        # otherwise an already co-located researcher is charged a phantom
        # route minute and a fixed-duration test can submit while the BT is
        # still moving.
        source_id = runtime_parameters.get("source_id")
        if source_id:
            try:
                source = self.store.get_entity(str(source_id))
            except KeyError:
                source = {}
            source_state = source.get("initial_state") if isinstance(source.get("initial_state"), dict) else {}
            source_location = source_state.get("location_id") or source.get("location_id")
            if source_location:
                runtime_parameters.setdefault("source_location_id", source_location)
        intent_data["parameters"] = {**normalized_parameters, **runtime_parameters}
        entry_context = self._behavior_condition_context(agent, runtime_parameters, intent.target)
        entry_target = {**runtime_parameters, **intent.target, **(entry_context.get("target") or {})}
        for predicate in bt.get("entry_conditions", []) or []:
            if not self.conditions.evaluate(
                str(predicate),
                agent=agent,
                target=entry_target,
                context=entry_context,
                world=self.store.get_meta("world_properties", {}) or {},
            ):
                return self._reject_intent(intent.intent_id, intent.actor_id, intent.intent_type, intent_data, {
                    "status": "rejected",
                    "message": "The selected behavior is no longer executable in the current state.",
                    "failed_check": "entry_condition",
                    "condition": str(predicate),
                    "retryable": True,
                })
        cooldown_minutes = max(0, int(bt.get("cooldown_minutes", 0) or 0))
        world_minute = int(self.store.get_meta("world_minute", 0))
        if cooldown_minutes:
            last_minute = self.store.last_behavior_minute(intent.actor_id, behavior_id)
            if last_minute is not None and world_minute - last_minute < cooldown_minutes:
                return self._reject_intent(intent.intent_id, intent.actor_id, intent.intent_type, intent_data, {
                    "status": "rejected", "message": "This behavior is cooling down after its last completion.", "failed_check": "cooldown", "retryable": True,
                    "retry_after_minute": last_minute + cooldown_minutes,
                })
        # A choice made from an old view must be checked against the current
        # executable catalog before it can reserve anything. Explicit target
        # parameters are retained for player/API commands whose destination is
        # supplied outside the catalog; ordinary Agent choices have neither.
        # A model decision carries the revision of the view it used and must
        # be rechecked against the current executable catalog. A direct API
        # request may intentionally attempt a stale institution allocation so
        # the Action can return its typed failure for audit; all other direct
        # requests still receive the normal catalog check.
        allocation_effect = any(
            any(str(effect.get("type") or effect.get("op") or "") == "institution_allocation" for effect in (next((item for item in self.store.get_definitions("action_def") if item.get("id") == node.get("action_id")), {}).get("effects", []) or []))
            for node in self.view._iter_behavior_actions(behavior_id)
        )
        catalog_check_required = intent.observed_agent_revision is not None or (not intent.target and not intent.parameters)
        if catalog_check_required and not (intent.observed_agent_revision is None and allocation_effect):
            current_view = self.view.build_decision_view(intent.actor_id, role="agent_decision")
            if behavior_id not in {str(item.get("ref", "")).removeprefix("behavior:") for item in current_view.get("executable_behaviors", [])}:
                return self._reject_intent(intent.intent_id, intent.actor_id, intent.intent_type, intent_data, {
                    "status": "rejected", "message": "The selected behavior is no longer executable in the current state.", "failed_check": "behavior_unavailable", "retryable": True,
                })
        estimator = BehaviorDurationEstimator(
            {item["id"]: item for item in self.store.get_definitions("action_def")},
            available_defs,
            {item["id"]: item for item in self.store.get_definitions("recipe_def")},
            self._route_distance,
            capabilities=agent.get("capabilities", {}),
            capability_modifiers=(self.store.get_meta("rules", {}) or {}).get("capability_modifiers", {}),
        )
        remaining_minutes = estimator.behavior_minutes(behavior_id, {**runtime_parameters, **intent.target}, state.get("location_id"))
        current_world_revision = int(self.store.get_meta("world_revision", 0) or 0)
        current_agent_revision = int(agent.get("agent_revision", 0) or 0)
        stale_observation = (
            intent.observed_world_revision is not None and int(intent.observed_world_revision) != current_world_revision
        ) or (
            intent.observed_agent_revision is not None and int(intent.observed_agent_revision) != current_agent_revision
        )
        # The character projection is a strong consistency boundary: another
        # accepted intent for this character may have changed location,
        # inventory, or an active activity after the model read its view.
        # Re-evaluate instead of reserving resources from that stale view.
        # World revisions are intentionally weak here because unrelated
        # characters may advance in the same minute; all configured entry
        # conditions and reservations are still checked against current state.
        if (
            intent.observed_agent_revision is not None
            and int(intent.observed_agent_revision) != current_agent_revision
        ):
            return self._reject_intent(intent.intent_id, intent.actor_id, intent.intent_type, intent_data, {
                "status": "rejected",
                "message": "This character changed after the decision view was read; refresh the current behavior list and choose again.",
                "failed_check": "stale_agent_revision",
                "retryable": True,
                "observed_agent_revision": int(intent.observed_agent_revision),
                "current_agent_revision": current_agent_revision,
                "world_revision": current_world_revision,
            })
        expected = {
            "summary": bt.get("summary") or bt.get("description") or behavior_id,
            "success": list(bt.get("success_results", []) or []),
            "failure": list(bt.get("failure_results", []) or []),
            "estimated_minutes": remaining_minutes,
            "goal_tags": list(bt.get("goal_tags", []) or []),
            "drive_tags": list(bt.get("drive_tags", []) or []),
            "behavior_version": int(bt.get("definition_version", bt.get("version", 1)) or 1),
        }
        with self.store.transaction():
            self.store.create_intent(intent.intent_id, intent.actor_id, intent.intent_type, intent_data, world_minute)
            state["status"] = "executing"
            state["active_bt_instance"] = intent.intent_id
            state["active_behavior_id"] = behavior_id
            state["active_action"] = None
            state["remaining_minutes"] = remaining_minutes
            state["active_action_index"] = 0
            state["active_parameters"] = {**runtime_parameters, **intent.target}
            state["last_platform_result"] = {"status": "accepted", "behavior_ref": intent.behavior_ref}
            agent["initial_state"] = state
            self.store.update_agent(intent.actor_id, agent)
            self.store.put_bt_instance(
                intent.intent_id,
                intent.actor_id,
                behavior_id,
                "executing",
                0,
                {
                    "target": intent.target,
                    "parameters": runtime_parameters,
                    "actions": self._compile_behavior_actions(behavior_id),
                    "behavior_version": int(bt.get("definition_version", bt.get("version", 1)) or 1),
                    "expected": expected,
                    "decision": {"reason": intent.reason, "confidence": intent.confidence},
                    "before": self._execution_snapshot(agent),
                },
                world_minute,
                world_minute,
            )
            self.uow.events.append(
                "behavior.started", intent.actor_id, world_minute,
                {"intent_id": intent.intent_id, "behavior_id": behavior_id, "target": intent.target, "expected": expected, "decision": {"reason": intent.reason, "confidence": intent.confidence}},
            )
            result = {
                "status": "accepted",
                "intent_id": intent.intent_id,
                "execution_id": intent.intent_id,
                "behavior_ref": intent.behavior_ref,
                "remaining_minutes": remaining_minutes,
                "stale_observation": stale_observation,
                "world_revision": current_world_revision,
                "agent_revision": current_agent_revision,
            }
            self.store.finish_intent(intent.intent_id, "accepted", result)
        return result
class BehaviorExecutionService(PlatformService):
    """Action parameter resolution, progress and interruption."""

    def _behavior_condition_context(self, agent: dict[str, Any], parameters: dict[str, Any], target: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build the small declarative context used to recheck BT entry rules."""

        state = agent.get("initial_state", {}) or {}
        location_id = state.get("location_id")
        location = {}
        if location_id:
            try:
                location = self.store.get_entity(str(location_id))
            except KeyError:
                location = {}
        resolved_target, _ = self.targets.resolve(agent, {**parameters, **(target or {})})
        participants = self.targets.visible_agents(agent)
        rules = self.store.get_meta("rules", {}) or {}
        conflict_types = {str(item) for item in (rules.get("conflict_relation_types", ["conflict"]) or [])}
        participant_ids = {str(item.get("id")) for item in participants}
        conflict_available = False
        for relation in self.store.get_related_relationships(agent["id"], limit=50):
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
            "current_object": self._entity_for_context(parameters.get("object_id")),
            "mapped_areas": self.store.explored_areas(agent["id"]),
            "researched_sources": self.store.researched_sources(agent["id"]),
            "known_fact_keys": [item.get("fact_key") for item in self.store.list_knowledge_facts(agent["id"], limit=1000) if item.get("fact_key")],
            "target": resolved_target or {},
            "target.visible": bool((resolved_target or {}).get("visible", False)),
            "target.available": bool((resolved_target or {}).get("available", False)),
            "known_route_destinations": reachable_locations(self.store, location_id),
            "recent_event_types": self._recent_event_types(),
            "day_phase": world_time_view(self.store).get("day_phase"),
            "conflict": {"available": conflict_available},
        }
        result.update(configured_context_values(self.store.get_meta("rules", {}) or {}, agent))
        return result

    def _entity_for_context(self, entity_id: Any) -> dict[str, Any]:
        """Return a configured entity for a declarative condition context."""

        if not entity_id:
            return {}
        try:
            return self.store.get_entity(str(entity_id))
        except KeyError:
            return {}

    def _compile_behavior_actions(self, behavior_id: str, seen: set[str] | None = None) -> list[dict[str, Any]]:
        seen = set(seen or set())
        if behavior_id in seen:
            return []
        seen.add(behavior_id)
        definitions = {item["id"]: item for item in self.store.get_definitions("bt_def")}
        behavior = definitions.get(behavior_id)
        if not behavior:
            return []

        def visit(nodes: list[Any], parallel_group: str | None = None) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            for raw in nodes or []:
                node = raw if isinstance(raw, dict) else {"type": "behavior", "behavior_id": raw}
                node_type = node.get("type")
                if node_type == "action" and node.get("action_id"):
                    action = next((item for item in self.store.get_definitions("action_def") if item.get("id") == node["action_id"]), {})
                    result.append({"action_id": node["action_id"], "action_version": int(action.get("definition_version", action.get("version", 1)) or 1), "parameters": dict(node.get("parameters") or {}), "parallel_group": parallel_group})
                elif node_type in {"behavior", "subtree"} and node.get("behavior_id"):
                    nested = self._compile_behavior_actions(str(node["behavior_id"]), seen)
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
    def _execution_snapshot(agent: dict[str, Any]) -> dict[str, Any]:
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
    def _execution_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
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

    def _resolve_activity_parameters(self, parameters: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        context = dict(state)
        context.update(state.get("active_parameters") or {})
        source_id = context.get("source_id")
        if source_id:
            try:
                source = self.store.get_entity(str(source_id))
            except KeyError:
                source = {}
            source_state = source.get("initial_state") if isinstance(source.get("initial_state"), dict) else {}
            context.setdefault("source_location_id", source_state.get("location_id") or source.get("location_id"))

        return self._resolve_parameters(parameters, context)

    def _route_distance(self, origin: str | None, destination: str | None) -> int:
        """Return weighted route distance in configured route hops."""

        if not origin or not destination or origin == destination:
            return 0
        hop_minutes = max(1, int((self.store.get_meta("rules", {}) or {}).get("route_minutes_per_hop", 1)))
        queue: list[tuple[str, int]] = [(str(origin), 0)]
        visited = {str(origin)}
        while queue:
            current, distance = queue.pop(0)
            try:
                location = self.store.get_entity(current)
            except KeyError:
                continue
            for neighbor in location.get("routes", []) or []:
                neighbor = str(neighbor)
                if neighbor in visited:
                    continue
                if neighbor == str(destination):
                    return (distance + 1) * hop_minutes
                visited.add(neighbor)
                queue.append((neighbor, distance + 1))
        return hop_minutes

    def _action_duration(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], origin_location: str | None = None) -> int:
        estimator = BehaviorDurationEstimator(
            {item["id"]: item for item in self.store.get_definitions("action_def")},
            {},
            {item["id"]: item for item in self.store.get_definitions("recipe_def")},
            self._route_distance,
            capabilities=agent.get("capabilities", {}),
            capability_modifiers=(self.store.get_meta("rules", {}) or {}).get("capability_modifiers", {}),
        )
        return estimator._action_minutes(action, parameters, origin_location)

    def _apply_action_parameter_defaults(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        """Resolve declarative defaults without making the LLM fill hidden plumbing."""

        result = dict(parameters)
        configured_context = configured_context_values(self.store.get_meta("rules", {}) or {}, agent)
        for key, default in (action.get("parameter_defaults", {}) or {}).items():
            value = result.get(key)
            if value not in (None, "") and not (isinstance(value, str) and value.startswith("context.")):
                continue
            if isinstance(default, dict) and str(default.get("resolver") or "") == "context_value":
                result[key] = configured_context.get(str(default.get("key") or key), value)
            elif default == "first_available_resource":
                location_id = (agent.get("initial_state") or {}).get("location_id")
                sources = self.store.entities_at_location(location_id) if location_id else []
                rules = self.store.get_meta("rules", {}) or {}
                entity_types = {str(item) for item in (rules.get("resource_entity_types", ["resource", "material"]) or [])}
                protected_key = str(rules.get("resource_protected_state", "protected"))
                source = next((item for item in sources if item.get("entity_type") in entity_types and not (item.get("initial_state") or {}).get(protected_key) and self._entity_quantity(item) > 0), None)
                result[key] = source.get("id") if source else value
            elif isinstance(default, str) and default in configured_context:
                # Declarative legacy aliases (for example ``first_food``)
                # resolve through the package context projection rather than
                # becoming a literal inventory key.
                result[key] = configured_context.get(default, value)
            elif value in (None, "") or (isinstance(value, str) and value.startswith("context.")):
                result[key] = default
        return result

    @staticmethod
    def _entity_quantity(entity: dict[str, Any]) -> float:
        state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
        value = entity.get("amount")
        if value is None:
            value = state.get("quantity", state.get("amount", 0))
        return float(value or 0)

    def _reserve_action_costs(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], *, commit: bool = True) -> tuple[bool, str, list[dict[str, Any]]]:
        return self.resources.reserve(agent, action, parameters, commit=commit)

    def _check_action_capacity(self, agent: dict[str, Any], action: dict[str, Any], duration: int) -> tuple[bool, str]:
        return check_action_capacity(agent, action, duration, self.store.get_meta("attributes", []) or [])

    def _apply_action_progress(self, agent: dict[str, Any], action: dict[str, Any]) -> None:
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
        agent = self.store.get_agent(actor_id)
        state = dict(agent.get("initial_state") or {})
        execution_id = state.get("active_bt_instance")
        if not execution_id:
            return {"status": "ignored", "message": "The actor has no active behavior."}
        minute = int(self.store.get_meta("world_minute", 0))
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
                "actual": self._execution_snapshot(agent),
                "delta": self._execution_delta(before, self._execution_snapshot(agent)) if before else {},
                "decision": dict(bt_data.get("decision") or {}),
            }
            action_instance_id = state.get("active_action_instance")
            if action_instance_id:
                action_row = self.store.connection.execute("SELECT elapsed_minutes,remaining_minutes,data_json FROM action_instance WHERE action_instance_id=?", (action_instance_id,)).fetchone()
                if action_row is not None:
                    self.store.update_action_instance(
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
                    self.store.update_action_instance(entry["action_instance_id"], status="cancelled" if hard else "paused", elapsed_minutes=action_row["elapsed_minutes"], remaining_minutes=action_row["remaining_minutes"], data=json.loads(action_row["data_json"]), updated_minute=minute)
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
            agent["initial_state"] = state
            self.store.update_agent(actor_id, agent)
            bt_data["outcome"] = outcome
            bt_data["result"] = reason
            self.store.update_bt_instance(execution_id, status=status, current_node=int(state.get("active_action_index", 0)), data=bt_data, updated_minute=minute)
            self.uow.events.append("behavior.interrupted", actor_id, minute, {"execution_id": execution_id, "reason": reason, "hard": hard, "outcome": outcome})
        return {"status": status, "execution_id": execution_id, "reason": reason}

    def resume_behavior(self, actor_id: str) -> dict[str, Any]:
        agent = self.store.get_agent(actor_id)
        state = dict(agent.get("initial_state") or {})
        execution_id = state.get("active_bt_instance")
        if not execution_id or state.get("status") != "paused":
            return {"status": "ignored", "message": "The actor has no paused behavior."}
        minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            state["status"] = "executing"
            agent["initial_state"] = state
            self.store.update_agent(actor_id, agent)
            self.store.connection.execute("UPDATE action_instance SET status='running' WHERE execution_id=? AND status='paused'", (execution_id,))
            self.store.connection.execute("UPDATE bt_instance SET status='executing',updated_minute=? WHERE execution_id=?", (minute, execution_id))
            self.uow.events.append("behavior.resumed", actor_id, minute, {"execution_id": execution_id})
        return {"status": "resumed", "execution_id": execution_id}
