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

    if item.get("type") in {"dialogue", "testimony"} or item.get("source_type") in {"testimony", "own_statement"}:
        # A topic is only a retrieval key. The Agent needs the actual words to
        # distinguish a report, promise, uncertainty, and fabrication.
        summary = item.get("message") or item.get("summary") or item.get("topic") or "unlabeled dialogue"
    else:
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
    credibility = item.get("credibility")
    if isinstance(credibility, (int, float)):
        metadata.append(f"credibility={float(credibility):.2f}")
    verification = item.get("verification_status")
    if verification:
        metadata.append(f"verification={_plain(verification)}")
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
    status = item.get("status")
    if status and str(status) != "active":
        metadata.append(f"status={_plain(status)}")
    if item.get("superseded_by"):
        metadata.append(f"superseded_by={_plain(item.get('superseded_by'))}")
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


# Background sections an over-long prompt gives up whole, in this order. The
# character sections come first. The world-level ones follow: statistics, the
# rule-field list and recent outcomes only matter to particular proposals, while
# the evidence, the institutions and the task decide every session and are never
# dropped.
_BACKGROUND_SECTIONS = (
    "registered_actions", "knowledge_and_memory", "nearby_entities", "relationships", "recent_actions",
    "world_trends", "world_outcomes", "knowledge_gaps", "rule_condition_fields",
)


def _compact_world_prompt(rendered: str, max_chars: int) -> str | None:
    """Fit a world-level prompt by cutting reference prose, never institutions or the task.

    The reviewer reads its evidence first and the institutions it may revise
    and the task last, so only the end of the reference sections in between
    gives way.  Returns ``None`` when even those two do not fit.
    """

    decision_start = rendered.find("<decision>")
    if decision_start < 0:
        return None
    marker = "\n<context_limit>Earlier context was compacted; use a read tool if needed.</context_limit>\n"
    # The latest tool/platform feedback is part of the decision protocol. Keep
    # it with the task when compacting a world prompt; otherwise a rejection
    # can disappear and the next model turn cannot correct its proposal.
    interaction_start = rendered.find("  <interaction_history>")
    tail_start = interaction_start if 0 <= interaction_start < decision_start else decision_start
    tail = rendered[tail_start:]
    institutions_start = rendered.find("<social_institutions>")
    institutions_end = rendered.find("</social_institutions>", institutions_start) if institutions_start >= 0 else -1
    if institutions_start >= 0 and 0 <= institutions_end < decision_start:
        preserved = rendered[institutions_start : institutions_end + len("</social_institutions>")] + "\n"
        head = rendered[:institutions_start]
    else:
        preserved = ""
        head = rendered[:decision_start]
    budget = max_chars - len(marker) - len(preserved) - len(tail)
    if budget <= 0:
        return None
    # Cut at a line end so the reviewer never reads half a sentence.
    return head[:budget].rsplit("\n", 1)[0] + marker + preserved + tail


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

    if role in {"story", "god"}:
        lines.append("<world_history>")
        lines.append("  Recent validated history is causal context. Continue from it; do not restate a completed step or recreate an existing entity.")
        history = context.get("recent_history") or []
        for item in history[:20]:
            lines.append(
                f"  - minute {item.get('world_minute', 'unknown')}; {item.get('event_type', 'event')}; "
                f"actor={_line(item.get('actor'))}; {_plain(item.get('details') or {})}"
            )
        if not history:
            lines.append("  - no other recent validated history" if triggering_evidence else "  - no recent validated history")
        lines.append("  Use query_history for an older or narrower causal slice before proposing a continuation.")
        lines.append("</world_history>")

        gaps = context.get("knowledge_gaps") or []
        lines.append("<knowledge_gaps>")
        lines.append("  These are current research questions, not historical events. Continue an active question; do not create a duplicate of a resolved or already open question.")
        for gap in gaps[:12]:
            detail = f"; source={_line(gap.get('source'))}" if gap.get("source") else ""
            stages = f"; stages={_line(gap.get('stages'))}" if gap.get("stages") else ""
            finding = f"; last_finding={_line(gap.get('last_finding'))}" if gap.get("last_finding") else ""
            lines.append(f"  - {_line(gap.get('question'))}; status={_line(gap.get('status'))}{detail}{stages}{finding}")
        if not gaps:
            lines.append("  - no active knowledge gap is recorded")
        lines.append("  Use query_knowledge_gaps for the bounded current projection; use query_history for the event evidence behind it.")
        lines.append("</knowledge_gaps>")

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
    tracks = context.get("research_tracks") or []
    if tracks:
        lines.append("  <research_tracks>")
        lines.append("    Research is staged. A completed stage does not end a source; choose another available stage or a newly proposed source.")
        for track in tracks[:8]:
            stages = "; ".join(
                f"stage {'available' if item.get('available') else item.get('status', 'completed')} (work units {item.get('required', 1)})"
                for item in (track.get("stages") or [])[:8]
            )
            location = f" at {track.get('location')}" if track.get('location') else ""
            lines.append(f"    - {track.get('source')}{location}: {stages or 'no stage metadata'}")
        lines.append("  </research_tracks>")
    assignments = context.get("behavior_assignments") or []
    story_goals = context.get("story_goals") or []
    active_beats = context.get("active_story_beats") or []
    if assignments or story_goals or active_beats:
        lines.append("  <story_directives>")
        for assignment in assignments[:4]:
            if isinstance(assignment, dict):
                lines.append(
                    f"    assigned_work: {_line(assignment.get('goal'))}; "
                    f"choose {_line(assignment.get('behavior_id'))} when it is executable"
                )
        for goal in story_goals[:3]:
            lines.append(f"    goal: {_line(goal)}")
        for beat in active_beats[:3]:
            if isinstance(beat, dict):
                lines.append(
                    f"    beat: {_line(beat.get('summary'))} at world minute {_line(beat.get('world_minute'))}"
                )
        lines.append("  </story_directives>")
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
        lines.append("  Keep an experience only when it is new, durable, and useful for a future decision; otherwise return wait. Never treat a topic, report, or promise as a confirmed fact. Set claim_status: observed, confirmed, reported, promised, inferred, disputed, false, or failed. Use disputed or false only when event evidence contradicts the claim. Write one to three concise sentences, under 500 characters.")
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
            if source.get("research_source"):
                lines.append(f"  research source: {_line(source.get('research_source'))}")
            if source.get("allocation_summary"):
                lines.append(f"  allocations: {_plain(source.get('allocation_summary'))}")
            lines.append("  Narrative must not add an unrecorded move, delivery, public reading, or gathering.")
        else:
            lines.append("  no validated source event; return wait")
        lines.append("</validated_source>")
        lines.append("<story_state>")
        lines.append("  Choose one configured beat whose evidence is present. If none is listed, propose an emergent beat using only the latest validated event's fact, actor, location, and result; do not add numbers, measurements, causes, or details from an unrelated memory. The beat must carry a concrete next goal or decision and name an exact executable follow-up from the audience when one is shown; do not merely repeat the event summary. Research always uses the existing generic research behavior with a selected source parameter.")
        candidates = context.get("audience_candidates") or []
        lines.append("  <audience_candidates>")
        if candidates:
            for item in candidates[:12]:
                lines.append(f"    - exact character name: {item.get('name')}; {item.get('reason', 'recent validated participant')}")
                choices = item.get("executable_behaviors") or []
                if choices:
                    lines.append("      executable follow-ups:")
                    for choice in choices[:6]:
                        lines.append(f"        - {choice.get('choice')}: {choice.get('purpose') or 'configured behavior'}")
                        options = choice.get("parameter_options") or {}
                        if options.get("source_id"):
                            lines.append("          research sources: " + "; ".join(str(option.get("name")) for option in options["source_id"][:8]))
        else:
            lines.append("    - none available; return wait instead of inventing a character name")
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
            for label in ("choice", "summary", "when", "requires", "audience", "followup_behaviors", "impact"):
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
        trigger = context.get("evolution_trigger") or {}
        if trigger:
            lines.append("<evolution_trigger>")
            lines.append(f"  id: {trigger.get('id', 'configured trigger')}")
            lines.append(f"  condition: {trigger.get('condition', 'configured condition')}")
            if trigger.get("purpose"):
                lines.append(f"  purpose: {trigger.get('purpose')}")
            if trigger.get("required_result"):
                lines.append(f"  required result: {trigger.get('required_result')}")
            if trigger.get("required_definition_type"):
                lines.append(f"  required definition type: {trigger.get('required_definition_type')}")
            if trigger.get("assign_to"):
                lines.append(f"  assign the new behavior to: {_plain(trigger.get('assign_to'))}")
            if trigger.get("evidence_sources"):
                lines.append(f"  evidence selectors: {_plain(trigger.get('evidence_sources'))}")
            if trigger.get("minimum_evidence") is not None:
                lines.append(f"  minimum matching events: {trigger.get('minimum_evidence')}; observed: {trigger.get('observed_evidence', 'unknown')}")
            if trigger.get("require_story_outcome"):
                lines.append("  required handoff: an Action followup_behavior must include one outcome_events entry with wake ['story'] so Story receives the real completed result.")
            # The submission format of a research source is shown to wakes that
            # review research evidence. Matching the wording of the required
            # result instead pulled every wake that merely listed a research
            # source among its options toward proposing one.
            evidence_types = {
                str(event_type)
                for source in (trigger.get("evidence_sources") or []) if isinstance(source, dict)
                for event_type in (source.get("event_types") or [])
            }
            if evidence_types & {"research.completed", "research.progressed"}:
                lines.append("  For this research continuation, use propose_entity only for one genuinely new unanswered question. Include entity_type=document, a short name, a visible location, state.research_novelty_key, state.research_question, state.research_discoveries: [{fact: 'one new question or finding'}], research_required: a positive number, and research_assignees: [the exact visible name of a character whose own choices already include researching a configured source; Platform rejects unreachable or near-duplicate sources]. Do not create a page, draft, ledger, continuation or copy of an existing question; use revise_institution or a supply behavior for a resource problem. If every current source is exhausted, do not wait merely because the last topic shares words with the new evidence: derive a distinct causal dimension (effect, risk, intervention, provenance, or application) from the verified result, give it a new novelty key, and require a different fact or decision. A changed title for the same question is still a duplicate.")
            lines.append("  Use only the configured trigger tools. A rule signal or narrative acknowledgement is not a substitute for the required result.")
            lines.append("</evolution_trigger>")
        lines.append("<world_proposal_contract>")
        lines.append("  - Create any missing world content the validated situation needs: agent, location, faction, object, empty resource node, institution, relationship, rule, attribute, world property, capability, Action, behavior, recipe, or narrative event.")
        lines.append("  - For a research source, put research_discoveries, research_required/required, and any research_assignees in the proposed entity data. Reuse the registered generic research behavior; do not create one BT per source.")
        lines.append("  - Use the smallest supported content type that makes the next character decision possible. Platform still validates names, references, conservation, executable behavior, and consequences.")
        lines.append("  - rule.behavior_names must use exact BT names from world_behavior_catalog.")
        lines.append("  - In a behavior_constraint, every listed BT is blocked; list only the blocked BT, never the desired follow-up BT.")
        lines.append("  - rule.action_names must use exact Action names from world_action_catalog; a BT name is not an Action name.")
        lines.append("  - A typed signal records or wakes work; prose descriptions never enforce policy. To block behavior, use effect type behavior_constraint or a denial effect.")
        lines.append("  - Use the exact machine field in rule_condition_fields, including underscores inside world[\"...\"]. A God proposal must not wake God itself; use a character/follow-up behavior or wait.")
        lines.append("  - A behavior proposal must use definition.nodes: [{type: action, action_id: exact registered Action name, parameters: {...}}], plus entry_conditions, success_results and failure_results. Do not invent steps, trigger, outcomes, action_names or free-form action objects.")
        lines.append("  - A new Action cannot be submitted alone: include followup_behavior {name, assign_to, definition} in the same propose_definition call. If its duration is recipe_duration or its reserve uses recipe_inputs, include followup_recipe {name, definition} in that same call. The BT must reference the new Action by visible name and be executable for the assigned NPC immediately.")
        lines.append("  - Recipe Action shape: parameters:{recipe_id:'string'}, duration:{mode:'recipe_duration'}, costs:{reserve:{recipe_inputs:1}}; put recipe_inputs under costs.reserve. Include followup_recipe with positive duration_minutes, inputs and outputs; existing resource names are resolved to registry keys.")
        lines.append("  - Action success/failure each need {condition?, result}; Travel To uses location_id. Copy parameter names from world_action_catalog. A dynamic parameter such as item_id is not an inventory key; omit the condition or use the literal visible item name.")
        lines.append("  - A new Action must have a real observable result: declare effects, information or capability/progress effects, or bind a Recipe with outputs. A success label or outcome_events field alone never changes resources or state; do not call a resource refill successful without a configured effect that performs it.")
        lines.append("  - To move carried material into an existing world resource, use costs.reserve:{item_parameter:<item parameter>, item_quantity_parameter:<quantity parameter>} and effect:{type:'resource_transfer', resource_id:<existing visible resource>, source_item_parameter:<same item parameter>, quantity_parameter:<same quantity parameter>}; omit item_quantity_parameter only for one item with quantity:1. The transfer must equal the reserved amount and cannot mint stock or target a missing resource.")
        lines.append("  - Action parameters whose names end in _entity or _id must use exact visible entity names from the world view and parameter names from the Action catalog. A behavior is rejected unless its assigned character can execute the whole sequence now.")
        visible_names = ", ".join(str(item.get("name")) for item in (context.get("visible_entities") or [])[:48] if item.get("name"))
        lines.append(f"  - Exact visible entities (use these names): {visible_names or 'none'}.")
        examples = context.get("condition_examples") or {}
        if examples:
            lines.append("  - Machine condition examples: " + "; ".join(f"{label}={example}" for label, example in examples.items()) + ". A bare world property is not a condition; compare it with a number or true/false, or use [] when no durable condition is needed.")
        lines.append("  - Reusable behavior entry_conditions must use durable actor, capability, inventory, location, relationship or world state. Do not use event.recent.*, which would make the behavior disappear immediately after the current event.")
        lines.append("  - Research is open-ended: when validated results leave a concrete unanswered question, you may propose one new declarative research source or behavior. Do not invent a result; let a character perform the next research.")
        lines.append("  - A newer verified research fact may supersede an older one. Preserve the old fact for audit and use the configured lifecycle status instead of silently deleting it.")
        lines.append("  - If the assigned actor is not already at the required place, include a Travel node before the destination Action; Platform must be able to execute the complete sequence from current state. If no durable condition is needed, use entry_conditions: [] rather than prose.")
        lines.append("  - After a read-budget warning, use the facts already collected. Do not re-read the same source and exhaust the session.")
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
        # God needs the registered action vocabulary and outcome, but not the
        # full parameter schema for every action during a world review. The
        # detailed schema remains in character/BT views where it is used to
        # execute a behavior.
        action_limit = 16 if role == "god" else 24
        for action in actions[:action_limit]:
            parameters = "; ".join(str(item) for item in (action.get("parameters") or []))
            schema = action.get("parameter_schema") or {}
            entity_requirements = "; ".join(
                f"{key} must be a visible {value.get('entity_type')}"
                for key, value in schema.items()
                if isinstance(value, dict) and value.get("entity_type")
            )
            defaults = _plain(action.get("parameter_defaults") or {})
            success = _plain(action.get("success") or [])
            if role == "god":
                costs = action.get("costs") if isinstance(action.get("costs"), dict) else {}
                reserve = _plain((costs or {}).get("reserve") or {})
                duration_value = action.get("duration") if isinstance(action.get("duration"), dict) else {}
                recipe_like = duration_value.get("mode") == "recipe_duration" or "recipe_inputs" in ((costs or {}).get("reserve") or {})
                detail = f"parameters={parameters or 'none'}"
                if recipe_like:
                    detail += f"; defaults={defaults or 'none'}; duration={_plain(duration_value) or 'not stated'}; reserve={reserve or 'none'}"
                lines.append(
                    f"  - Action: {action.get('name', 'action')}: {action.get('purpose', 'configured action')}; "
                    f"{detail}; result={success or 'not stated'}"
                )
            else:
                lines.append(
                    f"  - Action: {action.get('name', 'action')}: {action.get('purpose', 'configured action')}; "
                    f"parameters={parameters or 'none'}; entity requirements={entity_requirements or 'none'}; "
                    f"defaults={defaults or 'none'}; result={success or 'not stated'}"
                )
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
        configured_regeneration = context.get("configured_resource_regeneration") or []
        if configured_regeneration:
            lines.append(
                "  Configured regeneration (future world rule, do not confuse with the current observation window): "
                + "; ".join(
                    f"{item.get('resource')} +{_plain(item.get('amount'))} every {item.get('every_minutes')} minutes"
                    + (f" up to {_plain(item.get('cap'))}" if item.get('cap') is not None else "")
                    for item in configured_regeneration[:8]
                )
                + "."
            )
        else:
            lines.append("  Configured regeneration: none declared.")
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
        # Only the world-level reviewer compares terms with results; the
        # characters see what an institution is for, not its bookkeeping.
        lines.extend([
            f"    - {item.get('name')}: {item.get('purpose')}" + (f" [terms: {item.get('terms')}]" if role == "god" and item.get("terms") else "")
            for item in active_institutions[:6]
        ] or ["    - none"])
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
            for label in ("name", "summary", "purpose", "when", "requires", "target", "parameters", "parameter_options", "steps", "time", "cost", "impact", "success", "failure", "interrupt", "cooldown_minutes", "tags"):
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
    lines.extend(["<decision>", f"  {task}"])
    if role == "agent_decision" and context.get("decision_guidance"):
        lines.append(f"  Decision guidance: {context['decision_guidance']}")
    lines.append("</decision>")
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
    background_sections = list(_BACKGROUND_SECTIONS)
    # An empty outcome scaffold is less useful than the resource/trend
    # projection. Once a proposal or outcome exists, preserve that causal
    # review section and compact trends first. This keeps the choice driven by
    # evidence instead of a fixed token-order accident.
    outcomes_start = rendered.find("<world_outcomes>")
    outcomes_end = rendered.find("</world_outcomes>", outcomes_start)
    if outcomes_start >= 0 and outcomes_end > outcomes_start:
        outcomes = rendered[outcomes_start:outcomes_end]
        has_active_institution = "[terms:" in rendered
        if (
            "Pending proposal evaluations: none." in outcomes
            and "- no recent world proposal result" in outcomes
            and not has_active_institution
        ):
            background_sections.remove("world_outcomes")
            background_sections.insert(0, "world_outcomes")
    for tag in background_sections:
        rendered, removed = _remove_block(rendered, tag)
        if removed:
            omitted.append(tag)
            note = f"\n<context_limit>Omitted {', '.join(omitted)}; use a read tool if needed.</context_limit>"
            if len(rendered) + len(note) <= max_chars:
                return rendered + note
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
    if role in {"god", "story"}:
        compacted = _compact_world_prompt(rendered, max_chars)
        if compacted is not None:
            return compacted
    return rendered[: max_chars - 100] + "\n<context_limit>Context was compacted; use a read tool if needed.</context_limit>"
