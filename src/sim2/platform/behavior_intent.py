"""Behavior intent admission: validate one selected behavior and start its activity.

``BehaviorIntentService.submit_behavior`` normalizes the intent's parameters
and runs the admission steps in a fixed order.  Each check returns the
rejection result or ``None``; the first rejection is stored with the intent.
An admitted behavior opens its activity in one transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..behavior.estimator import BehaviorDurationEstimator
from ..behavior.recovery import failure_block_recovered
from ..intents.models import BehaviorIntent
from ..world.navigation import (
    configured_location_tags,
    movement_speed,
    nearest_location_with_tag,
    nearest_resource_node,
    nearest_safe_location,
)
from ..world.authorization import behavior_authorization_spec
from .authorization import AuthorizationRejected, withdrawal_effect
from .contested import ContestedRejected, resource_take_effect
from .services import PlatformService


@dataclass
class BehaviorSubmission:
    """One behavior intent on its way through admission.

    ``parameters`` is the normalized parameter dict the stored intent shows
    until ``_bind_runtime_parameters`` records the merged set; ``runtime_parameters``
    additionally holds the values derived from the world.
    """

    intent: BehaviorIntent
    agent: dict[str, Any]
    behavior_id: str
    parameters: dict[str, Any]
    intent_data: dict[str, Any]
    definitions: dict[str, dict[str, Any]] = field(default_factory=dict)
    behavior: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    runtime_parameters: dict[str, Any] = field(default_factory=dict)
    world_minute: int = 0
    remaining_minutes: int = 0
    world_revision: int = 0
    agent_revision: int = 0
    stale_observation: bool = False
    authorization: dict[str, Any] = field(default_factory=dict)
    authorization_operation: str | None = None
    withdrawal: dict[str, Any] | None = None
    take: dict[str, Any] | None = None


class BehaviorIntentService(PlatformService):
    """Behavior intent validation and activity admission."""

    def reject_intent(
        self,
        intent_id: str,
        actor_id: str,
        intent_type: str,
        data: dict[str, Any],
        result: dict[str, Any],
        *,
        submission: BehaviorSubmission | None = None,
    ) -> dict[str, Any]:
        world_minute = int(self.store.repositories.metadata.get("world_minute", 0))
        with self.store.transaction():
            self.store.repositories.evolution.create_intent(intent_id, actor_id, intent_type, data, world_minute)
            self.store.repositories.evolution.finish_intent(intent_id, "rejected", result)
            if submission is not None and submission.authorization.get("gated"):
                self._record_authorization_rejection(submission, result, world_minute)
        return result

    def _record_authorization_rejection(self, submission: BehaviorSubmission, result: dict[str, Any], minute: int) -> None:
        """One denied authority attempt is a fact, even though no state changed."""

        decision = submission.authorization
        reasons = list(decision.get("reasons") or [])
        if not reasons:
            return
        reason = reasons[0]
        behavior_id = str(submission.behavior_id)
        behavior_label = str(submission.behavior.get("agent_label") or submission.behavior.get("summary") or behavior_id)
        request = decision.get("request") or {}
        target = str(
            (request or {}).get("target")
            or submission.parameters.get("target")
            or submission.parameters.get("resource_id")
            or ""
        )
        spec = behavior_authorization_spec(self._dependency("rule_compiler"), submission.behavior) or {}
        if not target:
            target = str(spec.get("target") or "")
        if target:
            target = str(self._dependency("targets").canonical_id(target) or target)
        event = {
            "intent_id": str(submission.intent.intent_id),
            "actor": str(submission.intent.actor_id),
            "behavior": behavior_label,
            "behavior_id": behavior_id,
            "target": target,
            "scope": str(spec.get("scope") or ""),
        }
        self._context.services["events"].append("behavior.requested", submission.intent.actor_id, minute, {**event, "minute": minute})
        denial_event_id = self._context.services["events"].append(
            "behavior.denied",
            submission.intent.actor_id,
            minute,
            {
                **event,
                "rule_id": str(reason.get("rule_id") or ""),
                "reason": str(reason.get("type") or ""),
                "authority": dict(reason.get("authority") or {}),
                "minute": minute,
            },
        )
        self.store.repositories.memory.put_memory(
            str(submission.intent.actor_id),
            "denial",
            {
                "summary": f"{behavior_label} was denied: {reason.get('message') or reason.get('type')}",
                "event_id": denial_event_id,
                "behavior": behavior_label,
                "behavior_id": behavior_id,
                "rule": str(reason.get("rule_id") or ""),
                "reason": str(reason.get("type") or ""),
                "authority": dict(reason.get("authority") or {}),
                "target": target,
                "scope": str(spec.get("scope") or ""),
                "minute": int(minute),
            },
            tier="short_term",
        )

    def _validate_behavior_parameters(self, behavior_id: str, parameters: dict[str, Any]) -> list[str]:
        bt = next((item for item in self.store.repositories.entities.get_definitions("bt_def") if item.get("id") == behavior_id), None)
        if not bt:
            return ["behavior is not registered"]
        action_defs = {item["id"]: item for item in self.store.repositories.entities.get_definitions("action_def")}
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

    def _ensure_generic_travel_behavior(self) -> dict[str, Any] | None:
        """Register the player route affordance derived from ``travel_to``.

        The player projection exposes one generic travel choice for every known
        route.  A world may not ship a dedicated ``travel`` behavior tree (the
        action layer still owns route validation and duration), so create the
        minimal BT on demand from the world's own Action definition.
        """

        action_defs = {item["id"]: item for item in self.store.repositories.entities.get_definitions("action_def")}
        if "travel_to" not in action_defs:
            return None
        behavior = {
            "id": "travel",
            "agent_label": "Travel",
            "summary": "Travel",
            "description": "Move to a connected known location.",
            "entry_conditions": ["actor.alive"],
            "estimated_minutes": 1,
            "agent_parameters": {
                "location_id": {
                    "type": "string",
                    "required": True,
                    "max_length": 120,
                    "description": "A connected known location selected from the player's route list.",
                },
            },
            "nodes": [{
                "type": "action",
                "action_id": "travel_to",
                "parameters": {"location_id": "context.location_id"},
            }],
            "success_results": ["arrived"],
            "failure_results": ["route_unknown", "blocked"],
            "goal_tags": ["movement"],
            "drive_tags": ["autonomy"],
            "definition_version": 1,
            "status": "active",
        }
        with self.store.transaction():
            self.store.repositories.entities.put_definition("bt_def", "travel", behavior)
        return behavior

    def submit_behavior(self, intent: BehaviorIntent) -> dict[str, Any]:
        previous = self.store.repositories.evolution.get_intent(intent.intent_id)
        if previous is not None:
            return previous["result"] or {"status": previous["status"], "intent_id": intent.intent_id}
        try:
            agent = self.store.repositories.entities.get_agent(intent.actor_id)
        except KeyError:
            return {"status": "rejected", "message": "The actor does not exist.", "failed_check": "actor", "retryable": False}
        submission = self._open_submission(intent, agent)
        rejection = self._admit(submission)
        if rejection is not None:
            return self.reject_intent(
                intent.intent_id, intent.actor_id, intent.intent_type,
                submission.intent_data, rejection, submission=submission,
            )
        if submission.authorization_operation:
            return self._start_authorization_behavior(submission)
        return self._start_behavior(submission)

    def _open_submission(self, intent: BehaviorIntent, agent: dict[str, Any]) -> BehaviorSubmission:
        parameters = self._normalize_parameters(intent)
        intent_data = {
            "actor_id": intent.actor_id,
            "behavior_ref": intent.behavior_ref,
            "target": intent.target,
            "parameters": parameters,
            "reason": intent.reason,
            "confidence": intent.confidence,
            "observed_world_revision": intent.observed_world_revision,
            "observed_agent_revision": intent.observed_agent_revision,
        }
        return BehaviorSubmission(intent, agent, intent.behavior_ref.removeprefix("behavior:"), parameters, intent_data)

    def _admit(self, submission: BehaviorSubmission) -> dict[str, Any] | None:
        """Run the admission steps in their fixed order; the first rejection ends the submission.

        The order is part of the contract: each step may read what the one
        before it normalized, and the stored intent record shows the
        parameters as they were when the submission was rejected.
        """

        submission.world_minute = int(self.store.repositories.metadata.get("world_minute", 0))
        rejection = self._resolve_behavior(submission)
        if rejection is not None:
            return rejection
        self._apply_parameter_defaults(submission)
        for check in (self._check_available, self._check_parameters, self._check_actor_free):
            rejection = check(submission)
            if rejection is not None:
                return rejection
        submission.authorization_operation = self._context.services["authorization"].operation_of(submission.behavior)
        rejection = self._check_authorization(submission)
        if rejection is not None:
            return rejection
        submission.runtime_parameters = dict(submission.parameters)
        rejection = self._resolve_targets(submission)
        if rejection is not None:
            return rejection
        self._bind_runtime_parameters(submission)
        rejection = self._check_entry_conditions(submission)
        if rejection is not None:
            return rejection
        rejection = self._check_contested_feasibility(submission)
        if rejection is not None:
            return rejection
        for check in (self._check_cooldown, self._check_catalog):
            rejection = check(submission)
            if rejection is not None:
                return rejection
        submission.remaining_minutes = self._estimate_minutes(submission)
        self._record_revisions(submission)
        return self._check_agent_revision(submission)

    def _normalize_parameters(self, intent: BehaviorIntent) -> dict[str, Any]:
        """Fold target and destination aliases into the registered Action parameters."""

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
        # from ``target.location`` as display text, while navigation compares
        # stable entity IDs.  The behavior was accepted from a valid view and
        # then failed one tick later.
        for key in ("target_id", "patient_id", "protected_target_id", "counterparty_id", "listener_id", "location_id", "source_id", "object_id", "area_id"):
            if normalized_parameters.get(key):
                canonical = self.targets.canonical_id(normalized_parameters[key])
                if canonical:
                    normalized_parameters[key] = canonical
        return normalized_parameters

    def _resolve_behavior(self, submission: BehaviorSubmission) -> dict[str, Any] | None:
        """Load the registered behaviors; the player's generic travel behavior is created on demand."""

        behavior_id = submission.behavior_id
        available_defs = {x["id"]: x for x in self.store.repositories.entities.get_definitions("bt_def")}
        if behavior_id not in available_defs and behavior_id == "travel" and str(submission.agent.get("role") or "") == "player":
            registered = self._ensure_generic_travel_behavior()
            if registered is not None:
                available_defs[behavior_id] = registered
        submission.definitions = available_defs
        if behavior_id not in available_defs:
            return {
                "status": "rejected", "message": "The selected behavior is not registered.", "failed_check": "behavior_ref", "retryable": True,
            }
        submission.behavior = available_defs[behavior_id]
        return None

    @staticmethod
    def _apply_parameter_defaults(submission: BehaviorSubmission) -> None:
        # A package may provide an opening source as a default while the same
        # BT remains reusable for every later source created by Story/God.
        # Defaults are declarative BT metadata; the generic runtime does not
        # know any source name.
        for key, value in (submission.behavior.get("parameter_defaults", {}) or {}).items():
            if submission.parameters.get(str(key)) in (None, "", [], {}):
                submission.parameters[str(key)] = value

    def _check_available(self, submission: BehaviorSubmission) -> dict[str, Any] | None:
        agent, behavior_id = submission.agent, submission.behavior_id
        allowed = (
            set(agent.get("initial_bt_ids", []))
            | set(agent.get("unlocked_bt_ids", []))
            | set((self.store.repositories.metadata.get("rules", {}) or {}).get("fallback_behavior_ids", []))
        )
        if behavior_id == "travel" and str(agent.get("role") or "") == "player":
            # Route movement is a player affordance; the reachable routes were
            # already projected by build_player_view and are validated here by
            # travel_to's route.known / destination.reachable preconditions.
            allowed.add("travel")
        if behavior_id not in allowed:
            return {
                "status": "rejected", "message": "This behavior is not available to this character.", "failed_check": "capability", "retryable": True,
            }
        return None

    def _check_parameters(self, submission: BehaviorSubmission) -> dict[str, Any] | None:
        parameter_errors = self._validate_behavior_parameters(submission.behavior_id, submission.parameters)
        if parameter_errors:
            return {
                "status": "rejected", "message": "Behavior parameters must match the registered Action schema.", "failed_check": "parameters", "errors": parameter_errors, "retryable": True,
            }
        return None

    def _check_actor_free(self, submission: BehaviorSubmission) -> dict[str, Any] | None:
        """The actor must have no active behavior and no unrecovered failure block on this one.

        Reads the actor's state as the submission's working copy.
        """

        submission.state = state = dict(submission.agent.get("initial_state", {}))
        if state.get("status") in {"executing", "paused"}:
            return {
                "status": "rejected", "message": "The actor already has an active behavior; wait for its result or an interrupt.", "failed_check": "active_behavior", "retryable": False,
            }
        blocked = (state.get("blocked_behaviors") or {}).get(str(submission.behavior_id)) if isinstance(state, dict) else None
        if isinstance(blocked, dict) and not failure_block_recovered(self.store, blocked):
            return {
                "status": "rejected",
                "message": "This behavior is blocked after its last failed result; wait for a configured recovery event.",
                "failed_check": "failed_behavior_blocked",
                "retryable": True,
                "failure_result": blocked.get("result"),
            }
        return None

    def _check_authorization(self, submission: BehaviorSubmission) -> dict[str, Any] | None:
        """Re-evaluate authority against the current state, never a stale view."""

        service = self._context.services["authorization"]
        decision = str(submission.behavior.get("authorization_decision") or "") or None
        authorization = service.eligibility(
            submission.agent,
            submission.behavior,
            submission.parameters,
            decision=decision,
        )
        submission.authorization = authorization
        action_defs = {item["id"]: item for item in self.store.repositories.entities.get_definitions("action_def")}
        submission.withdrawal = self._resolve_effect_parameters(withdrawal_effect(submission.behavior, action_defs), submission.parameters)
        submission.take = self._resolve_effect_parameters(resource_take_effect(submission.behavior, action_defs, submission.definitions), submission.parameters)
        if authorization.get("gated") and not authorization.get("available"):
            reason = (authorization.get("reasons") or [{}])[0]
            return {
                "status": "rejected",
                "message": str(reason.get("message") or "Authorization is not available."),
                "failed_check": str(reason.get("type") or "missing_delegation"),
                "rule_id": str(reason.get("rule_id") or ""),
                "authority": dict(reason.get("authority") or {}),
                "retryable": True,
            }
        return None

    def _resolve_targets(self, submission: BehaviorSubmission) -> dict[str, Any] | None:
        """Check the supplied targets against the visible participants and fill the one that is implied."""

        intent, runtime_parameters = submission.intent, submission.runtime_parameters
        target_keys = self.view._target_parameter_keys(submission.behavior, submission.definitions)
        visible_participants = self.targets.visible_agents(submission.agent)
        supplied_target = any(runtime_parameters.get(key) for key in target_keys)
        if supplied_target:
            visible_ids = {str(item.get("id")) for item in visible_participants}
            hidden_targets = [
                (key, str(runtime_parameters.get(key)))
                for key in target_keys
                if runtime_parameters.get(key) and str(runtime_parameters.get(key)) not in visible_ids
            ]
            if hidden_targets:
                return {
                    "status": "rejected", "message": "Choose one exact visible participant from the current target list.", "failed_check": "target_visibility", "retryable": True,
                    "available_targets": [str(item.get("name", "participant")) for item in visible_participants],
                }
        if target_keys and not supplied_target:
            visible_name = next((intent.target.get(key) for key in ("name", "entity", "participant", "target") if isinstance(intent.target, dict) and intent.target.get(key)), None)
            if visible_name:
                canonical = self.targets.canonical_id(visible_name)
                if canonical is None or not any(str(item.get("id")) == str(canonical) for item in visible_participants):
                    return {
                        "status": "rejected", "message": "Choose one exact visible participant from the current target list.", "failed_check": "target_choice", "retryable": True,
                        "available_targets": [str(item.get("name", "participant")) for item in visible_participants],
                    }
                runtime_parameters[next(iter(target_keys))] = canonical
                submission.parameters[next(iter(target_keys))] = canonical
            elif len(visible_participants) > 1:
                return {
                    "status": "rejected", "message": "This behavior requires one explicit visible participant.", "failed_check": "target_required", "retryable": True,
                    "available_targets": [str(item.get("name", "participant")) for item in visible_participants],
                }
            elif len(visible_participants) == 1:
                runtime_parameters[next(iter(target_keys))] = visible_participants[0]["id"]
        return None

    def _bind_runtime_parameters(self, submission: BehaviorSubmission) -> None:
        """Add the parameters the behavior derives from the world, then record the merged set on the intent."""

        runtime_parameters, state = submission.runtime_parameters, submission.state
        for parameter, resolver in (submission.behavior.get("parameter_bindings", {}) or {}).items():
            resolver = str(resolver)
            if resolver == "nearest_safe_location":
                value = nearest_safe_location(self.store, state.get("location_id"))
            elif resolver in {"nearest_resource_node", "nearest_resource_tag"}:
                resource = nearest_resource_node(self.store, state.get("location_id"))
                value = (resource or {}).get("location_id" if resolver == "nearest_resource_node" else "resource_tag")
            elif resolver == "nearest_health_service":
                value = nearest_location_with_tag(self.store, state.get("location_id"), configured_location_tags(self.store, "health_service"))
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
                source = self.store.repositories.entities.get_entity(str(source_id))
            except KeyError:
                source = {}
            source_state = source.get("initial_state") if isinstance(source.get("initial_state"), dict) else {}
            source_location = source_state.get("location_id") or source.get("location_id")
            if source_location:
                runtime_parameters.setdefault("source_location_id", source_location)
        submission.intent_data["parameters"] = {**submission.parameters, **runtime_parameters}

    def _check_entry_conditions(self, submission: BehaviorSubmission) -> dict[str, Any] | None:
        intent = submission.intent
        entry_context = self._context.services["execution"].behavior_condition_context(submission.agent, submission.runtime_parameters, intent.target)
        entry_target = {**submission.runtime_parameters, **intent.target, **(entry_context.get("target") or {})}
        for predicate in submission.behavior.get("entry_conditions", []) or []:
            if not self.conditions.evaluate(
                str(predicate),
                agent=submission.agent,
                target=entry_target,
                context=entry_context,
                world=self.store.repositories.metadata.get("world_properties", {}) or {},
            ):
                return {
                    "status": "rejected",
                    "message": "The selected behavior is no longer executable in the current state.",
                    "failed_check": "entry_condition",
                    "condition": str(predicate),
                    "retryable": True,
                }
        return None

    def _check_contested_feasibility(self, submission: BehaviorSubmission) -> dict[str, Any] | None:
        """Reject a configured take only when the resource cannot supply it now.

        Authorization is deliberately absent from this check.  A take is a
        priced choice, not an unauthorized branch of the Phase 1 withdrawal.
        """

        if not submission.take:
            return None
        error = self._context.services["contested"].feasibility(submission.agent, submission.take)
        if error is None:
            return None
        return {
            "status": "rejected",
            "message": str(error.get("message") or "The take is not feasible."),
            "failed_check": str(error.get("failed_check") or "resource_empty"),
            "retryable": True,
        }

    def _check_cooldown(self, submission: BehaviorSubmission) -> dict[str, Any] | None:
        cooldown_minutes = max(0, int(submission.behavior.get("cooldown_minutes", 0) or 0))
        if cooldown_minutes:
            last_minute = self.store.repositories.activity.last_behavior_minute(submission.intent.actor_id, submission.behavior_id)
            if last_minute is not None and submission.world_minute - last_minute < cooldown_minutes:
                return {
                    "status": "rejected", "message": "This behavior is cooling down after its last completion.", "failed_check": "cooldown", "retryable": True,
                    "retry_after_minute": last_minute + cooldown_minutes,
                }
        return None

    def _check_catalog(self, submission: BehaviorSubmission) -> dict[str, Any] | None:
        # A choice made from an old view must be checked against the current
        # executable catalog before it can reserve anything. Explicit target
        # parameters are retained for player/API commands whose destination is
        # supplied outside the catalog; ordinary Agent choices have neither.
        # A model decision carries the revision of the view it used and must
        # be rechecked against the current executable catalog. A direct API
        # request may intentionally attempt a stale institution allocation so
        # the Action can return its typed failure for audit; all other direct
        # requests still receive the normal catalog check.
        intent, behavior_id = submission.intent, submission.behavior_id
        allocation_effect = any(
            any(str(effect.get("type") or effect.get("op") or "") == "institution_allocation" for effect in (next((item for item in self.store.repositories.entities.get_definitions("action_def") if item.get("id") == node.get("action_id")), {}).get("effects", []) or []))
            for node in self.view._iter_behavior_actions(behavior_id)
        )
        catalog_check_required = intent.observed_agent_revision is not None or (not intent.target and not intent.parameters)
        if catalog_check_required and not (intent.observed_agent_revision is None and allocation_effect):
            current_view = self.view.build_decision_view(intent.actor_id, role="agent_decision")
            attemptable = list(current_view.get("executable_behaviors", [])) + [
                item for item in (current_view.get("blocked_behaviors", []) or [])
                if item.get("attemptable", False)
            ]
            if behavior_id not in {str(item.get("ref", "")).removeprefix("behavior:") for item in attemptable}:
                return {
                    "status": "rejected", "message": "The selected behavior is no longer executable in the current state.", "failed_check": "behavior_unavailable", "retryable": True,
                }
        return None

    def _estimate_minutes(self, submission: BehaviorSubmission) -> int:
        agent, intent = submission.agent, submission.intent
        estimator = BehaviorDurationEstimator(
            {item["id"]: item for item in self.store.repositories.entities.get_definitions("action_def")},
            submission.definitions,
            {item["id"]: item for item in self.store.repositories.entities.get_definitions("recipe_def")},
            self._context.services["execution"].route_distance,
            capabilities=agent.get("capabilities", {}),
            capability_modifiers=(self.store.repositories.metadata.get("rules", {}) or {}).get("capability_modifiers", {}),
            travel_speed=lambda values: movement_speed(self.store, agent, values),
        )
        return estimator.behavior_minutes(submission.behavior_id, {**submission.runtime_parameters, **intent.target}, submission.state.get("location_id"))

    def _record_revisions(self, submission: BehaviorSubmission) -> None:
        intent = submission.intent
        submission.world_revision = int(self.store.repositories.metadata.get("world_revision", 0) or 0)
        submission.agent_revision = int(submission.agent.get("agent_revision", 0) or 0)
        submission.stale_observation = (
            intent.observed_world_revision is not None and int(intent.observed_world_revision) != submission.world_revision
        ) or (
            intent.observed_agent_revision is not None and int(intent.observed_agent_revision) != submission.agent_revision
        )

    @staticmethod
    def _check_agent_revision(submission: BehaviorSubmission) -> dict[str, Any] | None:
        # The character projection is a strong consistency boundary: another
        # accepted intent for this character may have changed location,
        # inventory, or an active activity after the model read its view.
        # Re-evaluate instead of reserving resources from that stale view.
        # World revisions are intentionally weak here because unrelated
        # characters may advance in the same minute; all configured entry
        # conditions and reservations are still checked against current state.
        intent = submission.intent
        if (
            intent.observed_agent_revision is not None
            and int(intent.observed_agent_revision) != submission.agent_revision
        ):
            return {
                "status": "rejected",
                "message": "This character changed after the decision view was read; refresh the current behavior list and choose again.",
                "failed_check": "stale_agent_revision",
                "retryable": True,
                "observed_agent_revision": int(intent.observed_agent_revision),
                "current_agent_revision": submission.agent_revision,
                "world_revision": submission.world_revision,
            }
        return None

    def _resolve_effect_parameters(self, effect: dict[str, Any] | None, parameters: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(effect, dict):
            return effect
        resolved = {
            key: parameters.get(value.removeprefix("context."), value)
            if isinstance(value, str) and value.startswith("context.") else value
            for key, value in effect.items()
        }
        if resolved.get("resource_id"):
            canonical = self._dependency("targets").canonical_id(str(resolved["resource_id"]))
            if canonical:
                resolved["resource_id"] = str(canonical)
        return resolved

    @staticmethod
    def _authorization_target(submission: BehaviorSubmission) -> str:
        if submission.withdrawal:
            return str(submission.withdrawal.get("resource_id") or "")
        request = submission.authorization.get("request") or {}
        return str(request.get("target") or submission.parameters.get("target") or "")

    def _start_authorization_behavior(self, submission: BehaviorSubmission) -> dict[str, Any]:
        """Execute one instant administrative decision in the admission commit.

        Approval, rejection and revocation are decisions, not physical work.
        Their request/delegation/event writes commit in the same transaction as
        the behavior envelope, so a retry cannot observe a half-applied
        institutional result.
        """

        intent = submission.intent
        service = self._context.services["authorization"]
        minute = submission.world_minute
        operation = str(submission.authorization_operation)
        try:
            with self.store.transaction():
                self.store.repositories.evolution.create_intent(
                    intent.intent_id, intent.actor_id, intent.intent_type, submission.intent_data, minute,
                )
                self._mark_executing(submission)
                self._open_bt_instance(submission, self._expected_outcome(submission))
                if submission.authorization.get("gated"):
                    self._context.services["events"].append(
                        "behavior.requested",
                        intent.actor_id,
                        minute,
                        {
                            "intent_id": intent.intent_id,
                            "actor": intent.actor_id,
                            "behavior": str(submission.behavior.get("agent_label") or submission.behavior_id),
                            "behavior_id": submission.behavior_id,
                            "target": self._authorization_target(submission),
                            "minute": minute,
                        },
                    )
                self._context.services["events"].append(
                    "behavior.started",
                    intent.actor_id,
                    minute,
                    {
                        "intent_id": intent.intent_id,
                        "behavior_id": submission.behavior_id,
                        "target": intent.target,
                        "expected": self._expected_outcome(submission),
                        "decision": {"reason": intent.reason, "confidence": intent.confidence},
                    },
                )
                domain = self._execute_authorization_operation(submission, operation, service, minute)
                state = dict(submission.agent.get("initial_state") or {})
                state.update({
                    "status": "idle",
                    "active_bt_instance": None,
                    "active_behavior_id": None,
                    "active_action": None,
                    "active_action_instance": None,
                    "active_parameters": {},
                    "active_action_index": 0,
                    "action_progress": 0.0,
                    "active_action_duration": 0,
                    "remaining_minutes": 0,
                    "last_platform_result": {
                        "status": "completed",
                        "behavior_ref": intent.behavior_ref,
                        "result": "authorization_completed",
                        "authorization_result": domain,
                    },
                })
                submission.agent["initial_state"] = state
                self.store.repositories.entities.update_agent(intent.actor_id, submission.agent)
                self.store.repositories.activity.update_bt_instance(
                    intent.intent_id, status="completed", current_node=0,
                    data={"domain_result": domain}, updated_minute=minute,
                )
                self._context.services["events"].append(
                    "behavior.completed",
                    intent.actor_id,
                    minute,
                    {
                        "execution_id": intent.intent_id,
                        "behavior_id": submission.behavior_id,
                        "result": "authorization_completed",
                        "domain_result": domain,
                    },
                )
                result = {
                    "status": "accepted",
                    "intent_id": intent.intent_id,
                    "execution_id": intent.intent_id,
                    "behavior_ref": intent.behavior_ref,
                    "remaining_minutes": 0,
                    "authorization_result": domain,
                    **{key: value for key, value in domain.items() if key in {"request_id", "delegation_id"}},
                }
                self.store.repositories.evolution.finish_intent(intent.intent_id, "accepted", result)
                return result
        except AuthorizationRejected as exc:
            return self.reject_intent(
                intent.intent_id, intent.actor_id, intent.intent_type, submission.intent_data, exc.result(),
            )
        except ContestedRejected as exc:
            return self.reject_intent(
                intent.intent_id, intent.actor_id, intent.intent_type, submission.intent_data, exc.result(),
            )

    def _execute_authorization_operation(
        self,
        submission: BehaviorSubmission,
        operation: str,
        service: Any,
        minute: int,
    ) -> dict[str, Any]:
        parameters = {**submission.parameters, **submission.runtime_parameters}
        agent = submission.agent
        if operation == "open_access_request":
            created = service.create_request(
                agent,
                {
                    "request_id": parameters.get("request_id"),
                    "behavior": parameters.get("behavior") or parameters.get("denied_behavior"),
                    "behavior_id": parameters.get("behavior_id"),
                    "target": parameters.get("target") or parameters.get("target_id"),
                    "scope": parameters.get("scope"),
                    "reason": parameters.get("reason") or submission.intent.reason,
                    "based_on_event": parameters.get("based_on_event") or parameters.get("denial_event"),
                },
                minute=minute,
                validate_causal=True,
            )
            request = created["request"]
            return {"request_id": request["request_id"], "status": request["status"], "created": created["created"]}
        if operation == "decide_access_request":
            request = submission.authorization.get("request")
            if not request:
                raise AuthorizationRejected("request_not_found", "No pending access request is available to decide.")
            decision = str(submission.behavior.get("authorization_decision") or "approve")
            if decision == "approve":
                decided = service.approve(
                    agent, request, minute=minute,
                    delegation_id=parameters.get("delegation_id"),
                )
                return {
                    "request_id": decided["request"]["request_id"],
                    "delegation_id": decided["delegation"]["delegation_id"],
                    "status": decided["request"]["status"],
                }
            decided = service.reject(
                agent, request,
                reason=str(parameters.get("reason") or submission.intent.reason or "rejected"),
                minute=minute,
            )
            return {"request_id": decided["request"]["request_id"], "status": decided["request"]["status"]}
        if operation == "revoke_access":
            delegation = submission.authorization.get("delegation")
            if not delegation:
                raise AuthorizationRejected("delegation_not_found", "No active delegation is available to revoke.")
            decided = service.revoke(
                agent, delegation,
                reason=str(parameters.get("reason") or submission.intent.reason or "revoked"),
                minute=minute,
            )
            return {"delegation_id": decided["delegation"]["delegation_id"], "status": decided["delegation"]["status"]}
        if operation == "review_violation":
            violation = submission.authorization.get("violation")
            if not violation:
                raise AuthorizationRejected("violation_not_found", "No violation is available to review.")
            decision = str(parameters.get("decision") or "sanction").strip().casefold()
            return self._context.services["contested"].review_violation(
                agent,
                violation,
                decision,
                reason=str(parameters.get("reason") or submission.intent.reason or decision),
                minute=minute,
            )
        raise AuthorizationRejected("unsupported_authorization_operation", f"Unsupported authorization operation: {operation}")

    def _start_behavior(self, submission: BehaviorSubmission) -> dict[str, Any]:
        """Open the activity: intent, actor state, BT instance and ``behavior.started`` commit together."""

        intent = submission.intent
        expected = self._expected_outcome(submission)
        with self.store.transaction():
            self.store.repositories.evolution.create_intent(intent.intent_id, intent.actor_id, intent.intent_type, submission.intent_data, submission.world_minute)
            self._mark_executing(submission)
            self._open_bt_instance(submission, expected)
            if submission.authorization.get("gated"):
                self._context.services["events"].append(
                    "behavior.requested",
                    intent.actor_id,
                    submission.world_minute,
                    {
                        "intent_id": intent.intent_id,
                        "actor": intent.actor_id,
                        "behavior": str(submission.behavior.get("agent_label") or submission.behavior_id),
                        "behavior_id": submission.behavior_id,
                        "target": self._authorization_target(submission),
                        "minute": submission.world_minute,
                    },
                )
            if submission.withdrawal:
                submission.state["active_resource_withdrawal"] = dict(submission.withdrawal)
                submission.state["active_resource_authorization"] = submission.authorization.get("authorization")
                submission.agent["initial_state"] = submission.state
                self.store.repositories.entities.update_agent(intent.actor_id, submission.agent)
                self._context.services["events"].append(
                    "resource.withdrawal.started",
                    intent.actor_id,
                    submission.world_minute,
                    {
                        "intent_id": intent.intent_id,
                        "actor": intent.actor_id,
                        "target": str(submission.withdrawal.get("resource_id") or ""),
                        "authorization": submission.authorization.get("authorization"),
                        "minute": submission.world_minute,
                    },
                )
            if submission.take:
                submission.state["active_resource_take"] = dict(submission.take)
                submission.agent["initial_state"] = submission.state
                self.store.repositories.entities.update_agent(intent.actor_id, submission.agent)
            self._context.services["events"].append(
                "behavior.started", intent.actor_id, submission.world_minute,
                {"intent_id": intent.intent_id, "behavior_id": submission.behavior_id, "target": intent.target, "expected": expected, "decision": {"reason": intent.reason, "confidence": intent.confidence}},
            )
            result = {
                "status": "accepted",
                "intent_id": intent.intent_id,
                "execution_id": intent.intent_id,
                "behavior_ref": intent.behavior_ref,
                "remaining_minutes": submission.remaining_minutes,
                "stale_observation": submission.stale_observation,
                "world_revision": submission.world_revision,
                "agent_revision": submission.agent_revision,
            }
            self.store.repositories.evolution.finish_intent(intent.intent_id, "accepted", result)
        return result

    @staticmethod
    def _expected_outcome(submission: BehaviorSubmission) -> dict[str, Any]:
        behavior = submission.behavior
        return {
            "summary": behavior.get("summary") or behavior.get("description") or submission.behavior_id,
            "success": list(behavior.get("success_results", []) or []),
            "failure": list(behavior.get("failure_results", []) or []),
            "estimated_minutes": submission.remaining_minutes,
            "goal_tags": list(behavior.get("goal_tags", []) or []),
            "drive_tags": list(behavior.get("drive_tags", []) or []),
            "behavior_version": int(behavior.get("definition_version", behavior.get("version", 1)) or 1),
        }

    def _open_bt_instance(self, submission: BehaviorSubmission, expected: dict[str, Any]) -> None:
        intent = submission.intent
        self.store.repositories.activity.put_bt_instance(
            intent.intent_id,
            intent.actor_id,
            submission.behavior_id,
            "executing",
            0,
            {
                "target": intent.target,
                "parameters": submission.runtime_parameters,
                "actions": self._context.services["execution"].compile_behavior_actions(submission.behavior_id),
                "behavior_version": expected["behavior_version"],
                "expected": expected,
                "decision": {"reason": intent.reason, "confidence": intent.confidence},
                "before": self._context.services["execution"].execution_snapshot(submission.agent),
            },
            submission.world_minute,
            submission.world_minute,
        )

    def _mark_executing(self, submission: BehaviorSubmission) -> None:
        intent, state = submission.intent, submission.state
        state["status"] = "executing"
        state["active_bt_instance"] = intent.intent_id
        state["active_behavior_id"] = submission.behavior_id
        state["active_action"] = None
        state["remaining_minutes"] = submission.remaining_minutes
        state["active_action_index"] = 0
        state["active_parameters"] = {**submission.runtime_parameters, **intent.target}
        state["last_platform_result"] = {"status": "accepted", "behavior_ref": intent.behavior_ref}
        submission.agent["initial_state"] = state
        self.store.repositories.entities.update_agent(intent.actor_id, submission.agent)
