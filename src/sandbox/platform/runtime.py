from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from ..agent_view.compiler import AgentViewCompiler
from ..intents.models import BehaviorIntent
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

    def close(self) -> None:
        self.store.close()

    def decision_view(self, agent_id: str, role: str = "agent_decision") -> dict[str, Any]:
        return self.view.build_decision_view(agent_id, role=role)

    def _reject_intent(self, intent_id: str, actor_id: str, intent_type: str, data: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        world_minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, intent_type, data, world_minute)
            self.store.finish_intent(intent_id, "rejected", result)
        return result

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
        allowed = set(agent.get("initial_bt_ids", []))
        if behavior_id not in allowed:
            return self._reject_intent(intent.intent_id, intent.actor_id, intent.intent_type, intent_data, {
                "status": "rejected", "message": "This behavior is not available to this character.", "failed_check": "capability", "retryable": True,
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
        with self.store.transaction():
            self.store.create_intent(intent.intent_id, intent.actor_id, intent.intent_type, intent_data, world_minute)
            state["status"] = "executing"
            state["active_bt_instance"] = intent.intent_id
            state["active_behavior_id"] = behavior_id
            state["active_action"] = None
            state["remaining_minutes"] = remaining_minutes
            state["last_platform_result"] = {"status": "accepted", "behavior_ref": intent.behavior_ref}
            agent["initial_state"] = state
            self.store.update_agent(intent.actor_id, agent)
            self.store.append_event(
                "behavior.started", intent.actor_id, world_minute,
                {"intent_id": intent.intent_id, "behavior_id": behavior_id, "target": intent.target},
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
        data = {"event_ref": event_ref, "summary": summary, "confidence": confidence}
        intent_id = str(uuid4())
        if not summary.strip():
            return self._reject_intent(intent_id, actor_id, "record_experience", data, {"status": "rejected", "message": "Experience summary is required.", "failed_check": "summary", "retryable": True})
        if not 0 <= confidence <= 1:
            return self._reject_intent(intent_id, actor_id, "record_experience", data, {"status": "rejected", "message": "Confidence must be between 0 and 1.", "failed_check": "confidence", "retryable": True})
        world_minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            self.store.get_agent(actor_id)
            self.store.create_intent(intent_id, actor_id, "record_experience", data, world_minute)
            memory_id = self.store.put_memory(actor_id, "experience", {**data, "world_minute": world_minute})
            self.store.append_event("memory.experience_recorded", actor_id, world_minute, {"memory_id": memory_id, **data})
            result = {"status": "accepted", "intent_id": intent_id, "memory_id": memory_id}
            self.store.finish_intent(intent_id, "accepted", result)
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
            "nodes": nodes, "definition_source": "agent_proposal", "kind": "proposed",
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
            self.store.append_event("entity.created", actor_id, world_minute, {"entity_id": entity_id, "entity_type": proposal["entity_type"]})
            result = {"status": "accepted", "intent_id": intent_id, "entity_id": entity_id}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def submit_world_change(self, actor_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        intent_id = str(uuid4())
        if not proposal.get("change_type") or not proposal.get("summary"):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "change_type and summary are required.", "retryable": True})
        world_minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, "propose_world_change", proposal, world_minute)
            self.store.append_event("world.change.proposed", actor_id, world_minute, proposal)
            result = {"status": "accepted", "intent_id": intent_id, "applied": False, "message": "The proposal is recorded for the world validator."}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def validate_world_template(self, template: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        if not isinstance(template, dict):
            return {"status": "ok", "valid": False, "errors": ["template must be an object"], "failed_check": "template", "message": "Template must be an object.", "retryable": True}
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
