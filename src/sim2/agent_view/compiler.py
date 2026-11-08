from __future__ import annotations

from typing import Any

from ..actions.conditions import ConditionEngine
from ..actions.resources import ResourceLedger
from ..agents.tools import tools_for_role
from ..persistence.store import WorldStore
from ..world.drives import DriveCalculator


def _label(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _choice_key(value: Any) -> str:
    """Normalize a model-facing label for deterministic exact matching."""

    return "".join(char for char in str(value or "").casefold() if char.isalnum())


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
                if key == "item_parameter":
                    out.append(f"Required item ({phase.replace('_', ' ')})")
                    continue
                shown_value = "the selected target" if str(value).endswith("_id") else value
                out.append(f"{labels.get(key, _label(str(key)))} {shown_value} ({phase.replace('_', ' ')})")
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

    @staticmethod
    def _assign_choices(behaviors: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Give the current view short, human-readable, collision-free choices.

        ``ref`` remains an internal Platform value.  The choice is the only
        behavior selector rendered to the model.  A suffix is used only when
        two configured behaviors have the same visible name.
        """

        counts: dict[str, int] = {}
        for behavior in behaviors:
            base = str(behavior.get("choice") or behavior.get("name") or "Choose")
            key = _choice_key(base) or "choice"
            counts[key] = counts.get(key, 0) + 1
        seen: dict[str, int] = {}
        result: list[dict[str, Any]] = []
        for behavior in behaviors:
            item = dict(behavior)
            base = str(item.get("choice") or item.get("name") or "Choose")
            key = _choice_key(base) or "choice"
            seen[key] = seen.get(key, 0) + 1
            item["choice"] = base if counts[key] == 1 else f"{base} (option {seen[key]})"
            result.append(item)
        return result

    def resolve_behavior_choice(self, agent_id: str, choice: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Resolve a visible choice to an internal behavior only in Platform.

        Matching is exact after case/spacing/punctuation normalization.  No
        fuzzy matching is used because a wrong behavior can have side effects.
        ``behavior:<id>`` is accepted only as a private compatibility path for
        old integrations; it is never advertised in the model context.
        """

        value = str(choice or "").strip()
        view = self.build_decision_view(agent_id, role="agent_decision")
        behaviors = view.get("executable_behaviors", [])
        if value.startswith("behavior:"):
            matches = [item for item in behaviors if item.get("ref") == value]
        else:
            key = _choice_key(value)
            matches = [item for item in behaviors if _choice_key(item.get("choice") or item.get("name")) == key]
        if len(matches) == 1:
            return matches[0], None
        available = [str(item.get("choice") or item.get("name")) for item in behaviors]
        if not matches:
            return None, {
                "status": "rejected",
                "failed_check": "behavior_choice",
                "retryable": True,
                "message": "Choose one behavior by its exact visible name.",
                "available_choices": available,
            }
        return None, {
            "status": "rejected",
            "failed_check": "ambiguous_behavior_choice",
            "retryable": True,
            "message": "That behavior name is ambiguous; choose the displayed option.",
            "available_choices": available,
        }

    def _trade_offer_view(self, agent_id: str) -> list[dict[str, Any]]:
        offers: list[dict[str, Any]] = []
        for offer in self.store.list_trade_offers(agent_id, include_closed=False, limit=8):
            other_id = offer["recipient_id"] if offer["offerer_id"] == agent_id else offer["offerer_id"]
            try:
                other_name = self.store.get_agent(other_id).get("name", other_id)
            except KeyError:
                other_name = other_id
            direction = "to" if offer["offerer_id"] == agent_id else "from"
            give = ", ".join(f"{amount} {item}" for item, amount in offer.get("give", {}).items())
            want = ", ".join(f"{amount} {item}" for item, amount in offer.get("want", {}).items())
            offers.append({
                "offer_id": offer["offer_id"],
                "choice": f"Offer {direction} {other_name}: give {give}; want {want}",
                "summary": f"Trade {give} for {want}.",
                "round": offer.get("round", 1),
                "status": offer.get("status", "open"),
            })
        return offers

    def resolve_trade_offer_choice(self, agent_id: str, choice: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        value = str(choice or "").strip()
        offers = self._trade_offer_view(agent_id)
        if value in {str(item.get("offer_id")) for item in offers}:
            matches = [item for item in offers if str(item.get("offer_id")) == value]
        else:
            key = _choice_key(value)
            matches = [item for item in offers if _choice_key(item.get("choice")) == key]
        available = [str(item.get("choice")) for item in offers]
        if len(matches) == 1:
            return matches[0], None
        return None, {
            "status": "rejected",
            "failed_check": "trade_offer_choice",
            "retryable": True,
            "message": "Choose one exact open trade offer from the current interaction list.",
            "available_choices": available,
        }

    def _humanize_fact(self, fact: Any) -> Any:
        """Replace compact config references in facts with visible names."""

        if isinstance(fact, str) and ":" in fact:
            prefix, value = fact.split(":", 1)
            try:
                visible_value = self.store.get_entity(value).get("name", value)
            except KeyError:
                visible_value = value.replace("_", " ")
            return f"{prefix.replace('_', ' ').title()}: {visible_value}"
        if isinstance(fact, dict) and "fact" in fact:
            item = dict(fact)
            item["fact"] = self._humanize_fact(item["fact"])
            return item
        return fact

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
                "choice": bt.get("agent_label") or bt.get("summary") or _label(bt_id),
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
        known_routes = []
        for route_id in (current_location.get("routes", []) if isinstance(current_location, dict) else []):
            try:
                known_routes.append(self.store.get_entity(str(route_id)).get("name", str(route_id)))
            except KeyError:
                known_routes.append(str(route_id))
        active_behavior_id = state.get("active_behavior_id")
        active_action_id = state.get("active_action")
        active_behavior = next((item for item in bt_defs.values() if item.get("id") == active_behavior_id), {})
        active_action = next((item for item in action_defs.values() if item.get("id") == active_action_id), {})
        available_actions = [
            {"choice": action.get("agent_label") or _label(str(action.get("id", "action"))), "name": action.get("agent_label") or _label(str(action.get("id", "action"))), "summary": action.get("agent_summary") or _label(str(action.get("id", "action")))}
            for action in action_defs.values()
        ]
        needs = agent.get("needs", {})
        drives = DriveCalculator(self.store.get_meta("rules", {})).calculate(agent)
        drive_model = (self.store.get_meta("rules", {}) or {}).get("motivation_model", {})
        known_facts = [self._humanize_fact(item) for item in self.store.list_knowledge_facts(agent_id, limit=8)]
        if not known_facts:
            known_facts = [{"fact": self._humanize_fact(fact), "source_type": "config_seed", "confidence": 1.0} for fact in agent.get("knowledge_seed", [])[:8]]
        relationships = self.store.get_related_relationships(agent_id, limit=5)
        for relationship in relationships:
            person_id = relationship.get("person")
            try:
                relationship["person"] = self.store.get_agent(person_id).get("name", person_id)
            except KeyError:
                try:
                    relationship["person"] = self.store.get_entity(person_id).get("name", person_id)
                except KeyError:
                    pass
        memories = self.store.list_memories(agent_id, limit=5)
        actor_events = self.store.recent_events_for_actor(agent_id, limit=8)
        recent_actions = actor_events[:2]
        recent_outcomes = self.store.recent_outcomes_for_actor(agent_id, limit=3)
        return {
            "context_version": "1", "role": role,
            "world_time": {"minute": self.store.get_meta("world_minute", 0), "label": f"World minute {self.store.get_meta('world_minute', 0)}"},
            "self": {"id": agent_id, "name": agent.get("name", agent_id), "role": agent.get("role", "agent"), "status": state.get("status", "idle"), "location": current_location.get("name", location_id) if isinstance(current_location, dict) else location_id, "known_routes": known_routes, "health": state.get("health"), "stamina": state.get("stamina"), "fatigue": state.get("fatigue"), "goal": agent.get("ultimate_goal")},
            "identity": {"traits": agent.get("traits", {}), "beliefs": agent.get("beliefs", []), "capabilities": agent.get("capabilities", {})},
            "goal_tags": agent.get("goal_tags", []),
            "capability_history": self.store.list_capability_progress(agent_id, limit=5),
            "needs": needs, "inventory": agent.get("inventory", {}), "injuries": state.get("injuries", []),
            "drives": drives[:5], "drive_model": drive_model, "observation": {"facts": [], "source": "platform_snapshot"}, "nearby_entities": nearby[:8], "known_facts": known_facts, "memories": memories, "recent_actions": recent_actions, "recent_outcomes": recent_outcomes, "direct_relationships": relationships,
            "active_task": {"bt": active_behavior.get("summary") or ("in progress" if active_behavior_id else None), "action": active_action.get("description") or (_label(str(active_action_id)) if active_action_id else None), "remaining_minutes": state.get("remaining_minutes", 0)},
            "open_trade_offers": self._trade_offer_view(agent_id),
            "available_actions": available_actions if role == "agent_reflection" else [],
            "executable_behaviors": self._assign_choices(available[:5]), "last_platform_result": state.get("last_platform_result"), "tools": tools_for_role(role),
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
        if action.get("requires_destination") and parameters.get("location_id") == (agent.get("initial_state") or {}).get("location_id"):
            return False
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
