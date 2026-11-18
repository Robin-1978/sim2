"""Prompt sections about one character: identity, scene, memory, directives, activity and executable choices."""

from __future__ import annotations

from typing import Any

from .formatting import fact_line, line, memory_line, number, plain


def character_lines(context: dict[str, Any]) -> list[str]:
    """The character's own state, identity, needs and drives."""

    self_state = context.get("self", {})
    lines: list[str] = []
    lines.extend([
        "<self>",
        f"  name: {line(self_state.get('name'))}",
        f"  role: {line(self_state.get('role'))}",
        f"  status: {line(self_state.get('status'))}",
        f"  location: {line(self_state.get('location'))}",
        f"  known_routes: {line(self_state.get('known_routes'))}",
        f"  long_term_goal: {line(self_state.get('goal'))}",
        "</self>",
        "<state_attributes>",
    ])
    attributes = self_state.get("attributes") or []
    lines.extend([
        f"  - {item.get('label', 'attribute')}: {line(item.get('value'))} {item.get('unit', '')}"
        f"; meaning: {item.get('meaning')}" if item.get("meaning") else
        f"  - {item.get('label', 'attribute')}: {line(item.get('value'))} {item.get('unit', '')}"
        for item in attributes
    ] or ["  - none"])
    lines.extend([
        "</state_attributes>",
        "<identity>",
        f"  traits: {line(context.get('identity', {}).get('traits'))}",
        f"  beliefs: {line(context.get('identity', {}).get('beliefs'))}",
        f"  capabilities: {line(context.get('identity', {}).get('capabilities'))}",
        f"  recent_capability_changes: {line(context.get('capability_history'))}",
        f"  goal_tags: {line(context.get('goal_tags'))}",
        "</identity>",
        "<needs_and_state>",
        "  needs: see the named attributes above",
        f"  inventory: {line(context.get('inventory'))}",
        f"  injuries_and_conditions: {line(context.get('injuries'))}",
        "</needs_and_state>",
        "<drives>",
    ])
    drives = context.get("drives") or []
    lines.extend([f"  - {d.get('id', 'need')}: urgency={number(d.get('urgency', 0))}; {d.get('reason', 'no reason')}" for d in drives[:3]] or ["  - no urgent drive"])
    model = context.get("drive_model") or {}
    if model:
        lines.append(f"  model: {model.get('family', 'configured hybrid')}; needs are parallel, not a fixed hierarchy")
    lines.append("  Use the highest relevant drive with the goal and executable behavior descriptions to choose one behavior.")
    lines.append("</drives>")
    return lines


def scene_lines(context: dict[str, Any]) -> list[str]:
    """The local scene, nearby entities and relationships."""

    lines: list[str] = []
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
            f"direction={item.get('direction', 'unknown')}; trust={plain(trust)}"
        )
    lines.extend(relationship_lines or ["    - none relevant"])
    lines.append("  </relationships>")
    return lines


def knowledge_lines(context: dict[str, Any], role: str) -> list[str]:
    """Known facts, memories and staged research tracks."""

    lines: list[str] = []
    lines.extend(["  <knowledge_and_memory>"])
    facts = context.get("known_facts") or []
    lines.extend([f"    fact: {fact_line(item)}" for item in facts[:8]] or ["    fact: none relevant"])
    short_memories = context.get("short_term_memories")
    long_memories = context.get("long_term_memories")
    if short_memories is None and long_memories is None:
        memories = context.get("memories") or []
        short_memories = [item for item in memories if item.get("tier") == "short_term"]
        long_memories = [item for item in memories if item.get("tier") != "short_term"]
    if role != "story":
        lines.append("    <short_term_memory>")
        lines.extend([f"      {memory_line(item)}" for item in (short_memories or [])[:3]] or ["      memory: none"])
        lines.append("    </short_term_memory>")
        lines.append("    <long_term_memory>")
        lines.extend([f"      {memory_line(item)}" for item in (long_memories or [])[:4]] or ["      memory: none"])
        lines.append("    </long_term_memory>")
    lines.append("  </knowledge_and_memory>")
    denial_memories = context.get("denial_memories") or []
    if denial_memories and role != "story":
        lines.append("  <denial_memory>")
        for item in denial_memories[:3]:
            lines.append(
                f"    denial_event: {item.get('event_id')}; behavior: {item.get('behavior')}; "
                f"reason: {item.get('reason')}; rule: {item.get('rule')}; "
                f"target: {item.get('target')}; minute: {item.get('minute')}; "
                f"authority: {item.get('authority')}"
            )
        lines.append("    A denial is a fact you may cite as based_on_event when requesting access.")
        lines.append("  </denial_memory>")
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
    return lines


def directive_lines(context: dict[str, Any]) -> list[str]:
    """Story directives and configured progression stages."""

    lines: list[str] = []
    assignments = context.get("behavior_assignments") or []
    story_goals = context.get("story_goals") or []
    active_beats = context.get("active_story_beats") or []
    if assignments or story_goals or active_beats:
        lines.append("  <story_directives>")
        for assignment in assignments[:4]:
            if isinstance(assignment, dict):
                lines.append(
                    f"    assigned_work: {line(assignment.get('goal'))}; "
                    f"choose {line(assignment.get('behavior_id'))} when it is executable"
                )
        for goal in story_goals[:3]:
            lines.append(f"    goal: {line(goal)}")
        for beat in active_beats[:3]:
            if isinstance(beat, dict):
                lines.append(
                    f"    beat: {line(beat.get('summary'))} at world minute {line(beat.get('world_minute'))}"
                )
        lines.append("  </story_directives>")
    progression = context.get("progression_goals") or []
    if progression:
        lines.append("  <progression>")
        lines.append("    These are configured world stages. Prefer ready targets, then visible support behaviors that can unblock the highest-priority stage.")
        for goal in progression[:5]:
            progress = goal.get("progress") or {}
            progress_text = f" ({progress.get('current', 0)}/{progress.get('required', 1)} verified)" if progress else ""
            targets = "; ".join(goal.get("ready_behaviors") or goal.get("target_behaviors") or []) or "none ready"
            support = "; ".join(goal.get("support_behaviors") or []) or "none"
            missing = "; ".join(goal.get("missing_requirements") or []) or "none"
            lines.append(
                f"    - {goal.get('label')}: status={goal.get('status')}; "
                f"target={targets}; support={support}; missing={missing}{progress_text}"
            )
        lines.append("  </progression>")
    return lines


def activity_lines(context: dict[str, Any]) -> list[str]:
    """Recent actions, open trade offers and decision constraints."""

    lines: list[str] = []
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
    return lines


def reflection_lines(context: dict[str, Any]) -> list[str]:
    """Capability update candidates and the outcome review for a reflection turn."""

    lines: list[str] = []
    lines.append("<capability_updates>")
    candidates = context.get("capability_update_candidates") or []
    for item in candidates[:8]:
        lines.append(f"  - {item.get('capability')}: {item.get('completed_outcomes')}/{item.get('required_outcomes')} completed outcomes; maximum update={number(item.get('max_delta'))}")
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
                f"observed_changes={plain(delta) if delta else 'none'}"
            )
    else:
        lines.append("  - no completed or interrupted behavior needs review")
    lines.append("  Keep an experience only when it is new, durable, and useful for a future decision; otherwise return wait. Never treat a topic, report, or promise as a confirmed fact. Set claim_status: observed, confirmed, reported, promised, inferred, disputed, false, or failed. Use disputed or false only when event evidence contradicts the claim. Write one to three concise sentences, under 500 characters.")
    lines.append("</outcome_review>")
    return lines


def executable_behavior_lines(context: dict[str, Any]) -> list[str]:
    """The active execution and the Platform-validated behaviors a character may choose."""

    lines: list[str] = []
    active = context.get("active_task") or {}
    lines.extend([
        "  <active_execution>",
        f"    behavior: {line(active.get('bt'))}",
        f"    action: {line(active.get('action'))}",
        f"    remaining_minutes: {line(active.get('remaining_minutes'))}",
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
        for label in ("name", "summary", "purpose", "when", "requires", "target", "parameters", "parameter_options", "steps", "time", "cost", "impact", "success", "failure", "interrupt", "cooldown_minutes", "tags", "goal_tags", "drive_tags", "supports_drive_tags"):
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
    blocked = context.get("blocked_behaviors") or []
    if blocked:
        lines.append("  <blocked_behaviors>")
        lines.append("    A blocked behavior is visible so you can understand the obstacle. Attempt it only when attemptable is true; Platform will record a real denial.")
        for behavior in blocked:
            eligibility = behavior.get("eligibility") or {}
            reasons = eligibility.get("reasons") or []
            reason_text = "; ".join(
                f"{item.get('type')} ({item.get('message', '')})"
                for item in reasons
            ) or "authorization unavailable"
            lines.append(
                f"    - choice: {behavior.get('choice') or behavior.get('name')}; "
                f"attemptable: {str(bool(behavior.get('attemptable'))).lower()}; "
                f"reasons: {reason_text}"
            )
        lines.append("  </blocked_behaviors>")
    pending = context.get("pending_access_requests") or []
    delegations = context.get("active_delegations") or []
    if pending or delegations:
        lines.append("  <access_authority>")
        for item in pending[:6]:
            lines.append(
                f"    pending_request: {item.get('request_id')}; requester: {item.get('requester')}; "
                f"behavior: {item.get('behavior')}; target: {item.get('target')}; scope: {item.get('scope')}; "
                f"reason: {item.get('reason')}"
            )
        for item in delegations[:6]:
            lines.append(
                f"    active_delegation: {item.get('delegation_id')}; source: {item.get('source')}; "
                f"target: {item.get('target')}; scope: {item.get('scope')}; granted_by: {item.get('granted_by')}"
            )
        lines.append("  </access_authority>")
    return lines


def registered_action_lines(context: dict[str, Any]) -> list[str]:
    """The registered action vocabulary shown to a reflection turn."""

    lines: list[str] = []
    lines.append("  <registered_actions>")
    actions = context.get("available_actions") or []
    lines.extend([f"    - choice: {item.get('choice')}; {item.get('summary', 'configured action')}" for item in actions[:24]] or ["    - none"])
    lines.append("  </registered_actions>")
    return lines
