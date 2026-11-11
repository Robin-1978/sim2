from __future__ import annotations

from typing import Any


def _number(value: Any) -> str:
    """Render numeric state without exposing binary floating-point noise."""

    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            return "unknown"
        # Six significant digits preserve meaningful world precision while
        # turning values such as 81.200000000004 into ``81.2``.
        return format(value, ".6g")
    return str(value)


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
    if isinstance(value, (int, float)):
        return _number(value)
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
    claim_status = item.get("claim_status")
    if claim_status:
        metadata.append(f"claim={_plain(claim_status)}")
    confidence = item.get("confidence")
    if isinstance(confidence, (int, float)):
        metadata.append(f"confidence={float(confidence):.2f}")
    tags = item.get("tags") if isinstance(item.get("tags"), dict) else {}
    for key, label in (("location", "where"), ("participants", "people"), ("behavior", "behavior"), ("topic", "topic"), ("goal", "goal")):
        value = tags.get(key)
        if value not in (None, "", []):
            metadata.append(f"{label}={_plain(value)}")
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

    interaction = context.get("interaction") or {}
    if interaction:
        lines.extend([
            "<incoming_interaction>",
            f"  from: {_line(interaction.get('from'))}",
            f"  topic: {_line(interaction.get('topic'))}",
            f"  message: {_line(interaction.get('message'))}",
            "  Reply only if this adds information, asks a question, or needs a decision; otherwise return wait.",
            "</incoming_interaction>",
        ])

    triggering_evidence = context.get("triggering_evidence") or []
    if triggering_evidence:
        lines.append("<triggering_evidence>")
        for item in triggering_evidence[:16]:
            lines.append(
                f"  - minute {_line(item.get('world_minute'))}; {_line(item.get('event_type'))}; "
                f"actor={_line(item.get('actor'))}; {_line(item.get('summary'))}"
            )
        lines.append("</triggering_evidence>")

    world_rules = context.get("active_world_rules") or []
    if world_rules:
        lines.append("<world_rules>")
        lines.append("  A rule applies only when its condition is currently active.")
        for rule in world_rules[:8]:
            description = f"; {rule.get('description')}" if rule.get("description") else ""
            applies = f"; applies to {_plain(rule.get('applies_to'))}" if rule.get("applies_to") else ""
            lines.append(
                f"  - {rule.get('name', 'world policy')} [currently {rule.get('current_status', 'unknown')}]: when {rule.get('condition', 'configured condition')}, "
                f"the platform applies {rule.get('effect', 'configured effect')}{applies}{description}"
            )
        lines.append("</world_rules>")

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
    lines.extend([f"  - {d.get('id', 'need')}: urgency={_number(d.get('urgency', 0))}; {d.get('reason', 'no reason')}" for d in drives[:3]] or ["  - no urgent drive"])
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
    if role != "story":
        lines.append("    <short_term_memory>")
        lines.extend([f"      {_memory_line(item)}" for item in (short_memories or [])[:3]] or ["      memory: none"])
        lines.append("    </short_term_memory>")
        lines.append("    <long_term_memory>")
        lines.extend([f"      {_memory_line(item)}" for item in (long_memories or [])[:4]] or ["      memory: none"])
        lines.append("    </long_term_memory>")
    lines.append("  </knowledge_and_memory>")
    lines.append("<recent_actions>")
    recent_actions = context.get("recent_actions") or []
    recent_lines = []
    for item in recent_actions[:2]:
        label = f" ({item['behavior_choice']})" if item.get("behavior_choice") else ""
        recent_lines.append(
            f"  - {item.get('event_type')}{label}: "
            f"{item.get('result') or item.get('summary') or 'completed'} "
            f"at world minute {item.get('world_minute', 'unknown')}"
        )
    lines.extend(recent_lines or ["  - none"])
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
        lines.append("<capability_updates>")
        candidates = context.get("capability_update_candidates") or []
        for item in candidates[:8]:
            lines.append(f"  - {item.get('capability')}: {item.get('completed_outcomes')}/{item.get('required_outcomes')} completed outcomes; maximum update={_number(item.get('max_delta'))}")
        if not candidates:
            lines.append("  - no capability update is currently supported by the configured evidence threshold")
        lines.append("</capability_updates>")
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
        lines.append("  Keep an experience only when it is new, durable, and useful for a future decision; otherwise return wait. Never treat a topic or promise as a confirmed fact. Set claim_status: observed, confirmed, promised, inferred, or failed. Write one to three concise sentences, under 500 characters.")
        lines.append("</outcome_review>")

    if role == "story":
        source = context.get("validated_source") or {}
        lines.append("<validated_source>")
        if source:
            lines.append(f"  event: {source.get('event_type', 'validated event')} at world minute {source.get('world_minute', 'unknown')}")
            lines.append(f"  actor: {_line(source.get('actor'))}; recorded location: {_line(source.get('location'))}")
            lines.append(f"  result: {_line(source.get('result'))}")
            if source.get("fact"):
                lines.append(f"  fact: {_line(source.get('fact'))}")
            lines.append("  Narrative must not add an unrecorded move, delivery, public reading, or gathering.")
        else:
            lines.append("  no validated source event; return wait")
        lines.append("</validated_source>")
        lines.append("<story_state>")
        lines.append("  Choose one configured beat whose evidence is present. If none is listed, propose an emergent beat grounded in the latest validated event.")
        candidates = context.get("audience_candidates") or []
        lines.append("  <audience_candidates>")
        lines.extend([
            f"    - exact character name: {item.get('name')}; {item.get('reason', 'recent validated participant')}"
            for item in candidates[:12]
        ] or ["    - none available; return wait instead of inventing a character name"])
        lines.append("  </audience_candidates>")
        progress_candidates = context.get("world_progress_candidates") or []
        if progress_candidates:
            lines.append("  Configured public progress (submit only when the validated source matches):")
            for item in progress_candidates[:8]:
                evidence = list(item.get("evidence_sources") or []) or (list(item.get("evidence_event_types") or []) + list(item.get("evidence_behaviors") or []))
                lines.append(f"  - {item.get('progress_key')}: {item.get('description') or 'configured progress'}; accepted evidence={_plain(evidence) or 'any validated outcome'}")
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
        lines.append("<world_proposal_contract>")
        lines.append("  - rule.behavior_names must use exact BT names from world_behavior_catalog.")
        lines.append("  - In a behavior_constraint, every listed BT is blocked; list only the blocked BT, never the desired follow-up BT.")
        lines.append("  - rule.action_names must use exact Action names from world_action_catalog; a BT name is not an Action name.")
        lines.append("  - A typed signal records or wakes work; prose descriptions never enforce policy. To block behavior, use effect type behavior_constraint or a denial effect.")
        lines.append("</world_proposal_contract>")
        progress_candidates = context.get("world_progress_candidates") or []
        lines.append("<configured_public_progress>")
        if progress_candidates:
            for item in progress_candidates[:12]:
                evidence = list(item.get("evidence_sources") or []) or (list(item.get("evidence_event_types") or []) + list(item.get("evidence_behaviors") or []))
                lines.append(
                    f"  - {item.get('progress_key')} target={_number(item.get('required', 1))}: "
                    f"{item.get('description') or 'configured progress'}; accepted evidence={_plain(evidence) or 'any validated outcome'}"
                )
            lines.append("  Submit progress only when the triggering evidence matches the named progress definition; otherwise wait.")
        else:
            lines.append("  none configured; do not invent a progress key")
        lines.append("</configured_public_progress>")
        lines.append("<rule_condition_fields>")
        fields = context.get("rule_condition_fields") or []
        for field in fields[:20]:
            lines.append(f"  - {field.get('condition')}: {field.get('meaning', 'configured world value')}")
        if not fields:
            lines.append("  - none")
        lines.append("</rule_condition_fields>")
        lines.append("<world_behavior_catalog>")
        catalog = context.get("registered_behaviors") or []
        for behavior in catalog[:24]:
            success = f"; success={_plain(behavior.get('success'))}" if behavior.get("success") else ""
            lines.append(f"  - BT: {behavior.get('name', 'behavior')}: {behavior.get('purpose', 'configured behavior')}{success}")
        if not catalog:
            lines.append("  - none")
        lines.append("</world_behavior_catalog>")
        lines.append("<world_action_catalog>")
        actions = context.get("registered_actions") or []
        for action in actions[:24]:
            lines.append(f"  - Action: {action.get('name', 'action')}: {action.get('purpose', 'configured action')}")
        if not actions:
            lines.append("  - none")
        lines.append("</world_action_catalog>")
        lines.append("<world_trends>")
        trends = context.get("world_trends") or {}
        resource_labels = trends.get("resource_labels") or {}

        def resource_changes(values: Any) -> str:
            if not isinstance(values, dict) or not values:
                return "none"
            return "; ".join(f"{resource_labels.get(str(key), str(key))}={_plain(value)}" for key, value in values.items())

        current_resources = trends.get("current_resources") or []
        lines.append("  Current resources: " + ("; ".join(f"{item.get('name', 'resource')}={_plain(item.get('current', 'unknown'))}" for item in current_resources[:8]) if current_resources else "none") + ".")
        for period in ("weekly", "monthly"):
            item = trends.get(period) or {}
            goal = item.get("goal_trends") or {}
            relationship = item.get("relationship_trends") or []
            lines.append(
                f"  {period}: resource regeneration={resource_changes(item.get('resources_regenerated') or {})}; "
                f"resource net change={resource_changes(item.get('resource_deltas') or {})}; "
                f"relationship change={_plain(relationship) if relationship else 'none'}; "
                f"goal progress={_plain(goal.get('progress_by_tag') or {})}; "
                f"completed={goal.get('completed_behaviors', 0)}, failed_or_interrupted={goal.get('failed_or_interrupted_behaviors', 0)}."
            )
        progress = context.get("civilization_progress") or []
        lines.append("  Civilization progress: " + ("; ".join(f"{item.get('progress_key')}={_plain(item.get('current', 0))}/{_plain(item.get('required', 0))}" for item in progress[:8]) if progress else "none") + ".")
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
        pending = [item for item in (context.get("world_proposals") or []) if item.get("status") == "pending_evaluation"]
        if pending:
            lines.append("  Pending proposal evaluations:")
            for item in pending[:6]:
                # Proposal/intent IDs are audit-only identifiers.  They add no
                # meaning to a model decision and make prompt cache/content
                # checks noisy, so expose the typed change and due time only.
                expected = item.get("expected") if isinstance(item.get("expected"), dict) else {}
                state = expected.get("state") if isinstance(expected, dict) else {}
                label = expected.get("summary") or item.get("change_type") or "world proposal"
                lines.append(f"  - {label}; due={item.get('evaluation_due_minute')}; expected={_plain(state or expected)}")
        else:
            lines.append("  Pending proposal evaluations: none.")
        outcomes = context.get("recent_agent_outcomes") or []
        if outcomes:
            lines.append("  Recent character outcomes:")
            for item in outcomes[:6]:
                outcome = item.get("outcome") or {}
                lines.append(f"  - {item.get('actor_name') or 'a character'}: {item.get('behavior_choice') or item.get('behavior_name') or 'a behavior'} -> {outcome.get('status', item.get('event_type', 'unknown'))}; {outcome.get('result', 'no result')}")
        lines.append("  Compare expected and observed effects before proposing one next world change.")
        lines.append("</world_outcomes>")

    pending_institutions = context.get("pending_institutions") or []
    active_institutions = context.get("active_institutions") or []
    institution_ledgers = context.get("institution_ledgers") or []
    if pending_institutions or active_institutions or institution_ledgers:
        lines.append("<social_institutions>")
        lines.append("  pending:")
        lines.extend([f"    - {item.get('name')}: {item.get('purpose')}" for item in pending_institutions[:6]] or ["    - none"])
        lines.append("  active:")
        lines.extend([f"    - {item.get('name')}: {item.get('purpose')}" for item in active_institutions[:6]] or ["    - none"])
        if institution_ledgers:
            lines.append("  verified ledger totals:")
            for ledger in institution_ledgers[:4]:
                lines.append(f"    - {ledger.get('name')}: {_plain(ledger.get('entries') or [])}")
        lines.append("  A proposal is only a recorded suggestion. It changes the world only after an authorized activation.")
        lines.append("</social_institutions>")

    if role == "agent_decision":
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
        # The view already contains only Platform-validated executable BTs.
        # Reflection receives outcomes and the registered Action vocabulary,
        # not an unrelated planning catalog or a misleading instruction to
        # wait because no behavior is currently executable.
        for behavior in behaviors:
            lines.append("    <behavior>")
            previous_values: dict[str, str] = {}
            for label in ("name", "summary", "purpose", "when", "requires", "target", "parameters", "steps", "time", "cost", "impact", "success", "failure", "interrupt", "cooldown_minutes", "tags"):
                value = behavior.get(label)
                if value:
                    if isinstance(value, list):
                        value = "; ".join(str(item) for item in value)
                    value_text = str(value)
                    # Purpose commonly repeats summary and requires commonly
                    # repeats when. Keep one authoritative sentence in the
                    # model view instead of charging tokens for duplication.
                    if label == "purpose" and value_text == previous_values.get("summary"):
                        continue
                    if label == "requires" and value_text == previous_values.get("when"):
                        continue
                    previous_values[label] = value_text
                    lines.append(f"      {label}: {value_text}")
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
    if role in {"god", "story"}:
        # World-level roles have projections rather than a character body,
        # inventory, personal drives, or an executable BT catalog. Keep the
        # evidence sections that differ by role and remove empty scaffolding.
        shared_empty_sections = (
            "self", "state_attributes", "identity", "needs_and_state", "drives",
            "nearby_entities", "relationships",
            "recent_actions", "open_trade_offers", "active_execution",
            "executable_behaviors",
        )
        role_empty_sections = ("knowledge_and_memory",) if role == "god" else ()
        for tag in shared_empty_sections + role_empty_sections:
            rendered, _ = _remove_block(rendered, tag)
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
