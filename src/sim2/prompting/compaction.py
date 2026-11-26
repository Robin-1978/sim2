"""Fitting a rendered prompt into its size limit without losing the task or the executable choices."""

from __future__ import annotations

import re
from typing import Any

_CJK_RANGES = (
    (0x3040, 0x30FF),  # Hiragana / Katakana
    (0x3400, 0x4DBF),  # CJK Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xAC00, 0xD7AF),  # Hangul
)

# Sections that carry a decision's causal evidence and must survive compaction.
# Background prose and retrievable projections can be omitted; these cannot.
_PINNED_SECTIONS = frozenset({
    "protocol",
    "platform_rules",
    "allowed_intents_and_tools",
    "world_state",
    "decision",
    "interaction_history",
    "active_execution",
    "outcome_review",
    "executable_behaviors",
    "blocked_behaviors",
    "access_authority",
    "denial_memory",
    # God cannot submit a valid rule or world-property change without the
    # exact machine field names this section carries.
    "rule_condition_fields",
    # Structural review must see which growth cells are empty.
    "world_growth_ledger",
    "season_plan",
})

_SECTION_TAG = re.compile(r"^\s*<([a-z_]+)>\s*$", re.MULTILINE)


def estimate_tokens(text: str) -> int:
    """Cheap language-aware token estimate used when no tokenizer is configured.

    ASCII prose is roughly four characters per token.  CJK text is closer to
    one token per character.  This is deliberately conservative enough for a
    budget guard; provider-reported usage remains the source of truth.
    """

    cjk = 0
    other = 0
    for char in str(text or ""):
        codepoint = ord(char)
        if any(start <= codepoint <= end for start, end in _CJK_RANGES):
            cjk += 1
        else:
            other += 1
    return int(cjk + other / 4 + 0.999)


def section_sizes(text: str) -> dict[str, int]:
    """Approximate the character weight of each top-level or nested tag block."""

    matches = list(_SECTION_TAG.finditer(text))
    if not matches:
        return {}
    sizes: dict[str, int] = {}
    for index, match in enumerate(matches):
        tag = match.group(1)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sizes[tag] = sizes.get(tag, 0) + max(0, end - match.start())
    return sizes


def context_metrics(
    rendered: str,
    fitted: str,
    *,
    role: str,
    max_chars: int,
    max_tokens: int | None = None,
) -> dict[str, object]:
    """Describe what the renderer produced and what compaction removed."""

    before = section_sizes(rendered)
    after = section_sizes(fitted)
    omitted = sorted(tag for tag in before if tag not in after)
    static_prefix = rendered.split("<world_state>", 1)[0]
    return {
        "role": role,
        "chars": len(fitted),
        "estimated_tokens": estimate_tokens(fitted),
        "max_chars": int(max_chars),
        "max_tokens": int(max_tokens) if max_tokens is not None else None,
        "compacted": fitted != rendered,
        "sections": dict(sorted(after.items())),
        # The source inventory lets a caller distinguish a section that never
        # existed from one that compaction removed.
        "source_sections": dict(sorted(before.items())),
        "omitted": omitted,
        "static_prefix_chars": len(static_prefix),
        "dynamic_chars": max(0, len(fitted) - len(static_prefix)),
    }


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
    "research_sources", "world_trends", "world_outcomes", "knowledge_gaps",
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


def _truncate_behavior_catalog(
    rendered: str,
    behavior_start: int,
    behavior_end: int,
    decision_start: int,
    fits: Any,
) -> str | None:
    """Keep complete behavior blocks and the decision when the catalog is too large.

    Ordinary compaction preserves the whole catalog because hiding an option
    changes what a character can choose.  A catalog that cannot fit at all is
    different: the previous fallback cut a block in half and could drop the
    ``<decision>`` section, producing an unusable prompt.  This guard keeps as
    many complete blocks as fit, states how many were omitted, and always keeps
    the decision.
    """

    catalog = rendered[behavior_start:behavior_end]
    blocks = re.findall(r"\n\s*<behavior>.*?\n\s*</behavior>", catalog, flags=re.DOTALL)
    more_match = re.search(r"\n\s*<more_behaviors>.*?\n\s*</more_behaviors>", catalog, flags=re.DOTALL)
    compact_lines = [
        line for line in (more_match.group(0).splitlines() if more_match else [])
        if line.strip().startswith("- ")
    ]
    if not blocks and not compact_lines:
        return None
    header_end = catalog.find("<behavior>")
    header = catalog[:header_end].rstrip() if header_end >= 0 else "  <executable_behaviors>"
    tail = rendered[decision_start:]
    closing = "  </executable_behaviors>"
    for keep_blocks in range(len(blocks), -1, -1):
        for keep_compact in range(len(compact_lines), -1, -1):
            omitted = (len(blocks) - keep_blocks) + (len(compact_lines) - keep_compact)
            more = ""
            if keep_compact:
                more = (
                    "\n    <more_behaviors>\n"
                    "      Use query_behavior_catalog for full details.\n"
                    + "\n".join(compact_lines[:keep_compact])
                    + "\n    </more_behaviors>"
                )
            note = (
                f"\n    <context_limit>{omitted} more behaviors omitted by the prompt budget; "
                "use the listed choices.</context_limit>"
            ) if omitted else ""
            candidate_catalog = header + "".join(blocks[:keep_blocks]) + more + note + "\n" + closing
            candidate = rendered[:behavior_start] + candidate_catalog + "\n" + tail
            if fits(candidate):
                return candidate
    return None


def fit_to_limit(
    rendered: str,
    role: str,
    max_chars: int,
    *,
    max_tokens: int | None = None,
    required_sections: frozenset[str] = frozenset(),
) -> str:
    """Compact a rendered prompt to its character and optional token budget."""

    def fits(text: str) -> bool:
        return (
            len(text) <= max_chars
            and (max_tokens is None or estimate_tokens(text) <= max_tokens)
            and not missing_sections(text, required_sections)
        )

    if fits(rendered):
        return rendered
    omitted: list[str] = []
    # Preserve the current task, executable choices, active execution and
    # latest tool result. Remove only retrievable background sections first;
    # cutting the middle of the behavior catalog can leave an unusable prompt.
    for tag in _background_order(rendered):
        if tag in _PINNED_SECTIONS or tag in required_sections:
            continue
        rendered, removed = _remove_block(rendered, tag)
        if removed:
            omitted.append(tag)
            note = f"\n<context_limit>Omitted {', '.join(omitted)}; use a read tool if needed.</context_limit>"
            if fits(rendered + note):
                return rendered + note
    if required_sections:
        # Under an aggregate request limit, remove whole optional blocks
        # before attempting the legacy line-based fallbacks.
        for tag in section_sizes(rendered):
            if tag in required_sections:
                continue
            candidate, removed = _remove_block(rendered, tag)
            if removed and not missing_sections(candidate, required_sections):
                rendered = candidate
                if fits(rendered):
                    return rendered
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
        while prefix_budget > 0:
            candidate = (
                rendered[:prefix_budget].rsplit("\n", 1)[0]
                + "\n<context_limit>Earlier context was compacted; use a read tool if needed.</context_limit>\n"
                + preserved + "\n" + tail
            )
            if fits(candidate):
                return candidate
            prefix_budget = int(prefix_budget * 0.85)
    if behavior_start >= 0 and behavior_end >= 0 and decision_start >= 0:
        truncated = _truncate_behavior_catalog(rendered, behavior_start, behavior_end, decision_start, fits)
        if truncated is not None:
            return truncated
    if role in {"god", "story"}:
        compacted = _compact_world_prompt(rendered, max_chars)
        if compacted is not None and fits(compacted):
            return compacted
    budget = max(0, max_chars - 100)
    while budget > 0:
        candidate = rendered[:budget].rsplit("\n", 1)[0] + "\n<context_limit>Context was compacted; use a read tool if needed.</context_limit>"
        if fits(candidate):
            return candidate
        budget = int(budget * 0.85)
    return rendered[: max(0, max_chars - 100)] + "\n<context_limit>Context was compacted; use a read tool if needed.</context_limit>"


def missing_sections(text: str, required: frozenset[str]) -> list[str]:
    return sorted(tag for tag in required if f"<{tag}>" not in text or f"</{tag}>" not in text)
