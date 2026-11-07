from __future__ import annotations

import json
from typing import Any


def _line(value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    value = str(value)
    return value if len(value) <= 1200 else value[:1197] + "..."


def render_context(context: dict[str, Any], task: str, *, max_chars: int = 16000) -> str:
    """Render a compact, human-readable prompt context.

    Stable instructions remain in the system message. This function renders only
    the current decision slice; it never serializes the database or raw predicates.
    """
    self_state = context.get("self", {})
    lines = [
        "# TASK",
        task,
        "Return one JSON object only. Choose one offered reference or return wait.",
        "",
        "# AGENT",
        f"Name: {_line(self_state.get('name'))}",
        f"Role: {_line(self_state.get('role'))}",
        f"Status: {_line(self_state.get('status'))}",
        f"Location: {_line(self_state.get('location'))}",
        f"Health: {_line(self_state.get('health'))}; Stamina: {_line(self_state.get('stamina'))}; Fatigue: {_line(self_state.get('fatigue'))}",
        f"Long-term goal: {_line(self_state.get('goal'))}",
        "",
        "# IDENTITY AND CAPABILITIES",
        f"Traits: {_line(context.get('identity', {}).get('traits'))}",
        f"Beliefs: {_line(context.get('identity', {}).get('beliefs'))}",
        f"Capabilities: {_line(context.get('identity', {}).get('capabilities'))}",
        f"Recent capability changes: {_line(context.get('capability_history'))}",
        "",
        "# STATE",
        f"Needs: {_line(context.get('needs'))}",
        f"Inventory: {_line(context.get('inventory'))}",
        f"Injuries and conditions: {_line(context.get('injuries'))}",
        "",
        "# DRIVES",
    ]
    drives = context.get("drives") or []
    lines.extend([f"- {d.get('id', 'need')}: {d.get('urgency', 0)} — {d.get('reason', 'no reason')}" for d in drives[:3]])
    if not drives:
        lines.append("- No urgent drive is currently reported.")
    lines.extend(["", "# SCENE"])
    observation = context.get("observation") or {}
    facts = observation.get("facts") or []
    lines.extend([f"- {fact}" for fact in facts[:8]] or ["- No new local observation."])
    if observation.get("source"):
        lines.append(f"Source: {observation['source']}; expires in {observation.get('expires_in_minutes', 'unknown')} minutes.")
    lines.extend(["", "# NEARBY"])
    nearby = context.get("nearby_entities") or []
    lines.extend([f"- {x.get('name', x.get('id'))}: {x.get('summary', x.get('type', 'entity'))}" for x in nearby[:8]] or ["- No nearby entity is currently visible."])
    lines.extend(["", "# RELATIONSHIPS"])
    relationships = context.get("direct_relationships") or []
    lines.extend([f"- {r.get('person', 'unknown')}: {r.get('relation', 'known relation')}; trust {r.get('trust', 'unknown')}; {r.get('recent_change', 'no recent change')}" for r in relationships[:5]] or ["- No directly relevant relationship is available."])
    lines.extend(["", "# KNOWLEDGE AND MEMORY"])
    facts = context.get("known_facts") or []
    lines.extend([f"- {f if isinstance(f, str) else f.get('fact', 'unknown fact')}" for f in facts[:8]] or ["- No relevant retained fact."])
    memories = context.get("memories") or []
    lines.extend([f"- Recent {m.get('type', 'memory')}: {m.get('summary') or m.get('fact') or m.get('topic', 'unlabeled experience')}" for m in memories[:5]] or ["- No recent retained experience."])
    lines.extend(["", "# ACTIVE TASK"])
    active = context.get("active_task") or {}
    lines.append(f"Behavior: {_line(active.get('bt'))}; Action: {_line(active.get('action'))}; Remaining: {_line(active.get('remaining_minutes'))} minutes.")
    lines.extend(["", "# TOOLS"])
    for tool in context.get("tools", []):
        lines.append(f"- {tool.get('name')}: {tool.get('purpose')}")
    lines.extend(["", "# TOOL RESULTS"])
    tool_results = context.get("tool_results") or []
    lines.extend([f"- {x.get('tool')}: {x.get('result')}" for x in tool_results[-4:]] or ["- No tool has been called in this turn."])
    lines.extend(["", "# OPTIONS"])
    options = context.get("available_behaviors") or []
    for option in options[:5]:
        lines.append(f"- {option.get('ref')}: {option.get('name', 'unnamed behavior')}")
        for label in ("purpose", "when", "requires", "target", "steps", "time", "cost", "impact", "success", "failure", "interrupt"):
            value = option.get(label)
            if value:
                if isinstance(value, list): value = "; ".join(str(x) for x in value)
                lines.append(f"  {label.title()}: {value}")
    if not options:
        lines.append("- No offered behavior is currently executable; return wait.")
    lines.extend(["", "# LAST PLATFORM RESULT"])
    last = context.get("last_platform_result")
    lines.append(_line(last) if last else "No previous result.")
    rendered = "\n".join(lines)
    if len(rendered) <= max_chars:
        return rendered
    # Preserve the beginning (identity, state and drives) and the end (the
    # current options and Platform result); middle history is the lowest-value
    # material when the configured context budget is exceeded.
    head_size = max_chars * 2 // 3
    tail_size = max_chars - head_size
    return rendered[:head_size] + "\n\n# CONTEXT LIMIT\nEarlier low-priority details were omitted; use a read tool if needed.\n\n" + rendered[-tail_size:]
