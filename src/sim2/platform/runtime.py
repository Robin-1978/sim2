from __future__ import annotations

import json
import re
import sqlite3
import copy
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ..actions.conditions import ConditionEngine
from ..actions.capacity import check_action_capacity
from ..actions.effects import EffectPipeline
from ..actions.targets import TargetResolver
from ..actions.resources import ResourceLedger
from ..agent_view.compiler import AgentViewCompiler
from ..behavior.estimator import BehaviorDurationEstimator
from ..intents.models import BehaviorIntent
from ..memory.service import MemoryService
from ..perception.service import PerceptionService
from ..persistence.store import WorldStore
from ..world.navigation import configured_location_tags, nearest_location_with_tag, nearest_resource_node, nearest_safe_location, reachable_locations
from ..world.rules import RuleCompiler
from ..world.schema import AttributeSchema
from ..evolution import EventWakeScheduler, WorldProposalEvaluator
from .activity import ActivityRuntimeMixin


class Platform(ActivityRuntimeMixin):
    """The only component allowed to commit world state changes.

    Agent sessions may read through tools and submit intents, but they never
    receive a store handle.  This boundary keeps LLM output declarative and
    makes accepted and rejected proposals auditable in one transaction.
    """

    def __init__(self, db_path: Path):
        self.store = WorldStore(db_path)
        self.view = AgentViewCompiler(self.store)
        self.perception = PerceptionService(self.store)
        self.memory = MemoryService(self.store)
        self.attribute_schema = AttributeSchema(self.store.get_meta("attributes", []) or [], self.store.get_meta("rules", {}) or {})
        self.conditions = ConditionEngine((self.store.get_meta("rules", {}) or {}))
        self.rule_compiler = self._new_rule_compiler()
        self.effects = EffectPipeline(self.store.get_meta("attributes", []) or [])
        self.targets = TargetResolver(self.store)
        self.resources = ResourceLedger(self.store)
        self.event_wakes = EventWakeScheduler(self.store)
        self.proposal_evaluator = WorldProposalEvaluator(self.store, self.targets)

    def close(self) -> None:
        self.store.close()

    def _new_rule_compiler(self) -> RuleCompiler:
        rules = self.store.get_meta("rules", {}) or {}
        aliases = {}
        for key in (self.store.get_meta("world_properties", {}) or {}):
            aliases[str(key)] = str(key)
            aliases[str(key).replace("_", " ")] = str(key)
        for entity_type in ("resource", "material", "location", "object", "faction", "agent"):
            for item in self.store.entities_of_type(entity_type):
                identifier, visible = str(item.get("id") or ""), str(item.get("name") or "")
                if identifier and visible:
                    aliases[visible] = identifier
        aliases.update({str(key): str(value) for key, value in (rules.get("condition_aliases", {}) or {}).items()})
        return RuleCompiler(
            rules,
            self.conditions,
            aliases,
            known_behaviors=list(self.store.get_definitions("bt_def")),
            known_actions=list(self.store.get_definitions("action_def")),
            world_keys=set((self.store.get_meta("world_properties", {}) or {}).keys()),
        )

    def run(
        self,
        minutes: int,
        *,
        providers: dict[str, Any] | None = None,
        provider_name: str | None = None,
        provider_model: str | None = None,
        wait_cooldown_minutes: int = 30,
        llm_error_cooldown_minutes: int = 60,
        turn_observer: Callable[[Any, str], None] | None = None,
    ) -> Any:
        """Run the autonomous world loop owned by Platform.

        ``step`` remains the single-tick manual/test primitive. Normal CLI,
        service, and daemon callers use this method so scheduling, Agent
        wakeups, interactions, and evolution stay inside the runtime.
        """

        from ..llm.gateway import LLMRouter
        from ..simulation.runner import SimulationRunner

        if providers is None:
            router = LLMRouter.from_default_config()
            if provider_name:
                providers = {
                    role: router.new_provider_for_name(provider_name, provider_model)
                    for role in ("agent_decision", "agent_reflection", "god", "story")
                }
            else:
                providers = {
                    "agent_decision": router.provider_for_role("agent_decision"),
                    "agent_reflection": router.provider_for_role("agent_reflection"),
                    "god": router.provider_for_role("god"),
                    "story": router.provider_for_role("story"),
                }
        return SimulationRunner(
            self,
            providers,
            wait_cooldown_minutes=wait_cooldown_minutes,
            llm_error_cooldown_minutes=llm_error_cooldown_minutes,
            turn_observer=turn_observer,
        ).run(minutes)

    def status(self) -> dict[str, Any]:
        return {
            "status": self.store.get_meta("runtime_status", "running"),
            "world_minute": int(self.store.get_meta("world_minute", 0)),
            "instance_id": self.store.get_meta("instance_id"),
        }

    def health(self) -> dict[str, Any]:
        """Return a read-only liveness check for external process probes."""

        database = str(self.store.connection.execute("PRAGMA quick_check").fetchone()[0])
        return {
            "status": "ok" if database == "ok" else "degraded",
            "database": database,
            "runtime": self.store.get_meta("runtime_status", "running"),
            "world_minute": int(self.store.get_meta("world_minute", 0)),
            "instance_id": self.store.get_meta("instance_id"),
            "memory_retrieval": self.memory.status(),
        }

    def pause(self, reason: str = "operator") -> dict[str, Any]:
        with self.store.transaction():
            self.store.set_meta("runtime_status", "paused")
            minute = int(self.store.get_meta("world_minute", 0))
            self.store.append_event("runtime.paused", None, minute, {"reason": reason})
        return self.status()

    def resume(self, reason: str = "operator") -> dict[str, Any]:
        with self.store.transaction():
            self.store.set_meta("runtime_status", "running")
            minute = int(self.store.get_meta("world_minute", 0))
            self.store.append_event("runtime.resumed", None, minute, {"reason": reason})
        return self.status()

    def inject_event(self, event_type: str, payload: dict[str, Any], actor_id: str | None = None) -> dict[str, Any]:
        if not event_type.strip():
            raise ValueError("event_type is required")
        minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            event_id = self.store.append_event(event_type, actor_id, minute, payload)
        return {"status": "accepted", "event_id": event_id, "world_minute": minute}

    def enter_player(self, player_id: str, origin_id: str, *, name: str | None = None) -> dict[str, Any]:
        """Instantiate a player from the package's generic entry template."""

        if not player_id.strip() or not origin_id.strip():
            raise ValueError("player_id and origin_id are required")
        try:
            self.store.get_entity(player_id)
            return {"status": "rejected", "message": "The player id already exists.", "failed_check": "player_id"}
        except KeyError:
            pass
        template = self.store.get_meta("player_template", {}).get("player", {})
        origin = next((item for item in template.get("origin_choices", []) if item.get("id") == origin_id), None)
        if origin is None:
            return {"status": "rejected", "message": "The selected origin is not configured.", "failed_check": "origin_id", "retryable": True}
        location_id = template.get("entry_location_id")
        try:
            self.store.get_entity(location_id)
        except KeyError:
            return {"status": "rejected", "message": "The configured player entry location does not exist.", "failed_check": "entry_location_id", "retryable": False}
        state_defaults = self.attribute_schema.state_defaults()
        state_defaults.update({"location_id": location_id, "status": "idle", "injuries": [], "conditions": [], "active_bt_instance": None, "active_action": None, "remaining_minutes": 0})
        needs = self.attribute_schema.need_defaults()
        for item in ((self.store.get_meta("rules", {}) or {}).get("needs", []) or []):
            if isinstance(item, dict) and item.get("id"):
                needs.setdefault(str(item["id"]), float(item.get("target", 100)))
        agent = {
            "id": player_id,
            "name": name or player_id,
            "role": "player",
            "traits": dict(origin.get("traits") or {}),
            "ultimate_goal": origin.get("starting_goal", "Find a meaningful place in this world."),
            "beliefs": [],
            "needs": needs,
            "knowledge_seed": list(template.get("knowledge_seed") or []),
            "initial_bt_ids": list(template.get("initial_bt_ids") or []),
            "capabilities": {},
            "initial_state": state_defaults,
            "inventory": {},
        }
        minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            self.store.put_agent(player_id, agent, agent["name"])
            for fact in agent["knowledge_seed"]:
                self.store.put_knowledge_fact(player_id, str(fact), {"fact": str(fact), "source_type": "player_template", "confidence": 1.0})
            self.store.append_event("player.entered", player_id, minute, {"origin_id": origin_id, "location_id": location_id})
        return {"status": "accepted", "player_id": player_id, "origin_id": origin_id, "location_id": location_id}

    def decision_view(self, agent_id: str, role: str = "agent_decision") -> dict[str, Any]:
        return self.view.build_decision_view(agent_id, role=role)

    def observer_view(self, *, event_limit: int = 20) -> dict[str, Any]:
        """Return public whole-world telemetry without private memories."""

        agents = []
        for agent in self.store.list_agents():
            state = agent.get("initial_state", {}) or {}
            agents.append({
                "id": agent["id"],
                "name": agent.get("name", agent["id"]),
                "role": agent.get("role", "agent"),
                "location": state.get("location_id"),
                "status": state.get("status", "idle"),
                "attributes": self.attribute_schema.values(state, agent.get("needs", {}) or {}),
                "active_behavior": state.get("active_behavior_id"),
                "remaining_minutes": state.get("remaining_minutes", 0),
            })
        public_events = []
        for event in self.store.recent_events(limit=max(1, min(int(event_limit), 100))):
            public = {key: event.get(key) for key in ("event_id", "event_type", "actor_id", "world_minute")}
            for key in ("summary", "behavior_id", "action_id", "result", "change_type", "effect", "activation"):
                if event.get(key) is not None:
                    public[key] = event[key]
            public_events.append(public)
        return {
            "status": "ok",
            "world_time": int(self.store.get_meta("world_minute", 0)),
            "world_id": self.store.get_meta("world_id"),
            "world_properties": self.store.get_meta("world_properties", {}) or {},
            "agents": agents,
            "entity_counts": {entity_type: len(self.store.entities_of_type(entity_type)) for entity_type in ("location", "agent", "faction", "resource", "object")},
            "recent_events": public_events,
            "private_data": "excluded",
        }

    def timeline(self, *, limit: int = 100, after_event_id: int = 0, include_ticks: bool = False) -> dict[str, Any]:
        """Return a public, chronological third-person event projection."""

        events = self.store.timeline_events(after_event_id=after_event_id, limit=limit, include_ticks=include_ticks)
        names: dict[str, str] = {}
        for event in events:
            for key in ("actor_id", "target_id", "recipient_id", "offerer_id", "speaker_id", "listener_id", "location_id"):
                value = event.get(key)
                if value and str(value) not in names:
                    try:
                        names[str(value)] = str(self.store.get_entity(str(value)).get("name", value))
                    except KeyError:
                        names[str(value)] = str(value)
        rendered = []
        for event in events:
            actor = names.get(str(event.get("actor_id")), "The world") if event.get("actor_id") else "The world"
            event_type = str(event.get("event_type", "world.event"))
            target = next((names.get(str(event.get(key))) for key in ("target_id", "recipient_id", "listener_id") if event.get(key)), None)
            if event_type == "behavior.started":
                sentence = f"{actor} began {event.get('expected', {}).get('summary', 'a behavior')}."
            elif event_type == "behavior.completed":
                sentence = f"{actor} completed a behavior: {event.get('result') or event.get('outcome', {}).get('result') or 'the expected result'}."
            elif event_type in {"behavior.failed", "behavior.interrupted"}:
                sentence = f"{actor}'s behavior {event_type.removeprefix('behavior.')} ({event.get('result') or 'without a stated result'})."
            elif event_type == "action.started":
                sentence = f"{actor} started the action {str(event.get('action_id', 'an action')).replace('_', ' ')}."
            elif event_type == "action.completed":
                sentence = f"{actor} completed the action {str(event.get('action_id', 'an action')).replace('_', ' ')}."
            elif event_type == "dialogue.completed":
                sentence = f"{actor} exchanged information{f' with {target}' if target else ''}."
            elif event_type == "trade.offer_created":
                sentence = f"{actor} offered a trade{f' to {target}' if target else ''}."
            elif event_type == "trade.completed":
                sentence = f"{actor} completed a trade{f' with {target}' if target else ''}."
            elif event_type.startswith("world.change."):
                summary = str(event.get("summary") or event_type.removeprefix("world.change.").replace("_", " ")).rstrip(".")
                sentence = f"The world changed: {summary}."
            elif event_type == "world.created":
                sentence = "The world was created from its configured opening state."
            elif event_type == "player.entered":
                sentence = f"{actor} entered the world."
            else:
                summary = str(event.get("summary") or event.get("result") or event_type.replace(".", " ").replace("_", " ")).rstrip(".")
                sentence = f"{actor}: {summary}."
            rendered.append({"event_id": event["event_id"], "world_minute": event["world_minute"], "event_type": event_type, "narrative": sentence, "public": {key: value for key, value in event.items() if key not in {"actor_id", "target_id", "recipient_id", "offerer_id", "speaker_id", "listener_id", "location_id"}}})
        return {"status": "ok", "world_id": self.store.get_meta("world_id"), "world_minute": int(self.store.get_meta("world_minute", 0)), "events": rendered, "next_after_event_id": events[-1]["event_id"] if events else after_event_id}

    def _reject_intent(self, intent_id: str, actor_id: str, intent_type: str, data: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        world_minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, intent_type, data, world_minute)
            self.store.finish_intent(intent_id, "rejected", result)
        return result

    def _validate_behavior_parameters(self, behavior_id: str, parameters: dict[str, Any]) -> list[str]:
        if not parameters:
            return []
        bt = next((item for item in self.store.get_definitions("bt_def") if item.get("id") == behavior_id), None)
        if not bt:
            return ["behavior is not registered"]
        action_defs = {item["id"]: item for item in self.store.get_definitions("action_def")}
        allowed: set[str] = set()
        for node in self.view._iter_behavior_actions(behavior_id):
            action = action_defs.get(node.get("action_id"), {})
            allowed.update((action.get("parameters") or {}).keys())
            allowed.update((node.get("parameters") or {}).keys())
        return [f"unknown behavior parameter: {key}" for key in parameters if key not in allowed]

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
        for key in ("target_id", "patient_id", "protected_target_id", "counterparty_id", "listener_id", "location_id"):
            if normalized_parameters.get(key):
                canonical = self.targets.canonical_id(normalized_parameters[key])
                if canonical:
                    normalized_parameters[key] = canonical
        for target_alias in ("entity_id", "id"):
            if target_alias in intent.target and "target_id" not in normalized_parameters:
                normalized_parameters["target_id"] = intent.target[target_alias]
        for alias in ("destination", "destination_id", "location"):
            if alias in normalized_parameters and "location_id" not in normalized_parameters:
                normalized_parameters["location_id"] = normalized_parameters[alias]
            normalized_parameters.pop(alias, None)
            if alias in intent.target and "location_id" not in normalized_parameters:
                normalized_parameters["location_id"] = intent.target[alias]
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
        if not intent.target and not intent.parameters:
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
            self.store.append_event(
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
        return {
            "location_id": location_id,
            "destination_id": parameters.get("location_id") or parameters.get("destination_id"),
            "area_id": parameters.get("area_id"),
            "item_id": parameters.get("item_id"),
            "entity_id": parameters.get("entity_id"),
            "object_id": parameters.get("object_id"),
            "inventory": agent.get("inventory", {}) or {},
            "food_items": (self.store.get_meta("rules", {}) or {}).get("food_items", []) or [],
            "satiety_alert_threshold": float((self.store.get_meta("rules", {}) or {}).get("satiety_alert_threshold", 40) or 40),
            "current_location": location,
            "current_object": self._entity_for_context(parameters.get("object_id")),
            "mapped_areas": self.store.explored_areas(agent["id"]),
            "researched_sources": self.store.researched_sources(agent["id"]),
            "target": resolved_target or {},
            "target.visible": bool((resolved_target or {}).get("visible", False)),
            "target.available": bool((resolved_target or {}).get("available", False)),
            "known_route_destinations": reachable_locations(self.store, location_id),
            "recent_event_types": self._recent_event_types(),
            "conflict": {"available": conflict_available},
        }

    def _entity_for_context(self, entity_id: Any) -> dict[str, Any]:
        """Return a configured entity for a declarative condition context."""

        if not entity_id:
            return {}
        try:
            return self.store.get_entity(str(entity_id))
        except KeyError:
            return {}

    def submit_experience(self, actor_id: str, event_ref: str, summary: str, confidence: float, claim_status: str = "inferred") -> dict[str, Any]:
        summary = str(summary).strip()
        claim_status = str(claim_status or "inferred").strip().casefold()
        data = {"event_ref": event_ref, "summary": summary, "confidence": confidence, "claim_status": claim_status}
        intent_id = str(uuid4())
        if not summary.strip():
            return self._reject_intent(intent_id, actor_id, "record_experience", data, {"status": "rejected", "message": "Experience summary is required.", "failed_check": "summary", "retryable": True})
        if len(summary) > 500:
            return self._reject_intent(intent_id, actor_id, "record_experience", data, {"status": "rejected", "message": "Experience summary must be at most 500 characters.", "failed_check": "summary_length", "retryable": True})
        if not 0 <= confidence <= 1:
            return self._reject_intent(intent_id, actor_id, "record_experience", data, {"status": "rejected", "message": "Confidence must be between 0 and 1.", "failed_check": "confidence", "retryable": True})
        if claim_status not in {"observed", "confirmed", "promised", "inferred", "failed"}:
            return self._reject_intent(intent_id, actor_id, "record_experience", data, {"status": "rejected", "message": "claim_status must be observed, confirmed, promised, inferred, or failed.", "failed_check": "claim_status", "retryable": True})
        world_minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            agent = self.store.get_agent(actor_id)
            state = agent.get("initial_state", {}) or {}
            location_name = ""
            try:
                location_name = str(self.store.get_entity(str(state.get("location_id"))).get("name", "")) if state.get("location_id") else ""
            except KeyError:
                location_name = ""
            data.update({
                "location": location_name,
                "goal": str(agent.get("ultimate_goal", "")),
                "goal_tags": list(agent.get("goal_tags", []) or []),
                "behavior": str(event_ref or ""),
            })
            source_event = self._resolve_experience_source(actor_id, event_ref)
            if source_event:
                data.update({
                    "source_event_id": int(source_event["event_id"]),
                    "source_event_type": str(source_event["event_type"]),
                    "source_world_minute": int(source_event["world_minute"]),
                })
            elif claim_status in {"observed", "confirmed", "failed"}:
                result = {
                    "status": "rejected",
                    "message": "Observed, confirmed, and failed lessons must cite a recorded event or completed outcome. Use inferred for a conclusion or promised for an unverified commitment.",
                    "failed_check": "claim_evidence",
                    "retryable": True,
                }
                self.store.create_intent(intent_id, actor_id, "record_experience", data, world_minute)
                self.store.finish_intent(intent_id, "rejected", result)
                return result
            similarity = self.memory.experience_match(actor_id, summary)
            if similarity["duplicate"]:
                result = {
                    "status": "rejected",
                    "message": "This experience is too similar to a retained lesson; return wait or submit a genuinely new lesson.",
                    "failed_check": "similar_experience",
                    "similarity": similarity,
                    "retryable": True,
                }
                self.store.create_intent(intent_id, actor_id, "record_experience", data, world_minute)
                self.store.finish_intent(intent_id, "rejected", result)
                return result
            duplicate = self.store.connection.execute(
                "SELECT 1 FROM memory_item WHERE agent_id=? AND memory_type='experience' AND json_extract(data_json,'$.summary')=? LIMIT 1",
                (actor_id, summary.strip()),
            ).fetchone()
            if duplicate is not None:
                result = {"status": "rejected", "message": "This experience is already retained; return wait or submit a genuinely new lesson.", "failed_check": "duplicate_experience", "retryable": True}
                self.store.create_intent(intent_id, actor_id, "record_experience", data, world_minute)
                self.store.finish_intent(intent_id, "rejected", result)
                return result
            self.store.create_intent(intent_id, actor_id, "record_experience", data, world_minute)
            memory_id = self.store.put_memory(actor_id, "experience", {**data, "world_minute": world_minute})
            self.store.append_event("memory.experience_recorded", actor_id, world_minute, {"memory_id": memory_id, **data})
            result = {"status": "accepted", "intent_id": intent_id, "memory_id": memory_id}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def _resolve_experience_source(self, actor_id: str, event_ref: str) -> dict[str, Any] | None:
        """Resolve a short model-facing event reference to one real event."""

        reference = str(event_ref or "").strip().casefold()
        if not reference:
            return None
        rows = self.store.recent_events(limit=500)
        for event in rows:
            if str(event.get("actor_id") or "") != str(actor_id) and str(event.get("target_id") or "") != str(actor_id):
                # Public world events may have no actor; keep them eligible.
                if event.get("actor_id") not in {None, ""}:
                    continue
            event_id = str(event.get("event_id") or "")
            event_type = str(event.get("event_type") or "").casefold()
            behavior_id = str(event.get("behavior_id") or "").casefold()
            if reference in {event_id, f"event:{event_id}"} or reference == event_type or event_type in reference or (behavior_id and behavior_id in reference):
                return event
        return None

    def submit_capability_update(self, actor_id: str, capability: str, delta: float, evidence: str) -> dict[str, Any]:
        """Apply a personal capability change only after Agent evidence review."""

        intent_id = str(uuid4())
        capability = str(capability).strip()
        evidence = str(evidence).strip()
        data = {"capability": capability, "delta": delta, "evidence": evidence}
        definitions = {str(item.get("id")): item for item in self.store.get_definitions("capability_def")}
        if capability not in definitions:
            return self._reject_intent(intent_id, actor_id, "update_capability", data, {"status": "rejected", "message": "Capability is not configured.", "failed_check": "capability", "retryable": True})
        if not 0 < float(delta) <= 0.2 or len(evidence) < 12:
            return self._reject_intent(intent_id, actor_id, "update_capability", data, {"status": "rejected", "message": "Capability delta or evidence is invalid.", "failed_check": "evidence", "retryable": True})
        rules = self.store.get_meta("rules", {}) or {}
        progression = (rules.get("capability_progression", {}) or {}).get(capability, {})
        minimum = max(1, int(progression.get("minimum_completed_outcomes", 3) or 3))
        maximum_delta = min(0.2, float(progression.get("max_delta", 0.1) or 0.1))
        if float(delta) > maximum_delta:
            return self._reject_intent(intent_id, actor_id, "update_capability", data, {"status": "rejected", "message": "Capability delta exceeds the configured review bound.", "failed_check": "delta_limit", "retryable": True})
        try:
            agent = self.store.get_agent(actor_id)
        except KeyError:
            return self._reject_intent(intent_id, actor_id, "update_capability", data, {"status": "rejected", "message": "Capability updates require an individual Agent.", "failed_check": "actor", "retryable": False})
        last = self.store.connection.execute("SELECT MAX(world_minute) AS minute FROM capability_progress WHERE agent_id=? AND capability=?", (actor_id, capability)).fetchone()["minute"]
        start = int(last or -1)
        row = self.store.connection.execute(
            "SELECT COUNT(*) AS n FROM event_log WHERE actor_id=? AND event_type='behavior.completed' AND world_minute>?",
            (actor_id, start),
        ).fetchone()
        completed = int(row["n"] or 0)
        if completed < minimum:
            return self._reject_intent(intent_id, actor_id, "update_capability", data, {"status": "rejected", "message": f"More completed outcomes are required before updating {capability}.", "failed_check": "insufficient_evidence", "completed_outcomes": completed, "required_outcomes": minimum, "retryable": True})
        world_minute = int(self.store.get_meta("world_minute", 0))
        current = float((agent.get("capabilities") or {}).get(capability, 0) or 0)
        maximum = float(progression.get("max_value", 1.0) or 1.0)
        after = min(maximum, current + float(delta))
        applied_delta = after - current
        if applied_delta <= 0:
            return self._reject_intent(intent_id, actor_id, "update_capability", data, {"status": "rejected", "message": "Capability is already at its configured maximum.", "failed_check": "capability_max", "retryable": False})
        with self.store.transaction():
            agent.setdefault("capabilities", {})[capability] = after
            self.store.create_intent(intent_id, actor_id, "update_capability", data, world_minute)
            self.store.update_agent(actor_id, agent)
            self.store.put_capability_progress(actor_id, capability, applied_delta, "agent_review", world_minute, {"evidence": evidence, "completed_outcomes": completed})
            event_id = self.store.append_event("capability.updated", actor_id, world_minute, {"capability": capability, "before": current, "after": after, "delta": applied_delta, "evidence": evidence, "completed_outcomes": completed})
            result = {"status": "accepted", "intent_id": intent_id, "event_id": event_id, "capability": capability, "before": current, "after": after, "delta": applied_delta}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def submit_world_progress(self, actor_id: str, progress_key: str, delta: float, evidence: str) -> dict[str, Any]:
        """Record public progress proposed by Story or God from real outcomes."""

        intent_id = str(uuid4())
        progress_key = str(progress_key).strip()
        evidence = str(evidence).strip()
        data = {"progress_key": progress_key, "delta": delta, "evidence": evidence}
        rules = self.store.get_meta("rules", {}) or {}
        definitions = rules.get("civilization_progress", {}) or {}
        definition = definitions.get(progress_key) if isinstance(definitions, dict) else None
        if not isinstance(definition, dict):
            return self._reject_intent(intent_id, actor_id, "record_world_progress", data, {"status": "rejected", "message": "World progress key is not configured.", "failed_check": "progress_key", "retryable": True})
        if not 0 < float(delta) <= 5 or len(evidence) < 12:
            return self._reject_intent(intent_id, actor_id, "record_world_progress", data, {"status": "rejected", "message": "Progress delta or evidence is invalid.", "failed_check": "evidence", "retryable": True})
        outcomes = self.store.recent_behavior_outcomes(limit=20)
        if not outcomes:
            return self._reject_intent(intent_id, actor_id, "record_world_progress", data, {"status": "rejected", "message": "A public progress update needs a recent behavior outcome.", "failed_check": "insufficient_evidence", "retryable": True})
        world_minute = int(self.store.get_meta("world_minute", 0))
        world_id = str(self.store.get_meta("world_id", "world"))
        required = float(definition.get("required", 1) or 1)
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, "record_world_progress", data, world_minute)
            progress = self.store.advance_progress(world_id, progress_key, float(delta), required, world_minute, {"evidence": evidence, "source_agent": actor_id})
            event_type = "civilization.milestone" if progress["completed"] else "civilization.progressed"
            event_id = self.store.append_event(event_type, actor_id, world_minute, {"progress": progress, "evidence": evidence, "source_agent": actor_id})
            result = {"status": "accepted", "intent_id": intent_id, "event_id": event_id, "progress": progress, "milestone": bool(progress["completed"])}
            if progress["completed"]:
                wake = list(definition.get("wake", ["story", "god"]) or [])
                self._queue_configured_followups({"wake": wake}, event_id, event_type, actor_id, world_minute)
                for role in wake:
                    self.store.mark_evolution_task_ready(f"{role}:event:{event_id}")
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def submit_institution_proposal(self, actor_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        """Record a proposed social institution without activating it.

        A proposal is world data, not a state mutation.  It creates no metric
        values and no ledger rows until an authorized world decision activates
        it later.
        """

        intent_id = str(uuid4())
        name = str(proposal.get("name") or "").strip()
        purpose = str(proposal.get("purpose") or "").strip()
        if len(name) < 2 or len(name) > 120:
            return self._reject_intent(intent_id, actor_id, "propose_institution", proposal, {"status": "rejected", "message": "Institution name must be 2 to 120 characters.", "failed_check": "name", "retryable": True})
        if len(purpose) < 12 or len(purpose) > 500:
            return self._reject_intent(intent_id, actor_id, "propose_institution", proposal, {"status": "rejected", "message": "Institution purpose must be 12 to 500 characters.", "failed_check": "purpose", "retryable": True})
        existing = self.store.list_institutions(limit=200)
        if any(str(item.get("name", "")).casefold() == name.casefold() and item.get("status") in {"proposed", "active"} for item in existing):
            return self._reject_intent(intent_id, actor_id, "propose_institution", proposal, {"status": "rejected", "message": "An institution with this name is already proposed or active.", "failed_check": "duplicate_institution", "retryable": False})
        institution_id = f"institution:{uuid4().hex[:16]}"
        minute = int(self.store.get_meta("world_minute", 0))
        data = {
            "name": name,
            "purpose": purpose,
            "scope": str(proposal.get("scope") or "world"),
            "evidence": str(proposal.get("evidence") or ""),
            "ledger": dict(proposal.get("ledger") or {}) if isinstance(proposal.get("ledger"), dict) else {},
            "policy": dict(proposal.get("policy") or {}) if isinstance(proposal.get("policy"), dict) else {},
            "proposed_by": actor_id,
        }
        with self.store.transaction():
            self.store.put_institution(institution_id, "proposed", data, minute)
            event_id = self.store.append_event("institution.proposed", actor_id, minute, {"institution": {"name": name, "purpose": purpose, "scope": data["scope"]}, "status": "proposed"})
            self.store.create_intent(intent_id, actor_id, "propose_institution", {**data, "institution_name": name}, minute)
            result = {"status": "accepted", "intent_id": intent_id, "event_id": event_id, "institution": {"name": name, "status": "proposed"}}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def activate_institution(self, actor_id: str, institution: str, reason: str = "") -> dict[str, Any]:
        """Activate one pending institution after a world-level decision."""

        intent_id = str(uuid4())
        choice = str(institution or "").strip()
        candidates = [item for item in self.store.list_institutions(status="proposed", limit=200) if str(item.get("name", "")).casefold() == choice.casefold() or str(item.get("institution_id", "")) == choice]
        if len(candidates) != 1:
            return self._reject_intent(intent_id, actor_id, "activate_institution", {"institution": choice, "reason": reason}, {"status": "rejected", "message": "Choose one exact pending institution name.", "failed_check": "institution_choice", "retryable": True, "available_choices": [str(item.get("name", "")) for item in self.store.list_institutions(status="proposed", limit=20)]})
        selected = candidates[0]
        minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            institution_data = {key: value for key, value in selected.items() if key not in {"institution_id", "status", "created_minute", "activated_minute"}}
            self.store.put_institution(str(selected["institution_id"]), "active", institution_data, int(selected.get("created_minute", minute)), minute)
            event_id = self.store.append_event("institution.activated", actor_id, minute, {"institution": {"name": selected.get("name"), "purpose": selected.get("purpose")}, "reason": reason, "status": "active"})
            self.store.create_intent(intent_id, actor_id, "activate_institution", {"institution": choice, "reason": reason}, minute)
            result = {"status": "accepted", "intent_id": intent_id, "event_id": event_id, "institution": {"name": selected.get("name"), "status": "active"}}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def institution_ledger(self, institution: str, period: str | None = None) -> dict[str, Any]:
        """Return a public aggregate for an active institution."""

        candidates = [item for item in self.store.list_institutions(status="active", limit=200) if str(item.get("name", "")).casefold() == str(institution).casefold() or str(item.get("institution_id", "")) == str(institution)]
        if len(candidates) != 1:
            return {"status": "rejected", "message": "Choose one exact active institution name.", "failed_check": "institution_choice", "retryable": True}
        selected = candidates[0]
        return {"status": "ok", "institution": {"name": selected.get("name"), "status": selected.get("status")}, "entries": self.store.institution_ledger_totals(str(selected["institution_id"]), period)}

    def calculate_institution_allocation(
        self,
        institution: str,
        subjects: list[str],
        available_pool: float,
        period: str | None = None,
    ) -> dict[str, Any]:
        """Calculate, but do not apply, a declarative resource allocation.

        The policy must explicitly define a minimum allocation and the share of
        the remaining pool that may use verified ledger totals.  This keeps an
        emergent contribution institution safe before a world-specific
        settlement Action is configured.
        """

        candidates = [item for item in self.store.list_institutions(status="active", limit=200) if str(item.get("name", "")).casefold() == str(institution).casefold() or str(item.get("institution_id", "")) == str(institution)]
        if len(candidates) != 1:
            return {"status": "rejected", "message": "Choose one exact active institution name.", "failed_check": "institution_choice", "retryable": True}
        selected = candidates[0]
        policy = selected.get("policy") if isinstance(selected.get("policy"), dict) else {}
        allocation = policy.get("allocation") if isinstance(policy.get("allocation"), dict) else {}
        minimum = float(allocation.get("minimum_per_subject", 0) or 0)
        contribution_share = float(allocation.get("contribution_share", 0) or 0)
        if minimum < 0 or not 0 <= contribution_share <= 1:
            return {"status": "rejected", "message": "The active institution has an invalid allocation policy.", "failed_check": "allocation_policy", "retryable": False}
        subject_ids = [str(item) for item in subjects if str(item)]
        if not subject_ids:
            return {"status": "accepted", "institution": {"name": selected.get("name")}, "period": period or "world", "allocations": [], "unallocated": float(available_pool)}
        pool = max(0.0, float(available_pool))
        base_total = minimum * len(subject_ids)
        if base_total > pool + 1e-9:
            return {"status": "rejected", "message": "The available pool cannot satisfy the configured minimum allocation.", "failed_check": "minimum_allocation", "retryable": True, "required": base_total, "available": pool}
        remaining = pool - base_total
        bonus_pool = remaining * contribution_share
        totals = self.store.institution_ledger_totals(str(selected["institution_id"]), period)
        category = allocation.get("category")
        weights = {
            subject: sum(
                float(row.get("total", 0) or 0)
                for row in totals
                if str(row.get("subject_id")) == subject and (not category or str(row.get("category")) == str(category))
            )
            for subject in subject_ids
        }
        weight_total = sum(weights.values())
        rows = []
        for subject in subject_ids:
            bonus = bonus_pool * weights[subject] / weight_total if weight_total > 0 else 0.0
            rows.append({"subject": subject, "minimum": minimum, "bonus": bonus, "total": minimum + bonus, "verified_weight": weights[subject]})
        return {"status": "accepted", "institution": {"name": selected.get("name")}, "period": period or "world", "allocations": rows, "unallocated": remaining - bonus_pool}

    @staticmethod
    def _valid_transfer(bundle: dict[str, Any]) -> bool:
        return bool(bundle) and all(isinstance(item, str) and item and isinstance(quantity, (int, float)) and not isinstance(quantity, bool) and quantity > 0 for item, quantity in bundle.items())

    @staticmethod
    def _has_inventory(agent: dict[str, Any], bundle: dict[str, Any]) -> bool:
        inventory = agent.get("inventory", {}) or {}
        return all(float(inventory.get(item, 0)) >= float(quantity) for item, quantity in bundle.items())

    @staticmethod
    def _same_public_location(first: dict[str, Any], second: dict[str, Any]) -> bool:
        first_state = first.get("initial_state", {}) or {}
        second_state = second.get("initial_state", {}) or {}
        return bool(first_state.get("location_id")) and first_state.get("location_id") == second_state.get("location_id")

    def _available_for_instant_interaction(self, agent: dict[str, Any]) -> bool:
        """Check interaction eligibility without interrupting an active BT."""

        state = agent.get("initial_state", {}) or {}
        if state.get("status", "idle") in {"unconscious", "dead"}:
            return False
        if state.get("status") != "executing":
            return True
        action_id = state.get("active_action")
        if action_id == "parallel":
            action_ids = [item.get("action_id") for item in (state.get("active_parallel_actions") or {}).values()]
            definitions = {item["id"]: item for item in self.store.get_definitions("action_def")}
            return bool(action_ids) and all(definitions.get(item, {}).get("interaction_policy") == "allow" for item in action_ids)
        if not action_id:
            return False
        action = next((item for item in self.store.get_definitions("action_def") if item.get("id") == action_id), None)
        return bool(action and action.get("interaction_policy") == "allow")

    def submit_dialogue(self, speaker_id: str, listener_id: str, topic: str, message: str) -> dict[str, Any]:
        """Record a direct, permission-checked text exchange."""

        intent_id = str(uuid4())
        listener_id = self.targets.canonical_id(listener_id) or str(listener_id)
        topic = str(topic).strip()
        message = str(message).strip()
        if not listener_id or listener_id == speaker_id or not topic or not message:
            return {"status": "rejected", "message": "speaker, listener, topic and message are required.", "failed_check": "dialogue", "retryable": True}
        if len(message) > 1000:
            return {"status": "rejected", "message": "message must be at most 1000 characters.", "failed_check": "message_length", "retryable": True}
        try:
            speaker = self.store.get_agent(speaker_id)
            listener = self.store.get_agent(listener_id)
        except KeyError:
            return {"status": "rejected", "message": "Both participants must exist.", "failed_check": "participant", "retryable": False}
        if not self._same_public_location(speaker, listener):
            return {"status": "rejected", "message": "Both participants must be at the same location.", "failed_check": "location", "retryable": True}
        if not self._available_for_instant_interaction(speaker) or not self._available_for_instant_interaction(listener):
            return {"status": "rejected", "message": "A participant is busy with an action that does not allow instant interaction.", "failed_check": "participant_busy", "retryable": True}
        minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            self.store.create_intent(intent_id, speaker_id, "send_message", {"target_id": listener_id, "topic": topic, "message": message}, minute)
            exchange_id = self.store.put_dialogue_exchange(speaker_id, listener_id, topic, minute, {"message": message, "source": "direct_text"})
            self.store.put_memory(speaker_id, "dialogue", {"topic": topic, "message": message, "target_id": listener_id, "world_minute": minute, "dialogue_id": exchange_id, "importance": 0.35})
            self.store.put_memory(listener_id, "testimony", {"topic": topic, "message": message, "speaker_id": speaker_id, "world_minute": minute, "dialogue_id": exchange_id, "importance": 0.45, "pending_decision": True})
            self._apply_configured_need_effect(speaker, "dialogue.completed")
            self._apply_configured_need_effect(listener, "dialogue.completed")
            self._apply_configured_relationship_effect(speaker, listener, "dialogue.completed", minute)
            self.store.update_agent(speaker_id, speaker)
            self.store.update_agent(listener_id, listener)
            event_id = self.store.append_event("dialogue.completed", speaker_id, minute, {"dialogue_id": exchange_id, "target_id": listener_id, "topic": topic, "summary": "A direct exchange was recorded.", "activation": "recipient_next_decision_or_reflection", "duration_minutes": 0, "priority": "normal"})
            result = {"status": "accepted", "intent_id": intent_id, "dialogue_id": exchange_id, "event_id": event_id, "topic": topic}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def _apply_configured_need_effect(self, agent: dict[str, Any], event_type: str) -> None:
        """Apply package-declared social/interaction need outcomes."""

        rules = self.store.get_meta("rules", {}) or {}
        effects = (rules.get("need_effects", {}) or {}).get(event_type, [])
        if isinstance(effects, dict):
            effects = [{"type": "need_delta", "need": key, "delta": value} for key, value in effects.items()]
        for effect in effects or []:
            self.effects.apply(agent, dict(effect))

    def _apply_configured_relationship_effect(self, source: dict[str, Any], target: dict[str, Any], event_type: str, minute: int) -> None:
        """Apply package-declared relationship changes for completed interactions."""

        rules = self.store.get_meta("rules", {}) or {}
        effects = (rules.get("relationship_effects", {}) or {}).get(event_type, [])
        if isinstance(effects, dict):
            effects = [effects]
        for effect in effects or []:
            self._apply_relationship_effect(source, target, dict(effect), minute)

    def create_trade_offer(self, offerer_id: str, recipient_id: str, give: dict[str, Any], want: dict[str, Any]) -> dict[str, Any]:
        """Create a two-phase item offer; no inventory moves until acceptance."""

        give = dict(give or {})
        want = dict(want or {})
        recipient_id = self.targets.canonical_id(recipient_id) or str(recipient_id)
        if not recipient_id or recipient_id == offerer_id or not self._valid_transfer(give) or not self._valid_transfer(want):
            return {"status": "rejected", "message": "A trade needs two participants and positive give/want quantities.", "failed_check": "trade_schema", "retryable": True}
        try:
            offerer = self.store.get_agent(offerer_id)
            recipient = self.store.get_agent(recipient_id)
        except KeyError:
            return {"status": "rejected", "message": "Both traders must exist.", "failed_check": "participant", "retryable": False}
        if not self._same_public_location(offerer, recipient):
            return {"status": "rejected", "message": "Both traders must be at the same location.", "failed_check": "location", "retryable": True}
        if not self._available_for_instant_interaction(offerer) or not self._available_for_instant_interaction(recipient):
            return {"status": "rejected", "message": "A participant is busy with an action that does not allow instant trade.", "failed_check": "participant_busy", "retryable": True}
        if not self._has_inventory(offerer, give):
            return {"status": "rejected", "message": "The offerer lacks an offered item.", "failed_check": "offer_inventory", "retryable": True}
        minute = int(self.store.get_meta("world_minute", 0))
        offer_id = str(uuid4())
        intent_id = str(uuid4())
        with self.store.transaction():
            self.store.create_intent(intent_id, offerer_id, "create_trade_offer", {"recipient_id": recipient_id, "give": give, "want": want}, minute)
            self.store.put_trade_offer(offer_id, offerer_id, recipient_id, give, want, minute)
            self.store.put_memory(recipient_id, "trade_offer", {"offer_id": offer_id, "offerer_id": offerer_id, "give": give, "want": want, "world_minute": minute, "pending_decision": True, "importance": 0.6})
            self.store.put_memory(offerer_id, "trade_offer", {"offer_id": offer_id, "recipient_id": recipient_id, "give": give, "want": want, "world_minute": minute, "pending_decision": True, "importance": 0.45})
            event_id = self.store.append_event("trade.offer_created", offerer_id, minute, {"offer_id": offer_id, "recipient_id": recipient_id, "give": give, "want": want, "activation": "recipient_next_decision", "duration_minutes": 0, "priority": "normal"})
            result = {"status": "accepted", "intent_id": intent_id, "offer_id": offer_id, "event_id": event_id, "trade_status": "open"}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def accept_trade_offer(self, recipient_id: str, offer_id: str) -> dict[str, Any]:
        recipient_id = self.targets.canonical_id(recipient_id) or str(recipient_id)
        offer = self.store.get_trade_offer(offer_id)
        if offer is None:
            return {"status": "rejected", "message": "Trade offer does not exist.", "failed_check": "offer_id", "retryable": False}
        if offer["status"] != "open":
            return {"status": "rejected", "message": "Trade offer is no longer open.", "failed_check": "offer_status", "retryable": False}
        if offer["recipient_id"] != recipient_id:
            return {"status": "rejected", "message": "Only the named recipient can accept this offer.", "failed_check": "recipient", "retryable": False}
        try:
            offerer = self.store.get_agent(offer["offerer_id"])
            recipient = self.store.get_agent(recipient_id)
        except KeyError:
            return {"status": "rejected", "message": "Both traders must still exist.", "failed_check": "participant", "retryable": False}
        if not self._same_public_location(offerer, recipient):
            return {"status": "rejected", "message": "Both traders must still be at the same location.", "failed_check": "location", "retryable": True}
        if not self._available_for_instant_interaction(offerer) or not self._available_for_instant_interaction(recipient):
            return {"status": "rejected", "message": "A participant is busy with an action that does not allow instant trade.", "failed_check": "participant_busy", "retryable": True}
        if not self._has_inventory(offerer, offer["give"]):
            return {"status": "rejected", "message": "The offered items are no longer available.", "failed_check": "offer_inventory", "retryable": False}
        if not self._has_inventory(recipient, offer["want"]):
            return {"status": "rejected", "message": "The requested items are no longer available.", "failed_check": "request_inventory", "retryable": True}
        minute = int(self.store.get_meta("world_minute", 0))
        intent_id = str(uuid4())
        with self.store.transaction():
            self.store.create_intent(intent_id, recipient_id, "accept_trade_offer", {"offer_id": offer_id}, minute)
            for item, quantity in offer["give"].items():
                offerer.setdefault("inventory", {})[item] = offerer["inventory"].get(item, 0) - quantity
                recipient.setdefault("inventory", {})[item] = recipient["inventory"].get(item, 0) + quantity
            for item, quantity in offer["want"].items():
                recipient.setdefault("inventory", {})[item] = recipient["inventory"].get(item, 0) - quantity
                offerer.setdefault("inventory", {})[item] = offerer["inventory"].get(item, 0) + quantity
            self._apply_configured_need_effect(offerer, "trade.completed")
            self._apply_configured_need_effect(recipient, "trade.completed")
            self._apply_configured_relationship_effect(offerer, recipient, "trade.completed", minute)
            self.store.update_agent(offerer_id := offer["offerer_id"], offerer)
            self.store.update_agent(recipient_id, recipient)
            result = {"status": "accepted", "offer_id": offer_id, "trade_status": "completed", "give": offer["give"], "want": offer["want"]}
            self.store.finish_trade_offer(offer_id, "completed", minute, result)
            self.store.put_memory(offerer_id, "trade_result", {"offer_id": offer_id, "trade_status": "completed", "world_minute": minute, "importance": 0.55})
            self.store.put_memory(recipient_id, "trade_result", {"offer_id": offer_id, "trade_status": "completed", "world_minute": minute, "importance": 0.55})
            event_id = self.store.append_event("trade.completed", recipient_id, minute, {"offer_id": offer_id, "offerer_id": offerer_id, "recipient_id": recipient_id, "give": offer["give"], "want": offer["want"], "activation": "both_next_reflection", "duration_minutes": 0, "priority": "normal"})
            result["intent_id"] = intent_id
            result["event_id"] = event_id
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def counter_trade_offer(self, counterparty_id: str, offer_id: str, give: dict[str, Any], want: dict[str, Any]) -> dict[str, Any]:
        """Close an offer and create the counterparty's next-round offer."""

        counterparty_id = self.targets.canonical_id(counterparty_id) or str(counterparty_id)
        offer = self.store.get_trade_offer(offer_id)
        if offer is None or offer["status"] != "open" or offer["recipient_id"] != counterparty_id:
            return {"status": "rejected", "message": "Only the open offer recipient can counter it.", "failed_check": "offer", "retryable": False}
        if int(offer.get("round", 1)) >= 8:
            return {"status": "rejected", "message": "The negotiation reached its round limit.", "failed_check": "round_limit", "retryable": False}
        give = dict(give or {})
        want = dict(want or {})
        if not self._valid_transfer(give) or not self._valid_transfer(want):
            return {"status": "rejected", "message": "A counteroffer needs positive give/want quantities.", "failed_check": "trade_schema", "retryable": True}
        try:
            counterparty = self.store.get_agent(counterparty_id)
            original_offerer = self.store.get_agent(offer["offerer_id"])
        except KeyError:
            return {"status": "rejected", "message": "Both traders must still exist.", "failed_check": "participant", "retryable": False}
        if not self._same_public_location(counterparty, original_offerer):
            return {"status": "rejected", "message": "Both traders must still be at the same location.", "failed_check": "location", "retryable": True}
        if not self._available_for_instant_interaction(counterparty) or not self._available_for_instant_interaction(original_offerer):
            return {"status": "rejected", "message": "A participant is busy with an action that does not allow instant trade.", "failed_check": "participant_busy", "retryable": True}
        if not self._has_inventory(counterparty, give):
            return {"status": "rejected", "message": "The counterparty lacks the counteroffer items.", "failed_check": "offer_inventory", "retryable": True}
        minute = int(self.store.get_meta("world_minute", 0))
        counter_id = str(uuid4())
        with self.store.transaction():
            self.store.finish_trade_offer(offer_id, "countered", minute, {"status": "accepted", "trade_status": "countered", "counter_offer_id": counter_id})
            self.store.put_trade_offer(counter_id, counterparty_id, offer["offerer_id"], give, want, minute, parent_offer_id=offer_id, round_number=int(offer.get("round", 1)) + 1)
            self.store.put_memory(offer["offerer_id"], "trade_offer", {"offer_id": counter_id, "parent_offer_id": offer_id, "offerer_id": counterparty_id, "give": give, "want": want, "world_minute": minute, "pending_decision": True, "importance": 0.6})
            event_id = self.store.append_event("trade.countered", counterparty_id, minute, {"offer_id": counter_id, "parent_offer_id": offer_id, "recipient_id": offer["offerer_id"], "give": give, "want": want, "activation": "recipient_next_decision", "duration_minutes": 0, "priority": "normal"})
        return {"status": "accepted", "offer_id": counter_id, "parent_offer_id": offer_id, "trade_status": "open", "event_id": event_id}

    def reject_trade_offer(self, recipient_id: str, offer_id: str) -> dict[str, Any]:
        recipient_id = self.targets.canonical_id(recipient_id) or str(recipient_id)
        offer = self.store.get_trade_offer(offer_id)
        if offer is None or offer["recipient_id"] != recipient_id or offer["status"] != "open":
            return {"status": "rejected", "message": "The trade offer cannot be rejected by this participant.", "failed_check": "offer", "retryable": False}
        minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            result = {"status": "accepted", "offer_id": offer_id, "trade_status": "rejected"}
            self.store.finish_trade_offer(offer_id, "rejected", minute, result)
            self.store.append_event("trade.rejected", recipient_id, minute, {"offer_id": offer_id, "offerer_id": offer["offerer_id"], "recipient_id": recipient_id, "activation": "offerer_next_decision_or_reflection", "duration_minutes": 0, "priority": "normal"})
        return result

    def submit_belief_update(self, actor_id: str, belief_id: str, statement: str, confidence: float, reason: str) -> dict[str, Any]:
        data = {"belief_id": belief_id, "statement": statement, "confidence": confidence, "reason": reason}
        intent_id = str(uuid4())
        if not statement and not belief_id:
            return self._reject_intent(intent_id, actor_id, "update_belief", data, {"status": "rejected", "message": "A belief id or statement is required.", "failed_check": "belief", "retryable": True})
        if not 0 <= confidence <= 1:
            return self._reject_intent(intent_id, actor_id, "update_belief", data, {"status": "rejected", "message": "Confidence must be between 0 and 1.", "failed_check": "confidence", "retryable": True})
        world_minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            agent = self.store.get_agent(actor_id)
            beliefs = list(agent.get("beliefs", []))
            match = next((belief for belief in beliefs if belief.get("id") == belief_id), None)
            if match is None:
                match = {"id": belief_id or f"belief_{uuid4().hex[:8]}", "statement": statement}
                beliefs.append(match)
            elif statement:
                match["statement"] = statement
            match["confidence"] = confidence
            match["updated_world_minute"] = world_minute
            agent["beliefs"] = beliefs
            self.store.create_intent(intent_id, actor_id, "update_belief", data, world_minute)
            self.store.update_agent(actor_id, agent)
            self.store.append_event("belief.updated", actor_id, world_minute, data)
            result = {"status": "accepted", "intent_id": intent_id, "belief_id": match["id"], "confidence": confidence}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def submit_goal_update(self, actor_id: str, goal_id: str, statement: str, status: str, priority: float) -> dict[str, Any]:
        intent_id = str(uuid4())
        data = {"goal_id": goal_id, "statement": statement, "status": status, "priority": priority}
        if not statement.strip():
            return self._reject_intent(intent_id, actor_id, "update_goal", data, {"status": "rejected", "message": "Goal statement is required.", "failed_check": "statement", "retryable": True})
        if not 0 <= priority <= 1:
            return self._reject_intent(intent_id, actor_id, "update_goal", data, {"status": "rejected", "message": "Goal priority must be between 0 and 1.", "failed_check": "priority", "retryable": True})
        world_minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            agent = self.store.get_agent(actor_id)
            if goal_id in {"", "ultimate_goal"}:
                agent["ultimate_goal"] = statement
            else:
                goals = list(agent.get("goals", []))
                match = next((goal for goal in goals if goal.get("id") == goal_id), None)
                if match is None:
                    match = {"id": goal_id}
                    goals.append(match)
                match.update({"statement": statement, "status": status, "priority": priority, "updated_world_minute": world_minute})
                agent["goals"] = goals
            self.store.create_intent(intent_id, actor_id, "update_goal", data, world_minute)
            self.store.update_agent(actor_id, agent)
            self.store.append_event("goal.updated", actor_id, world_minute, data)
            result = {"status": "accepted", "intent_id": intent_id, "goal_id": goal_id or "ultimate_goal"}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def submit_behavior_proposal(self, actor_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        intent_id = str(uuid4())
        action_ids = list(proposal.get("action_ids") or [])
        action_defs = {x["id"]: x for x in self.store.get_definitions("action_def")}
        errors: list[str] = []
        if not proposal.get("goal"):
            errors.append("goal is required")
        if not action_ids:
            errors.append("at least one action_id is required")
        if len(action_ids) > 16:
            errors.append("a behavior may contain at most 16 actions")
        errors.extend(f"unknown action: {action_id}" for action_id in action_ids if action_id not in action_defs)
        behavior_id = str(proposal.get("behavior_id") or f"proposed_{uuid4().hex[:10]}")
        if any(x["id"] == behavior_id for x in self.store.get_definitions("bt_def")):
            errors.append(f"behavior already exists: {behavior_id}")
        proposed_label = str(proposal.get("agent_label") or proposal.get("summary") or proposal.get("goal") or "").casefold()
        proposed_actions = list(action_ids)
        for existing in self.store.get_definitions("bt_def"):
            existing_label = str(existing.get("agent_label") or existing.get("summary") or existing.get("id", "").replace("_", " ")).casefold()
            existing_actions = [node.get("action_id") for node in existing.get("nodes", []) if node.get("type") == "action"]
            if proposed_label and existing_label == proposed_label and existing_actions == proposed_actions:
                errors.append("an equivalent behavior is already registered")
                break
        data = {**proposal, "behavior_id": behavior_id, "actor_id": actor_id}
        if errors:
            return self._reject_intent(intent_id, actor_id, "propose_behavior", data, {"status": "rejected", "message": "Behavior proposal failed validation.", "errors": errors, "retryable": True})
        estimated = 0
        nodes = []
        for action_id in action_ids:
            action = action_defs[action_id]
            duration = action.get("duration", {}).get("minutes", 0)
            try:
                estimated += int(duration)
            except (TypeError, ValueError):
                pass
            nodes.append({"type": "action", "action_id": action_id})
        definition = {
            "id": behavior_id, "version": 1, "definition_version": 1, "scope": "personal", "summary": proposal.get("summary") or proposal["goal"],
            "agent_label": proposal.get("agent_label") or proposal.get("summary") or proposal["goal"],
            "entry_conditions": proposal.get("entry_conditions", []), "estimated_minutes": estimated,
            "success_results": proposal.get("success_results", []), "failure_results": proposal.get("failure_results", []),
            "nodes": nodes, "definition_source": "agent_proposal", "kind": "proposed", "validation_status": "validated",
        }
        world_minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            agent = self.store.get_agent(actor_id)
            self.store.create_intent(intent_id, actor_id, "propose_behavior", data, world_minute)
            self.store.put_definition("bt_def", behavior_id, definition)
            ids = list(agent.get("initial_bt_ids", []))
            if behavior_id not in ids:
                ids.append(behavior_id)
            agent["initial_bt_ids"] = ids
            self.store.update_agent(actor_id, agent)
            self.store.append_event("behavior.registered", actor_id, world_minute, {"behavior_id": behavior_id, "source": "agent_proposal"})
            result = {"status": "accepted", "intent_id": intent_id, "behavior_ref": f"behavior:{behavior_id}", "registered": True}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    @staticmethod
    def _definition_slug(value: Any) -> str:
        text = re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")
        return text[:80]

    @staticmethod
    def _definition_name_key(value: Any) -> str:
        return "".join(char for char in str(value or "").casefold() if char.isalnum())

    def _resolve_registered_definition(self, table: str, value: Any) -> tuple[str | None, list[str]]:
        """Resolve a visible definition name without exposing registry IDs."""

        key = self._definition_name_key(value)
        if not key:
            return None, []
        matches: list[str] = []
        for item in self.store.get_definitions(table):
            candidates = [item.get("id"), item.get("name"), item.get("label"), item.get("agent_label"), item.get("summary"), item.get("description")]
            if any(key == self._definition_name_key(candidate) for candidate in candidates if candidate):
                matches.append(str(item["id"]))
        return (matches[0] if len(matches) == 1 else None), matches

    def _validate_definition_effects(self, definition: dict[str, Any]) -> list[str]:
        allowed = {
            "need_delta", "state_delta", "attribute_delta", "attribute_set",
            "inventory_add", "inventory_remove", "inventory.consume_item",
            "relationship_delta", "public_event", "world_property_delta",
            "entity_state_set", "status_add", "status_remove", "location_set",
            "capability_xp", "capability.add_xp",
        }
        errors: list[str] = []
        for field in ("effects", "progress_effects"):
            values = definition.get(field, []) or []
            if not isinstance(values, list):
                errors.append(f"{field} must be a list")
                continue
            for index, effect in enumerate(values):
                if not isinstance(effect, dict):
                    errors.append(f"{field}[{index}] must be an object")
                    continue
                effect_type = str(effect.get("type") or effect.get("op") or "")
                if effect_type not in allowed:
                    errors.append(f"unsupported effect type: {effect_type or '<missing>'}")
        return errors

    def _validate_definition_proposal(self, definition_type: str, name: str, raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Validate and normalize a God definition before SQLite registration."""

        kind = str(definition_type or "").strip().casefold()
        definition = copy.deepcopy(raw if isinstance(raw, dict) else {})
        errors: list[str] = []
        if kind not in {"attribute", "world_property", "action", "behavior", "recipe", "capability"}:
            return definition, [f"unsupported definition_type: {kind}"]
        if not str(name or "").strip():
            errors.append("name is required")
        forbidden = {"handler", "python", "script", "sql", "executor_code", "code"}
        errors.extend(f"field is not declarative: {field}" for field in forbidden if field in definition)

        if kind == "attribute":
            attr_kind = str(definition.get("kind") or "").casefold()
            if attr_kind not in {"state", "need"}:
                errors.append("attribute.kind must be state or need")
            for field in ("min", "max", "default"):
                if not isinstance(definition.get(field), (int, float)) or isinstance(definition.get(field), bool):
                    errors.append(f"attribute.{field} must be numeric")
            if all(isinstance(definition.get(field), (int, float)) for field in ("min", "max", "default")):
                if float(definition["min"]) > float(definition["max"]):
                    errors.append("attribute.min must not exceed attribute.max")
                if not float(definition["min"]) <= float(definition["default"]) <= float(definition["max"]):
                    errors.append("attribute.default must be within min and max")
            if attr_kind == "state" and definition.get("cost_mode") not in {"increase", "decrease"}:
                errors.append("state attributes must declare cost_mode")
            if not str(definition.get("label") or name).strip():
                errors.append("attribute.label is required")
            definition.update({"id": str(definition.get("id") or self._definition_slug(name) or f"attribute_{uuid4().hex[:10]}"), "label": definition.get("label") or name, "prompt": definition.get("prompt") or str(name)})
            existing = self.store.get_meta("attributes", []) or []
            if any(str(item.get("id")) == definition["id"] for item in existing if isinstance(item, dict)):
                errors.append(f"attribute already exists: {definition['id']}")
            if any(self._definition_name_key(name) == self._definition_name_key(item.get("label") or item.get("id")) for item in existing if isinstance(item, dict)):
                errors.append(f"attribute name already exists: {name}")
            if definition.get("vital") and any(item.get("vital") is True for item in existing if isinstance(item, dict)):
                errors.append("only one vital attribute may be configured")
            if definition.get("capacity") and any(item.get("capacity") is True for item in existing if isinstance(item, dict)):
                errors.append("only one capacity attribute may be configured")
            return definition, errors

        if kind == "world_property":
            property_id = str(definition.get("id") or self._definition_slug(name) or f"property_{uuid4().hex[:10]}")
            default = definition.get("default")
            if not isinstance(default, (int, float, str, bool)) or isinstance(default, bool):
                errors.append("world_property.default must be a number or string")
            if property_id in {str(key) for key in (self.store.get_meta("world_properties", {}) or {})}:
                errors.append(f"world property already exists: {property_id}")
            if property_id not in {str(key) for key in (self.store.get_meta("world_properties", {}) or {})} and any(self._definition_name_key(name) == self._definition_name_key(item) for item in (self.store.get_meta("world_property_labels", {}) or {}).values()):
                errors.append(f"world property name already exists: {name}")
            definition.update({"id": property_id, "label": definition.get("label") or name, "prompt": definition.get("prompt") or str(name)})
            return definition, errors
        table = {"action": "action_def", "behavior": "bt_def", "recipe": "recipe_def", "capability": "capability_def"}[kind]
        explicit_id = str(definition.get("id") or "").strip()
        definition_id = explicit_id or self._definition_slug(name) or f"definition_{uuid4().hex[:10]}"
        if any(str(item.get("id")) == definition_id for item in self.store.get_definitions(table)):
            errors.append(f"definition already exists: {definition_id}")
        if any(self._definition_name_key(name) in {self._definition_name_key(item.get(field)) for field in ("name", "label", "agent_label", "summary", "description") if item.get(field)} for item in self.store.get_definitions(table)):
            errors.append(f"definition name already exists: {name}")

        if kind == "action":
            duration = definition.get("duration") if isinstance(definition.get("duration"), dict) else {}
            if not duration or not (isinstance(duration.get("minutes"), (int, float)) or duration.get("mode") in {"route_distance", "recipe_duration"}):
                errors.append("action.duration must declare minutes or a supported mode")
            if isinstance(duration.get("minutes"), (int, float)) and float(duration["minutes"]) <= 0:
                errors.append("action.duration.minutes must be positive")
            for field in ("preconditions", "effects", "progress_effects"):
                if field in definition and not isinstance(definition[field], list):
                    errors.append(f"action.{field} must be a list")
            errors.extend(self._validate_definition_effects(definition))
            for field in ("success", "failure"):
                if not isinstance(definition.get(field), dict) or not str((definition.get(field) or {}).get("result") or "").strip():
                    errors.append(f"action.{field} must declare an object with result")
            definition.update({"id": definition_id, "version": 1, "definition_version": 1, "description": definition.get("description") or str(name), "definition_source": "god_proposal", "kind": "atomic", "executor": "effect_pipeline", "validation_status": "validated"})
        elif kind == "behavior":
            nodes = definition.get("nodes")
            if nodes is None and isinstance(definition.get("actions"), list):
                nodes = [{"type": "action", "action": item} for item in definition["actions"]]
            if not isinstance(nodes, list) or not nodes:
                errors.append("behavior.nodes or behavior.actions must contain at least one step")
                nodes = []
            normalized_nodes: list[dict[str, Any]] = []
            if len(nodes) > 32:
                errors.append("a behavior may contain at most 32 nodes")
            node_count = 0

            def normalize_node(node: Any, path: str, depth: int = 0) -> dict[str, Any] | None:
                nonlocal node_count
                node_count += 1
                if node_count > 32:
                    return None
                if isinstance(node, str):
                    node = {"type": "behavior", "behavior": node}
                if not isinstance(node, dict):
                    errors.append(f"behavior node {path} must be an object")
                    return None
                node_copy = copy.deepcopy(node)
                node_type = str(node_copy.get("type") or "action")
                if node_type == "action":
                    ref = node_copy.get("action_id") or node_copy.get("action") or node_copy.get("name")
                    resolved, matches = self._resolve_registered_definition("action_def", ref)
                    if not resolved:
                        errors.append(f"unknown or ambiguous action in node {path}: {ref}; matches={matches}")
                    else:
                        node_copy["action_id"] = resolved
                    node_copy.pop("action", None)
                    node_copy.pop("name", None)
                elif node_type in {"behavior", "subtree"}:
                    ref = node_copy.get("behavior_id") or node_copy.get("behavior") or node_copy.get("name")
                    resolved, matches = self._resolve_registered_definition("bt_def", ref)
                    if not resolved:
                        errors.append(f"unknown or ambiguous behavior in node {path}: {ref}; matches={matches}")
                    else:
                        node_copy["behavior_id"] = resolved
                    node_copy.pop("behavior", None)
                    node_copy.pop("name", None)
                elif node_type not in {"sequence", "selector", "parallel", "condition", "wait"}:
                    errors.append(f"unsupported behavior node type: {node_type}")
                children = node_copy.get("children")
                if children is not None:
                    if not isinstance(children, list):
                        errors.append(f"behavior node {path}.children must be a list")
                    elif depth >= 8:
                        errors.append(f"behavior node {path} exceeds maximum nesting depth")
                    else:
                        normalized_children = []
                        for child_index, child in enumerate(children):
                            normalized = normalize_node(child, f"{path}.{child_index}", depth + 1)
                            if normalized is not None:
                                normalized_children.append(normalized)
                        node_copy["children"] = normalized_children
                return node_copy

            for index, node in enumerate(nodes[:32]):
                normalized = normalize_node(node, str(index))
                if normalized is not None:
                    normalized_nodes.append(normalized)
            if node_count > 32:
                errors.append("a behavior may contain at most 32 nodes")
            definition["nodes"] = normalized_nodes
            definition.pop("actions", None)
            definition.update({"id": definition_id, "version": 1, "definition_version": 1, "summary": definition.get("summary") or definition.get("description") or str(name), "agent_label": definition.get("agent_label") or name, "definition_source": "god_proposal", "kind": "proposed", "validation_status": "validated"})
        elif kind == "recipe":
            duration = definition.get("duration_minutes")
            if not isinstance(duration, (int, float)) or isinstance(duration, bool) or float(duration) <= 0:
                errors.append("recipe.duration_minutes must be positive")
            for field in ("inputs", "outputs"):
                values = definition.get(field)
                if not isinstance(values, list) or not values:
                    errors.append(f"recipe.{field} must be a non-empty list")
                elif any(not isinstance(item, dict) or not item.get("item") or not isinstance(item.get("quantity"), (int, float)) or float(item.get("quantity", 0)) <= 0 for item in values):
                    errors.append(f"recipe.{field} entries need item and positive quantity")
            definition.update({"id": definition_id, "version": 1, "definition_version": 1, "name": definition.get("name") or name, "definition_source": "god_proposal", "validation_status": "validated"})
        elif kind == "capability":
            default = definition.get("default", definition.get("initial", 0))
            if not isinstance(default, (int, float)) or isinstance(default, bool) or not 0 <= float(default) <= 1:
                errors.append("capability.default must be between 0 and 1")
            definition.update({"id": definition_id, "version": 1, "definition_version": 1, "name": definition.get("name") or name, "default": float(default) if isinstance(default, (int, float)) else 0.0, "value_path": definition.get("value_path") or f"agent.capabilities.{definition_id}", "definition_source": "god_proposal", "validation_status": "validated"})
        return definition, errors

    def submit_definition_proposal(self, actor_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        """Validate and register a new declarative definition in this instance."""

        intent_id = str(uuid4())
        definition_type = str(proposal.get("definition_type") or "").casefold()
        name = str(proposal.get("name") or "").strip()
        raw_definition = proposal.get("definition") if isinstance(proposal.get("definition"), dict) else {}
        definition, errors = self._validate_definition_proposal(definition_type, name, raw_definition)
        if actor_id != "god":
            errors.append("only God may register world definitions")
        assign_to = list(proposal.get("assign_to") or [])
        resolved_agents: list[tuple[str, str]] = []
        if definition_type != "behavior" and assign_to:
            errors.append("assign_to is only valid for behavior definitions")
        for name_or_id in assign_to:
            resolved = self.targets.canonical_id(name_or_id)
            try:
                agent = self.store.get_agent(str(resolved or name_or_id))
            except KeyError:
                errors.append(f"unknown character in assign_to: {name_or_id}")
                continue
            resolved_agents.append((str(agent["id"]), str(agent.get("name") or agent["id"])))
        data = {**proposal, "actor_id": actor_id, "definition": definition}
        if errors:
            return self._reject_intent(intent_id, actor_id, "propose_definition", data, {"status": "rejected", "message": "Definition proposal failed validation.", "failed_check": "definition_schema", "errors": errors, "retryable": True})
        world_minute = int(self.store.get_meta("world_minute", 0))
        table = {"action": "action_def", "behavior": "bt_def", "recipe": "recipe_def", "capability": "capability_def"}.get(definition_type)
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, "propose_definition", data, world_minute)
            if definition_type == "attribute":
                attributes = list(self.store.get_meta("attributes", []) or [])
                attributes.append(definition)
                self.store.set_meta("attributes", attributes)
                schema = AttributeSchema(attributes, self.store.get_meta("rules", {}) or {})
                if definition.get("kind") == "need":
                    rules = dict(self.store.get_meta("rules", {}) or {})
                    needs = list(rules.get("needs", []) or [])
                    needs.append({
                        "id": definition["id"],
                        "target": definition.get("target", definition.get("default", 0)),
                        "minimum": definition.get("min", 0),
                        "gamma": definition.get("gamma", 1.0),
                        "critical_threshold": definition.get("critical_threshold", 0.75),
                        "critical_gain": definition.get("critical_gain", 1.0),
                    })
                    rules["needs"] = needs
                    self.store.set_meta("rules", rules)
                    schema = AttributeSchema(attributes, rules)
                for agent in self.store.list_agents():
                    state = dict(agent.get("initial_state") or {})
                    needs = dict(agent.get("needs") or {})
                    if definition.get("kind") == "state" and definition["id"] not in state:
                        state[definition["id"]] = schema.default(definition["id"], 0)
                    if definition.get("kind") == "need" and definition["id"] not in needs:
                        needs[definition["id"]] = schema.default(definition["id"], definition.get("target", 0))
                    agent["initial_state"] = state
                    agent["needs"] = needs
                    self.store.update_agent(agent["id"], agent)
            elif definition_type == "world_property":
                properties = dict(self.store.get_meta("world_properties", {}) or {})
                properties[str(definition["id"])] = definition.get("default")
                self.store.set_meta("world_properties", properties)
                labels = dict(self.store.get_meta("world_property_labels", {}) or {})
                labels[str(definition["id"])] = str(definition.get("label") or name)
                self.store.set_meta("world_property_labels", labels)
            elif table:
                self.store.put_definition(table, str(definition["id"]), definition)
                if definition_type == "capability":
                    for agent in self.store.list_agents():
                        capabilities = dict(agent.get("capabilities") or {})
                        capabilities.setdefault(definition["id"], definition.get("default", 0.0))
                        agent["capabilities"] = capabilities
                        self.store.update_agent(agent["id"], agent)
                if definition_type == "behavior":
                    for agent_id, _ in resolved_agents:
                        agent = self.store.get_agent(agent_id)
                        unlocked = list(agent.get("unlocked_bt_ids", []) or [])
                        if definition["id"] not in unlocked:
                            unlocked.append(definition["id"])
                            agent["unlocked_bt_ids"] = unlocked
                            self.store.update_agent(agent_id, agent)
            event_id = self.store.append_event("definition.registered", actor_id, world_minute, {"definition_type": definition_type, "name": name, "definition_id": definition.get("id"), "assigned_to": [visible for _, visible in resolved_agents], "evidence": str(proposal.get("evidence") or "")})
            result = {"status": "accepted", "intent_id": intent_id, "definition_type": definition_type, "name": name, "registered": True, "assigned_to": [visible for _, visible in resolved_agents], "event_id": event_id}
            self.store.finish_intent(intent_id, "accepted", result)
        if definition_type == "attribute":
            attributes = self.store.get_meta("attributes", []) or []
            self.attribute_schema = AttributeSchema(attributes, self.store.get_meta("rules", {}) or {})
            self.effects = EffectPipeline(attributes)
            self.conditions = ConditionEngine(self.store.get_meta("rules", {}) or {})
            self.rule_compiler = self._new_rule_compiler()
        return result

    def submit_entity_proposal(self, actor_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        intent_id = str(uuid4())
        entity_id = str(proposal.get("entity_id") or f"entity_{uuid4().hex[:10]}")
        data = {**proposal, "entity_id": entity_id, "actor_id": actor_id}
        errors = []
        if not proposal.get("entity_type"):
            errors.append("entity_type is required")
        if not proposal.get("name"):
            errors.append("name is required")
        if actor_id == "god":
            rules = self.store.get_meta("rules", {}) or {}
            resource_types = {str(item) for item in (rules.get("resource_entity_types", ["resource", "material"]) or [])}
            entity_type = str(proposal.get("entity_type", ""))
            state = proposal.get("state") if isinstance(proposal.get("state"), dict) else {}
            quantity = state.get("quantity", state.get("amount"))
            if entity_type in resource_types and isinstance(quantity, (int, float)) and float(quantity) > 0:
                errors.append("God cannot create a stocked resource directly; a behavior result must create or recover the supply")
        if proposal.get("location_id"):
            try:
                self.store.get_entity(str(proposal["location_id"]))
            except KeyError:
                errors.append("location_id does not exist")
        try:
            self.store.get_entity(entity_id)
            errors.append("entity_id already exists")
        except KeyError:
            pass
        if errors:
            failed_check = "god_resource_creation" if any("stocked resource" in error for error in errors) else "entity_schema"
            return self._reject_intent(intent_id, actor_id, "propose_entity", data, {"status": "rejected", "message": "Entity proposal failed validation.", "failed_check": failed_check, "errors": errors, "retryable": True})
        world_minute = int(self.store.get_meta("world_minute", 0))
        entity_data = {"description": proposal.get("description", ""), "initial_state": dict(proposal.get("state") or {})}
        if proposal.get("location_id"):
            entity_data["initial_state"]["location_id"] = proposal["location_id"]
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, "propose_entity", data, world_minute)
            self.store.put_entity(entity_id, str(proposal["entity_type"]), str(proposal["name"]), entity_data)
            event_id = self.store.append_event("entity.created", actor_id, world_minute, {"intent_id": intent_id, "entity_id": entity_id, "entity_type": proposal["entity_type"], "summary": proposal.get("description", "")})
            result = {"status": "accepted", "intent_id": intent_id, "entity_id": entity_id, "event_id": event_id, "applied": True}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def submit_story_beat(self, actor_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        """Activate one configured story beat after Platform checks its evidence."""

        intent_id = str(uuid4())
        story_root = self.store.get_meta("story", {}) or {}
        story_root = story_root.get("story", story_root) if isinstance(story_root, dict) else {}
        beats = list(story_root.get("beats", []) or []) if isinstance(story_root, dict) else []
        choice = str(proposal.get("beat", "")).strip()
        key = "".join(char for char in choice.casefold() if char.isalnum())
        matches = [
            beat for beat in beats if isinstance(beat, dict)
            and key == "".join(char for char in str(beat.get("agent_label") or beat.get("summary") or beat.get("id", "")).casefold() if char.isalnum())
        ]
        emergent = False
        if not matches and str(proposal.get("summary", "")).strip():
            summary = str(proposal.get("summary", "")).strip()
            if len(summary) < 8 or len(summary) > 500:
                return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "An emergent story beat summary must be 8 to 500 characters.", "failed_check": "story_summary", "retryable": True})
            beat = {
                "id": f"emergent:{uuid4().hex[:12]}",
                "agent_label": choice or summary[:80],
                "summary": summary,
                "audience": list(proposal.get("audience", []) or []),
                "fact": str(proposal.get("fact", "")).strip(),
                "goal_hint": str(proposal.get("goal_hint", "")).strip(),
                "unlock_choices": list(proposal.get("unlock_choices", []) or []),
                "public_event": "story.emergent_beat",
            }
            matches = [beat]
            emergent = True
        if len(matches) != 1:
            return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {
                "status": "rejected", "message": "Choose one exact configured story beat from the current story view.", "failed_check": "story_beat_choice", "retryable": True,
                "available_choices": [str(beat.get("agent_label") or beat.get("summary") or beat.get("id", "")) for beat in beats if isinstance(beat, dict)],
            })
        beat = matches[0]
        beat_id = str(beat.get("id", ""))
        state = dict(self.store.get_meta("story_state", {}) or {})
        current = state.get(beat_id) if isinstance(state.get(beat_id), dict) else {}
        if current.get("status") in {"active", "completed"}:
            return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "This story beat is already active or completed.", "failed_check": "story_beat_state", "retryable": False})
        recent = self.store.recent_events(limit=500)
        source_event = next(
            (
                item for item in recent[:30]
                if bool(item.get("story_source"))
                or str(item.get("event_type", "")) in {"exploration.completed", "research.completed", "object.recovered", "relationship.changed"}
            ),
            None,
        )
        if emergent and source_event is None:
            return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "An emergent beat needs a recent discovery, research, recovery, or relationship result.", "failed_check": "story_evidence", "retryable": True})
        if emergent and source_event:
            prior_emergent = list(state.get("emergent_beats", []) or [])
            if any(int(item.get("source_event_id", -1)) == int(source_event.get("event_id", -2)) for item in prior_emergent if isinstance(item, dict)):
                return {"status": "wait", "reason": "This validated result already has an active emergent story beat; wait for a new discovery or relationship result."}
            beat["source_event_id"] = int(source_event.get("event_id"))
            # An emergent beat may interpret a validated result, but it cannot
            # silently invent a later delivery, public reading, gathering, or
            # movement. Require a matching event in the same chain before
            # allowing those claims into persistent story state.
            narrative = " ".join(str(proposal.get(key, "")) for key in ("summary", "fact", "goal_hint"))
            action_claims = (
                "brought", "delivered", "read aloud", "gathered", "met", "arrived",
                "带到", "送到", "宣读", "聚集", "会面", "到达", "公开",
            )
            if any(term in narrative.casefold() for term in action_claims):
                source_actor = str(source_event.get("actor_id") or "")
                source_event_id = int(source_event.get("event_id", 0) or 0)
                followup = next(
                    (
                        item for item in recent
                        if int(item.get("event_id", 0) or 0) > source_event_id
                        and str(item.get("actor_id") or "") == source_actor
                        and (
                            str(item.get("event_type", "")) in {"dialogue.completed", "dialogue.behavior_completed", "trade.completed"}
                            or (
                                str(item.get("event_type", "")) == "behavior.completed"
                                and str(item.get("behavior_id", "")) in {"travel", "communicate", "negotiate"}
                            )
                        )
                    ),
                    None,
                )
                if followup is None:
                    return self._reject_intent(
                        intent_id,
                        actor_id,
                        "propose_story_beat",
                        proposal,
                        {
                            "status": "rejected",
                            "message": "This emergent beat claims a later delivery, meeting, reading, or movement that is not present in a validated follow-up event. State only the source fact or wait until the action occurs.",
                            "failed_check": "story_unverified_action_claim",
                            "retryable": True,
                        },
                    )
        trigger_events = {str(item) for item in (beat.get("trigger_events", []) or [])}
        observed_events = {str(item.get("event_type", "")) for item in recent}
        if trigger_events and not trigger_events & observed_events:
            return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "The configured story beat has no observed trigger event yet.", "failed_check": "story_trigger", "retryable": True})
        required_facts = {str(item) for item in (beat.get("trigger_facts", []) or [])}
        if required_facts:
            rows = self.store.connection.execute("SELECT fact_key FROM knowledge_fact").fetchall()
            observed_facts = {str(row["fact_key"]) for row in rows}
            if not required_facts <= observed_facts:
                return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "The configured story beat is missing one or more required facts.", "failed_check": "story_facts", "retryable": True, "missing_facts": sorted(required_facts - observed_facts)})
        minute = int(self.store.get_meta("world_minute", 0))
        audience = [str(item) for item in (beat.get("audience", []) or [])]
        if emergent:
            resolved_audience: list[str] = []
            for value in audience[:8]:
                canonical = self.targets.canonical_id(value)
                if canonical and self.store.connection.execute("SELECT 1 FROM agent_state WHERE id=?", (canonical,)).fetchone():
                    resolved_audience.append(canonical)
            audience = resolved_audience
            if not audience:
                return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "An emergent beat needs at least one exact visible character audience.", "failed_check": "story_audience", "retryable": True})
        registered = {item["id"] for item in self.store.get_definitions("bt_def")}
        if emergent:
            labels = {
                "".join(char for char in str(item.get("agent_label") or item.get("summary") or item.get("id", "")).casefold() if char.isalnum()): str(item.get("id"))
                for item in self.store.get_definitions("bt_def")
            }
            beat["unlock_behavior_ids"] = [labels["".join(char for char in str(value).casefold() if char.isalnum())] for value in beat.get("unlock_choices", []) if "".join(char for char in str(value).casefold() if char.isalnum()) in labels]
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, "propose_story_beat", proposal, minute)
            state[beat_id] = {"status": "active", "started_minute": minute, "actor_id": actor_id}
            if emergent:
                emergent_beats = list(state.get("emergent_beats", []) or [])
                emergent_beats.append({key: value for key, value in beat.items() if key != "unlock_choices"})
                state["emergent_beats"] = emergent_beats[-64:]
            self.store.set_meta("story_state", state)
            for audience_id in audience:
                try:
                    target = self.store.get_agent(audience_id)
                except KeyError:
                    continue
                if beat.get("goal_hint"):
                    goals = list(target.get("story_goals", []) or [])
                    if str(beat["goal_hint"]) not in goals:
                        goals.append(str(beat["goal_hint"]))
                    target["story_goals"] = goals[-8:]
                for behavior_id in beat.get("unlock_behavior_ids", []) or []:
                    behavior_id = str(behavior_id)
                    if behavior_id in registered:
                        unlocked = list(target.get("unlocked_bt_ids", []) or [])
                        if behavior_id not in unlocked and behavior_id not in (target.get("initial_bt_ids", []) or []):
                            unlocked.append(behavior_id)
                        target["unlocked_bt_ids"] = unlocked
                if beat.get("fact"):
                    self.store.merge_knowledge_fact(target["id"], f"story:{beat_id}", {"fact": str(beat["fact"]), "source_type": "story", "source_id": beat_id, "confidence": 0.9, "observed_at": minute})
                self.store.update_agent(target["id"], target)
            event_id = self.store.append_event("story.beat.activated", actor_id, minute, {"beat_id": beat_id, "summary": beat.get("summary", ""), "audience": audience, "public_event": beat.get("public_event"), "reason": proposal.get("reason", "")})
            self.store.finish_intent(intent_id, "accepted", {"status": "accepted", "intent_id": intent_id, "beat": beat.get("agent_label") or beat_id, "event_id": event_id, "activated": True})
        return {"status": "accepted", "intent_id": intent_id, "beat": beat.get("agent_label") or beat_id, "event_id": event_id, "activated": True}

    def submit_world_change(self, actor_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        intent_id = str(uuid4())
        if not proposal.get("change_type") or not proposal.get("summary"):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "change_type and summary are required.", "retryable": True})
        change_type = str(proposal["change_type"])
        if change_type not in {"world_property", "rule", "relationship", "faction", "event"}:
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "Unsupported world change type.", "failed_check": "change_type", "retryable": True})
        if change_type in {"world_property", "event"} and proposal.get("property"):
            current_properties = self.store.get_meta("world_properties", {}) or {}
            proposal = {**proposal, "property": self._canonical_world_property(str(proposal["property"]), current_properties)}
            property_id = str(proposal["property"])
            if actor_id == "god" and not self._god_property_change_allowed(property_id):
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_world_change",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "God cannot directly change a world property. Submit a rule, entity, faction, or narrative proposal; world behavior must produce the state change.",
                        "failed_check": "god_direct_property_change",
                        "retryable": True,
                    },
                )
        if change_type == "world_property" and not proposal.get("property"):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "property is required for a world_property change.", "failed_check": "property", "retryable": True})
        if change_type == "rule" and not isinstance(proposal.get("rule"), dict):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "rule object is required for a rule change.", "failed_check": "rule", "retryable": True})
        if change_type == "rule" and isinstance(proposal.get("rule"), dict):
            valid, message = self.rule_compiler.validate(dict(proposal["rule"]))
            if not valid:
                return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": message, "failed_check": "rule_schema", "retryable": True})
            conditions_valid, conditions_message = self.rule_compiler.validate_declared_conditions(
                dict(proposal["rule"]), list(proposal.get("conditions") or [])
            )
            if not conditions_valid:
                return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": conditions_message, "failed_check": "rule_condition_semantics", "retryable": True})
            declared_valid, declared_message = self.rule_compiler.validate_declared_effects(
                dict(proposal["rule"]),
                list(proposal.get("effects") or []) + list(proposal.get("expected_effects") or []),
            )
            if not declared_valid:
                return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": declared_message, "failed_check": "rule_effect_semantics", "retryable": True})
            proposed_id = str(proposal["rule"].get("id") or proposal["rule"].get("name") or "").strip()
            existing_rules = (self.store.get_meta("rules", {}) or {}).get("world_rules", []) or []
            signal_conflict = self.rule_compiler.signal_conflict(dict(proposal["rule"]), existing_rules)
            if signal_conflict:
                conflict_type, conflict_name = signal_conflict
                if conflict_type == "cycle":
                    message = "A signal rule cannot wake the same consumer from another signal rule. Use the behavior result as the next state change, or wait for a new world event."
                    failed_check = "rule_signal_cycle"
                else:
                    message = f"A signal for this event and follow-up already exists ({conflict_name}); review its outcome instead of adding another signal."
                    failed_check = "rule_duplicate"
                return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": message, "failed_check": failed_check, "retryable": True})
            if proposed_id and any(str(item.get("id") or item.get("name") or "").strip() == proposed_id for item in existing_rules if isinstance(item, dict)):
                return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "A world rule with this name is already registered; review its existing outcome instead of adding a duplicate.", "failed_check": "rule_duplicate", "retryable": True})
        if change_type == "relationship" and not isinstance(proposal.get("relationship"), dict):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "relationship object is required for a relationship change.", "failed_check": "relationship", "retryable": True})
        if change_type == "faction" and not isinstance(proposal.get("faction"), dict):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "faction object is required for a faction change.", "failed_check": "faction", "retryable": True})
        if change_type == "event" and not proposal.get("property"):
            structured_mutation_types = {"state_delta", "need_delta", "attribute_delta", "attribute_set", "inventory_add", "inventory_remove", "world_property_delta", "relationship_delta", "entity_state_set"}
            structured_mutation = [item.get("type") or item.get("op") for item in (proposal.get("effects") or []) if isinstance(item, dict) and str(item.get("type") or item.get("op") or "") in structured_mutation_types]
            if structured_mutation:
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_world_change",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "Event effects must be narrative only: remove typed state-mutating effects. Use a world_property, relationship, faction, or rule proposal for structured change, or let a behavior outcome change state.",
                        "failed_check": "event_effects",
                        "retryable": True,
                    },
                )
        if change_type in {"world_property", "event"} and proposal.get("property"):
            property_name = str(proposal["property"])
            current_properties = self.store.get_meta("world_properties", {}) or {}
            before_value = current_properties.get(property_name)
            value = proposal.get("value")
            if isinstance(before_value, bool):
                if not isinstance(value, bool):
                    return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {
                        "status": "rejected", "message": f"World property {property_name} is boolean; value must be true or false.", "failed_check": "property_type", "retryable": True,
                    })
            elif isinstance(before_value, (int, float)):
                if isinstance(value, bool):
                    return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {
                        "status": "rejected", "message": f"World property {property_name} is numeric; value must be a number.", "failed_check": "property_type", "retryable": True,
                    })
                if isinstance(value, str):
                    try:
                        value = float(value.strip())
                    except ValueError:
                        return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {
                            "status": "rejected", "message": f"World property {property_name} is numeric; value must be a number.", "failed_check": "property_type", "retryable": True,
                        })
                elif not isinstance(value, (int, float)):
                    return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {
                        "status": "rejected", "message": f"World property {property_name} is numeric; value must be a number.", "failed_check": "property_type", "retryable": True,
                    })
                if isinstance(before_value, float):
                    value = float(value)
                elif isinstance(value, float) and value.is_integer():
                    value = int(value)
            proposal = {**proposal, "value": value}
            if change_type in {"world_property", "event"} and property_name in current_properties and current_properties.get(property_name) == value:
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_world_change",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "The proposed world property already has this value; submit a real state change or a narrative event without a property.",
                        "failed_check": "no_state_change",
                        "retryable": True,
                    },
                )
        world_minute = int(self.store.get_meta("world_minute", 0))
        evaluation_window = max(1, min(int(proposal.get("evaluation_window_minutes", 1440) or 1440), 43200))
        evaluation_due_minute = world_minute + evaluation_window
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, "propose_world_change", proposal, world_minute)
            effect: dict[str, Any] = {}
            if change_type == "world_property":
                effect = self._set_world_property(str(proposal["property"]), proposal.get("value"))
            elif change_type == "rule":
                rules = dict(self.store.get_meta("rules", {}) or {})
                world_rules = list(rules.get("world_rules", []))
                world_rules.append(dict(proposal["rule"]))
                rules["world_rules"] = world_rules
                self.store.set_meta("rules", rules)
                self.conditions = ConditionEngine(rules)
                self.rule_compiler = self._new_rule_compiler()
                effect = {"rule_added": dict(proposal["rule"]), "rule_count": len(world_rules)}
            elif change_type == "relationship":
                relation = dict(proposal["relationship"])
                source = relation.pop("from", relation.pop("from_id", ""))
                target = relation.pop("to", relation.pop("to_id", ""))
                # God and Story use visible names at the model boundary. Keep
                # the persisted relationship graph canonical so later views
                # and effects can resolve both directions reliably.
                participants = relation.pop("participants", None)
                if (not source or not target) and isinstance(participants, list) and len(participants) >= 2:
                    source, target = participants[0], participants[1]
                source = self.targets.canonical_id(str(source)) if source else None
                target = self.targets.canonical_id(str(target)) if target else None
                relation_type = str(relation.pop("type", "related"))
                if not source or not target:
                    self.store.finish_intent(intent_id, "rejected", {"status": "rejected", "message": "relationship from and to are required.", "failed_check": "relationship", "retryable": True})
                    return {"status": "rejected", "message": "relationship from and to are required.", "failed_check": "relationship", "retryable": True}
                self.store.put_relationship(str(source), str(target), relation_type, relation)
                effect = {"relationship": {"from": str(source), "to": str(target), "type": relation_type, **relation}}
            elif change_type == "faction":
                faction = dict(proposal["faction"])
                faction_id = str(faction.pop("id", faction.pop("faction_id", "")))
                if not faction_id:
                    self.store.finish_intent(intent_id, "rejected", {"status": "rejected", "message": "faction id is required.", "failed_check": "faction.id", "retryable": True})
                    return {"status": "rejected", "message": "faction id is required.", "failed_check": "faction.id", "retryable": True}
                try:
                    current = self.store.get_entity(faction_id)
                except KeyError:
                    self.store.finish_intent(intent_id, "rejected", {"status": "rejected", "message": "faction does not exist.", "failed_check": "faction.id", "retryable": True})
                    return {"status": "rejected", "message": "faction does not exist.", "failed_check": "faction.id", "retryable": True}
                current.update(faction)
                self.store.put_entity(faction_id, "faction", current.get("name", faction_id), {key: value for key, value in current.items() if key not in {"id", "entity_type", "name"}})
                effect = {"faction_id": faction_id, "changed_fields": sorted(faction), "after": {key: current.get(key) for key in faction}}
            elif change_type == "event" and proposal.get("property"):
                effect = self._set_world_property(str(proposal["property"]), proposal.get("value"))
            else:
                effect = {"event_recorded": True, "state_changed": False}
            event_data = {
                "intent_id": intent_id,
                "change_type": change_type,
                "summary": str(proposal.get("summary", "")),
                "scope": str(proposal.get("scope", "world")),
                "conditions": list(proposal.get("conditions") or []),
                "effects": list(proposal.get("effects") or []),
                "expected_effects": list(proposal.get("expected_effects") or proposal.get("effects") or []),
                "effect": effect,
                "proposal_id": intent_id,
                "evaluation_due_minute": evaluation_due_minute,
            }
            expected = self._world_proposal_expectation(change_type, proposal, effect)
            event_data["expected_state"] = expected
            event_id = self.store.append_event(f"world.change.{change_type}", actor_id, world_minute, event_data)
            self.store.put_world_proposal(intent_id, intent_id, actor_id, change_type, "pending_evaluation", world_minute, evaluation_due_minute, {"effects": event_data["expected_effects"], "summary": event_data["summary"], "state": expected}, {"event_id": event_id, "effect": effect})
            result = {"status": "accepted", "intent_id": intent_id, "proposal_id": intent_id, "event_id": event_id, "evaluation_status": "pending_evaluation", "evaluation_due_minute": evaluation_due_minute, "applied": True, "applied_effects": bool(effect.get("state_changed", True)), "change_type": change_type, "effect": effect, "expected_effects": event_data["expected_effects"]}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def _world_proposal_expectation(self, change_type: str, proposal: dict[str, Any], effect: dict[str, Any]) -> dict[str, Any]:
        """Compile the small machine-checkable expectation for God review.

        Free-form prose remains an explanation for the model.  Evaluation
        uses typed fields or a configured follow-up event, so unrelated events
        cannot make a proposal look successful merely because something
        happened during its observation window.
        """

        expected: dict[str, Any] = {"change_type": str(change_type), "mode": "signal_only"}
        if change_type in {"world_property", "event"} and proposal.get("property"):
            expected.update({"mode": "property_value", "property": str(proposal["property"]), "target_value": proposal.get("value")})
        elif change_type == "rule":
            rule = proposal.get("rule") if isinstance(proposal.get("rule"), dict) else {}
            expected.update({
                "mode": "followup_event",
                "rule_id": str(rule.get("id") or rule.get("name") or ""),
                "followup_behaviors": list(rule.get("behavior_names") or rule.get("behavior_ids") or rule.get("action_names") or []),
            })
        elif change_type == "relationship":
            expected.update({"mode": "relationship_state", "relationship": dict(effect.get("relationship") or proposal.get("relationship") or {})})
        elif change_type == "faction":
            expected.update({"mode": "faction_state", "faction_id": effect.get("faction_id"), "fields": dict(effect.get("after") or {})})
        return expected

    @staticmethod
    def _canonical_world_property(value: str, properties: dict[str, Any]) -> str:
        """Map a readable property label to its configured storage key."""

        def normalized(text: str) -> str:
            return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(text).casefold())).strip("_")

        candidate = normalized(value)
        for key in properties:
            if normalized(str(key)) == candidate:
                return str(key)
        return candidate or str(value).strip()

    def _god_property_change_allowed(self, property_id: str) -> bool:
        """Return whether the package explicitly permits God to set a property.

        The default is deny. Shared resources and personal/social outcomes must
        be changed by configured behaviors; a package may opt in to a narrow
        world property only when it has a documented reason.
        """

        try:
            entity = self.store.get_entity(property_id)
        except KeyError:
            entity = None
        if entity and entity.get("entity_type") == "resource":
            return False
        policy = self.store.get_meta("rules", {}).get("world_change_policy", {}) or {}
        allowed = policy.get("god_direct_properties", []) if isinstance(policy, dict) else []
        return property_id in {str(item) for item in (allowed or [])}

    def validate_world_template(self, template: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        if not isinstance(template, dict):
            return {"status": "ok", "valid": False, "errors": ["template must be an object"], "failed_check": "template", "message": "Template must be an object.", "retryable": True}
        world = template.get("world")
        if not isinstance(world, dict) or not world.get("id"):
            errors.append("world.id is required")
        if template.get("template_version") is not None and not isinstance(template.get("template_version"), str):
            errors.append("template_version must be a string")
        collections = {key: value for key, value in template.items() if isinstance(value, list)}
        for key, values in collections.items():
            ids = [value.get("id") for value in values if isinstance(value, dict)]
            if len(ids) != len(set(ids)):
                errors.append(f"duplicate id in {key}")
        action_ids = {x.get("id") for x in collections.get("actions", []) if isinstance(x, dict)}
        bt_ids = {x.get("id") for x in collections.get("behavior_trees", []) if isinstance(x, dict)}
        for bt in collections.get("behavior_trees", []):
            for node in bt.get("nodes", []):
                if node.get("type") == "action" and node.get("action_id") not in action_ids:
                    errors.append(f"behavior {bt.get('id')} references unknown action {node.get('action_id')}")
        for agent in collections.get("agents", []):
            for bt_id in agent.get("initial_bt_ids", []):
                if bt_id not in bt_ids:
                    errors.append(f"agent {agent.get('id')} references unknown behavior {bt_id}")
        return {
            "status": "ok",
            "valid": not errors,
            "errors": errors,
            "failed_check": "template" if errors else None,
            "message": "Template is valid." if not errors else "Template needs correction.",
            "retryable": bool(errors),
        }

    def submit_world_template(self, actor_id: str, template: dict[str, Any]) -> dict[str, Any]:
        validation = self.validate_world_template(template)
        if not validation["valid"]:
            return {"status": "rejected", "message": "World template failed validation.", "errors": validation["errors"], "retryable": True}
        intent_id = str(uuid4())
        world_minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, "submit_world_template", {"template": template}, world_minute)
            self.store.append_event("world.template.accepted", actor_id, world_minute, {"template_keys": sorted(template)})
            result = {"status": "accepted", "intent_id": intent_id, "template_valid": True, "message": "Template accepted for new-instance bootstrap."}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def bootstrap_world_from_template(self, template: dict[str, Any], instance_id: str, runtime_root: Path | None = None) -> dict[str, Any]:
        from ..bootstrap.service import create_instance_from_template

        validation = self.validate_world_template(template)
        if not validation["valid"]:
            return {"status": "rejected", "message": "World template failed validation.", "errors": validation["errors"], "retryable": True}
        db_path = create_instance_from_template(template, instance_id, runtime_root)
        return {"status": "accepted", "instance_id": instance_id, "db_path": str(db_path)}

    # ------------------------------------------------------------------
    # Deterministic Platform execution.  This code knows only generic
    # Action/BT fields; story packages provide the definitions.

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

        def resolve(value: Any) -> Any:
            if isinstance(value, str) and value.startswith("context."):
                # An unbound declarative reference is absent data, never a
                # literal value such as ``context.topic`` in world history.
                # Action defaults are applied immediately after this pass;
                # required targets then fail their configured condition.
                return context.get(value.removeprefix("context."))
            if isinstance(value, dict):
                return {key: resolve(item) for key, item in value.items()}
            if isinstance(value, list):
                return [resolve(item) for item in value]
            return value

        return {key: resolve(value) for key, value in parameters.items()}

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
        food_items = (self.store.get_meta("rules", {}) or {}).get("food_items", []) or []
        for key, default in (action.get("parameter_defaults", {}) or {}).items():
            value = result.get(key)
            if value not in (None, "") and not (isinstance(value, str) and value.startswith("context.")):
                continue
            if default == "first_food":
                result[key] = next((item for item in food_items if float((agent.get("inventory") or {}).get(item, 0) or 0) >= 1), value)
            elif default == "first_available_resource":
                location_id = (agent.get("initial_state") or {}).get("location_id")
                sources = self.store.entities_at_location(location_id) if location_id else []
                rules = self.store.get_meta("rules", {}) or {}
                entity_types = {str(item) for item in (rules.get("resource_entity_types", ["resource", "material"]) or [])}
                protected_key = str(rules.get("resource_protected_state", "protected"))
                source = next((item for item in sources if item.get("entity_type") in entity_types and not (item.get("initial_state") or {}).get(protected_key) and self._entity_quantity(item) > 0), None)
                result[key] = source.get("id") if source else value
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

    def _apply_minute_effects(self, minute: int) -> None:
        rules = self.store.get_meta("rules", {}) or {}
        for regeneration in rules.get("resource_regeneration", []) or []:
            every = max(1, int(regeneration.get("every_minutes", 1)))
            if minute % every != 0:
                continue
            try:
                entity = self.store.get_entity(str(regeneration["resource_id"]))
            except KeyError:
                continue
            state = dict(entity.get("initial_state") or {})
            key = "amount" if isinstance(entity.get("amount"), (int, float)) else "quantity"
            before = float(entity.get("amount") if key == "amount" else state.get("quantity", 0) or 0)
            cap = float(regeneration.get("cap", before) or before)
            after = min(cap, before + float(regeneration.get("amount", 0) or 0))
            if after <= before:
                continue
            if key == "amount":
                entity["amount"] = after
            else:
                state[key] = after
                entity["initial_state"] = state
            payload = {name: value for name, value in entity.items() if name not in {"id", "entity_type", "name"}}
            self.store.put_entity(entity["id"], entity["entity_type"], entity["name"], payload)
            self.store.sync_world_property_from_resource(entity["id"], after)
            self.store.append_event("resource.regenerated", None, minute, {"resource_id": entity["id"], "amount": after - before, "total": after})
        for effect in rules.get("minute_effects", []) or []:
            every = max(1, int(effect.get("every_minutes", 1)))
            if minute % every != 0:
                continue
            scope = effect.get("scope")
            definition = effect.get("effect", {}) or {}
            if scope == "agent":
                changed_agents: list[dict[str, Any]] = []
                for agent in self.store.list_agents():
                    state = dict(agent.get("initial_state") or {})
                    before_state = dict(state)
                    before_needs = dict(agent.get("needs") or {})
                    condition = str(effect.get("condition") or "").strip()
                    if condition == "action.none" and state.get("status") in {"executing", "paused"}:
                        continue
                    if condition and condition != "action.none" and not self.conditions.evaluate(
                        condition,
                        agent=agent,
                        target={},
                        context={"current_location": self._entity_for_context(state.get("location_id"))},
                        world=self.store.get_meta("world_properties", {}) or {},
                    ):
                        continue
                    self._apply_simple_effect(agent, definition)
                    self._emit_minute_effect_events(effect, agent, minute, before_state, before_needs)
                    state = dict(agent.get("initial_state") or {})
                    vital = self.attribute_schema.vital_attribute()
                    vital_value = float(state.get(vital, 1) or 0) if vital else 1
                    if vital and vital_value <= self.attribute_schema.minimum(vital) and state.get("status") not in {"unconscious", "dead"}:
                        state["status"] = "unconscious"
                        conditions = list(state.get("conditions") or [])
                        if "incapacitated" not in conditions:
                            conditions.append("incapacitated")
                        state["conditions"] = conditions
                        agent["initial_state"] = state
                        self.store.append_event("agent.incapacitated", agent["id"], minute, {"reason": effect.get("id", "minute_effect")})
                    changed_agents.append(agent)
                self.store.update_agents_bulk(changed_agents)
            elif scope == "world":
                key = definition.get("world_property") or definition.get("property") or definition.get("need")
                if key:
                    properties = dict(self.store.get_meta("world_properties", {}) or {})
                    self._set_world_property(str(key), float(properties.get(key, 0)) + float(definition.get("delta", 0)))
            elif scope == "relationship":
                relation_delta = float(definition.get("relation_strength", definition.get("delta", 0)))
                for relation in self.store.list_relationships():
                    data = {key: value for key, value in relation.items() if key not in {"from", "to", "type"}}
                    data["relation_strength"] = float(data.get("relation_strength", data.get("value", 0))) + relation_delta
                    self.store.put_relationship(relation["from"], relation["to"], relation["type"], data)

    def _apply_simple_effect(self, agent: dict[str, Any], effect: dict[str, Any]) -> None:
        self.effects.apply(agent, effect)

    def _emit_minute_effect_events(
        self,
        rule: dict[str, Any],
        agent: dict[str, Any],
        minute: int,
        before_state: dict[str, Any],
        before_needs: dict[str, Any],
    ) -> None:
        """Emit configured threshold or recurring events after a minute effect."""

        configured = rule.get("events", []) or []
        if not isinstance(configured, list):
            return
        event_state = dict(self.store.get_meta("minute_effect_event_state", {}) or {})
        location = self._entity_for_context((agent.get("initial_state") or {}).get("location_id"))
        rules = self.store.get_meta("rules", {}) or {}
        context = {
            "current_location": location,
            "location_id": (agent.get("initial_state") or {}).get("location_id"),
            "satiety_alert_threshold": float(rules.get("satiety_alert_threshold", 40) or 40),
        }
        world = self.store.get_meta("world_properties", {}) or {}
        for index, item in enumerate(configured):
            if not isinstance(item, dict):
                continue
            event_type = str(item.get("event_type") or item.get("event") or "").strip()
            condition = str(item.get("condition") or rule.get("condition") or "").strip()
            if not event_type or not condition:
                continue
            active = bool(self.conditions.evaluate(condition, agent=agent, target={}, context=context, world=world))
            state_key = f"{agent.get('id')}:{rule.get('id', index)}:{item.get('id', index)}"
            previous = bool(event_state.get(state_key, False))
            edge = str(item.get("edge", "each")).strip().lower()
            if edge == "enter":
                emit = active and not previous
            elif edge == "exit":
                emit = not active and previous
            else:
                emit = active
            if emit:
                changed: dict[str, dict[str, Any]] = {}
                for key in set(before_state) | set((agent.get("initial_state") or {})):
                    before = before_state.get(key)
                    after = (agent.get("initial_state") or {}).get(key)
                    if before != after:
                        changed[key] = {"before": before, "after": after}
                for key in set(before_needs) | set(agent.get("needs") or {}):
                    before = before_needs.get(key)
                    after = (agent.get("needs") or {}).get(key)
                    if before != after:
                        changed[key] = {"before": before, "after": after}
                payload = dict(item.get("data") or {})
                payload.update({"rule_id": rule.get("id"), "edge": edge, "changes": changed})
                if item.get("summary"):
                    payload["summary"] = str(item["summary"])
                if item.get("trend") is True:
                    payload["trend"] = True
                self.store.append_event(event_type, agent["id"], minute, payload)
            event_state[state_key] = active
        self.store.set_meta("minute_effect_event_state", event_state)

    def _world_trigger_context(self) -> dict[str, Any]:
        context = dict(self.store.get_meta("world_properties", {}) or {})
        for resource in self.store.entities_of_type("resource"):
            try:
                entity = self.store.get_entity(resource["id"])
            except KeyError:
                continue
            amount = entity.get("amount", (entity.get("initial_state") or {}).get("quantity"))
            if isinstance(amount, (int, float)):
                self.store.sync_world_property_from_resource(resource["id"], amount)
                context[resource["id"]] = amount
        return context

    def _recent_event_types(self, limit: int = 120) -> list[str]:
        rows = self.store.connection.execute(
            "SELECT DISTINCT event_type FROM event_log WHERE world_minute<=? ORDER BY event_id DESC LIMIT ?",
            (int(self.store.get_meta("world_minute", 0)), max(1, min(int(limit), 500))),
        ).fetchall()
        return [str(row["event_type"]) for row in rows]

    def _emit_world_rule_signals(self, minute: int) -> None:
        """Emit edge-triggered signals from active declarative world rules."""

        world = self._world_trigger_context()
        signals = self.rule_compiler.signal_specs(agent={}, context={"recent_event_types": self._recent_event_types()}, world=world)
        state = dict(self.store.get_meta("world_rule_signal_state", {}) or {})
        for signal in signals:
            name = str(signal.get("name") or "world_rule")
            state_key = str(signal.get("id") or name)
            was_active = bool(state.get(state_key, False))
            if not was_active:
                event_id = self.store.append_event(
                    str(signal.get("event_type") or f"world.rule.{name}"),
                    None,
                    minute,
                    {
                        "rule": name,
                        "condition": signal.get("condition"),
                        "effect": signal.get("effect"),
                        "signal": True,
                    },
                )
                for index, target in enumerate(signal.get("wake") or []):
                    target = str(target)
                    if target in {agent["id"] for agent in self.store.list_agents()}:
                        role, actor_id = "agent_reflection", target
                    else:
                        role, actor_id = target, None
                    self._queue_event_consumer_task(role, event_id, str(signal.get("event_type") or f"world.rule.{name}"), actor_id, minute, task_type=f"rule:{name}")
        active_ids = {str(item.get("id")) for item in signals}
        for rule in self.rule_compiler.all_rules():
            state_key = str(rule.get("id") or rule.get("name") or "world_rule")
            state[state_key] = state_key in active_ids
        self.store.set_meta("world_rule_signal_state", state)

    def _set_world_property(self, key: str, value: Any) -> dict[str, Any]:
        """Update one world property and its shared-resource projection atomically.

        A resource may also be exposed as a world property, for example
        ``village_food``.  The resource ledger remains the world quantity, while
        the property is the prompt/condition projection.  Updating only one side
        makes triggers, Agent context and God proposals disagree.
        """

        properties = dict(self.store.get_meta("world_properties", {}) or {})
        before = properties.get(str(key))
        properties[str(key)] = value
        self.store.set_meta("world_properties", properties)
        projection = self._sync_world_resource_projection(str(key), value)
        effect = {"world_property": str(key), "before": before, "after": value}
        if projection:
            effect["resource_projection"] = projection
        return effect

    def _sync_world_resource_projection(self, key: str, value: Any) -> dict[str, Any] | None:
        """Keep a numeric shared resource equal to its world-property view."""

        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        try:
            entity = self.store.get_entity(str(key))
        except KeyError:
            return None
        if entity.get("entity_type") != "resource":
            return None
        state = dict(entity.get("initial_state") or {})
        before = entity.get("amount", state.get("quantity", state.get("amount")))
        if not isinstance(before, (int, float)):
            return None
        if "amount" in entity:
            entity["amount"] = value
        else:
            state_key = "quantity" if "quantity" in state else "amount"
            state[state_key] = value
            entity["initial_state"] = state
        self.store.put_entity(
            entity["id"],
            entity["entity_type"],
            entity["name"],
            {name: item for name, item in entity.items() if name not in {"id", "entity_type", "name"}},
        )
        return {"entity_id": entity["id"], "before": before, "after": value}

    def _emit_evolution_triggers(self, minute: int) -> None:
        evolution = self.store.get_meta("evolution", {}) or {}
        config = evolution.get("evolution", evolution) if isinstance(evolution, dict) else {}
        triggers = config.get("triggers", []) if isinstance(config, dict) else []
        states = dict(self.store.get_meta("evolution_trigger_state", {}) or {})
        world = self._world_trigger_context()
        agents = {agent["id"] for agent in self.store.list_agents()}
        for trigger in triggers:
            trigger_id = str(trigger.get("id", ""))
            if not trigger_id:
                continue
            expression = str(trigger.get("when", ""))
            active = self.conditions.evaluate(expression, agent={}, target={}, world=world, context={}) if expression else False
            previous = bool(states.get(trigger_id, False))
            if active and not previous:
                self.store.append_event("evolution.triggered", None, minute, {"trigger_id": trigger_id, "condition": expression, "invoke": trigger.get("invoke", [])})
                for index, target in enumerate(trigger.get("invoke", []) or []):
                    target = str(target)
                    if target in agents:
                        role, task_type, actor_id = "agent_reflection", f"trigger:{trigger_id}", target
                    elif target.startswith("god"):
                        role, task_type, actor_id = "god", target, None
                    else:
                        role, task_type, actor_id = "agent_reflection", target, None
                    self.store.put_evolution_task(f"trigger:{trigger_id}:{minute}:{index}", role, actor_id, task_type, minute, 0, data={"trigger_id": trigger_id})
            states[trigger_id] = active
        self.store.set_meta("evolution_trigger_state", states)

    def _emit_due_evolution_tasks(self, minute: int) -> None:
        for task in self.store.due_evolution_tasks(minute):
            self.store.mark_evolution_task_ready(task["task_id"])
            self.store.append_event(
                "evolution.task_due",
                task.get("actor_id"),
                minute,
                {"task_id": task["task_id"], "role": task["role"], "task_type": task["task_type"]},
            )

    def ready_evolution_tasks(self) -> list[dict[str, Any]]:
        """Return low-frequency Agent tasks without invoking an LLM."""

        return self.store.ready_evolution_tasks()

    def claim_evolution_task(self, task_id: str) -> dict[str, Any] | None:
        minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            return self.store.claim_evolution_task(task_id, minute)

    def complete_evolution_task(self, task_id: str) -> None:
        minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            self.store.complete_evolution_task(task_id, minute)

    def retry_evolution_task(self, task_id: str, retry_minute: int, reason: str) -> None:
        """Return a failed low-frequency task to the scheduler with an audit event."""

        minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            self.store.reschedule_evolution_task(task_id, max(minute + 1, int(retry_minute)))
            self.store.append_event(
                "evolution.task_retry",
                None,
                minute,
                {"task_id": task_id, "retry_minute": max(minute + 1, int(retry_minute)), "reason": str(reason)},
            )

    def step(self) -> dict[str, Any]:
        """Commit exactly one world-minute tick."""

        snapshot = self.store.world_snapshot()
        start = int(snapshot["world_minute"])
        minute = start + 1
        completed: list[str] = []
        failed: list[str] = []
        with self.store.transaction():
            self._apply_minute_effects(minute)
            for agent in self.store.list_agents():
                before = agent.get("initial_state", {}).get("active_bt_instance")
                if not before:
                    continue
                self._progress_agent(agent, minute)
                after = self.store.get_agent(agent["id"]).get("initial_state", {})
                result = after.get("last_platform_result") or {}
                if result.get("status") == "completed" and result.get("behavior_ref"):
                    completed.append(agent["id"])
                if result.get("status") == "failed" and result.get("behavior_ref"):
                    failed.append(agent["id"])
            self.store.set_meta("world_minute", minute)
            self.store.append_event("world_minute.advanced", None, minute, {"minute": minute})
            self._emit_world_rule_signals(minute)
            self.proposal_evaluator.evaluate_due(minute)
            self._emit_evolution_triggers(minute)
            self._emit_due_evolution_tasks(minute)
        return {
            "status": "advanced",
            "from_minute": start,
            "to_minute": minute,
            "snapshot_revision": int(snapshot["world_revision"]),
            "world_revision": int(self.store.get_meta("world_revision", 0) or 0),
            "completed_agents": sorted(set(completed)),
            "failed_agents": sorted(set(failed)),
        }

    def tick_snapshot(self) -> dict[str, Any]:
        """Expose the current read-only boundary for schedulers and audits."""

        return self.store.world_snapshot()

    def metrics(self) -> dict[str, Any]:
        active = self.store.connection.execute("SELECT COUNT(*) AS n FROM agent_state WHERE json_extract(data_json,'$.initial_state.status') IN ('executing','paused')").fetchone()["n"]
        events = self.store.connection.execute("SELECT COUNT(*) AS n FROM event_log").fetchone()["n"]
        intents = self.store.connection.execute("SELECT COUNT(*) AS n FROM intent").fetchone()["n"]
        llm_calls = self.store.connection.execute("SELECT COUNT(*) AS n FROM llm_call").fetchone()["n"]
        return {"world_minute": int(self.store.get_meta("world_minute", 0)), "active_behaviors": int(active), "events": int(events), "intents": int(intents), "llm_calls": int(llm_calls), "llm_cache": self.store.llm_cache_metrics()}

    def export_audit(self) -> dict[str, Any]:
        def rows(query: str) -> list[dict[str, Any]]:
            result = []
            for row in self.store.connection.execute(query).fetchall():
                result.append(dict(row))
                if "data_json" in result[-1]:
                    result[-1]["data"] = json.loads(result[-1].pop("data_json"))
                if "result_json" in result[-1] and result[-1]["result_json"]:
                    result[-1]["result"] = json.loads(result[-1].pop("result_json"))
            return result
        return {"status": "ok", "world_minute": int(self.store.get_meta("world_minute", 0)), "events": rows("SELECT * FROM event_log ORDER BY event_id"), "intents": rows("SELECT * FROM intent ORDER BY created_minute,intent_id"), "llm_calls": rows("SELECT * FROM llm_call ORDER BY call_id")}

    def snapshot(self, destination: Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        backup = sqlite3.connect(destination)
        try:
            self.store.connection.backup(backup)
        finally:
            backup.close()
        return destination

    def restore_snapshot(self, source: Path) -> dict[str, Any]:
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(source)
        backup = sqlite3.connect(source)
        try:
            backup.backup(self.store.connection)
            self.store.connection.commit()
        finally:
            backup.close()
        return self.status()

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
            self.store.append_event("behavior.interrupted", actor_id, minute, {"execution_id": execution_id, "reason": reason, "hard": hard, "outcome": outcome})
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
            self.store.append_event("behavior.resumed", actor_id, minute, {"execution_id": execution_id})
        return {"status": "resumed", "execution_id": execution_id}
