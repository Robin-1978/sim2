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


def _tool_arguments(tool: dict[str, Any]) -> str:
    schema = tool.get("parameters") or {}
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    if not properties:
        return "none"
    return "; ".join(
        f"{name}: {definition.get('type', 'value')} ({'required' if name in required else 'optional'})"
        for name, definition in properties.items()
    )


def _protocol(role: str) -> list[str]:
    if role == "agent_decision":
        return [
            "Call request_behavior to select exactly one behavior from executable_behaviors.",
            "Return wait when no behavior is suitable. Do not invent behavior names or fill duration, cost, or effects.",
            "The Platform owns all state changes and continues long actions after this session ends.",
        ]
    if role == "agent_reflection":
        return ["Use the listed tools to submit one experience, belief, goal, or behavior proposal. Platform owns every state change."]
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
    lines = ["<protocol stable=\"true\">"]
    lines.extend(f"    - {item}" for item in _protocol(role))
    lines.extend(["</protocol>", "<tools stable=\"true\">"])
    tools = context.get("tools", [])
    if tools:
        for tool in tools:
            lines.extend([
                f"  <tool name=\"{tool.get('name')}\">",
                f"    purpose: {tool.get('purpose')}",
                f"    arguments: {_tool_arguments(tool)}",
                "  </tool>",
            ])
    else:
        lines.append("  - none")
    lines.extend(["</tools>", "", "<world_state dynamic=\"true\">"])
    world_time = context.get("world_time") or {}
    lines.append(f"  time: {world_time.get('label', world_time.get('minute', 'unknown'))}")
    lines.append(f"  minute: {world_time.get('minute', 'unknown')}")
    lines.append("</world_state>")

    lines.extend([
        "<self dynamic=\"true\">",
        f"  name: {_line(self_state.get('name'))}",
        f"  role: {_line(self_state.get('role'))}",
        f"  status: {_line(self_state.get('status'))}",
        f"  location: {_line(self_state.get('location'))}",
        f"  health: {_line(self_state.get('health'))}",
        f"  stamina: {_line(self_state.get('stamina'))}",
        f"  fatigue: {_line(self_state.get('fatigue'))}",
        f"  long_term_goal: {_line(self_state.get('goal'))}",
        "</self>",
        "<identity dynamic=\"true\">",
        f"  traits: {_line(context.get('identity', {}).get('traits'))}",
        f"  beliefs: {_line(context.get('identity', {}).get('beliefs'))}",
        f"  capabilities: {_line(context.get('identity', {}).get('capabilities'))}",
        f"  recent_capability_changes: {_line(context.get('capability_history'))}",
        f"  goal_tags: {_line(context.get('goal_tags'))}",
        "</identity>",
        "<needs_and_state dynamic=\"true\">",
        f"  needs: {_line(context.get('needs'))}",
        f"  inventory: {_line(context.get('inventory'))}",
        f"  injuries_and_conditions: {_line(context.get('injuries'))}",
        "</needs_and_state>",
        "<drives dynamic=\"true\">",
    ])
    drives = context.get("drives") or []
    lines.extend([f"  - {d.get('id', 'need')}: urgency={d.get('urgency', 0)}; {d.get('reason', 'no reason')}" for d in drives[:3]] or ["  - no urgent drive"])
    model = context.get("drive_model") or {}
    if model:
        lines.append(f"  model: {model.get('family', 'configured hybrid')}; needs are parallel, not a fixed hierarchy")
    lines.append("  Use the highest relevant drive with the goal and executable behavior descriptions to choose one behavior.")
    lines.append("</drives>")

    observation = context.get("observation") or {}
    lines.extend(["  <scene dynamic=\"true\">"])
    lines.extend([f"    - {fact}" for fact in (observation.get("facts") or [])[:8]] or ["    - no new local observation"])
    if observation.get("source"):
        lines.append(f"    source: {observation['source']}; expires: {observation.get('expires_in_minutes', 'unknown')} minutes")
    lines.append("  </scene>")

    lines.extend(["  <nearby_entities dynamic=\"true\">"])
    nearby = context.get("nearby_entities") or []
    lines.extend([f"    - {item.get('name', item.get('id'))}: {item.get('summary', item.get('type', 'entity'))}" for item in nearby[:8]] or ["    - none visible"])
    lines.append("  </nearby_entities>")

    lines.extend(["  <relationships dynamic=\"true\">"])
    relationships = context.get("direct_relationships") or []
    lines.extend([f"    - {item.get('person', 'unknown')}: {item.get('relation', 'related')}; direction={item.get('direction', 'unknown')}; trust={item.get('trust', 'unknown')}" for item in relationships[:5]] or ["    - none relevant"])
    lines.append("  </relationships>")

    lines.extend(["  <knowledge_and_memory dynamic=\"true\">"])
    facts = context.get("known_facts") or []
    lines.extend([f"    fact: {item if isinstance(item, str) else item.get('fact', 'unknown fact')}" for item in facts[:8]] or ["    fact: none relevant"])
    memories = context.get("memories") or []
    lines.extend([f"    memory ({item.get('type', 'memory')}): {item.get('summary') or item.get('fact') or item.get('topic', 'unlabeled experience')}" for item in memories[:5]] or ["    memory: none recent"])
    lines.append("  </knowledge_and_memory>")
    lines.append("<recent_actions dynamic=\"true\">")
    recent_actions = context.get("recent_actions") or []
    lines.extend([f"  - {item.get('event_type')}: {item.get('result') or item.get('behavior_id') or item.get('action_id') or 'completed'} at world minute {item.get('world_minute', 'unknown')}" for item in recent_actions[:2]] or ["  - none"])
    lines.append("</recent_actions>")

    active = context.get("active_task") or {}
    lines.extend([
        "  <active_execution dynamic=\"true\">",
        f"    behavior: {_line(active.get('bt'))}",
        f"    action: {_line(active.get('action'))}",
        f"    remaining_minutes: {_line(active.get('remaining_minutes'))}",
        "  </active_execution>",
        "  <executable_behaviors dynamic=\"true\">",
    ])
    behaviors = context.get("available_behaviors") or []
    for behavior in behaviors[:5]:
        lines.append(f"    <behavior ref=\"{behavior.get('ref')}\">")
        for label in ("name", "summary", "purpose", "when", "requires", "target", "steps", "time", "cost", "impact", "success", "failure", "interrupt", "tags"):
            value = behavior.get(label)
            if value:
                if isinstance(value, list):
                    value = "; ".join(str(item) for item in value)
                lines.append(f"      {label}: {value}")
        lines.append("    </behavior>")
    if not behaviors:
        lines.append("    - none; return wait")
    lines.append("  </executable_behaviors>")

    lines.extend(["  <decision dynamic=\"true\">", f"    {task}", "  </decision>"])
    lines.extend(["  <interaction_history append_only=\"true\">"])
    history = context.get("interaction_history") or []
    if history:
        for item in history[-8:]:
            kind = item.get("kind", "event")
            lines.append(f"    - {kind}: {_line(item)}")
    else:
        lines.append("    - none in this session")
    last = context.get("last_platform_result")
    if last:
        lines.append(f"    latest_platform_result: {_line(last)}")
    lines.append("</interaction_history>")
    rendered = "\n".join(lines)
    if len(rendered) <= max_chars:
        return rendered
    head_size = max_chars * 2 // 3
    tail_size = max_chars - head_size
    return rendered[:head_size] + "\n<context_limit>Earlier low-priority details were omitted; use a read tool if needed.</context_limit>\n" + rendered[-tail_size:]
