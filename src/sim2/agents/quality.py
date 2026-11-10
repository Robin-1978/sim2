from __future__ import annotations

import re
from typing import Any

from .engine import AgentTurn


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _terms(value: Any) -> set[str]:
    text = str(value or "").casefold()
    terms = set(re.findall(r"[a-z0-9]+", text.replace("_", " ")))
    cjk = "".join(re.findall(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]", text))
    terms.update(cjk[index:index + 2] for index in range(max(0, len(cjk) - 1)))
    return terms


def _outcome_evidence(item: dict[str, Any]) -> tuple[str, str]:
    """Return searchable evidence and a normalized kind for one outcome."""

    outcome = item.get("outcome") if isinstance(item.get("outcome"), dict) else {}
    evidence = " ".join([
        str(outcome.get("result", "")),
        str((outcome.get("expected") or {}).get("summary", "")),
        str(outcome.get("delta", {})),
        str(outcome.get("actual", {})),
        str(item.get("event_type", "")),
        str(item.get("behavior_id", "")),
        str(item.get("behavior_choice", "")),
    ])
    kind = " ".join(str(value).casefold() for value in (
        item.get("event_type"), item.get("behavior_id"), outcome.get("result"), item.get("behavior_choice")
    ))
    return evidence, kind


def _experience_matches_outcome(summary: str, item: dict[str, Any]) -> bool:
    """Require a lesson to cite one visible outcome in the recent window.

    Reflection may legitimately consolidate a result from the second or third
    recent entry instead of repeating the newest event.  Matching is still
    evidence-based: either terms overlap or the outcome's configured behavior
    family has a matching plain-language signal in the lesson.
    """

    evidence, event_kind = _outcome_evidence(item)
    summary_terms = _terms(summary)
    if summary_terms & _terms(evidence):
        return True
    semantic_signals = {
        "dialogue": ("dialogue", "conversation", "communicat", "exchange", "talk", "对话", "交谈", "交流", "沟通", "谈", "消息"),
        "research": ("research", "study", "fact", "研究", "调查", "事实"),
        "explor": ("explor", "survey", "route", "map", "探索", "勘察", "地图", "路线"),
        "rest": ("rest", "recover", "energy", "休息", "恢复", "体力", "能量"),
        "food": ("food", "ration", "satiety", "meal", "吃", "食物", "口粮", "饱食", "饱腹", "粮仓", "谷物", "配给"),
        "patrol": ("patrol", "watch", "security", "巡逻", "警戒", "安全"),
        "inspect": ("inspect", "check", "stock", "report", "检查", "巡查", "盘点", "库存", "报告"),
        "craft": ("craft", "forge", "recipe", "制作", "锻造", "晨钟", "工具"),
        "negotiate": ("negotiat", "agreement", "contract", "协商", "谈判", "协议"),
        "gather": ("gather", "collect", "resource", "采集", "收集", "资源", "谷物"),
        "travel": ("travel", "move", "arrive", "route", "移动", "到达", "路线"),
    }
    return any(
        key in event_kind and any(term in summary.casefold() for term in terms)
        for key, terms in semantic_signals.items()
    )


def _claims_enforcement(text: str) -> bool:
    """Detect enforcement claims without treating a place name as a gate."""

    text = str(text or "").casefold()
    terms = ("gated", "must", "priorit", "defer", "block", "restrict", "required", "only when", "until", "必须", "优先", "阻止", "限制", "直到")
    for term in terms:
        escaped = re.escape(term).replace(r"\ ", r"\s+")
        if term in {"priorit", "defer", "block", "restrict", "required"}:
            pattern = rf"(?<![a-z0-9_]){escaped}[a-z0-9]*(?![a-z0-9_])"
        else:
            pattern = rf"(?<![a-z0-9_]){escaped}(?![a-z0-9_])"
        if re.search(pattern, text):
            return True
    return False


def _final_submission(turn: AgentTurn) -> tuple[str, dict[str, Any]]:
    for step in reversed(turn.transcript):
        if step.get("step") == "tool" and step.get("result", {}).get("status") in {"accepted", "wait"}:
            return str(step.get("name", "")), dict(step.get("arguments") or {})
        if step.get("step") == "intent" and step.get("result", {}).get("status") in {"accepted", "wait"}:
            response = step.get("response") if isinstance(step.get("response"), dict) else {}
            if response.get("change_type"):
                inferred = "propose_world_change"
            elif response.get("entity_type") and response.get("name"):
                inferred = "propose_entity"
            elif response.get("beat") or response.get("summary") and response.get("audience"):
                inferred = "propose_story_beat"
            elif isinstance(response.get("intent"), str):
                inferred = str(response["intent"])
            else:
                inferred = "record_experience" if response.get("summary") and not response.get("intent_type") else response.get("intent_type", "intent")
            return str(inferred), response
    return "", {}


def evaluate_turn(turn: AgentTurn, view: dict[str, Any]) -> dict[str, Any]:
    """Score structural and semantic guardrails without trusting acceptance alone."""

    checks: list[dict[str, Any]] = []
    checks.append(_check("session_finished", turn.result.get("status") in {"accepted", "wait", "skipped"}, f"status={turn.result.get('status')}"))
    checks.append(_check("bounded_session", 0 <= turn.iterations <= 6, f"iterations={turn.iterations}"))
    rejected = [step for step in turn.transcript if step.get("result", {}).get("status") == "rejected"]
    checks.append(_check("platform_feedback_visible", not rejected or any(step.get("step") in {"tool", "intent"} for step in turn.transcript), f"rejections={len(rejected)}"))
    name, payload = _final_submission(turn)

    if turn.role == "agent_decision":
        behaviors = view.get("executable_behaviors") or view.get("available_behaviors", [])
        refs = {item.get("ref") for item in behaviors}
        choice = payload.get("choice")
        wait_choice = turn.result.get("status") == "wait" or name == "wait" or payload.get("intent") == "wait"
        platform_guarded = turn.result.get("status") == "skipped"
        interaction = name in {"send_message", "create_trade_offer", "accept_trade_offer", "counter_trade_offer", "reject_trade_offer"} or str(payload.get("intent_type") or "") in {"send_message", "create_trade_offer", "accept_trade_offer", "counter_trade_offer", "reject_trade_offer"}
        selected_by_choice = next((item for item in behaviors if str(item.get("choice") or item.get("name", "")).casefold() == str(choice or "").casefold()), None)
        behavior_ref = payload.get("behavior_ref") or turn.result.get("behavior_ref") or (selected_by_choice or {}).get("ref")
        if behavior_ref and not str(behavior_ref).startswith("behavior:"):
            behavior_ref = f"behavior:{behavior_ref}"
        checks.append(_check("behavior_is_offered", wait_choice or platform_guarded or interaction or behavior_ref in refs, f"selected={behavior_ref}; offered={sorted(refs)}"))
        reason = str(payload.get("reason") or "")
        checks.append(_check("decision_reason_advisory", True, f"reason_length={len(reason.strip())}; detailed rationale is optional and reviewed by Reflection"))
        parameters = payload.get("parameters") or {}
        checks.append(_check("decision_does_not_fill_execution_costs", not any(key in parameters for key in {"duration", "energy", "exposure", "cost", "effects"}), f"parameter_keys={sorted(parameters)}"))
        selected = next((item for item in behaviors if item.get("ref") == behavior_ref), selected_by_choice or {})
        drive_ids = {item.get("id") for item in (view.get("drives") or [])[:3]}
        goal_tags = set(view.get("goal_tags") or [])
        alignment = (goal_tags & set(selected.get("goal_tags") or [])) | (drive_ids & set(selected.get("drive_tags") or []))
        tokens = lambda value: set(re.findall(r"[a-z0-9]+", str(value).lower().replace("_", " ")))
        reason_terms = _terms(reason)
        evidence_terms = set().union(
            _terms(selected.get("name", "")), _terms(selected.get("summary", "")),
            _terms(selected.get("purpose", "")), _terms(selected.get("goal_tags", [])),
            _terms(view.get("identity", {}).get("capabilities", {})), _terms(view.get("goal_tags", [])),
            _terms(view.get("self", {}).get("goal", "")), _terms(view.get("observation", {}).get("facts", [])),
            _terms(view.get("known_facts", [])), _terms(view.get("memories", [])),
        )
        # Decision tools intentionally allow a short choice-only response.
        # A missing rationale is acceptable when the selected BT is grounded
        # in visible goals/drives; a supplied rationale still must overlap
        # visible evidence.
        reason_grounded = wait_choice or platform_guarded or interaction or bool(reason_terms & evidence_terms) or bool(alignment)
        semantic_ok = wait_choice or platform_guarded or interaction or bool(alignment) or bool(reason_terms & evidence_terms) or len(behaviors) <= 1
        checks.append(_check("decision_semantic_signal", semantic_ok, f"alignment={sorted(alignment)}; reason_overlap={bool(reason_terms & evidence_terms)}; candidates={len(behaviors)}"))
        reason_detail = "reason overlaps visible evidence" if reason_terms & evidence_terms else ("choice aligned with visible goals/drives; explanation language is advisory" if reason.strip() and alignment else ("choice aligned with visible goals/drives; no explanation emitted" if not reason.strip() and alignment else "no overlap with selected behavior, goals, drives or capabilities"))
        checks.append(_check("decision_reason_grounding", reason_grounded, reason_detail))
    elif turn.role == "agent_reflection":
        if name == "wait" or payload.get("intent_type") == "wait" or turn.result.get("status") == "wait":
            checks.append(_check("reflection_can_decline_low_value_memory", True, "no new durable lesson was submitted"))
        elif name == "record_experience" or payload.get("intent_type") == "record_experience":
            summary = str(payload.get("summary") or "")
            checks.append(_check("reflection_experience_is_specific_and_bounded", (len(summary.split()) >= 4 or len(summary) >= 16) and len(summary) <= 500, f"summary_length={len(summary)}"))
            outcomes = view.get("recent_outcomes") or []
            if outcomes:
                matched = [
                    item for item in outcomes
                    if isinstance(item, dict) and _experience_matches_outcome(summary, item)
                ]
                matched_labels = [str(item.get("behavior_id") or item.get("event_type") or "outcome") for item in matched[:3]]
                checks.append(_check(
                    "reflection_experience_matches_outcome",
                    bool(matched),
                    f"summary matches recent outcome(s): {matched_labels}" if matched else "summary does not cite any recent observed outcome",
                ))
        elif name == "update_capability" or payload.get("intent_type") == "update_capability":
            evidence = str(payload.get("evidence") or "")
            delta = payload.get("delta")
            checks.append(_check("reflection_capability_evidence_is_specific", len(evidence.strip()) >= 12, f"evidence_length={len(evidence)}"))
            checks.append(_check("reflection_capability_delta_is_bounded", isinstance(delta, (int, float)) and 0 < delta <= 0.2, f"delta={delta}"))
            checks.append(_check("reflection_capability_update_applied", turn.result.get("status") == "accepted" and turn.result.get("capability") is not None, f"status={turn.result.get('status')}; capability={turn.result.get('capability')}"))
        elif name == "update_goal" or payload.get("intent_type") == "update_goal":
            statement = str(payload.get("statement") or "")
            checks.append(_check("reflection_goal_is_specific", len(statement) >= 12, f"statement_length={len(statement)}"))
        elif name == "update_belief" or payload.get("intent_type") == "update_belief":
            statement = str(payload.get("statement") or payload.get("belief") or "")
            confidence = payload.get("confidence")
            checks.append(_check("reflection_belief_is_specific", len(statement.strip()) >= 12, f"statement_length={len(statement)}"))
            checks.append(_check("reflection_belief_confidence_is_valid", isinstance(confidence, (int, float)) and 0 <= confidence <= 1, f"confidence={confidence}"))
        elif name == "propose_behavior" or payload.get("intent_type") == "propose_behavior":
            actions = payload.get("action_ids") or payload.get("actions")
            checks.append(_check("reflection_behavior_proposal_has_actions", bool(actions), f"actions={actions}"))
        else:
            checks.append(_check("reflection_intent_is_known", name in {"record_experience", "update_belief", "update_goal", "propose_behavior"}, f"submission={name}"))
    elif turn.role == "god":
        if name == "wait" or payload.get("intent_type") == "wait" or turn.result.get("status") == "wait":
            checks.append(_check("god_can_wait_without_supported_proposal", True, "no evidence-backed world proposal was needed for this signal"))
        elif name == "record_world_progress" or payload.get("intent_type") == "record_world_progress":
            evidence = str(payload.get("evidence") or "")
            checks.append(_check("god_progress_evidence_is_specific", len(evidence.strip()) >= 12, f"evidence_length={len(evidence)}"))
            checks.append(_check("god_progress_update_applied", turn.result.get("status") == "accepted" and bool(turn.result.get("progress")), f"status={turn.result.get('status')}; progress={bool(turn.result.get('progress'))}"))
        elif name == "propose_entity":
            checks.append(_check("god_entity_has_type_and_name", bool(payload.get("entity_type")) and len(str(payload.get("name", "")).strip()) >= 2, f"type={payload.get('entity_type')}; name={payload.get('name')}"))
            checks.append(_check("god_entity_platform_created", bool(turn.result.get("entity_id")), f"entity_id={turn.result.get('entity_id')}"))
        elif name == "propose_world_change":
            checks.append(_check("god_world_change_has_summary", len(str(payload.get("summary", "")).strip()) >= 8, f"summary_length={len(str(payload.get('summary', '')))}"))
            checks.append(_check("god_world_change_applied", turn.result.get("status") == "accepted" and bool(turn.result.get("applied")), f"applied={turn.result.get('applied')}; effects={turn.result.get('applied_effects')}"))
            change_type = str(payload.get("change_type", ""))
            effects = " ".join(str(item).lower() for item in (payload.get("effects") or []))
            direct_change_terms = ("increase", "decrease", "restore", "activate", "stabilize", "reduce", "raise", "lower", "set ", "change", "remain", " can ")
            if change_type == "event" and not payload.get("property"):
                direct_claim = any(term in effects for term in direct_change_terms)
                checks.append(_check("god_event_does_not_claim_unapplied_state_change", not direct_claim, "event is narrative only" if not direct_claim else "direct state mutation requires a structured property/rule/relationship/faction proposal"))
                checks.append(_check("god_event_is_valid_narrative_signal", not direct_claim, "narrative event can be handled by a configured world rule" if not direct_claim else "event effects claim a direct state change"))
            if change_type == "rule":
                checks.append(_check("god_rule_has_behavior_followup", bool(turn.result.get("followup_verified")), "rule is stored; a later behavior outcome must verify its effect" if not turn.result.get("followup_verified") else "a later behavior outcome verified the rule"))
                rule = payload.get("rule") if isinstance(payload.get("rule"), dict) else {}
                condition = str(rule.get("condition") or rule.get("when") or "")
                effect = rule.get("effect")
                checks.append(_check("god_rule_is_declarative", len(condition.strip()) >= 5 and bool(effect), "condition and effect are explicit" if len(condition.strip()) >= 5 and bool(effect) else "rule needs an explicit condition and effect"))
                followup = rule.get("behavior_names") or rule.get("behavior_ids") or rule.get("action_names") or []
                checks.append(_check("god_rule_names_followup_behavior", bool(followup), "a later configured behavior is named" if followup else "rule has no configured behavior or action followup"))
                expected_text = " ".join(str(item).casefold() for item in (payload.get("expected_effects") or []))
                effect_text = str(rule.get("effect") or "").casefold()
                claims_denial = any(term in expected_text for term in ("deny", "refus", "restrict", "禁止", "拒绝"))
                effect_is_denial = effect_text in {"deny", "deny_access", "deny_action", "forbid"} or (isinstance(rule.get("effect"), dict) and str(rule["effect"].get("type", "")).casefold() in {"deny", "deny_access", "deny_action", "forbid"})
                checks.append(_check("god_rule_expected_effect_matches", not claims_denial or effect_is_denial, "expected denial is represented by a denial effect" if not claims_denial or effect_is_denial else "expected denial is not represented by the submitted rule effect"))
                claims_gate = _claims_enforcement(expected_text + " " + " ".join(str(item) for item in (payload.get("effects") or [])))
                effect_is_gate = effect_is_denial or (isinstance(rule.get("effect"), dict) and str(rule["effect"].get("type", "")).casefold() in {"behavior_constraint", "policy"})
                checks.append(_check("god_rule_claims_match_enforcement", not claims_gate or effect_is_gate, "the rule effect can enforce the stated gate" if not claims_gate or effect_is_gate else "the proposal claims priority or gating but submits only a signal"))
                rule_condition_text = str(rule.get("condition") or "").casefold()
                declared_paths: set[str] = set()
                for item in payload.get("conditions") or []:
                    text = str(item or "").casefold()
                    text = re.sub(r"world\[(?:\"([^\"]+)\"|'([^']+)')\]", lambda match: "world." + (match.group(1) or match.group(2)).replace(" ", "_"), text)
                    declared_paths.update(re.findall(r"(?<![a-z0-9_])((?:world|resource|actor|target|context|event\.recent)(?:\.[a-z0-9_]+)+)", text))
                normalized_rule = re.sub(r"world\[(?:\"([^\"]+)\"|'([^']+)')\]", lambda match: "world." + (match.group(1) or match.group(2)).replace(" ", "_"), rule_condition_text)
                normalized_rule = normalized_rule.replace(" ", "_")
                missing_paths = [path for path in sorted(declared_paths) if path not in normalized_rule]
                checks.append(_check("god_rule_conditions_match", not missing_paths, "declared machine fields are represented in the rule condition" if not missing_paths else f"declared condition fields are absent from rule condition: {missing_paths}"))
        else:
            checks.append(_check("god_submission_is_known", name in {"propose_entity", "propose_world_change"}, f"submission={name}"))
    elif turn.role == "story":
        if name == "record_world_progress" or payload.get("intent_type") == "record_world_progress":
            evidence = str(payload.get("evidence") or "")
            checks.append(_check("story_progress_evidence_is_specific", len(evidence.strip()) >= 12, f"evidence_length={len(evidence)}"))
            checks.append(_check("story_progress_update_applied", turn.result.get("status") == "accepted" and bool(turn.result.get("progress")), f"status={turn.result.get('status')}; progress={bool(turn.result.get('progress'))}"))
        elif name == "wait" or payload.get("intent_type") == "wait" or turn.result.get("status") == "wait":
            checks.append(_check("story_can_wait_without_unlocked_beat", True, "no configured continuation was selected"))
        elif name == "propose_story_beat" or payload.get("intent_type") == "propose_story_beat":
            summary = str(payload.get("summary") or "")
            configured = bool(payload.get("beat"))
            checks.append(_check("story_choice_or_emergent_summary", configured or len(summary) >= 8, f"configured={configured}; summary_length={len(summary)}"))
            checks.append(_check("story_platform_applied", turn.result.get("status") == "accepted" and bool(turn.result.get("activated")), f"activated={turn.result.get('activated')}"))
        else:
            checks.append(_check("story_submission_is_known", False, f"submission={name}"))
    passed = all(item["passed"] for item in checks)
    if turn.role == "agent_decision":
        space = len(behaviors)
        if turn.result.get("status") == "skipped":
            grade = "platform_guarded"
        elif not passed:
            grade = "invalid_protocol"
        elif interaction:
            grade = "grounded_interaction"
        elif wait_choice:
            grade = "grounded_wait"
        elif alignment:
            grade = "comparative_grounded_choice" if space >= 2 else "grounded_forced_choice"
        else:
            grade = "accepted_needs_reflection"
    else:
        if passed:
            grade = "grounded"
        elif turn.role == "god" and name == "propose_world_change" and turn.result.get("status") == "accepted":
            grade = "accepted_needs_world_followup"
        else:
            grade = "ungrounded_or_invalid"
    return {"role": turn.role, "iterations": turn.iterations, "checks": checks, "grade": grade, "passed": passed}
