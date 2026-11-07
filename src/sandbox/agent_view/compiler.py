from __future__ import annotations

from typing import Any

from ..agents.tools import tools_for_role
from ..persistence.store import WorldStore
from ..world.drives import DriveCalculator


def _label(value: str) -> str:
    return value.replace("_", " ").strip().title()


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
        "when": ["Platform confirms the configured requirements."],
        "requires": ["The actor, target, route, resources and capabilities must be valid."],
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
        for bt_id in agent.get("initial_bt_ids", []):
            bt = bt_defs.get(bt_id)
            if not bt:
                continue
            steps = []
            costs = []
            impacts = []
            for node in bt.get("nodes", []):
                if node.get("type") != "action":
                    continue
                action = action_defs.get(node.get("action_id"))
                if not action:
                    continue
                view = _action_view(action)
                steps.append(view["name"])
                costs.extend(view["cost"][:2])
                impacts.extend(view["impact"][:1])
            available.append({
                "ref": f"behavior:{bt_id}",
                "name": bt.get("summary") or _label(bt_id),
                "purpose": f"Execute the {_label(bt_id)} plan.",
                "when": ["The current goal and configured entry conditions are relevant."],
                "requires": ["Platform confirms all step requirements."],
                "target": "Platform resolves permitted targets from the current view.",
                "steps": steps[:5],
                "time": f"{bt.get('estimated_minutes', 'variable')} minutes",
                "cost": list(dict.fromkeys(costs))[:4] or ["Costs are defined by the selected steps."],
                "impact": list(dict.fromkeys(impacts))[:3] or ["Platform applies configured effects and records events."],
                "success": ["Platform reports the configured success result."],
                "failure": ["Platform reports a failed or interrupted step."],
                "interrupt": bt.get("interrupt_policy", "cancel"),
            })
        state = agent.get("initial_state", {})
        location_id = state.get("location_id")
        nearby = self.store.entities_at_location(location_id) if location_id else []
        needs = agent.get("needs", {})
        drives = DriveCalculator(self.store.get_meta("rules", {})).calculate(agent)
        return {
            "context_version": "1", "role": role,
            "world_time": {"minute": self.store.get_meta("world_minute", 0), "label": f"World minute {self.store.get_meta('world_minute', 0)}"},
            "self": {"id": agent_id, "name": agent.get("name", agent_id), "role": agent.get("role", "agent"), "status": state.get("status", "idle"), "location": location_id, "health": state.get("health"), "stamina": state.get("stamina"), "fatigue": state.get("fatigue"), "goal": agent.get("ultimate_goal")},
            "identity": {"traits": agent.get("traits", {}), "beliefs": agent.get("beliefs", []), "capabilities": agent.get("capabilities", {})},
            "needs": needs, "inventory": agent.get("inventory", {}), "injuries": state.get("injuries", []),
            "drives": drives[:5], "observation": {"facts": [], "source": "platform_snapshot"}, "nearby_entities": nearby[:8], "known_facts": agent.get("knowledge_seed", [])[:8], "direct_relationships": [],
            "active_task": {"bt": state.get("active_bt_instance"), "action": state.get("active_action"), "remaining_minutes": state.get("remaining_minutes", 0)},
            "available_behaviors": available[:5], "last_platform_result": state.get("last_platform_result"), "tools": tools_for_role(role),
        }

    def _build_world_view(self, actor_id: str, role: str) -> dict[str, Any]:
        minute = self.store.get_meta("world_minute", 0)
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
            "observation": {"facts": [f"World properties: {self.store.get_meta('world_properties', {})}"], "source": "platform_world_summary"},
            "nearby_entities": [],
            "known_facts": [],
            "direct_relationships": [],
            "active_task": {"bt": None, "action": None, "remaining_minutes": 0},
            "available_behaviors": [],
            "last_platform_result": None,
            "tools": tools_for_role(role),
        }
