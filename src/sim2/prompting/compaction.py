"""Fitting a rendered prompt into its size limit without losing the task or the executable choices."""

from __future__ import annotations


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
# character sections come first. The world-level ones follow: trends are
# removable reference data, while outcomes, knowledge gaps and rule-field
# details matter to particular proposals. A non-institutional God review may
# move trends to the end when it is the only useful long-term projection.
_BACKGROUND_SECTIONS = (
    "registered_actions", "knowledge_and_memory", "nearby_entities", "relationships", "recent_actions",
    "research_sources", "world_trends", "world_outcomes", "knowledge_gaps", "rule_condition_fields",
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


def strip_world_scaffolding(rendered: str, role: str) -> str:
    """Drop the empty character scaffolding a world-level prompt does not use."""

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
    return rendered


def _background_order(rendered: str) -> list[str]:
    """Order in which an over-long prompt gives up its background sections."""

    background_sections = list(_BACKGROUND_SECTIONS)
    # An empty outcome scaffold is less useful than the resource/trend
    # projection. Once a proposal or outcome exists, preserve that causal
    # review section and compact trends first. This keeps the choice driven by
    # evidence instead of a fixed token-order accident.
    outcomes_start = rendered.find("<world_outcomes>")
    outcomes_end = rendered.find("</world_outcomes>", outcomes_start)
    outcomes = rendered[outcomes_start:outcomes_end] if outcomes_start >= 0 and outcomes_end > outcomes_start else ""
    meaningful_outcomes = bool(outcomes) and not (
        "Pending proposal evaluations: none." in outcomes
        and "- no recent world proposal result" in outcomes
    )
    has_active_institution = "[terms:" in rendered
    if has_active_institution and "research_sources" in background_sections:
        # Institution review must keep its terms and the established trend
        # compaction contract. Research status remains available through the
        # query tool if this lower-priority reference block is trimmed.
        background_sections.remove("research_sources")
        background_sections.append("research_sources")
    if outcomes_start >= 0 and outcomes_end > outcomes_start:
        if meaningful_outcomes or has_active_institution:
            # A committed world change or institution evaluation is the
            # current causal evidence. Keep it until all less-specific
            # reference sections have been compacted.
            background_sections.remove("world_outcomes")
            background_sections.append("world_outcomes")
        else:
            # An empty outcome scaffold is cheaper to drop than trends or
            # configured resources, so remove it first when no causal result
            # needs to be reviewed.
            background_sections.remove("world_outcomes")
            background_sections.insert(0, "world_outcomes")
    # A non-institutional God review that contains a real trend should retain
    # that trend when only a small amount must be compacted. World outcomes or
    # another retrievable reference section can give way first. Institution
    # reviews keep the historical order so their terms and causal evidence
    # remain together and the established compaction contract is preserved.
    trends_start = rendered.find("<world_trends>")
    trends_end = rendered.find("</world_trends>", trends_start)
    trends = rendered[trends_start:trends_end] if trends_start >= 0 and trends_end > trends_start else ""
    # God needs the trend scaffold even when the current window contains no
    # changes: it still carries configured regeneration and the empty baseline
    # that distinguishes "no evidence" from a missing projection.
    if trends and not meaningful_outcomes and not has_active_institution:
        background_sections.remove("world_trends")
        background_sections.append("world_trends")
    return background_sections


def fit_to_limit(rendered: str, role: str, max_chars: int) -> str:
    """Compact a rendered prompt to ``max_chars`` while keeping the task and executable choices."""

    if len(rendered) <= max_chars:
        return rendered
    omitted: list[str] = []
    # Preserve the current task, executable choices, active execution and
    # latest tool result. Remove only retrievable background sections first;
    # cutting the middle of the behavior catalog can leave an unusable prompt.
    for tag in _background_order(rendered):
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
