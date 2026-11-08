from __future__ import annotations

from typing import Any

from ..actions.conditions import ConditionEngine
from ..actions.resources import ResourceLedger
from ..agents.tools import tools_for_role
from ..persistence.store import WorldStore
from ..world.drives import DriveCalculator


def _label(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _predicate_text(predicate: str) -> str:
    """Turn a configured predicate into one short sentence for the model."""

    text = str(predicate).replace("_", " ").replace(".", " ").strip()
    replacements = {
        "actor can explore": "You have exploration capability",
        "actor can gather": "You have gathering capability",
        "actor can craft": "You have crafting capability",
        "actor can communicate": "You can communicate",
        "actor can research": "You have research capability",
        "actor is guard": "You are a guard",
        "actor is healer": "You are a healer",
        "actor alive": "You are alive",
        "actor mobile": "You can move",
        "area unknown": "The area is not known yet",
        "area not fully mapped": "The area is not fully mapped",
        "threat detected": "A threat has been detected",
        "threat at village": "A threat is at the settlement",
        "threat overwhelming": "The threat is overwhelming",
        "patient available": "A patient is available",
        "inputs available": "Required inputs are available",
        "recipe available": "A recipe is available",
    }
    return replacements.get(text, text[:1].upper() + text[1:] + ".")


def _cost_text(action: dict[str, Any]) -> list[str]:
    costs = action.get("costs", {})
    labels = {"fatigue": "Fatigue", "stamina": "Stamina", "attention": "Attention", "exposure": "Exposure", "tool_durability": "Tool durability", "rations": "Rations", "medicine": "Medicine", "iron_ore": "Iron ore", "star_metal": "Star metal", "trade_goods": "Trade goods", "ritual_material": "Ritual material"}
    out: list[str] = []
    for phase, values in costs.items():
        if not values:
            continue
        if isinstance(values, dict):
            for key, value in values.items():
                out.append(f"{labels.get(key, _label(str(key)))} {value} ({phase.replace('_', ' ')})")
        else:
            out.append(f"{phase.replace('_', ' ')}: {values}")
    return out or ["No direct resource cost."]


def _action_view(action: dict[str, Any]) -> dict[str, Any]:
    success = action.get("success", {})
    failure = action.get("failure", {})
    return {
        "name": _label(action.get("id", "action")),
        "summary": action.get("summary") or action.get("description") or _label(action.get("id", "action")),
        "when": action.get("when") or action.get("precondition_text") or [_predicate_text(item) for item in action.get("preconditions", [])] or ["Platform confirms the configured requirements."],
        "requires": action.get("requirements") or action.get("requirement_text") or [_predicate_text(item) for item in action.get("preconditions", [])] or ["Platform confirms the configured requirements."],
        "target": "Platform selects a permitted target from the current view.",
        "time": f"{action.get('duration', {}).get('minutes', 'variable')} minutes",
        "cost": _cost_text(action),
        "impact": ["Platform applies the configured state effects and records an event."],
        "success": [str(success.get("result", "configured success")).replace("_", " ")],
        "failure": [str(failure.get("result", "configured failure")).replace("_", " ")],
        "interrupt": action.get("interrupt_policy", "cancel"),
    }


class AgentViewCompiler:
    """Build a small, role-scoped context instead of exposing database rows."""

    def __init__(self, store: WorldStore):
        self.store = store
        self.conditions = ConditionEngine()
        self.resources = ResourceLedger(store)

    def build_decision_view(self, agent_id: str, role: str = "agent_decision") -> dict[str, Any]:
        try:
            agent = self.store.get_agent(agent_id)
        except KeyError:
            if role in {"god", "world_builder"}:
                return self._build_world_view(agent_id, role)
            raise
        bt_defs = {x["id"]: x for x in self.store.get_definitions("bt_def")}
        action_defs = {x["id"]: x for x in self.store.get_definitions("action_def")}
        available = []
        fallback_ids = (self.store.get_meta("rules", {}) or {}).get("fallback_behavior_ids", [])
        catalog_ids = list(dict.fromkeys(list(agent.get("initial_bt_ids", [])) + list(fallback_ids)))
        state = agent.get("initial_state", {})
        location_id = state.get("location_id")
        current_location = {}
        if location_id:
            try:
                current_location = self.store.get_entity(location_id)
            except KeyError:
                pass
        for bt_id in catalog_ids:
            bt = bt_defs.get(bt_id)
            if not bt:
                continue
            rules = self.store.get_meta("rules", {}) or {}
            condition_context = {
                "current_location": current_location,
                "location_id": location_id,
                "inventory": agent.get("inventory", {}) or {},
                "food_items": rules.get("food_items", []) or [],
            }
            if any(not self.conditions.evaluate(str(predicate), agent=agent, target={}, context=condition_context, world=self.store.get_meta("world_properties", {}) or {}) for predicate in bt.get("entry_conditions", []) or []):
                continue
            steps = []
            costs = []
            impacts = []
            action_unavailable = False
            for node in bt.get("nodes", []):
                if node.get("type") != "action":
                    continue
                action = action_defs.get(node.get("action_id"))
                if not action:
                    continue
                parameters = self._view_action_parameters(node.get("parameters") or {}, state, agent.get("inventory", {}) or {}, rules.get("food_items", []) or [])
                if not self._action_available(agent, action, parameters, current_location, rules.get("food_items", []) or []):
                    action_unavailable = True
                    break
                view = _action_view(action)
                steps.append(view["name"])
                costs.extend(view["cost"])
                impacts.extend(view["impact"][:1])
            if action_unavailable:
                continue
            available.append({
                "ref": f"behavior:{bt_id}",
                "registration": "platform_validated",
                "catalog_source": bt.get("kind", "configured"),
                "name": bt.get("summary") or _label(bt_id),
                "summary": bt.get("description") or bt.get("summary") or _label(bt_id),
                "purpose": bt.get("purpose") or bt.get("description") or bt.get("summary") or _label(bt_id),
                "when": bt.get("when") or bt.get("entry_descriptions") or [_predicate_text(item) for item in bt.get("entry_conditions", [])] or ["The current goal makes this useful."],
                "requires": bt.get("requirements") or bt.get("requirement_descriptions") or [_predicate_text(item) for item in bt.get("entry_conditions", [])] or ["Platform confirms all step requirements."],
                "target": "Platform resolves permitted targets from the current view.",
                "steps": steps[:5],
                "time": f"{bt.get('estimated_minutes', 'variable')} minutes",
                "cost": list(dict.fromkeys(costs))[:6] or ["Costs are defined by the selected steps."],
                "impact": list(dict.fromkeys(impacts))[:3] or ["Platform applies configured effects and records events."],
                "success": [str(item).replace("_", " ") for item in bt.get("success_results", [])] or ["Platform reports the configured success result."],
                "failure": [str(item).replace("_", " ") for item in bt.get("failure_results", [])] or ["Platform reports a failed or interrupted step."],
                "interrupt": bt.get("interrupt_policy", "cancel"),
                "tags": bt.get("tags", []),
                "goal_tags": bt.get("goal_tags", []),
                "drive_tags": bt.get("drive_tags", []),
            })
        # Keep the context small, but never hide a configured food/survival
        # choice behind a character's first five unrelated BTs.
        available.sort(
            key=lambda item: 0 if set(item.get("goal_tags", [])) & {"food", "survival"} else 1
        )
        nearby = [item for item in (self.store.entities_at_location(location_id) if location_id else []) if item.get("id") != agent_id]
        needs = agent.get("needs", {})
        drives = DriveCalculator(self.store.get_meta("rules", {})).calculate(agent)
        drive_model = (self.store.get_meta("rules", {}) or {}).get("motivation_model", {})
        known_facts = self.store.list_knowledge_facts(agent_id, limit=8)
        if not known_facts:
            known_facts = [{"fact": fact, "source_type": "config_seed", "confidence": 1.0} for fact in agent.get("knowledge_seed", [])[:8]]
        relationships = self.store.get_related_relationships(agent_id, limit=5)
        memories = self.store.list_memories(agent_id, limit=5)
        actor_events = self.store.recent_events_for_actor(agent_id, limit=8)
        recent_actions = actor_events[:2]
        recent_outcomes = self.store.recent_outcomes_for_actor(agent_id, limit=3)
        return {
            "context_version": "1", "role": role,
            "world_time": {"minute": self.store.get_meta("world_minute", 0), "label": f"World minute {self.store.get_meta('world_minute', 0)}"},
            "self": {"id": agent_id, "name": agent.get("name", agent_id), "role": agent.get("role", "agent"), "status": state.get("status", "idle"), "location": location_id, "known_routes": current_location.get("routes", []) if isinstance(current_location, dict) else [], "health": state.get("health"), "stamina": state.get("stamina"), "fatigue": state.get("fatigue"), "goal": agent.get("ultimate_goal")},
            "identity": {"traits": agent.get("traits", {}), "beliefs": agent.get("beliefs", []), "capabilities": agent.get("capabilities", {})},
            "goal_tags": agent.get("goal_tags", []),
            "capability_history": self.store.list_capability_progress(agent_id, limit=5),
            "needs": needs, "inventory": agent.get("inventory", {}), "injuries": state.get("injuries", []),
            "drives": drives[:5], "drive_model": drive_model, "observation": {"facts": [], "source": "platform_snapshot"}, "nearby_entities": nearby[:8], "known_facts": known_facts, "memories": memories, "recent_actions": recent_actions, "recent_outcomes": recent_outcomes, "direct_relationships": relationships,
            "active_task": {"bt": state.get("active_bt_instance"), "action": state.get("active_action"), "remaining_minutes": state.get("remaining_minutes", 0)},
            "executable_behaviors": available[:5], "last_platform_result": state.get("last_platform_result"), "tools": tools_for_role(role),
        }

    @staticmethod
    def _view_action_parameters(parameters: dict[str, Any], state: dict[str, Any], inventory: dict[str, Any], food_items: list[str]) -> dict[str, Any]:
        resolved = {}
        first_food = next((item for item in food_items if float(inventory.get(item, 0) or 0) >= 1), None)
        for key, value in parameters.items():
            if value == "context.location_id" or value == "context.station_id":
                resolved[key] = state.get("location_id")
            elif value == "context.item_id":
                resolved[key] = first_food
            else:
                resolved[key] = value
        return resolved

    def _action_available(self, agent: dict[str, Any], action: dict[str, Any], parameters: dict[str, Any], location: dict[str, Any], food_items: list[str]) -> bool:
        context = {
            "current_location": location,
            "location_id": (agent.get("initial_state") or {}).get("location_id"),
            "destination_id": parameters.get("location_id") or parameters.get("destination_id"),
            "area_id": parameters.get("area_id"),
            "item_id": parameters.get("item_id"),
            "inventory": agent.get("inventory", {}) or {},
            "food_items": food_items,
            "station_tags": [],
        }
        if action.get("id") == "craft_recipe":
            recipe = next((item for item in self.store.get_definitions("recipe_def") if item.get("id") == parameters.get("recipe_id")), None)
            if recipe is None:
                return False
            context["station_tags"] = list(recipe.get("station_tags", []) or [])
        target = dict(parameters)
        for predicate in action.get("preconditions", []) or []:
            if not self.conditions.evaluate(str(predicate), agent=agent, target=target, context=context, world=self.store.get_meta("world_properties", {}) or {}):
                return False
        allowed, _, _ = self.resources.reserve(agent, action, parameters, commit=False)
        return allowed

    def _build_world_view(self, actor_id: str, role: str) -> dict[str, Any]:
        minute = self.store.get_meta("world_minute", 0)
        all_events = self.store.recent_events(limit=20)
        recent_world_changes = self.store.recent_world_change_events(limit=6)
        recent_agent_outcomes = self.store.recent_behavior_outcomes(limit=6)
        recent_world_events = (recent_world_changes + [item for item in all_events if item.get("actor_id") == actor_id])[:6]
        active_count = sum(
            1 for agent in self.store.list_agents()
            if (agent.get("initial_state") or {}).get("status") in {"executing", "paused"}
        )
        world_properties = self.store.get_meta("world_properties", {}) or {}
        world_summary = "; ".join(f"{key}={value}" for key, value in world_properties.items()) or "none"
        return {
            "context_version": "1",
            "role": role,
            "world_time": {"minute": minute, "label": f"World minute {minute}"},
            "self": {"id": actor_id, "name": actor_id, "role": role, "status": "ready", "location": "world", "goal": "preserve a coherent, evolving world"},
            "identity": {"traits": {}, "beliefs": [], "capabilities": {"world_scope": True}},
            "needs": {},
            "inventory": {},
            "injuries": [],
            "drives": [],
            "observation": {"facts": [f"World properties: {world_summary}", f"Active behaviors: {active_count}"], "source": "platform_world_summary"},
            "nearby_entities": [],
            "known_facts": [],
            "recent_actions": recent_world_events,
            "recent_world_changes": recent_world_changes,
            "recent_agent_outcomes": recent_agent_outcomes,
            "direct_relationships": [],
            "active_task": {"bt": None, "action": None, "remaining_minutes": 0},
            "executable_behaviors": [],
            "last_platform_result": recent_world_events[0] if recent_world_events else None,
            "tools": tools_for_role(role),
        }
