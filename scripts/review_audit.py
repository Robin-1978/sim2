"""Review a completed real-agent audit for context and causal invariants.

This is intentionally separate from the live evaluator.  ``audit_real_world``
records every prompt and transcript; this command checks the complete artifact
after the world stops, including prompt ordering, ReAct feedback continuity,
role-scoped quality, and persisted behavior/proposal causality.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def _issue(kind: str, detail: str, session: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": kind, "detail": detail}
    if session is not None:
        result["session"] = session
    return result


def _prompt_checks(session: dict[str, Any], index: int) -> list[dict[str, Any]]:
    prompts = list(session.get("prompt_history") or [])
    if not prompts:
        return [_issue("missing_prompt_history", "session has no rendered prompt", index)]
    issues: list[dict[str, Any]] = []
    prefixes: list[str] = []
    for prompt_index, prompt in enumerate(prompts):
        if not isinstance(prompt, str):
            issues.append(_issue("invalid_prompt", f"prompt {prompt_index} is not text", index))
            continue
        required = ("<protocol>", "<world_state>", "<interaction_history>", "<decision>")
        missing = [tag for tag in required if tag not in prompt]
        if missing:
            issues.append(_issue("prompt_missing_section", f"prompt {prompt_index}: {missing}", index))
            continue
        positions = {tag: prompt.find(tag) for tag in required}
        if not positions["<protocol>"] < positions["<world_state>"] < positions["<interaction_history>"] < positions["<decision>"]:
            issues.append(_issue("prompt_section_order", f"prompt {prompt_index}: {positions}", index))
        prefixes.append(prompt[: positions["<world_state>"]])
        if len(prompt) > 16000:
            issues.append(_issue("prompt_over_budget", f"prompt {prompt_index}: {len(prompt)} chars", index))
        if re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", prompt, re.IGNORECASE):
            issues.append(_issue("internal_uuid_in_prompt", f"prompt {prompt_index} exposes a UUID", index))
    if prefixes and any(prefix != prefixes[0] for prefix in prefixes[1:]):
        issues.append(_issue("unstable_static_prefix", "protocol/tool prefix changed inside one ReAct session", index))
    llm_steps = [step for step in session.get("transcript", []) if step.get("step") == "llm"]
    if len(prompts) != len(llm_steps):
        issues.append(_issue("prompt_response_count_mismatch", f"prompts={len(prompts)} llm_steps={len(llm_steps)}", index))
    return issues


def _feedback_checks(session: dict[str, Any], index: int) -> list[dict[str, Any]]:
    prompts = list(session.get("prompt_history") or [])
    transcript = list(session.get("transcript") or [])
    issues: list[dict[str, Any]] = []
    llm_position = -1
    for step in transcript:
        if step.get("step") == "llm":
            llm_position += 1
            continue
        if step.get("step") not in {"tool", "intent"}:
            continue
        result = step.get("result") if isinstance(step.get("result"), dict) else {}
        if result.get("status") != "rejected" or llm_position + 1 >= len(prompts):
            continue
        next_prompt = prompts[llm_position + 1]
        message = str(result.get("message") or result.get("failed_check") or "").strip()
        if message and message[:18] not in next_prompt and str(result.get("failed_check") or "") not in next_prompt:
            issues.append(_issue("platform_feedback_not_rendered", f"rejection after LLM step {llm_position} is absent from next prompt", index))
    return issues


def review(data: dict[str, Any]) -> dict[str, Any]:
    sessions = list(data.get("sessions") or [])
    issues: list[dict[str, Any]] = []
    role_stats: dict[str, dict[str, int]] = {}
    for index, session in enumerate(sessions):
        role = str(session.get("role") or "unknown")
        stats = role_stats.setdefault(role, {"sessions": 0, "quality_failed": 0, "prompt_issues": 0, "feedback_issues": 0})
        stats["sessions"] += 1
        quality = session.get("quality") or {}
        if not quality.get("passed") and not quality.get("pending") and session.get("result", {}).get("status") != "skipped":
            stats["quality_failed"] += 1
        prompt_issues = _prompt_checks(session, index) if session.get("result", {}).get("status") != "skipped" else []
        feedback_issues = _feedback_checks(session, index)
        stats["prompt_issues"] += len(prompt_issues)
        stats["feedback_issues"] += len(feedback_issues)
        issues.extend(prompt_issues)
        issues.extend(feedback_issues)
    world = data.get("world") or {}
    causality = world.get("causality") or {}
    if causality.get("orphan_behavior_intents"):
        issues.append(_issue("orphan_behavior_intents", str(causality["orphan_behavior_intents"])))
    if causality.get("god_direct_property_changes"):
        issues.append(_issue("god_direct_property_change", str(causality["god_direct_property_changes"])))
    for change in causality.get("world_rule_changes") or []:
        if change.get("status") not in {"pending", "verified"}:
            issues.append(_issue("unknown_rule_followup_status", str(change)))
    quality_counts = world.get("quality_counts") or {}
    return {
        "passed": not issues and int(quality_counts.get("failed", 0) or 0) == 0,
        "sessions": len(sessions),
        "quality_counts": quality_counts,
        "role_stats": role_stats,
        "rule_count": len(causality.get("world_rule_changes") or []),
        "rule_followup_status": {status: sum(1 for item in causality.get("world_rule_changes") or [] if item.get("status") == status) for status in ("verified", "pending")},
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audit", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = review(json.loads(args.audit.read_text(encoding="utf-8")))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
