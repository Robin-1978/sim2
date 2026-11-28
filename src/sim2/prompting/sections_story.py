"""Prompt sections for the story role and the history both world-level roles read."""

from __future__ import annotations

from typing import Any

from .formatting import line, plain


def recent_history_lines(context: dict[str, Any]) -> list[str]:
    """Recent validated history for the world-level roles."""

    triggering_evidence = context.get("triggering_evidence") or []
    lines: list[str] = []
    lines.append("<world_history>")
    lines.append("  Recent validated history is causal context. Continue from it; do not restate a completed step or recreate an existing entity.")
    history = context.get("recent_history") or []
    for item in history[:20]:
        lines.append(
            f"  - minute {item.get('world_minute', 'unknown')}; {item.get('event_type', 'event')}; "
            f"actor={line(item.get('actor'))}; {plain(item.get('details') or {})}"
        )
    if not history:
        lines.append("  - no other recent validated history" if triggering_evidence else "  - no recent validated history")
    lines.append("  Use query_history for an older or narrower causal slice before proposing a continuation.")
    lines.append("</world_history>")
    return lines


def open_question_lines(context: dict[str, Any]) -> list[str]:
    """Active story threads, knowledge gaps and research sources for the world-level roles."""

    lines: list[str] = []
    threads = context.get("active_story_threads") or []
    lines.append("<active_story_threads>")
    lines.append("  These are active audience-facing continuations already grounded in validated events. Use them as world evidence; do not treat a narrative summary as a new state change.")
    for thread in threads[:6]:
        audience = ", ".join(str(item) for item in (thread.get("audience") or [])) or "unspecified audience"
        source = f"; source={line(thread.get('research_source'))}" if thread.get("research_source") else ""
        goal = f"; next={line(thread.get('goal_hint'))}" if thread.get("goal_hint") else ""
        lines.append(f"  - audience={audience}; {line(thread.get('summary'))}{source}{goal}")
    if not threads:
        lines.append("  - none active")
    lines.append("</active_story_threads>")

    gaps = context.get("knowledge_gaps") or []
    lines.append("<knowledge_gaps>")
    lines.append("  These are current research questions, not historical events. Continue an active question; do not create a duplicate of a resolved or already open question.")
    for gap in gaps[:12]:
        detail = f"; source={line(gap.get('source'))}" if gap.get("source") else ""
        stages = f"; stages={line(gap.get('stages'))}" if gap.get("stages") else ""
        finding = f"; last_finding={line(gap.get('last_finding'))}" if gap.get("last_finding") else ""
        lines.append(f"  - {line(gap.get('question'))}; status={line(gap.get('status'))}{detail}{stages}{finding}")
    if not gaps:
        lines.append("  - no active knowledge gap is recorded")
    lines.append("  Use query_knowledge_gaps for the bounded current projection; use query_history for the event evidence behind it.")
    lines.append("</knowledge_gaps>")

    sources = context.get("research_sources") or []
    lines.append("<research_sources>")
    lines.append("  This is the current status of every configured research source. A resolved source has no pending stage; an old historical sentence does not reopen it.")
    for source in sources[:16]:
        question = f"; next={line(source.get('next_question'))}" if source.get("next_question") else ""
        finding = f"; last={line(source.get('last_finding'))}" if source.get("last_finding") else ""
        lines.append(
            f"  - {line(source.get('name'))}; status={line(source.get('status'))}; "
            f"stages={line(source.get('stages'))}; remaining={line(source.get('remaining_stages'))}{question}{finding}"
        )
    if not sources:
        lines.append("  - no configured research source")
    lines.append("</research_sources>")
    return lines


def story_lines(context: dict[str, Any]) -> list[str]:
    """The validated source event and the beats a story turn may choose from."""

    lines: list[str] = []
    plan = context.get("season_plan") or {}
    if isinstance(plan, dict) and str(plan.get("status") or "") == "active":
        lines.append("<season_plan>")
        lines.append(f"  target cell: {plan.get('target_cell', 'unspecified')}")
        lines.append(f"  reason: {plan.get('reason', 'configured coverage target')}")
        if mainline := plan.get("mainline"):
            lines.append(f"  Mainline objective: {mainline['objective']}")
            lines.append(f"  Player tradeoff: {mainline['player_choice']}")
        lines.append("  Use this as the arc's growth direction; do not claim a structural change the world has not made.")
        lines.append("</season_plan>")
    source = context.get("validated_source") or {}
    lines.append("<validated_source>")
    if source:
        lines.append(f"  event: {source.get('event_type', 'validated event')} at world minute {source.get('world_minute', 'unknown')}")
        lines.append(f"  actor: {line(source.get('actor'))}; recorded location: {line(source.get('location'))}")
        lines.append(f"  result: {line(source.get('result'))}")
        if source.get("fact"):
            lines.append(f"  fact: {line(source.get('fact'))}")
        if source.get("research_source"):
            lines.append(f"  research source: {line(source.get('research_source'))}")
        if source.get("allocation_summary"):
            lines.append(f"  allocations: {plain(source.get('allocation_summary'))}")
        if source.get("closed_thread"):
            lines.append(f"  completed chapter and actual choice: {plain(source['closed_thread'])}")
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
            lines.append(f"  - {item.get('progress_key')}: {item.get('description') or 'configured progress'}; accepted evidence={plain(evidence) or 'any validated outcome'}")
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
    return lines
