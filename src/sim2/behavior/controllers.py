"""Configured character controllers and their optional resident routines."""

from __future__ import annotations

from typing import Any


BT_CONTROLLERS = frozenset({"bt", "bt_only", "behavior_tree"})


def controller_mode(agent: dict[str, Any]) -> str:
    state = agent.get("initial_state") if isinstance(agent.get("initial_state"), dict) else {}
    role = str(agent.get("role") or state.get("role") or "").casefold()
    controller = str(agent.get("controller") or state.get("controller") or "llm").casefold()
    if role == "player" or controller in {"manual", "player"}:
        return "manual"
    return "bt" if controller in BT_CONTROLLERS else "llm"


def routine_error(agent: dict[str, Any]) -> str | None:
    routine = agent.get("routine_bt_id")
    if routine is None:
        return None
    if not isinstance(routine, str) or not routine.strip():
        return "routine_bt_id must name a registered behavior"
    mode = controller_mode(agent)
    if mode != "bt" and not (mode == "llm" and agent.get("controller_fallback") == "bt"):
        return "routine_bt_id requires a BT controller or BT fallback"
    assigned = agent.get("initial_bt_ids")
    if not isinstance(assigned, list) or routine not in assigned:
        return "routine_bt_id must be included in initial_bt_ids"
    return None
