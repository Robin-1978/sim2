from __future__ import annotations

import re
from typing import Any

from .engine import AgentTurn


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _final_submission(turn: AgentTurn) -> tuple[str, dict[str, Any]]:
    for step in reversed(turn.transcript):
        if step.get("step") == "tool" and step.get("result", {}).get("status") in {"accepted", "wait"}:
            return str(step.get("name", "")), dict(step.get("arguments") or {})
        if step.get("step") == "intent" and step.get("result", {}).get("status") in {"accepted", "wait"}:
            response = step.get("response") if isinstance(step.get("response"), dict) else {}
            return str(response.get("intent_type", "intent")), response
    return "", {}


def evaluate_turn(turn: AgentTurn, view: dict[str, Any]) -> dict[str, Any]:
    """Score structural and semantic guardrails without trusting acceptance alone."""

    checks: list[dict[str, Any]] = []
    checks.append(_check("session_finished", turn.result.get("status") in {"accepted", "wait"}, f"status={turn.result.get('status')}"))
    checks.append(_check("bounded_session", 0 <= turn.iterations <= 6, f"iterations={turn.iterations}"))
    rejected = [step for step in turn.transcript if step.get("result", {}).get("status") == "rejected"]
    checks.append(_check("platform_feedback_visible", not rejected or any(step.get("step") in {"tool", "intent"} for step in turn.transcript), f"rejections={len(rejected)}"))
    name, payload = _final_submission(turn)

    if turn.role == "agent_decision":
        behaviors = view.get("executable_behaviors") or view.get("available_behaviors", [])
        refs = {item.get("ref") for item in behaviors}
        behavior_ref = payload.get("behavior_ref") or turn.result.get("behavior_ref")
        if behavior_ref and not str(behavior_ref).startswith("behavior:"):
            behavior_ref = f"behavior:{behavior_ref}"
        checks.append(_check("behavior_is_offered", behavior_ref in refs, f"selected={behavior_ref}; offered={sorted(refs)}"))
        reason = str(payload.get("reason") or "")
        checks.append(_check("decision_reason_advisory", True, f"reason_length={len(reason.strip())}; detailed rationale is optional and reviewed by Reflection"))
        parameters = payload.get("parameters") or {}
        checks.append(_check("decision_does_not_fill_execution_costs", not any(key in parameters for key in {"duration", "fatigue", "exposure", "cost", "effects"}), f"parameter_keys={sorted(parameters)}"))
        selected = next((item for item in behaviors if item.get("ref") == behavior_ref), {})
        drive_ids = {item.get("id") for item in (view.get("drives") or [])[:3]}
        goal_tags = set(view.get("goal_tags") or [])
        alignment = (goal_tags & set(selected.get("goal_tags") or [])) | (drive_ids & set(selected.get("drive_tags") or []))
        checks.append(_check("decision_semantic_signal", True, f"alignment={sorted(alignment)}; semantic outcome is reviewed by Reflection Agent"))
        tokens = lambda value: set(re.findall(r"[a-z0-9]+", str(value).lower().replace("_", " ")))
        reason_terms = tokens(reason)
        evidence_terms = tokens(selected.get("name", "")) | tokens(selected.get("goal_tags", [])) | tokens(view.get("identity", {}).get("capabilities", {}))
        checks.append(_check("decision_reason_grounding_advisory", True, "reason overlaps visible evidence" if reason_terms & evidence_terms else "no evidence overlap; Reflection review required"))
    elif turn.role == "agent_reflection":
        if name == "record_experience" or payload.get("intent_type") == "record_experience":
            summary = str(payload.get("summary") or "")
            checks.append(_check("reflection_experience_is_specific_and_bounded", (len(summary.split()) >= 4 or len(summary) >= 16) and len(summary) <= 500, f"summary_length={len(summary)}"))
        elif name == "update_goal" or payload.get("intent_type") == "update_goal":
            statement = str(payload.get("statement") or "")
            checks.append(_check("reflection_goal_is_specific", len(statement) >= 12, f"statement_length={len(statement)}"))
        elif name == "propose_behavior" or payload.get("intent_type") == "propose_behavior":
            checks.append(_check("reflection_behavior_proposal_has_actions", bool(payload.get("action_ids")), f"action_ids={payload.get('action_ids')}"))
        else:
            checks.append(_check("reflection_intent_is_known", name in {"record_experience", "update_belief", "update_goal", "propose_behavior"}, f"submission={name}"))
    elif turn.role == "god":
        if name == "propose_entity":
            checks.append(_check("god_entity_has_type_and_name", bool(payload.get("entity_type")) and len(str(payload.get("name", "")).strip()) >= 2, f"type={payload.get('entity_type')}; name={payload.get('name')}"))
            checks.append(_check("god_entity_platform_created", bool(turn.result.get("entity_id")), f"entity_id={turn.result.get('entity_id')}"))
        elif name == "propose_world_change":
            checks.append(_check("god_world_change_has_summary", len(str(payload.get("summary", "")).strip()) >= 8, f"summary_length={len(str(payload.get('summary', '')))}"))
        else:
            checks.append(_check("god_submission_is_known", name in {"propose_entity", "propose_world_change"}, f"submission={name}"))
    passed = all(item["passed"] for item in checks)
    if turn.role == "agent_decision":
        space = len(behaviors)
        if not passed:
            grade = "invalid_protocol"
        elif alignment:
            grade = "comparative_grounded_choice" if space >= 2 else "grounded_forced_choice"
        else:
            grade = "accepted_needs_reflection"
    else:
        grade = "grounded" if passed else "ungrounded_or_invalid"
    return {"role": turn.role, "iterations": turn.iterations, "checks": checks, "grade": grade, "passed": passed}
