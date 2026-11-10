from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

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
from ..world.time import world_time_view
from ..evolution import EventWakeScheduler, WorldProposalEvaluator
from .activity import ActivityRuntimeMixin
from .learning import LearningRuntimeMixin
from .social import SocialRuntimeMixin
from .world_evolution import WorldEvolutionRuntimeMixin


class Platform(
    ActivityRuntimeMixin,
    LearningRuntimeMixin,
    SocialRuntimeMixin,
    WorldEvolutionRuntimeMixin,
):
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

        behavior_defs = {
            str(item.get("id")): item for item in self.store.get_definitions("bt_def")
        }
        agents = []
        for agent in self.store.list_agents():
            state = agent.get("initial_state", {}) or {}
            location_id = str(state.get("location_id") or "")
            try:
                location_name = str(self.store.get_entity(location_id).get("name") or location_id)
            except KeyError:
                location_name = location_id or "unknown"
            behavior_id = str(state.get("active_behavior_id") or "")
            behavior = behavior_defs.get(behavior_id, {})
            agents.append({
                "id": agent["id"],
                "name": agent.get("name", agent["id"]),
                "role": agent.get("role", "agent"),
                "location_id": location_id,
                "location": location_name,
                "status": state.get("status", "idle"),
                "attributes": self.attribute_schema.values(state, agent.get("needs", {}) or {}),
                "active_behavior_id": behavior_id or None,
                "active_behavior": (
                    behavior.get("agent_label")
                    or behavior.get("summary")
                    or behavior.get("description")
                    or behavior_id
                    or None
                ),
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
            "world_clock": world_time_view(self.store),
            "world_id": self.store.get_meta("world_id"),
            "world_properties": self.store.get_meta("world_properties", {}) or {},
            "agents": agents,
            "entity_counts": {entity_type: len(self.store.entities_of_type(entity_type)) for entity_type in ("location", "agent", "faction", "resource", "object")},
            "recent_events": public_events,
            "private_data": "excluded",
        }

    def map_view(self, *, viewer_id: str | None = None, observer: bool = False) -> dict[str, Any]:
        """Return the visible location topology for text, TUI or HTTP clients."""

        from ..api.map_view import build_map_view

        return build_map_view(self.store, viewer_id=viewer_id, observer=observer)

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
            if spec.get("required") and value in (None, "", [], {}):
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
        for key in ("target_id", "patient_id", "protected_target_id", "counterparty_id", "listener_id", "location_id"):
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
        if intent.observed_agent_revision is not None or (not intent.target and not intent.parameters):
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
            "known_fact_keys": [item.get("fact_key") for item in self.store.list_knowledge_facts(agent["id"], limit=1000) if item.get("fact_key")],
            "target": resolved_target or {},
            "target.visible": bool((resolved_target or {}).get("visible", False)),
            "target.available": bool((resolved_target or {}).get("available", False)),
            "known_route_destinations": reachable_locations(self.store, location_id),
            "recent_event_types": self._recent_event_types(),
            "day_phase": world_time_view(self.store).get("day_phase"),
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

    def _apply_minute_effects(self, minute: int, *, agents: list[dict[str, Any]] | None = None) -> None:
        rules = self.store.get_meta("rules", {}) or {}
        minute_event_state = dict(self.store.get_meta("minute_effect_event_state", {}) or {})
        minute_event_state_changed = False
        world_properties = self.store.get_meta("world_properties", {}) or {}
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
        due_effects = [
            effect
            for effect in (rules.get("minute_effects", []) or [])
            if minute % max(1, int(effect.get("every_minutes", 1))) == 0
        ]
        agent_effects = [effect for effect in due_effects if effect.get("scope") == "agent"]
        if agent_effects:
            changed_agents: list[dict[str, Any]] = []
            vital = self.attribute_schema.vital_attribute()
            vital_minimum = self.attribute_schema.minimum(vital) if vital else 0
            for agent in agents if agents is not None else self.store.list_agents():
                agent_changed = False
                for effect in agent_effects:
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
                        world=world_properties,
                    ):
                        continue
                    self._apply_simple_effect(agent, effect.get("effect", {}) or {})
                    agent_changed = True
                    if effect.get("events"):
                        minute_event_state_changed = self._emit_minute_effect_events(
                            effect,
                            agent,
                            minute,
                            before_state,
                            before_needs,
                            event_state=minute_event_state,
                            rules=rules,
                            world=world_properties,
                        ) or minute_event_state_changed
                    state = dict(agent.get("initial_state") or {})
                    vital_value = float(state.get(vital, 1) or 0) if vital else 1
                    if vital and vital_value <= vital_minimum and state.get("status") not in {"unconscious", "dead"}:
                        state["status"] = "unconscious"
                        conditions = list(state.get("conditions") or [])
                        if "incapacitated" not in conditions:
                            conditions.append("incapacitated")
                        state["conditions"] = conditions
                        agent["initial_state"] = state
                        self.store.append_event("agent.incapacitated", agent["id"], minute, {"reason": effect.get("id", "minute_effect")})
                if agent_changed:
                    changed_agents.append(agent)
            self.store.update_agents_bulk(changed_agents)

        for effect in due_effects:
            scope = effect.get("scope")
            definition = effect.get("effect", {}) or {}
            if scope == "world":
                key = definition.get("world_property") or definition.get("property") or definition.get("need")
                if key:
                    properties = dict(self.store.get_meta("world_properties", {}) or {})
                    before = float(properties.get(key, 0) or 0)
                    after = before + float(definition.get("delta", 0) or 0)
                    if definition.get("minimum") is not None:
                        after = max(float(definition["minimum"]), after)
                    if definition.get("maximum") is not None:
                        after = min(float(definition["maximum"]), after)
                    if abs(after - before) > 1e-12:
                        self._set_world_property(str(key), after)
            elif scope == "relationship":
                relation_delta = float(definition.get("relation_strength", definition.get("delta", 0)))
                for relation in self.store.list_relationships():
                    data = {key: value for key, value in relation.items() if key not in {"from", "to", "type"}}
                    data["relation_strength"] = float(data.get("relation_strength", data.get("value", 0))) + relation_delta
                    self.store.put_relationship(relation["from"], relation["to"], relation["type"], data)
        if minute_event_state_changed:
            # Threshold state belongs to the whole minute. Persisting the
            # growing map after every agent/effect made a 1,000-agent tick
            # encode and decode the same JSON thousands of times.
            self.store.set_meta("minute_effect_event_state", minute_event_state)

    def _apply_simple_effect(self, agent: dict[str, Any], effect: dict[str, Any]) -> None:
        self.effects.apply(agent, effect)

    def _emit_minute_effect_events(
        self,
        rule: dict[str, Any],
        agent: dict[str, Any],
        minute: int,
        before_state: dict[str, Any],
        before_needs: dict[str, Any],
        *,
        event_state: dict[str, Any],
        rules: dict[str, Any],
        world: dict[str, Any],
    ) -> bool:
        """Emit configured threshold or recurring events after a minute effect."""

        configured = rule.get("events", []) or []
        if not isinstance(configured, list):
            return False
        changed_state = False
        conditions = " ".join(
            str(item.get("condition") or rule.get("condition") or "")
            for item in configured
            if isinstance(item, dict)
        )
        location = (
            self._entity_for_context((agent.get("initial_state") or {}).get("location_id"))
            if "location." in conditions or "current_location" in conditions
            else {}
        )
        context = {
            "current_location": location,
            "location_id": (agent.get("initial_state") or {}).get("location_id"),
            "satiety_alert_threshold": float(rules.get("satiety_alert_threshold", 40) or 40),
        }
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
            if previous != active or state_key not in event_state:
                event_state[state_key] = active
                changed_state = True
        return changed_state

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

    def _emit_world_rule_signals(self, minute: int, *, agent_ids: set[str] | None = None) -> None:
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
                    if target in (agent_ids if agent_ids is not None else {agent["id"] for agent in self.store.list_agents()}):
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

    def _emit_evolution_triggers(self, minute: int, *, agent_ids: set[str] | None = None) -> None:
        evolution = self.store.get_meta("evolution", {}) or {}
        config = evolution.get("evolution", evolution) if isinstance(evolution, dict) else {}
        triggers = config.get("triggers", []) if isinstance(config, dict) else []
        states = dict(self.store.get_meta("evolution_trigger_state", {}) or {})
        world = self._world_trigger_context()
        known_agents = agent_ids if agent_ids is not None else {agent["id"] for agent in self.store.list_agents()}
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
                    if target in known_agents:
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

        snapshot = self.store.world_snapshot(include_agent_revisions=False)
        start = int(snapshot["world_minute"])
        minute = start + 1
        completed: list[str] = []
        failed: list[str] = []
        with self.store.transaction():
            agents = self.store.list_agents()
            agent_ids = {str(agent["id"]) for agent in agents}
            self._apply_minute_effects(minute, agents=agents)
            for agent in agents:
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
            self._emit_world_rule_signals(minute, agent_ids=agent_ids)
            self.proposal_evaluator.evaluate_due(minute)
            self._emit_evolution_triggers(minute, agent_ids=agent_ids)
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
