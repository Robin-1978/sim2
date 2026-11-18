"""Compose the prompt sections for one role and fit the result into its size limit."""

from __future__ import annotations

from typing import Any

from .compaction import context_metrics, fit_to_limit, strip_world_scaffolding
from .sections_character import (
    activity_lines,
    character_lines,
    directive_lines,
    executable_behavior_lines,
    knowledge_lines,
    reflection_lines,
    registered_action_lines,
    scene_lines,
    violation_lines,
)
from .sections_common import (
    institution_lines,
    interaction_lines,
    protocol_lines,
    session_lines,
    world_rule_lines,
    world_state_lines,
)
from .sections_god import god_lines
from .sections_story import open_question_lines, recent_history_lines, story_lines


def _render_sections(context: dict[str, Any], task: str) -> str:
    role = context.get("role", "agent_decision")
    lines = protocol_lines(context)
    lines.extend(world_state_lines(context))
    lines.extend(interaction_lines(context))
    if role in {"story", "god"}:
        lines.extend(recent_history_lines(context))
        lines.extend(open_question_lines(context))
    lines.extend(world_rule_lines(context))
    lines.extend(character_lines(context))
    lines.extend(scene_lines(context))
    lines.extend(knowledge_lines(context, role))
    lines.extend(directive_lines(context))
    lines.extend(activity_lines(context))
    lines.extend(violation_lines(context))
    if role == "agent_reflection":
        lines.extend(reflection_lines(context))
    if role == "story":
        lines.extend(story_lines(context))
    if role == "god":
        lines.extend(god_lines(context))
    lines.extend(institution_lines(context, role))
    if role == "agent_decision":
        lines.extend(executable_behavior_lines(context))
    if role == "agent_reflection":
        lines.extend(registered_action_lines(context))
    lines.extend(session_lines(context, task, role))
    return strip_world_scaffolding("\n".join(lines), role)


def render_context_with_metrics(
    context: dict[str, Any],
    task: str,
    *,
    max_chars: int | None = None,
    max_tokens: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Render the prompt and return the exact text plus its budget metrics."""

    role = context.get("role", "agent_decision")
    if max_chars is None:
        # World reviewers carry catalogs, trends and proposal contracts; the
        # old one-size character budget silently evicted their evidence.
        max_chars = 20000 if role in {"god", "story"} else 16000
    rendered = _render_sections(context, task)
    fitted = fit_to_limit(rendered, role, max_chars, max_tokens=max_tokens)
    metrics = context_metrics(
        rendered,
        fitted,
        role=role,
        max_chars=max_chars,
        max_tokens=max_tokens,
    )
    if role == "god" and isinstance(context.get("world_index"), dict) and context.get("world_index"):
        # These sections carry the current target and the machine fields a
        # structural proposal needs.  Their loss is not harmless reference
        # compaction, so make it visible in the audit record.
        required = {
            tag for tag in (
                "evolution_trigger", "season_plan", "world_growth_ledger",
                "world_behavior_catalog", "world_action_catalog",
            )
            if tag in (metrics.get("source_sections") or {})
        }
        metrics["required_sections"] = sorted(required)
        metrics["omitted_required"] = sorted(required & set(metrics.get("omitted") or []))
    return fitted, metrics


def render_context(
    context: dict[str, Any],
    task: str,
    *,
    max_chars: int | None = None,
    max_tokens: int | None = None,
) -> str:
    """Render a stable XML-like prefix followed by dynamic Agent state.

    The system policy and native tool schemas are stable.  This user message
    puts static protocol/tool explanations first, dynamic world state next,
    the current decision at the end, and appends ReAct history last.
    """

    return render_context_with_metrics(context, task, max_chars=max_chars, max_tokens=max_tokens)[0]
