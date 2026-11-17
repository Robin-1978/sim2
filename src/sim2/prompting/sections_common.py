"""Prompt sections every role receives: protocol, world time, interaction, rules, institutions and the session."""

from __future__ import annotations

from typing import Any

from .formatting import history_line, line, plain, result_line


def protocol_lines(context: dict[str, Any]) -> list[str]:
    """Stable protocol, platform rules, and tool names; they precede the dynamic state so a provider can reuse the prefix."""

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
    return lines


def world_state_lines(context: dict[str, Any]) -> list[str]:
    """Current world time."""

    lines: list[str] = []
    world_time = context.get("world_time") or {}
    lines.extend([
        "",
        "<world_state>",
        f"  time: {world_time.get('label', world_time.get('minute', 'unknown'))}",
        f"  minute: {world_time.get('minute', 'unknown')}",
        "</world_state>",
    ])
    return lines


def interaction_lines(context: dict[str, Any]) -> list[str]:
    """The incoming interaction and the evidence that woke this turn."""

    lines: list[str] = []
    interaction = context.get("interaction") or {}
    if interaction:
        player_message = str(interaction.get("origin") or "") == "player"
        lines.extend([
            "<incoming_interaction>",
            f"  from: {line(interaction.get('from'))}",
            f"  topic: {line(interaction.get('topic'))}",
            f"  message: {line(interaction.get('message'))}",
            "  This is a direct player message; always acknowledge it in one short reply, even when the answer is that the detail is unknown." if player_message else "  Reply only if this adds information, asks a question, or needs a decision; otherwise return wait.",
            "</incoming_interaction>",
        ])

    triggering_evidence = context.get("triggering_evidence") or []
    if triggering_evidence:
        lines.append("<triggering_evidence>")
        for item in triggering_evidence[:16]:
            lines.append(
                f"  - minute {line(item.get('world_minute'))}; {line(item.get('event_type'))}; "
                f"actor={line(item.get('actor'))}; {line(item.get('summary'))}"
            )
        lines.append("</triggering_evidence>")
    return lines


def world_rule_lines(context: dict[str, Any]) -> list[str]:
    """Active world rules."""

    lines: list[str] = []
    world_rules = context.get("active_world_rules") or []
    if world_rules:
        lines.append("<world_rules>")
        lines.append("  A rule applies only when its condition is currently active.")
        for rule in world_rules[:8]:
            description = f"; {rule.get('description')}" if rule.get("description") else ""
            applies = f"; applies to {plain(rule.get('applies_to'))}" if rule.get("applies_to") else ""
            lines.append(
                f"  - {rule.get('name', 'world policy')} [currently {rule.get('current_status', 'unknown')}]: when {rule.get('condition', 'configured condition')}, "
                f"the platform applies {rule.get('effect', 'configured effect')}{applies}{description}"
            )
        lines.append("</world_rules>")
    return lines


def institution_lines(context: dict[str, Any], role: str) -> list[str]:
    """Pending and active institutions and their verified ledgers."""

    lines: list[str] = []
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
                lines.append(f"    - {ledger.get('name')}: {plain(ledger.get('entries') or [])}")
        lines.append("  A proposal is only a recorded suggestion. It changes the world only after an authorized activation.")
        lines.append("</social_institutions>")
    return lines


def session_lines(context: dict[str, Any], task: str, role: str) -> list[str]:
    """ReAct history, platform correction, and the current task."""

    lines: list[str] = []
    lines.extend(["  <interaction_history>"])
    history = context.get("interaction_history") or []
    if history:
        for item in history[-8:]:
            lines.append(f"    - {history_line(item)}")
    else:
        lines.append("    - none in this session")
    last = context.get("last_platform_result")
    if last:
        lines.append(f"    latest_platform_result: {result_line(last)}")
    lines.append("</interaction_history>")
    if isinstance(last, dict) and str(last.get("status") or "") == "rejected":
        lines.append(
            "<platform_correction>"
            "  The previous request was rejected. Read the failed_check, message and errors above, correct the request, and do not repeat the same arguments."
            "</platform_correction>"
        )
    lines.extend(["<decision>", f"  {task}"])
    if role == "agent_decision" and context.get("decision_guidance"):
        lines.append(f"  Decision guidance: {context['decision_guidance']}")
    lines.append("</decision>")
    return lines
