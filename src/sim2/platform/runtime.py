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


class Platform:
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
        self.conditions = ConditionEngine((self.store.get_meta("rules", {}) or {}))
        self.rule_compiler = self._new_rule_compiler()
        self.effects = EffectPipeline()
        self.targets = TargetResolver(self.store)
        self.resources = ResourceLedger(self.store)

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
                    role: router.new_provider_for_name(provider_name)
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
        agent = {
            "id": player_id,
            "name": name or player_id,
            "role": "player",
            "traits": dict(origin.get("traits") or {}),
            "ultimate_goal": origin.get("starting_goal", "Find a meaningful place in this world."),
            "beliefs": [],
            "needs": {
                str(item["id"]): float(item.get("target", 100))
                for item in ((self.store.get_meta("rules", {}) or {}).get("needs", []) or [])
                if isinstance(item, dict) and item.get("id")
            },
            "knowledge_seed": list(template.get("knowledge_seed") or []),
            "initial_bt_ids": list(template.get("initial_bt_ids") or []),
            "capabilities": {},
            "initial_state": {"location_id": location_id, "health": 100, "energy": 100, "status": "idle", "injuries": [], "conditions": [], "active_bt_instance": None, "active_action": None, "remaining_minutes": 0},
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
                "health": state.get("health"),
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
        expected = {
            "summary": bt.get("summary") or bt.get("description") or behavior_id,
            "success": list(bt.get("success_results", []) or []),
            "failure": list(bt.get("failure_results", []) or []),
            "estimated_minutes": remaining_minutes,
            "goal_tags": list(bt.get("goal_tags", []) or []),
            "drive_tags": list(bt.get("drive_tags", []) or []),
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

    def submit_experience(self, actor_id: str, event_ref: str, summary: str, confidence: float) -> dict[str, Any]:
        summary = str(summary).strip()
        data = {"event_ref": event_ref, "summary": summary, "confidence": confidence}
        intent_id = str(uuid4())
        if not summary.strip():
            return self._reject_intent(intent_id, actor_id, "record_experience", data, {"status": "rejected", "message": "Experience summary is required.", "failed_check": "summary", "retryable": True})
        if len(summary) > 500:
            return self._reject_intent(intent_id, actor_id, "record_experience", data, {"status": "rejected", "message": "Experience summary must be at most 500 characters.", "failed_check": "summary_length", "retryable": True})
        if not 0 <= confidence <= 1:
            return self._reject_intent(intent_id, actor_id, "record_experience", data, {"status": "rejected", "message": "Confidence must be between 0 and 1.", "failed_check": "confidence", "retryable": True})
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
            "id": behavior_id, "version": 1, "scope": "personal", "summary": proposal.get("summary") or proposal["goal"],
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
        if emergent and not any(str(item.get("event_type", "")) in {"exploration.completed", "research.completed", "object.recovered", "relationship.changed"} for item in recent[:30]):
            return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "An emergent beat needs a recent discovery, research, recovery, or relationship result.", "failed_check": "story_evidence", "retryable": True})
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
            if proposed_id and any(str(item.get("id") or item.get("name") or "").strip() == proposed_id for item in existing_rules if isinstance(item, dict)):
                return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "A world rule with this name is already registered; review its existing outcome instead of adding a duplicate.", "failed_check": "rule_duplicate", "retryable": True})
        if change_type == "relationship" and not isinstance(proposal.get("relationship"), dict):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "relationship object is required for a relationship change.", "failed_check": "relationship", "retryable": True})
        if change_type == "faction" and not isinstance(proposal.get("faction"), dict):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "faction object is required for a faction change.", "failed_check": "faction", "retryable": True})
        if change_type == "event" and not proposal.get("property"):
            effects_text = " ".join(str(item).casefold() for item in (proposal.get("effects") or []))
            direct_terms = ("increase", "decrease", "restore", "activate", "stabilize", "reduce", "raise", "lower", "set ", "change", "remain", " can ")
            if any(term in effects_text for term in direct_terms):
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_world_change",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "Event effects must be narrative only. Use world_property, rule, relationship or faction for direct state changes.",
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
            }
            event_id = self.store.append_event(f"world.change.{change_type}", actor_id, world_minute, event_data)
            result = {"status": "accepted", "intent_id": intent_id, "event_id": event_id, "applied": True, "applied_effects": bool(effect.get("state_changed", True)), "change_type": change_type, "effect": effect, "expected_effects": event_data["expected_effects"]}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

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
                    result.append({"action_id": node["action_id"], "parameters": dict(node.get("parameters") or {}), "parallel_group": parallel_group})
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
        return {
            "location": state.get("location_id"),
            "health": state.get("health"),
            "energy": state.get("energy"),
            "needs": dict(agent.get("needs") or {}),
            "inventory": dict(agent.get("inventory") or {}),
            "capabilities": dict(agent.get("capabilities") or {}),
        }

    @staticmethod
    def _execution_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        """Describe only changed values so the next Agent sees consequences."""

        delta: dict[str, Any] = {}
        for key in ("location", "health", "energy"):
            if before.get(key) != after.get(key):
                delta[key] = {"before": before.get(key), "after": after.get(key)}
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
                return context.get(value.removeprefix("context."), value)
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
            if not isinstance(value, (int, float)) or key in {"time", "attention"} and key not in state:
                if key == "attention":
                    state["attention"] = float(state.get("attention", 0)) + float(value or 0)
                continue
            numeric = float(value)
            if key in {"exposure", "attention"}:
                state[key] = max(0.0, min(100.0, float(state.get(key, 0)) + numeric))
            elif key in {"energy", "health"}:
                state[key] = max(0.0, min(100.0, float(state.get(key, 0)) - numeric))
            elif key in needs:
                needs[key] = max(0.0, float(needs.get(key, 0)) - numeric)
            else:
                state[key] = float(state.get(key, 0)) + numeric
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
                    if float(state.get("health", 100) or 0) <= 0 and state.get("status") not in {"unconscious", "dead"}:
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

    @staticmethod
    def _apply_simple_effect(agent: dict[str, Any], effect: dict[str, Any]) -> None:
        if effect.get("need"):
            key = str(effect["need"])
            state = dict(agent.get("initial_state") or {})
            if key in state:
                state[key] = max(0.0, min(100.0, float(state.get(key, 0)) + float(effect.get("delta", 0))))
                agent["initial_state"] = state
            else:
                needs = dict(agent.get("needs") or {})
                needs[key] = max(0.0, min(100.0, float(needs.get(key, 0)) + float(effect.get("delta", 0))))
                agent["needs"] = needs
        elif effect.get("state"):
            state = dict(agent.get("initial_state") or {})
            key = str(effect["state"])
            state[key] = max(0.0, min(100.0, float(state.get(key, 0)) + float(effect.get("delta", 0))))
            agent["initial_state"] = state

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
            "item_id": parameters.get("item_id"),
            "object_id": parameters.get("object_id"),
            "inventory": agent.get("inventory", {}) or {},
            "food_items": (self.store.get_meta("rules", {}) or {}).get("food_items", []) or [],
            "station_tags": [],
            "current_location": current_location,
            "current_object": self._entity_for_context(parameters.get("object_id")),
            "mapped_areas": self.store.explored_areas(agent["id"]),
            "researched_sources": self.store.researched_sources(agent["id"]),
            "target": resolved_target or {},
            "target.visible": (resolved_target or {}).get("visible", False),
            "target.available": (resolved_target or {}).get("available", False),
            "known_route_destinations": reachable_locations(self.store, location_id),
            "recent_event_types": self._recent_event_types(),
        }
        if action_id == "craft_recipe":
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
        agent["initial_state"] = state
        self.store.put_action_instance(action_instance_id, execution_id, agent["id"], action_id, "running", 0, duration, {"parameters": parameters, "target": resolved_target, "reservation": reservation, "no_op": already_at_destination}, minute, minute)
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
            "item_id": parameters.get("item_id"),
            "object_id": parameters.get("object_id"),
            "inventory": agent.get("inventory", {}) or {},
            "food_items": (self.store.get_meta("rules", {}) or {}).get("food_items", []) or [],
            "station_tags": [],
            "current_location": current_location,
            "current_object": self._entity_for_context(parameters.get("object_id")),
            "mapped_areas": self.store.explored_areas(agent["id"]),
            "researched_sources": self.store.researched_sources(agent["id"]),
            "target": target or {},
            "target.visible": (target or {}).get("visible", False),
            "target.available": (target or {}).get("available", False),
            "known_route_destinations": reachable_locations(self.store, state.get("location_id")),
            "recent_event_types": self._recent_event_types(),
        }
        if action_info.get("action_id") == "craft_recipe":
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
                }
                self.store.put_action_instance(action_instance_id, execution_id, agent["id"], action_id, "running", 0, info["duration"], {"parameters": info["parameters"], "target": info["target"], "reservation": info.get("reservation", []), "parallel_group": group}, minute, minute)
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
            action = action_defs.get(entry["action_id"])
            if not action:
                self._finish_behavior(agent, execution_id, behavior_id, "failed", f"action is not registered: {entry['action_id']}", minute)
                return
            if not entry.get("no_op"):
                self._apply_action_progress(agent, action)
            entry["remaining"] = max(0, int(entry["remaining"]) - 1)
            elapsed = int(entry["duration"]) - int(entry["remaining"])
            self.store.update_action_instance(entry["action_instance_id"], status="succeeded" if entry["remaining"] == 0 else "running", elapsed_minutes=elapsed, remaining_minutes=entry["remaining"], data={"parameters": entry["parameters"], "target": entry["target"], "reservation": entry.get("reservation", []), "parallel_group": group, "no_op": bool(entry.get("no_op"))}, updated_minute=minute)
            if entry["remaining"] == 0:
                failure = self._action_failure_reason(agent, action, entry["parameters"], entry.get("target"))
                if failure:
                    self._finish_behavior(agent, execution_id, behavior_id, "failed", failure, minute)
                    return
                self._apply_success_effects(agent, action, entry["parameters"], minute, target=entry.get("target"))
                information_result = self._record_information_result(agent, action, entry["parameters"], minute)
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
        action = next((item for item in self.store.get_definitions("action_def") if item.get("id") == action_id), None)
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
            self.store.update_action_instance(action_instance_id, status="succeeded" if remaining == 0 else "running", elapsed_minutes=duration - remaining, remaining_minutes=remaining, data={"parameters": state.get("active_action_parameters", {}), "reservation": state.get("active_action_reservation", []), "no_op": bool(state.get("active_action_noop"))}, updated_minute=minute)
        if remaining > 0:
            self.store.update_bt_instance(execution_id, status="executing", current_node=index, data=bt_data, updated_minute=minute)
            self.store.update_agent(agent["id"], agent)
            return
        action_parameters = state.get("active_action_parameters", {})
        failure = self._action_failure_reason(agent, action, action_parameters, self.targets.resolve(agent, action_parameters)[0])
        if failure:
            self._finish_behavior(agent, execution_id, behavior_id, "failed", failure, minute)
            return
        self._apply_success_effects(agent, action, action_parameters, minute, target=self.targets.resolve(agent, action_parameters)[0])
        information_result = self._record_information_result(agent, action, state.get("active_action_parameters", {}), minute)
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
        state["action_progress"] = 1.0
        agent["initial_state"] = state
        self.store.update_bt_instance(execution_id, status="executing", current_node=index + 1, data=bt_data, updated_minute=minute)
        if index + 1 >= len(actions):
            self._finish_behavior(agent, execution_id, behavior_id, "completed", "behavior_completed", minute)
        else:
            self.store.update_agent(agent["id"], agent)

    def _add_capability_progress(self, agent: dict[str, Any], capability: str, delta: float, source: str, minute: int) -> None:
        agent.setdefault("capabilities", {})[capability] = min(1.0, float(agent["capabilities"].get(capability, 0)) + float(delta))
        self.store.put_capability_progress(agent["id"], capability, float(delta), source, minute)

    def _apply_success_effects(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], minute: int, *, target: dict[str, Any] | None = None) -> None:
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
            if effect.get("type") == "damage" and parameters.get("weapon_id"):
                combat = agent.get("combat_stats", {}) or {}
                weapon_damage = (combat.get("weapon_damage", {}) or {}).get(str(parameters["weapon_id"]), 0)
                effect_target = {**effect_target, "weapon": {"damage": weapon_damage}}
            effect_type = str(effect.get("type") or effect.get("op") or "")
            if effect_type in {"relationship_delta", "relationship.delta"}:
                self._apply_relationship_effect(agent, target or effect_target, effect, minute)
            elif effect_type in {"public_event", "event.public"}:
                event_type = str(effect.get("event") or "public.event")
                self.store.append_event(event_type, agent["id"], minute, {"action_id": action.get("id"), "parameters": parameters})
            elif effect_type in {"world_property_delta", "world.property_delta"}:
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
            elif effect_type in {"entity_state_set", "entity.state_set"}:
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
            return
        recipe = next((item for item in self.store.get_definitions("recipe_def") if item.get("id") == recipe_id), None)
        if not recipe:
            return
        for output in recipe.get("outputs", []) or []:
            item = str(output.get("item"))
            agent.setdefault("inventory", {})[item] = agent["inventory"].get(item, 0) + output.get("quantity", 0)
        for effect in recipe.get("effects", []) or []:
            effect_type = str(effect.get("type") or effect.get("op") or "")
            if effect_type in {"entity_state_set", "entity.state_set"}:
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

    def _action_failure_reason(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], target: dict[str, Any] | None) -> str | None:
        condition = str((action.get("failure") or {}).get("condition") or "").strip()
        if not condition:
            return None
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
        context = {
            "location_id": location_id,
            "destination_id": parameters.get("location_id") or parameters.get("destination_id"),
            "item_id": parameters.get("item_id"),
            "current_location": location,
            "location": location,
            "source": source,
            "station": location,
            "target": target or {},
            "danger": {"detected": "danger" in (state.get("conditions") or [])},
            "hostile_contact": "hostile_contact" in (state.get("conditions") or []),
            "target.refused": "target_refused" in (state.get("conditions") or []),
            "known_route_destinations": reachable_locations(self.store, location_id),
        }
        if self.conditions.evaluate(condition, agent=agent, target=target or {}, context=context, world=self.store.get_meta("world_properties", {}) or {}):
            return str((action.get("failure") or {}).get("result") or condition)
        return None

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

    def _queue_configured_followups(self, configured: dict[str, Any], event_id: int, event_type: str, actor_id: str, minute: int) -> None:
        """Wake configured result consumers without knowing story-specific IDs."""

        for role in configured.get("wake", configured.get("wake_roles", [])) or []:
            role = str(role)
            if role not in {"story", "god", "agent_reflection"}:
                continue
            task_id = f"{role}:event:{event_id}"
            task_type = f"event:{event_type}"
            target = actor_id if role == "agent_reflection" else role
            self.store.put_evolution_task(
                task_id,
                role,
                target,
                task_type,
                minute,
                0,
                data={"source_event_id": int(event_id), "source_event_type": event_type, "source_actor_id": actor_id},
            )

    def _advance_information_progress(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], minute: int) -> dict[str, Any] | None:
        definition = action.get("progress") or {}
        if not isinstance(definition, dict) or not definition.get("key"):
            return None
        key = str(definition["key"])
        for name, value in parameters.items():
            key = key.replace("{" + str(name) + "}", str(value))
        scope = str(definition.get("scope", "agent"))
        owner = str(self.store.get_meta("world_id", "world")) if scope == "world" else str(agent["id"])
        progress = self.store.advance_progress(owner, key, float(definition.get("amount", 1) or 1), float(definition.get("required", 1) or 1), minute, {"source_action": action.get("id"), "source_agent": agent["id"]})
        event_type = "research.completed" if progress["completed"] else "research.progressed"
        event_id = self.store.append_event(event_type, agent["id"], minute, {"progress_key": key, "progress": progress, "source_action": action.get("id"), "parameters": parameters})
        # Partial work is retained in SQLite for the next attempt. Story
        # feedback is a completion boundary, so an incomplete sample does not
        # pause the world or wake the Story Agent without a new fact.
        if progress["completed"]:
            self._queue_story_task(event_id, event_type, agent["id"], minute)
        return progress

    def _record_information_result(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], minute: int) -> dict[str, Any]:
        """Persist information produced by configured information channels."""

        information = action.get("information", {}) or {}
        result: dict[str, Any] = {}
        area_id = parameters.get("area_id") or information.get("area_parameter") and parameters.get(information["area_parameter"])
        source_id = parameters.get("source_id") or information.get("source_parameter") and parameters.get(information["source_parameter"])
        target_id = parameters.get("target_id")
        topic = parameters.get("topic")
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
                if source_id not in self.store.researched_sources(agent["id"]):
                    source = self.store.get_entity(source_id) if source_id else {}
                    discovery = (source.get("research_discovery") or {}) if isinstance(source, dict) else {}
                    fact_key = str(discovery.get("id") or f"researched:{source_id}")
                    fact = str(discovery.get("fact") or f"Source {source_id} was researched.")
                    self.store.merge_knowledge_fact(agent["id"], fact_key, {"fact": fact, "source_type": "research", "source_id": source_id, "behavior": action.get("id"), "confidence": 0.8, "observed_at": minute})
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
            dialogue_id = self.store.put_dialogue_exchange(agent["id"], target_id if listener else None, str(topic), minute, {"source": action.get("id"), "parameters": parameters})
            self.store.put_memory(agent["id"], "dialogue", {"topic": str(topic), "target_id": target_id, "world_minute": minute, "dialogue_id": dialogue_id})
            if listener:
                self.store.put_memory(target_id, "testimony", {"topic": str(topic), "speaker_id": agent["id"], "world_minute": minute, "dialogue_id": dialogue_id})
                self._apply_configured_relationship_effect(agent, listener, "dialogue.completed", minute)
                self.store.update_agent(listener["id"], listener)
            event_id = self.store.append_event("dialogue.completed", agent["id"], minute, {"target_id": target_id, "topic": str(topic), "dialogue_id": dialogue_id})
            result = {"result": "dialogue_recorded", "event_id": event_id}
        configured_event = information.get("event_type") or information.get("event")
        observed: dict[str, Any] = {}
        observed_id = parameters.get("object_id") or parameters.get("source_id") or parameters.get("area_id")
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
                    "location": entity_state.get("location_id"),
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
                event_id = self.store.append_event(event_type, agent["id"], minute, payload)
                self._queue_configured_followups(configured, event_id, event_type, agent["id"], minute)
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
        self.store.append_event(f"behavior.{status}", agent["id"], minute, {"execution_id": execution_id, "behavior_id": behavior_id, "result": result, "outcome": outcome})

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
                    if target in {"god", "story"}:
                        role, actor_id = target, None
                    elif target in {agent["id"] for agent in self.store.list_agents()}:
                        role, actor_id = "agent_reflection", target
                    else:
                        continue
                    task_id = f"rule:{name}:{minute}:{index}"
                    self.store.put_evolution_task(task_id, role, actor_id, f"rule:{name}", minute, 0, status="ready", data={"rule": name, "event_id": event_id})
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

        start = int(self.store.get_meta("world_minute", 0))
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
            self._emit_evolution_triggers(minute)
            self._emit_due_evolution_tasks(minute)
        return {"status": "advanced", "from_minute": start, "to_minute": minute, "completed_agents": sorted(set(completed)), "failed_agents": sorted(set(failed))}

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
