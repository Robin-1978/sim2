from __future__ import annotations

import json
import sqlite3
import copy
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..actions.conditions import ConditionEngine
from ..actions.effects import EffectPipeline
from ..actions.targets import TargetResolver
from ..actions.resources import ResourceLedger
from ..agent_view.compiler import AgentViewCompiler
from ..intents.models import BehaviorIntent
from ..memory.service import MemoryService
from ..perception.service import PerceptionService
from ..persistence.store import WorldStore


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
        self.conditions = ConditionEngine()
        self.effects = EffectPipeline()
        self.targets = TargetResolver(self.store)
        self.resources = ResourceLedger(self.store)

    def close(self) -> None:
        self.store.close()

    def run(
        self,
        minutes: int,
        *,
        providers: dict[str, Any] | None = None,
        provider_name: str | None = None,
        wait_cooldown_minutes: int = 30,
        llm_error_cooldown_minutes: int = 60,
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
                    for role in ("agent_decision", "agent_reflection", "god")
                }
            else:
                providers = {
                    "agent_decision": router.provider_for_role("agent_decision"),
                    "agent_reflection": router.provider_for_role("agent_reflection"),
                    "god": router.provider_for_role("god"),
                }
        return SimulationRunner(
            self,
            providers,
            wait_cooldown_minutes=wait_cooldown_minutes,
            llm_error_cooldown_minutes=llm_error_cooldown_minutes,
        ).run(minutes)

    def status(self) -> dict[str, Any]:
        return {
            "status": self.store.get_meta("runtime_status", "running"),
            "world_minute": int(self.store.get_meta("world_minute", 0)),
            "instance_id": self.store.get_meta("instance_id"),
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
            "needs": {},
            "knowledge_seed": list(template.get("knowledge_seed") or []),
            "initial_bt_ids": list(template.get("initial_bt_ids") or []),
            "capabilities": {},
            "initial_state": {"location_id": location_id, "health": 100, "stamina": 100, "fatigue": 0, "status": "idle", "injuries": [], "conditions": [], "active_bt_instance": None, "active_action": None, "remaining_minutes": 0},
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
        for node in bt.get("nodes", []) or []:
            if node.get("type") != "action":
                continue
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
        intent_data = {
            "actor_id": intent.actor_id,
            "behavior_ref": intent.behavior_ref,
            "target": intent.target,
            "parameters": intent.parameters,
            "reason": intent.reason,
            "confidence": intent.confidence,
        }
        available_defs = {x["id"]: x for x in self.store.get_definitions("bt_def")}
        if behavior_id not in available_defs:
            return self._reject_intent(intent.intent_id, intent.actor_id, intent.intent_type, intent_data, {
                "status": "rejected", "message": "The selected behavior is not registered.", "failed_check": "behavior_ref", "retryable": True,
            })
        allowed = set(agent.get("initial_bt_ids", [])) | set((self.store.get_meta("rules", {}) or {}).get("fallback_behavior_ids", []))
        if behavior_id not in allowed:
            return self._reject_intent(intent.intent_id, intent.actor_id, intent.intent_type, intent_data, {
                "status": "rejected", "message": "This behavior is not available to this character.", "failed_check": "capability", "retryable": True,
            })
        parameter_errors = self._validate_behavior_parameters(behavior_id, intent.parameters)
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
        try:
            remaining_minutes = int(bt.get("estimated_minutes", 0))
        except (TypeError, ValueError):
            remaining_minutes = 0
        world_minute = int(self.store.get_meta("world_minute", 0))
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
            state["active_parameters"] = {**intent.parameters, **intent.target}
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
                    "parameters": intent.parameters,
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
            self.store.get_agent(actor_id)
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
            event_id = self.store.append_event("dialogue.completed", speaker_id, minute, {"dialogue_id": exchange_id, "target_id": listener_id, "topic": topic, "summary": "A direct exchange was recorded.", "activation": "recipient_next_decision_or_reflection", "duration_minutes": 0, "priority": "normal"})
            result = {"status": "accepted", "intent_id": intent_id, "dialogue_id": exchange_id, "event_id": event_id, "topic": topic}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def create_trade_offer(self, offerer_id: str, recipient_id: str, give: dict[str, Any], want: dict[str, Any]) -> dict[str, Any]:
        """Create a two-phase item offer; no inventory moves until acceptance."""

        give = dict(give or {})
        want = dict(want or {})
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
            return self._reject_intent(intent_id, actor_id, "propose_entity", data, {"status": "rejected", "message": "Entity proposal failed validation.", "errors": errors, "retryable": True})
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

    def submit_world_change(self, actor_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        intent_id = str(uuid4())
        if not proposal.get("change_type") or not proposal.get("summary"):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "change_type and summary are required.", "retryable": True})
        change_type = str(proposal["change_type"])
        if change_type not in {"world_property", "rule", "relationship", "faction", "event"}:
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "Unsupported world change type.", "failed_check": "change_type", "retryable": True})
        if change_type == "world_property" and not proposal.get("property"):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "property is required for a world_property change.", "failed_check": "property", "retryable": True})
        if change_type == "rule" and not isinstance(proposal.get("rule"), dict):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "rule object is required for a rule change.", "failed_check": "rule", "retryable": True})
        if change_type == "relationship" and not isinstance(proposal.get("relationship"), dict):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "relationship object is required for a relationship change.", "failed_check": "relationship", "retryable": True})
        if change_type == "faction" and not isinstance(proposal.get("faction"), dict):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "faction object is required for a faction change.", "failed_check": "faction", "retryable": True})
        world_minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, "propose_world_change", proposal, world_minute)
            effect: dict[str, Any] = {}
            if change_type == "world_property":
                properties = dict(self.store.get_meta("world_properties", {}) or {})
                before_value = properties.get(str(proposal["property"]))
                properties[str(proposal["property"])] = proposal.get("value")
                self.store.set_meta("world_properties", properties)
                effect = {"world_property": str(proposal["property"]), "before": before_value, "after": proposal.get("value")}
            elif change_type == "rule":
                rules = dict(self.store.get_meta("rules", {}) or {})
                world_rules = list(rules.get("world_rules", []))
                world_rules.append(dict(proposal["rule"]))
                rules["world_rules"] = world_rules
                self.store.set_meta("rules", rules)
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
                properties = dict(self.store.get_meta("world_properties", {}) or {})
                before_value = properties.get(str(proposal["property"]))
                properties[str(proposal["property"])] = proposal.get("value")
                self.store.set_meta("world_properties", properties)
                effect = {"world_property": str(proposal["property"]), "before": before_value, "after": proposal.get("value")}
            else:
                effect = {"event_recorded": True, "state_changed": False}
            event_data = {
                "intent_id": intent_id,
                "change_type": change_type,
                "summary": str(proposal.get("summary", "")),
                "scope": str(proposal.get("scope", "world")),
                "effect": effect,
            }
            event_id = self.store.append_event(f"world.change.{change_type}", actor_id, world_minute, event_data)
            result = {"status": "accepted", "intent_id": intent_id, "event_id": event_id, "applied": True, "applied_effects": bool(effect.get("state_changed", True)), "change_type": change_type, "effect": effect}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

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
            "stamina": state.get("stamina"),
            "fatigue": state.get("fatigue"),
            "needs": dict(agent.get("needs") or {}),
            "inventory": dict(agent.get("inventory") or {}),
            "capabilities": dict(agent.get("capabilities") or {}),
        }

    @staticmethod
    def _execution_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        """Describe only changed values so the next Agent sees consequences."""

        delta: dict[str, Any] = {}
        for key in ("location", "health", "stamina", "fatigue"):
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

    def _action_duration(self, action: dict[str, Any], parameters: dict[str, Any]) -> int:
        duration = action.get("duration") or {}
        minutes = duration.get("minutes")
        if isinstance(minutes, (int, float)):
            return max(1, int(minutes))
        mode = duration.get("mode")
        if mode == "recipe_duration":
            recipe_id = parameters.get("recipe_id")
            recipe = next((item for item in self.store.get_definitions("recipe_def") if item.get("id") == recipe_id), None)
            if recipe and isinstance(recipe.get("duration_minutes"), (int, float)):
                return max(1, int(recipe["duration_minutes"]))
        configured = duration.get("default_minutes", action.get("default_minutes", 1))
        try:
            return max(1, int(configured))
        except (TypeError, ValueError):
            return 1

    def _reserve_action_costs(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], *, commit: bool = True) -> tuple[bool, str, list[dict[str, Any]]]:
        return self.resources.reserve(agent, action, parameters, commit=commit)

    def _apply_action_progress(self, agent: dict[str, Any], action: dict[str, Any]) -> None:
        state = dict(agent.get("initial_state") or {})
        needs = dict(agent.get("needs") or {})
        for key, value in (action.get("costs", {}).get("per_minute", {}) or {}).items():
            if not isinstance(value, (int, float)) or key in {"time", "attention"} and key not in state:
                if key == "attention":
                    state["attention"] = float(state.get("attention", 0)) + float(value or 0)
                continue
            numeric = float(value)
            if key in {"fatigue", "exposure", "attention"}:
                state[key] = max(0.0, min(100.0, float(state.get(key, 0)) + numeric))
            elif key in {"stamina", "health"}:
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
                    if effect.get("condition") == "action.none" and state.get("status") in {"executing", "paused"}:
                        continue
                    self._apply_simple_effect(agent, definition)
                    changed_agents.append(agent)
                self.store.update_agents_bulk(changed_agents)
            elif scope == "world":
                properties = dict(self.store.get_meta("world_properties", {}) or {})
                key = definition.get("world_property") or definition.get("property") or definition.get("need")
                if key:
                    properties[key] = float(properties.get(key, 0)) + float(definition.get("delta", 0))
                    self.store.set_meta("world_properties", properties)
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
            if key == "fatigue":
                state = dict(agent.get("initial_state") or {})
                state[key] = max(0.0, min(100.0, float(state.get(key, 0)) + float(effect.get("delta", 0))))
                agent["initial_state"] = state
            else:
                needs = dict(agent.get("needs") or {})
                needs[key] = max(0.0, min(100.0, float(needs.get(key, 0)) + float(effect.get("delta", 0))))
                agent["needs"] = needs

    def _start_action(self, agent: dict[str, Any], execution_id: str, action_info: dict[str, Any], minute: int) -> tuple[bool, str]:
        action_defs = {item["id"]: item for item in self.store.get_definitions("action_def")}
        action_id = action_info.get("action_id")
        action = action_defs.get(action_id)
        if not action:
            return False, f"action is not registered: {action_id}"
        state = dict(agent.get("initial_state") or {})
        parameters = self._resolve_parameters(dict(action_info.get("parameters") or {}), state)
        resolved_target, target_error = self.targets.resolve(agent, parameters)
        if target_error:
            return False, target_error
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
            "current_location": current_location,
            "target": resolved_target or {},
            "target.visible": (resolved_target or {}).get("visible", True),
            "target.available": (resolved_target or {}).get("available", True),
        }
        for predicate in action.get("preconditions", []) or []:
            condition_target = {**parameters, **(resolved_target or {})}
            if not self.conditions.evaluate(str(predicate), agent=agent, target=condition_target, context=condition_context, world=self.store.get_meta("world_properties", {}) or {}):
                return False, f"precondition failed: {predicate}"
        allowed, reason, reservation = self._reserve_action_costs(agent, action, parameters)
        if not allowed:
            return False, reason
        duration = self._action_duration(action, parameters)
        action_instance_id = f"{execution_id}:{state.get('active_action_index', 0)}"
        state["active_action"] = action_id
        state["active_action_instance"] = action_instance_id
        state["action_progress"] = 0.0
        state["active_action_duration"] = duration
        state["remaining_minutes"] = duration
        state["active_action_parameters"] = parameters
        state["active_action_reservation"] = reservation
        agent["initial_state"] = state
        self.store.put_action_instance(action_instance_id, execution_id, agent["id"], action_id, "running", 0, duration, {"parameters": parameters, "target": resolved_target, "reservation": reservation}, minute, minute)
        self.store.append_event("action.started", agent["id"], minute, {"execution_id": execution_id, "action_id": action_id, "duration_minutes": duration})
        return True, ""

    def _prepare_parallel_action(self, agent: dict[str, Any], action_info: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        action_defs = {item["id"]: item for item in self.store.get_definitions("action_def")}
        action = action_defs.get(action_info.get("action_id"))
        if not action:
            return None, f"action is not registered: {action_info.get('action_id')}"
        state = dict(agent.get("initial_state") or {})
        parameters = self._resolve_parameters(dict(action_info.get("parameters") or {}), state)
        target, target_error = self.targets.resolve(agent, parameters)
        if target_error:
            return None, target_error
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
            "current_location": current_location,
            "target": target or {},
            "target.visible": (target or {}).get("visible", True),
            "target.available": (target or {}).get("available", True),
        }
        condition_target = {**parameters, **(target or {})}
        for predicate in action.get("preconditions", []) or []:
            if not self.conditions.evaluate(str(predicate), agent=agent, target=condition_target, context=context, world=self.store.get_meta("world_properties", {}) or {}):
                return None, f"precondition failed: {predicate}"
        probe = copy.deepcopy(agent)
        allowed, reason, reservation = self._reserve_action_costs(probe, action, parameters, commit=False)
        if not allowed:
            return None, reason
        return {"action": action, "parameters": parameters, "target": target, "duration": self._action_duration(action, parameters), "reservation": reservation}, ""

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
            self._apply_action_progress(agent, action)
            entry["remaining"] = max(0, int(entry["remaining"]) - 1)
            elapsed = int(entry["duration"]) - int(entry["remaining"])
            self.store.update_action_instance(entry["action_instance_id"], status="succeeded" if entry["remaining"] == 0 else "running", elapsed_minutes=elapsed, remaining_minutes=entry["remaining"], data={"parameters": entry["parameters"], "target": entry["target"], "reservation": entry.get("reservation", []), "parallel_group": group}, updated_minute=minute)
            if entry["remaining"] == 0:
                self._apply_success_effects(agent, action, entry["parameters"], minute, target=entry.get("target"))
                self._record_information_result(agent, action, entry["parameters"], minute)
                self.store.append_event("action.completed", agent["id"], minute, {"execution_id": execution_id, "action_id": entry["action_id"], "parallel_group": group, "result": action.get("success", {}).get("result", "completed")})
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
        self._apply_action_progress(agent, action)
        state = dict(agent.get("initial_state") or {})
        remaining = max(0, int(state.get("remaining_minutes", 1)) - 1)
        duration = max(1, int(state.get("active_action_duration", state.get("remaining_minutes", 1))))
        state["remaining_minutes"] = remaining
        state["action_progress"] = round(1 - remaining / duration, 4)
        agent["initial_state"] = state
        action_instance_id = state.get("active_action_instance")
        if action_instance_id:
            self.store.update_action_instance(action_instance_id, status="succeeded" if remaining == 0 else "running", elapsed_minutes=duration - remaining, remaining_minutes=remaining, data={"parameters": state.get("active_action_parameters", {}), "reservation": state.get("active_action_reservation", [])}, updated_minute=minute)
        if remaining > 0:
            self.store.update_bt_instance(execution_id, status="executing", current_node=index, data=bt_data, updated_minute=minute)
            self.store.update_agent(agent["id"], agent)
            return
        action_parameters = state.get("active_action_parameters", {})
        self._apply_success_effects(agent, action, action_parameters, minute, target=self.targets.resolve(agent, action_parameters)[0])
        self._record_information_result(agent, action, state.get("active_action_parameters", {}), minute)
        self.store.append_event("action.completed", agent["id"], minute, {"execution_id": execution_id, "action_id": action_id, "result": action.get("success", {}).get("result", "completed")})
        # Effects may update location, health, inventory or needs; retain that
        # projection before clearing the completed action fields.
        state = dict(agent.get("initial_state") or {})
        state["active_action_index"] = index + 1
        state["active_action"] = None
        state["active_action_instance"] = None
        state["active_action_duration"] = 0
        state["active_action_parameters"] = {}
        state["active_action_reservation"] = []
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
            self.effects.apply(agent, resolved_effect, target=target or parameters)
            if effect.get("type") in {"capability_xp", "capability.add_xp"} or effect.get("op") == "capability.add_xp":
                capability = effect.get("capability")
                if capability:
                    self._add_capability_progress(agent, capability, float(effect.get("value", effect.get("delta", 0))), str(action.get("id", "action")), minute)
        for effect in action.get("capability_effects", []) or []:
            capability = effect.get("capability")
            if capability:
                self._add_capability_progress(agent, capability, float(effect.get("value", 0)), str(action.get("id", "action")), minute)
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
            if effect.get("op") == "capability.add_xp":
                capability = effect.get("capability")
                if capability:
                    self._add_capability_progress(agent, capability, float(effect.get("value", 0)), f"recipe:{recipe_id}", minute)

    def _record_information_result(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], minute: int) -> None:
        """Persist information produced by configured information channels."""

        information = action.get("information", {}) or {}
        area_id = parameters.get("area_id") or information.get("area_parameter") and parameters.get(information["area_parameter"])
        source_id = parameters.get("source_id") or information.get("source_parameter") and parameters.get(information["source_parameter"])
        target_id = parameters.get("target_id")
        topic = parameters.get("topic")
        if area_id:
            area_id = str(area_id)
            result_id = self.store.put_exploration_result(agent["id"], area_id, minute, {"source": action.get("id"), "parameters": parameters})
            self.store.merge_knowledge_fact(agent["id"], f"explored:{area_id}", {"fact": f"Area {area_id} was explored.", "source_type": "exploration", "source_id": result_id, "confidence": 0.8, "observed_at": minute})
            self.store.append_event("exploration.completed", agent["id"], minute, {"area_id": area_id, "result_id": result_id})
        if source_id:
            source_id = str(source_id)
            self.store.merge_knowledge_fact(agent["id"], f"researched:{source_id}", {"fact": f"Source {source_id} was researched.", "source_type": "research", "confidence": 0.8, "observed_at": minute})
            self.store.append_event("research.completed", agent["id"], minute, {"source_id": source_id})
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
            self.store.append_event("dialogue.completed", agent["id"], minute, {"target_id": target_id, "topic": str(topic), "dialogue_id": dialogue_id})

    def _finish_behavior(self, agent: dict[str, Any], execution_id: str, behavior_id: str, status: str, result: str, minute: int) -> None:
        state = dict(agent.get("initial_state") or {})
        bt_row = self.store.connection.execute("SELECT data_json FROM bt_instance WHERE execution_id=?", (execution_id,)).fetchone()
        bt_data = json.loads(bt_row["data_json"]) if bt_row is not None else {}
        before = dict(bt_data.get("before") or {})
        if status != "completed":
            reservations = list(state.get("active_action_reservation") or [])
            for item in (state.get("active_parallel_actions") or {}).values():
                reservations.extend(item.get("reservation", []) or [])
            self.resources.release(agent, reservations)
        after = self._execution_snapshot(agent)
        outcome = {
            "status": status,
            "result": result,
            "expected": dict(bt_data.get("expected") or {}),
            "actual": after,
            "delta": self._execution_delta(before, after) if before else {},
            "decision": dict(bt_data.get("decision") or {}),
        }
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
                context[resource["id"]] = amount
        return context

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
