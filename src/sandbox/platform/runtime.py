from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..actions.conditions import ConditionEngine
from ..actions.effects import EffectPipeline
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
        self.conditions = ConditionEngine()
        self.effects = EffectPipeline()

    def close(self) -> None:
        self.store.close()

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
            "initial_bt_ids": [],
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
                {"target": intent.target, "parameters": intent.parameters, "actions": self._compile_behavior_actions(behavior_id)},
                world_minute,
                world_minute,
            )
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

        def visit(nodes: list[Any]) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            for raw in nodes or []:
                node = raw if isinstance(raw, dict) else {"type": "behavior", "behavior_id": raw}
                node_type = node.get("type")
                if node_type == "action" and node.get("action_id"):
                    result.append({"action_id": node["action_id"], "parameters": dict(node.get("parameters") or {})})
                elif node_type in {"behavior", "subtree"} and node.get("behavior_id"):
                    result.extend(self._compile_behavior_actions(str(node["behavior_id"]), seen))
                elif node_type in {"sequence", "parallel"}:
                    result.extend(visit(node.get("children", [])))
                elif node_type == "selector":
                    # Selector branch conditions are evaluated by the fuller
                    # condition engine in M4.  Until then, choose the first
                    # configured branch deterministically and record that
                    # choice in the BT instance.
                    children = node.get("children", [])
                    if children:
                        result.extend(visit([children[0]]))
                # condition/wait nodes do not consume an Action minute.
            return result

        return visit(behavior.get("nodes", []))

    @staticmethod
    def _resolve_parameters(parameters: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        context = dict(state.get("active_parameters") or {})
        context.update(state)

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

    def _reserve_action_costs(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any]) -> tuple[bool, str]:
        inventory = dict(agent.get("inventory") or {})
        reserve = action.get("costs", {}).get("reserve", {}) or {}
        required: dict[str, float] = {}
        for key, value in reserve.items():
            if key == "recipe_inputs":
                recipe = next((item for item in self.store.get_definitions("recipe_def") if item.get("id") == parameters.get("recipe_id")), None)
                for item in (recipe or {}).get("inputs", []):
                    required[str(item.get("item"))] = required.get(str(item.get("item")), 0) + float(item.get("quantity", 0))
            elif isinstance(value, (int, float)):
                required[key] = required.get(key, 0) + float(value)
        for key, amount in required.items():
            available = float(inventory.get(key, 0))
            if available < amount:
                return False, f"required resource {key} is unavailable"
        for key, amount in required.items():
            remaining = inventory.get(key, 0) - amount
            inventory[key] = int(remaining) if float(remaining).is_integer() else remaining
        agent["inventory"] = inventory
        return True, ""

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
                for agent in self.store.list_agents():
                    state = dict(agent.get("initial_state") or {})
                    if effect.get("condition") == "action.none" and state.get("status") in {"executing", "paused"}:
                        continue
                    self._apply_simple_effect(agent, definition)
                    self.store.update_agent(agent["id"], agent)
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
        }
        for predicate in action.get("preconditions", []) or []:
            if not self.conditions.evaluate(str(predicate), agent=agent, target=parameters, context=condition_context, world=self.store.get_meta("world_properties", {}) or {}):
                return False, f"precondition failed: {predicate}"
        allowed, reason = self._reserve_action_costs(agent, action, parameters)
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
        agent["initial_state"] = state
        self.store.put_action_instance(action_instance_id, execution_id, agent["id"], action_id, "running", 0, duration, {"parameters": parameters}, minute, minute)
        self.store.append_event("action.started", agent["id"], minute, {"execution_id": execution_id, "action_id": action_id, "duration_minutes": duration})
        return True, ""

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
            self.store.update_action_instance(action_instance_id, status="succeeded" if remaining == 0 else "running", elapsed_minutes=duration - remaining, remaining_minutes=remaining, data={"parameters": state.get("active_action_parameters", {})}, updated_minute=minute)
        if remaining > 0:
            self.store.update_bt_instance(execution_id, status="executing", current_node=index, data=bt_data, updated_minute=minute)
            self.store.update_agent(agent["id"], agent)
            return
        self._apply_success_effects(agent, action, state.get("active_action_parameters", {}))
        self._record_information_result(agent, action, state.get("active_action_parameters", {}), minute)
        self.store.append_event("action.completed", agent["id"], minute, {"execution_id": execution_id, "action_id": action_id, "result": action.get("success", {}).get("result", "completed")})
        state["active_action_index"] = index + 1
        state["active_action"] = None
        state["active_action_instance"] = None
        state["active_action_duration"] = 0
        state["active_action_parameters"] = {}
        state["active_action_duration"] = 0
        state["action_progress"] = 1.0
        agent["initial_state"] = state
        self.store.update_bt_instance(execution_id, status="executing", current_node=index + 1, data=bt_data, updated_minute=minute)
        if index + 1 >= len(actions):
            self._finish_behavior(agent, execution_id, behavior_id, "completed", "behavior_completed", minute)
        else:
            self.store.update_agent(agent["id"], agent)

    def _apply_success_effects(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any]) -> None:
        effects = list(action.get("effects", []) or [])
        for effect in effects:
            self.effects.apply(agent, effect, target=parameters)
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
                    agent.setdefault("capabilities", {})[capability] = min(1.0, float(agent["capabilities"].get(capability, 0)) + float(effect.get("value", 0)))

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
            self.store.put_knowledge_fact(agent["id"], f"explored:{area_id}", {"fact": f"Area {area_id} was explored.", "source_type": "exploration", "source_id": result_id, "confidence": 0.8, "observed_at": minute})
            self.store.append_event("exploration.completed", agent["id"], minute, {"area_id": area_id, "result_id": result_id})
        if source_id:
            source_id = str(source_id)
            self.store.put_knowledge_fact(agent["id"], f"researched:{source_id}", {"fact": f"Source {source_id} was researched.", "source_type": "research", "confidence": 0.8, "observed_at": minute})
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
        state["status"] = "idle" if status == "completed" else "failed"
        state["active_bt_instance"] = None
        state["active_behavior_id"] = None
        state["active_action"] = None
        state["active_action_instance"] = None
        state["remaining_minutes"] = 0
        state["last_platform_result"] = {"status": status, "behavior_ref": f"behavior:{behavior_id}", "result": result}
        agent["initial_state"] = state
        self.store.update_agent(agent["id"], agent)
        self.store.update_bt_instance(execution_id, status=status, current_node=int(state.get("active_action_index", 0)), data={"result": result}, updated_minute=minute)
        self.store.append_event(f"behavior.{status}", agent["id"], minute, {"execution_id": execution_id, "behavior_id": behavior_id, "result": result})

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

    def advance(self, minutes: int = 1, *, force: bool = False) -> dict[str, Any]:
        """Advance world time and active Action/BT instances deterministically."""

        minutes = int(minutes)
        if minutes < 1:
            raise ValueError("minutes must be at least 1")
        if not force and self.store.get_meta("runtime_status", "running") != "running":
            return {"status": "paused", "world_minute": int(self.store.get_meta("world_minute", 0))}
        start = int(self.store.get_meta("world_minute", 0))
        completed: list[str] = []
        failed: list[str] = []
        with self.store.transaction():
            for offset in range(1, minutes + 1):
                minute = start + offset
                self._apply_minute_effects(minute)
                for agent in self.store.list_agents():
                    before = agent.get("initial_state", {}).get("active_bt_instance")
                    self._progress_agent(agent, minute)
                    after = self.store.get_agent(agent["id"]).get("initial_state", {})
                    result = after.get("last_platform_result") or {}
                    if before and result.get("status") == "completed" and result.get("behavior_ref"):
                        completed.append(agent["id"])
                    if before and result.get("status") == "failed" and result.get("behavior_ref"):
                        failed.append(agent["id"])
                self.store.set_meta("world_minute", minute)
                self.store.append_event("world_minute.advanced", None, minute, {"minute": minute})
                self._emit_due_evolution_tasks(minute)
        return {"status": "advanced", "from_minute": start, "to_minute": start + minutes, "completed_agents": sorted(set(completed)), "failed_agents": sorted(set(failed))}

    def step(self) -> dict[str, Any]:
        return self.advance(1, force=True)

    def metrics(self) -> dict[str, Any]:
        active = self.store.connection.execute("SELECT COUNT(*) AS n FROM agent_state WHERE json_extract(data_json,'$.initial_state.status') IN ('executing','paused')").fetchone()["n"]
        events = self.store.connection.execute("SELECT COUNT(*) AS n FROM event_log").fetchone()["n"]
        intents = self.store.connection.execute("SELECT COUNT(*) AS n FROM intent").fetchone()["n"]
        llm_calls = self.store.connection.execute("SELECT COUNT(*) AS n FROM llm_call").fetchone()["n"]
        return {"world_minute": int(self.store.get_meta("world_minute", 0)), "active_behaviors": int(active), "events": int(events), "intents": int(intents), "llm_calls": int(llm_calls)}

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
            state["status"] = "idle" if hard else "paused"
            state["last_platform_result"] = {"status": status, "reason": reason}
            if hard:
                state["active_bt_instance"] = None
                state["active_behavior_id"] = None
                state["active_action"] = None
                state["active_action_instance"] = None
                state["remaining_minutes"] = 0
            agent["initial_state"] = state
            self.store.update_agent(actor_id, agent)
            self.store.update_bt_instance(execution_id, status=status, current_node=int(state.get("active_action_index", 0)), data={"reason": reason}, updated_minute=minute)
            self.store.append_event("behavior.interrupted", actor_id, minute, {"execution_id": execution_id, "reason": reason, "hard": hard})
        return {"status": status, "execution_id": execution_id, "reason": reason}
