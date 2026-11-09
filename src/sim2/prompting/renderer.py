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


def _memory_line(item: dict[str, Any]) -> str:
    """Keep durable memory useful without copying a raw transcript into context."""

    summary = (
        item.get("summary")
        or item.get("lesson")
        or item.get("fact")
        or item.get("topic")
        or item.get("message")
        or "unlabeled experience"
    )
    summary = " ".join(str(summary).split())
    if len(summary) > 360:
        summary = summary[:357] + "..."
    metadata: list[str] = []
    source = item.get("source_type") or item.get("source")
    if source:
        metadata.append(f"source={_plain(source)}")
    confidence = item.get("confidence")
    if isinstance(confidence, (int, float)):
        metadata.append(f"confidence={float(confidence):.2f}")
    suffix = f" [{'; '.join(metadata)}]" if metadata else ""
    return f"memory ({item.get('type', 'memory')}): {summary}{suffix}"


def _fact_line(item: Any) -> str:
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return _plain(item)
    fact = item.get("fact") or item.get("statement") or "unknown fact"
    metadata = []
    source = item.get("source_type") or item.get("source")
    confidence = item.get("confidence")
    if source:
        metadata.append(f"source={_plain(source)}")
    if isinstance(confidence, (int, float)):
        metadata.append(f"confidence={float(confidence):.2f}")
    suffix = f" [{'; '.join(metadata)}]" if metadata else ""
    return f"{_plain(fact)}{suffix}"


def _remove_block(text: str, tag: str) -> tuple[str, bool]:
    """Remove one low-priority tagged block without damaging other sections."""

    start_tag = f"<{tag}>"
    end_tag = f"</{tag}>"
    start = text.find(start_tag)
    end = text.find(end_tag, start + len(start_tag)) if start >= 0 else -1
    if start < 0 or end < 0:
        return text, False
    end += len(end_tag)
    return text[:start].rstrip() + text[end:], True


def render_context(context: dict[str, Any], task: str, *, max_chars: int = 16000) -> str:
    """Render a stable XML-like prefix followed by dynamic Agent state.

    The system policy and native tool schemas are stable.  This user message
    puts static protocol/tool explanations first, dynamic world state next,
    the current decision at the end, and appends ReAct history last.
    """

    role = context.get("role", "agent_decision")
    self_state = context.get("self", {})
    lines = ["<protocol>"]
    lines.extend(f"    - {item}" for item in (context.get("prompt_protocol") or []))
    lines.extend(["</protocol>"])
    platform_rules = context.get("platform_rules") or []
    if platform_rules:
        lines.extend(["<platform_rules>", *[f"  - {item}" for item in platform_rules], "</platform_rules>"])
    tools = context.get("tools") or []
    if tools:
        lines.append("<allowed_intents_and_tools>")
        for tool in tools:
            if isinstance(tool, dict) and tool.get("name"):
                lines.append(f"  - {tool['name']}")
        lines.append("</allowed_intents_and_tools>")

    # Keep all stable protocol, rule, and tool-name text before the dynamic
    # snapshot. Providers can then reuse the prefix across world minutes and
    # only reprocess the changing state suffix.
    world_time = context.get("world_time") or {}
    lines.extend([
        "",
        "<world_state>",
        f"  time: {world_time.get('label', world_time.get('minute', 'unknown'))}",
        f"  minute: {world_time.get('minute', 'unknown')}",
        "</world_state>",
    ])

    lines.extend([
        "<self>",
        f"  name: {_line(self_state.get('name'))}",
        f"  role: {_line(self_state.get('role'))}",
        f"  status: {_line(self_state.get('status'))}",
        f"  location: {_line(self_state.get('location'))}",
        f"  known_routes: {_line(self_state.get('known_routes'))}",
        f"  long_term_goal: {_line(self_state.get('goal'))}",
        "</self>",
        "<state_attributes>",
    ])
    attributes = self_state.get("attributes") or []
    lines.extend([
        f"  - {item.get('label', 'attribute')}: {_line(item.get('value'))} {item.get('unit', '')}"
        f"; meaning: {item.get('meaning')}" if item.get("meaning") else
        f"  - {item.get('label', 'attribute')}: {_line(item.get('value'))} {item.get('unit', '')}"
        for item in attributes
    ] or ["  - none"])
    lines.extend([
        "</state_attributes>",
        "<identity>",
        f"  traits: {_line(context.get('identity', {}).get('traits'))}",
        f"  beliefs: {_line(context.get('identity', {}).get('beliefs'))}",
        f"  capabilities: {_line(context.get('identity', {}).get('capabilities'))}",
        f"  recent_capability_changes: {_line(context.get('capability_history'))}",
        f"  goal_tags: {_line(context.get('goal_tags'))}",
        "</identity>",
        "<needs_and_state>",
        "  needs: see the named attributes above",
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
    relationship_lines = []
    for item in relationships[:5]:
        trust = item.get("trust", item.get("relation_strength", item.get("value", "unknown")))
        relationship_lines.append(
            f"    - {item.get('person', 'unknown')}: {item.get('relation', 'related')}; "
            f"direction={item.get('direction', 'unknown')}; trust={_plain(trust)}"
        )
    lines.extend(relationship_lines or ["    - none relevant"])
    lines.append("  </relationships>")

    lines.extend(["  <knowledge_and_memory>"])
    facts = context.get("known_facts") or []
    lines.extend([f"    fact: {_fact_line(item)}" for item in facts[:8]] or ["    fact: none relevant"])
    short_memories = context.get("short_term_memories")
    long_memories = context.get("long_term_memories")
    if short_memories is None and long_memories is None:
        memories = context.get("memories") or []
        short_memories = [item for item in memories if item.get("tier") == "short_term"]
        long_memories = [item for item in memories if item.get("tier") != "short_term"]
    lines.append("    <short_term_memory>")
    lines.extend([f"      {_memory_line(item)}" for item in (short_memories or [])[:3]] or ["      memory: none"])
    lines.append("    </short_term_memory>")
    lines.append("    <long_term_memory>")
    lines.extend([f"      {_memory_line(item)}" for item in (long_memories or [])[:4]] or ["      memory: none"])
    lines.append("    </long_term_memory>")
    lines.append("  </knowledge_and_memory>")
    lines.append("<recent_actions>")
    recent_actions = context.get("recent_actions") or []
    lines.extend([f"  - {item.get('event_type')}: {item.get('result') or item.get('summary') or 'completed'} at world minute {item.get('world_minute', 'unknown')}" for item in recent_actions[:2]] or ["  - none"])
    lines.append("</recent_actions>")

    lines.append("<open_trade_offers>")
    offers = context.get("open_trade_offers") or []
    lines.extend([f"  - choice: {item.get('choice')}; {item.get('summary', 'open offer')}" for item in offers[:5]] or ["  - none"])
    lines.append("</open_trade_offers>")

    constraints = context.get("decision_constraints") or []
    if constraints:
        lines.append("<decision_constraints>")
        lines.extend([f"  - {item}" for item in constraints[:5]])
        lines.append("</decision_constraints>")

    if role == "agent_reflection":
        lines.append("<outcome_review>")
        outcomes = context.get("recent_outcomes") or []
        if outcomes:
            for item in outcomes[:3]:
                outcome = item.get("outcome") or {}
                expected = outcome.get("expected") or {}
                delta = outcome.get("delta") or {}
                lines.append(
                    f"  - {item.get('behavior_choice') or item.get('behavior_name') or 'behavior'}: {outcome.get('status', item.get('event_type', 'unknown'))}; "
                    f"result={outcome.get('result', 'unknown')}; expected={expected.get('summary', 'not stated')}; "
                    f"observed_changes={_plain(delta) if delta else 'none'}"
                )
        else:
            lines.append("  - no completed or interrupted behavior needs review")
        lines.append("  Keep an experience only when it is new, durable, and useful for a future decision; otherwise return wait. Write one to three concise sentences, under 500 characters.")
        lines.append("</outcome_review>")

    if role == "story":
        lines.append("<story_state>")
        lines.append("  Choose one configured beat whose evidence is present. If none is listed, propose an emergent beat grounded in the latest validated event.")
        beats = context.get("story_beats") or []
        for beat in beats[:12]:
            lines.append("  <story_beat>")
            for label in ("choice", "summary", "when", "requires", "audience", "impact"):
                value = beat.get(label)
                if value:
                    if isinstance(value, list):
                        value = "; ".join(str(item) for item in value)
                    lines.append(f"    {label}: {value}")
            lines.append("  </story_beat>")
        if not beats:
            lines.append("  - no configured continuation is currently unlocked; propose a short evidence-grounded emergent beat or wait")
        lines.append("</story_state>")

    if role == "god":
        lines.append("<world_trends>")
        trends = context.get("world_trends") or {}
        current_resources = trends.get("current_resources") or []
        lines.append("  Current resources: " + ("; ".join(f"{item.get('name', 'resource')}={item.get('current', 'unknown')}" for item in current_resources[:8]) if current_resources else "none") + ".")
        for period in ("weekly", "monthly"):
            item = trends.get(period) or {}
            goal = item.get("goal_trends") or {}
            relationship = item.get("relationship_trends") or []
            lines.append(
                f"  {period}: resource regeneration={_plain(item.get('resources_regenerated') or {})}; "
                f"resource net change={_plain(item.get('resource_deltas') or {})}; "
                f"relationship change={_plain(relationship) if relationship else 'none'}; "
                f"goal progress={_plain(goal.get('progress_by_tag') or {})}; "
                f"completed={goal.get('completed_behaviors', 0)}, failed_or_interrupted={goal.get('failed_or_interrupted_behaviors', 0)}."
            )
        lines.append("</world_trends>")
        lines.append("<world_outcomes>")
        changes = context.get("recent_world_changes") or []
        if changes:
            for item in changes[:6]:
                effect = item.get("effect") or {}
                expected = item.get("expected_effects") or item.get("effects") or []
                lines.append(
                    f"  - {item.get('event_type', 'world.change')}: {item.get('summary', 'proposal applied')}; "
                    f"expected={_plain(expected) if expected else 'not stated'}; "
                    f"observed={_plain(effect) if effect else 'event recorded'} at minute {item.get('world_minute', 'unknown')}"
                )
        else:
            lines.append("  - no recent world proposal result")
        outcomes = context.get("recent_agent_outcomes") or []
        if outcomes:
            lines.append("  Recent character outcomes:")
            for item in outcomes[:6]:
                outcome = item.get("outcome") or {}
                lines.append(f"  - {item.get('actor_name') or 'a character'}: {item.get('behavior_choice') or item.get('behavior_name') or 'a behavior'} -> {outcome.get('status', item.get('event_type', 'unknown'))}; {outcome.get('result', 'no result')}")
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
    # The view already contains only Platform-validated executable BTs. Keep
    # the complete current catalog visible so the model never has to choose
    # from a truncated or stale subset.
    for behavior in behaviors:
        lines.append("    <behavior>")
        for label in ("name", "summary", "purpose", "when", "requires", "target", "steps", "time", "cost", "impact", "success", "failure", "interrupt", "cooldown_minutes", "tags"):
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
    lines.extend(["<decision>", f"  {task}", "</decision>"])
    rendered = "\n".join(lines)
    if len(rendered) <= max_chars:
        return rendered
    omitted: list[str] = []
    # Preserve the current task, executable choices, active execution and
    # latest tool result. Remove only retrievable background sections first;
    # cutting the middle of the behavior catalog can leave an unusable prompt.
    for tag in ("registered_actions", "knowledge_and_memory", "nearby_entities", "relationships", "recent_actions"):
        rendered, removed = _remove_block(rendered, tag)
        if removed:
            omitted.append(tag)
        if len(rendered) <= max_chars:
            break
    if len(rendered) <= max_chars:
        note = ", ".join(omitted) or "low-priority details"
        suffix = f"\n<context_limit>Omitted {note}; use a read tool if needed.</context_limit>"
        if len(rendered) + len(suffix) <= max_chars:
            return rendered + suffix
        return rendered[: max_chars - len(suffix)] + suffix
    # As a final guard, keep the complete behavior catalog and the final task
    # section while compacting the remaining prose. This is rarely reached
    # because the compiler already caps memories, facts, and candidates.
    behavior_start = rendered.find("  <executable_behaviors>")
    behavior_end = rendered.find("  </executable_behaviors>", behavior_start)
    decision_start = rendered.find("<decision>")
    if behavior_start >= 0 and behavior_end >= 0 and decision_start >= 0:
        behavior_end += len("  </executable_behaviors>")
        preserved = rendered[behavior_start:behavior_end]
        tail = rendered[decision_start:]
        prefix_budget = max(0, max_chars - len(preserved) - len(tail) - 120)
        prefix = rendered[:prefix_budget]
        return prefix + "\n<context_limit>Earlier context was compacted; use a read tool if needed.</context_limit>\n" + preserved + "\n" + tail
    return rendered[: max_chars - 100] + "\n<context_limit>Context was compacted; use a read tool if needed.</context_limit>"
