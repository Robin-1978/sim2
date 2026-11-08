from __future__ import annotations

from typing import Any


def _line(value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    value = _plain(value)
    return value if len(value) <= 1200 else value[:1197] + "..."


def _plain(value: Any) -> str:
    """Render dynamic facts as compact prose instead of nested JSON."""

    if value is None or value == "":
        return "unknown"
    if isinstance(value, dict):
        hidden = {"id", "ref", "behavior_ref", "intent_id", "event_id", "entity_id", "actor_id", "target_id", "location_id", "offer_id"}
        visible = ((key, item) for key, item in value.items() if key not in hidden and not key.endswith("_id"))
        return "; ".join(f"{key}={_plain(item)}" for key, item in visible) or "none"
    if isinstance(value, (list, tuple, set)):
        separator = " | " if any(isinstance(item, dict) for item in value) else ", "
        return separator.join(_plain(item) for item in value) or "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _result_line(result: Any) -> str:
    if not isinstance(result, dict):
        return _plain(result)
    parts: list[str] = []
    # Canonical IDs are Platform/audit data.  The model receives the visible
    # choice or name instead of an implementation reference.
    for key in ("status", "choice", "behavior_choice", "result", "reason", "event_type", "failed_check", "message", "available_choices"):
        if result.get(key) not in (None, ""):
            parts.append(f"{key}={_plain(result[key])}")
    outcome = result.get("outcome")
    if isinstance(outcome, dict):
        if outcome.get("status"):
            parts.append(f"outcome={outcome['status']}")
        if outcome.get("delta"):
            parts.append(f"changes={_plain(outcome['delta'])}")
    return "; ".join(parts) or _plain(result)


def _history_line(item: dict[str, Any]) -> str:
    kind = item.get("kind", "event")
    if kind == "assistant_tool_call":
        return f"tool request {item.get('name', 'unknown')}: {_plain(item.get('arguments'))}"
    if kind == "tool_result":
        return f"tool result {item.get('name', 'unknown')}: {_result_line(item.get('result'))}"
    if kind == "assistant_intent":
        return f"intent request: {_result_line(item.get('intent'))}"
    if kind == "platform_result":
        return f"Platform result: {_result_line(item.get('result'))}"
    if kind == "llm_error":
        return f"LLM error: {_plain(item.get('error'))}"
    return f"{kind}: {_plain(item)}"


def _protocol(role: str) -> list[str]:
    if role == "agent_decision":
        return [
            "Select exactly one allowed intent: request_behavior, send_message, create_trade_offer, accept_trade_offer, counter_trade_offer, reject_trade_offer, or wait.",
            "Return wait when no behavior is suitable. Use the exact visible choice/name; never output an internal ID. Do not invent behavior names or fill duration, cost, or effects.",
            "The Platform owns all state changes and continues long actions after this session ends.",
        ]
    if role == "agent_reflection":
        return ["Compare expected and observed results. Keep only a new, durable lesson; if there is no useful change, return wait. Use visible names and statements, never internal IDs. Platform owns every state change."]
    if role == "god":
        return ["Use the listed tools to submit one validated entity or world proposal. Platform owns every state change."]
    return ["Use validate_world_template before submit_world_template. Do not execute code or write files."]


def render_context(context: dict[str, Any], task: str, *, max_chars: int = 16000) -> str:
    """Render a stable XML-like prefix followed by dynamic Agent state.

    The system policy and native tool schemas are stable.  This user message
    puts static protocol/tool explanations first, dynamic world state next,
    the current decision at the end, and appends ReAct history last.
    """

    role = context.get("role", "agent_decision")
    self_state = context.get("self", {})
    lines = ["<protocol>"]
    lines.extend(f"    - {item}" for item in _protocol(role))
    lines.extend(["</protocol>", "", "<world_state>"])
    world_time = context.get("world_time") or {}
    lines.append(f"  time: {world_time.get('label', world_time.get('minute', 'unknown'))}")
    lines.append(f"  minute: {world_time.get('minute', 'unknown')}")
    lines.append("</world_state>")

    lines.extend([
        "<self>",
        f"  name: {_line(self_state.get('name'))}",
        f"  role: {_line(self_state.get('role'))}",
        f"  status: {_line(self_state.get('status'))}",
        f"  location: {_line(self_state.get('location'))}",
        f"  known_routes: {_line(self_state.get('known_routes'))}",
        f"  health: {_line(self_state.get('health'))}",
        f"  stamina: {_line(self_state.get('stamina'))}",
        f"  fatigue: {_line(self_state.get('fatigue'))}",
        f"  long_term_goal: {_line(self_state.get('goal'))}",
        "</self>",
        "<identity>",
        f"  traits: {_line(context.get('identity', {}).get('traits'))}",
        f"  beliefs: {_line(context.get('identity', {}).get('beliefs'))}",
        f"  capabilities: {_line(context.get('identity', {}).get('capabilities'))}",
        f"  recent_capability_changes: {_line(context.get('capability_history'))}",
        f"  goal_tags: {_line(context.get('goal_tags'))}",
        "</identity>",
        "<needs_and_state>",
        f"  needs: {_line(context.get('needs'))}",
        f"  inventory: {_line(context.get('inventory'))}",
        f"  injuries_and_conditions: {_line(context.get('injuries'))}",
        "</needs_and_state>",
        "<drives>",
    ])
    drives = context.get("drives") or []
    lines.extend([f"  - {d.get('id', 'need')}: urgency={d.get('urgency', 0)}; {d.get('reason', 'no reason')}" for d in drives[:3]] or ["  - no urgent drive"])
    model = context.get("drive_model") or {}
    if model:
        lines.append(f"  model: {model.get('family', 'configured hybrid')}; needs are parallel, not a fixed hierarchy")
    lines.append("  Use the highest relevant drive with the goal and executable behavior descriptions to choose one behavior.")
    lines.append("</drives>")

    observation = context.get("observation") or {}
    lines.extend(["  <scene>"])
    lines.extend([f"    - {fact}" for fact in (observation.get("facts") or [])[:8]] or ["    - no new local observation"])
    if observation.get("source"):
        lines.append(f"    source: {observation['source']}; expires: {observation.get('expires_in_minutes', 'unknown')} minutes")
    lines.append("  </scene>")

    lines.extend(["  <nearby_entities>"])
    nearby = context.get("nearby_entities") or []
    lines.extend([f"    - {item.get('name', item.get('id'))}: {item.get('summary', item.get('type', 'entity'))}" for item in nearby[:8]] or ["    - none visible"])
    lines.append("  </nearby_entities>")

    lines.extend(["  <relationships>"])
    relationships = context.get("direct_relationships") or []
    lines.extend([f"    - {item.get('person', 'unknown')}: {item.get('relation', 'related')}; direction={item.get('direction', 'unknown')}; trust={item.get('trust', 'unknown')}" for item in relationships[:5]] or ["    - none relevant"])
    lines.append("  </relationships>")

    lines.extend(["  <knowledge_and_memory>"])
    facts = context.get("known_facts") or []
    lines.extend([f"    fact: {item if isinstance(item, str) else item.get('fact', 'unknown fact')}" for item in facts[:8]] or ["    fact: none relevant"])
    memories = context.get("memories") or []
    lines.extend([f"    memory ({item.get('type', 'memory')}): {item.get('summary') or item.get('fact') or item.get('message') or item.get('topic') or item.get('offer_id', 'unlabeled experience')}" for item in memories[:5]] or ["    memory: none recent"])
    lines.append("  </knowledge_and_memory>")
    lines.append("<recent_actions>")
    recent_actions = context.get("recent_actions") or []
    lines.extend([f"  - {item.get('event_type')}: {item.get('result') or item.get('summary') or 'completed'} at world minute {item.get('world_minute', 'unknown')}" for item in recent_actions[:2]] or ["  - none"])
    lines.append("</recent_actions>")

    lines.append("<open_trade_offers>")
    offers = context.get("open_trade_offers") or []
    lines.extend([f"  - choice: {item.get('choice')}; {item.get('summary', 'open offer')}" for item in offers[:5]] or ["  - none"])
    lines.append("</open_trade_offers>")

    if role == "agent_reflection":
        lines.append("<outcome_review>")
        outcomes = context.get("recent_outcomes") or []
        if outcomes:
            for item in outcomes[:3]:
                outcome = item.get("outcome") or {}
                expected = outcome.get("expected") or {}
                delta = outcome.get("delta") or {}
                lines.append(
                    f"  - {item.get('behavior_id', 'behavior')}: {outcome.get('status', item.get('event_type', 'unknown'))}; "
                    f"result={outcome.get('result', 'unknown')}; expected={expected.get('summary', 'not stated')}; "
                    f"observed_changes={_plain(delta) if delta else 'none'}"
                )
        else:
            lines.append("  - no completed or interrupted behavior needs review")
        lines.append("  Keep an experience only when it is new, durable, and useful for a future decision; otherwise return wait. Write one to three concise sentences, under 500 characters.")
        lines.append("</outcome_review>")

    if role == "god":
        lines.append("<world_outcomes>")
        changes = context.get("recent_world_changes") or []
        if changes:
            for item in changes[:6]:
                effect = item.get("effect") or {}
                lines.append(
                    f"  - {item.get('event_type', 'world.change')}: {item.get('summary', 'proposal applied')}; "
                    f"observed={_plain(effect) if effect else 'event recorded'} at minute {item.get('world_minute', 'unknown')}"
                )
        else:
            lines.append("  - no recent world proposal result")
        outcomes = context.get("recent_agent_outcomes") or []
        if outcomes:
            lines.append("  Recent character outcomes:")
            for item in outcomes[:6]:
                outcome = item.get("outcome") or {}
                lines.append(f"  - {item.get('actor_id', 'character')}: {item.get('behavior_id', 'behavior')} -> {outcome.get('status', item.get('event_type', 'unknown'))}; {outcome.get('result', 'no result')}")
        lines.append("  Compare expected and observed effects before proposing one next world change.")
        lines.append("</world_outcomes>")

    active = context.get("active_task") or {}
    lines.extend([
        "  <active_execution>",
        f"    behavior: {_line(active.get('bt'))}",
        f"    action: {_line(active.get('action'))}",
        f"    remaining_minutes: {_line(active.get('remaining_minutes'))}",
        "  </active_execution>",
        "  <executable_behaviors>",
    ])
    behaviors = context.get("executable_behaviors") or context.get("available_behaviors") or []
    for behavior in behaviors[:5]:
        lines.append("    <behavior>")
        for label in ("name", "summary", "purpose", "when", "requires", "target", "steps", "time", "cost", "impact", "success", "failure", "interrupt", "tags"):
            value = behavior.get(label)
            if value:
                if isinstance(value, list):
                    value = "; ".join(str(item) for item in value)
                lines.append(f"      {label}: {value}")
        lines.append(f"      choice: {behavior.get('choice') or behavior.get('name') or 'unnamed behavior'}")
        lines.append("    </behavior>")
    if not behaviors:
        lines.append("    - none; return wait")
    lines.append("  </executable_behaviors>")

    if role == "agent_reflection":
        lines.append("  <registered_actions>")
        actions = context.get("available_actions") or []
        lines.extend([f"    - choice: {item.get('choice')}; {item.get('summary', 'configured action')}" for item in actions[:24]] or ["    - none"])
        lines.append("  </registered_actions>")

    lines.extend(["  <decision>", f"    {task}", "  </decision>"])
    lines.extend(["  <interaction_history>"])
    history = context.get("interaction_history") or []
    if history:
        for item in history[-8:]:
            kind = item.get("kind", "event")
            lines.append(f"    - {_history_line(item)}")
    else:
        lines.append("    - none in this session")
    last = context.get("last_platform_result")
    if last:
        lines.append(f"    latest_platform_result: {_result_line(last)}")
    lines.append("</interaction_history>")
    rendered = "\n".join(lines)
    if len(rendered) <= max_chars:
        return rendered
    head_size = max_chars * 2 // 3
    tail_size = max_chars - head_size
    return rendered[:head_size] + "\n<context_limit>Earlier low-priority details were omitted; use a read tool if needed.</context_limit>\n" + rendered[-tail_size:]
