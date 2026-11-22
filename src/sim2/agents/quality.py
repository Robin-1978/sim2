from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .engine import AgentTurn

_GENERIC_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "in", "is", "it", "of", "on", "or", "that", "the",
    "this", "to", "was", "were", "with",
})


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
    expected = outcome.get("expected") if isinstance(outcome.get("expected"), dict) else {}
    labels = expected.get("goal_tags") or expected.get("drive_tags") or []
    if isinstance(labels, str):
        labels = [labels]
    evidence = " ".join([
        str(outcome.get("result", "")),
        str(expected.get("summary", "")),
        " ".join(str(value) for value in labels),
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


def _related_terms(left: set[str], right: set[str]) -> bool:
    """Match exact or inflection-related terms without a world vocabulary.

    The quality layer must not know that one world has food, patrols or
    satiety.  Outcome projections already contain the configured behavior
    label, result, summary and goal tags.  A small prefix relation handles
    ordinary inflections (``inspect``/``inspection``) while keeping the check
    bounded and deterministic.
    """

    left = {term for term in left if term not in _GENERIC_STOP_WORDS}
    right = {term for term in right if term not in _GENERIC_STOP_WORDS}
    if left & right:
        return True
    for first in left:
        if len(first) < 4:
            continue
        for second in right:
            if len(second) < 4:
                continue
            if first.startswith(second) or second.startswith(first):
                return True
    return False


def _experience_matches_outcome(summary: str, item: dict[str, Any]) -> bool:
    """Require a lesson to cite one visible outcome in the recent window.

    Reflection may legitimately consolidate a result from the second or third
    recent entry instead of repeating the newest event.  Matching is still
    evidence-based: either terms overlap or the outcome's configured behavior
    family has a matching plain-language signal in the lesson.
    """

    evidence, event_kind = _outcome_evidence(item)
    summary_terms = _terms(summary)
    if _related_terms(summary_terms, _terms(evidence)):
        return True
    # If direct evidence did not overlap, compare the configured behavior
    # family with the event family.  This keeps the fallback generic: both
    # sides come from the persisted outcome, never from a world vocabulary.
    return _related_terms(_terms(event_kind), summary_terms)


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
            elif response.get("definition_type") and response.get("definition") and response.get("name"):
                inferred = "propose_definition"
            elif response.get("beat") or response.get("summary") and response.get("audience"):
                inferred = "propose_story_beat"
            elif isinstance(response.get("intent"), str):
                inferred = str(response["intent"])
            else:
                inferred = "record_experience" if response.get("summary") and not response.get("intent_type") else response.get("intent_type", "intent")
            return str(inferred), response
    return "", {}


def _context_evidence(view: dict[str, Any]) -> set[str]:
    """Collect visible evidence terms for proposal grounding checks."""

    values: list[Any] = [
        view.get("known_facts"), view.get("observation", {}).get("facts"),
        view.get("recent_actions"), view.get("recent_world_changes"),
        view.get("recent_agent_outcomes"), view.get("world_trends"),
        view.get("civilization_progress"), view.get("story_beats"),
        view.get("audience_candidates"), view.get("active_world_rules"),
    ]
    return set().union(*(_terms(value) for value in values))


@dataclass
class _DecisionChoice:
    """What a decision turn selected and the Platform candidates it chose from."""

    behaviors: list[dict[str, Any]]
    refs: set[Any]
    wait_choice: bool
    platform_guarded: bool
    interaction: bool
    institution_intent: bool
    selected_by_choice: dict[str, Any] | None
    behavior_ref: Any
    alignment: set[Any] = field(default_factory=set)


def _protocol_checks(turn: AgentTurn) -> list[dict[str, Any]]:
    """Checks every role shares: the session ended, stayed bounded and showed Platform feedback."""

    checks: list[dict[str, Any]] = []
    checks.append(_check("session_finished", turn.result.get("status") in {"accepted", "wait", "skipped"}, f"status={turn.result.get('status')}"))
    checks.append(_check("bounded_session", 0 <= turn.iterations <= 6, f"iterations={turn.iterations}"))
    rejected = [step for step in turn.transcript if step.get("result", {}).get("status") == "rejected"]
    checks.append(_check("platform_feedback_visible", not rejected or any(step.get("step") in {"tool", "intent"} for step in turn.transcript), f"rejections={len(rejected)}"))
    return checks


def evaluate_turn(turn: AgentTurn, view: dict[str, Any]) -> dict[str, Any]:
    """Score structural and semantic guardrails without trusting acceptance alone."""

    checks = _protocol_checks(turn)
    if turn.result.get("failed_check") == "llm_response":
        checks.append(_check("provider_response_available", False, str(turn.result.get("message") or "provider returned no usable response")))
        return {
            "role": turn.role,
            "iterations": turn.iterations,
            "checks": checks,
            "grade": "provider_error",
            "passed": False,
        }
    name, payload = _final_submission(turn)
    decision = None
    if turn.role == "agent_decision":
        decision = _decision_checks(turn, view, name, payload, checks)
    elif turn.role == "agent_reflection":
        _reflection_checks(turn, view, name, payload, checks)
    elif turn.role == "god":
        _god_checks(turn, view, name, payload, checks)
    elif turn.role == "story":
        _story_checks(turn, view, name, payload, checks)
    passed = all(item["passed"] for item in checks)
    grade = _decision_grade(turn, decision, passed) if decision is not None else _role_grade(turn, name, passed)
    return {"role": turn.role, "iterations": turn.iterations, "checks": checks, "grade": grade, "passed": passed}


def _decision_grade(turn: AgentTurn, decision: _DecisionChoice, passed: bool) -> str:
    """Grade a decision: guarded, invalid, an interaction, a wait or how well the choice is aligned."""

    space = len(decision.behaviors)
    if turn.result.get("status") == "skipped":
        return "platform_guarded"
    if not passed:
        return "invalid_protocol"
    if decision.interaction:
        return "grounded_interaction"
    if decision.wait_choice:
        return "grounded_wait"
    if decision.alignment:
        return "comparative_grounded_choice" if space >= 2 else "grounded_forced_choice"
    return "accepted_needs_reflection"


def _role_grade(turn: AgentTurn, name: str, passed: bool) -> str:
    """Grade a reflection, God or Story turn: grounded, or accepted but still needing a follow-up."""

    if passed:
        return "grounded"
    if turn.role == "god" and name == "propose_world_change" and turn.result.get("status") == "accepted":
        return "accepted_needs_world_followup"
    return "ungrounded_or_invalid"


# --- character decision ---


def _select_decision(turn: AgentTurn, view: dict[str, Any], name: str, payload: dict[str, Any]) -> _DecisionChoice:
    """Resolve the behavior a decision turn selected out of the Platform candidates."""

    behaviors = view.get("executable_behaviors") or view.get("available_behaviors", [])
    refs = {item.get("ref") for item in behaviors}
    choice = payload.get("choice")
    wait_choice = turn.result.get("status") == "wait" or name == "wait" or payload.get("intent") == "wait"
    platform_guarded = turn.result.get("status") == "skipped"
    interaction = name in {"send_message", "create_trade_offer", "accept_trade_offer", "counter_trade_offer", "reject_trade_offer"} or str(payload.get("intent_type") or "") in {"send_message", "create_trade_offer", "accept_trade_offer", "counter_trade_offer", "reject_trade_offer"}
    institution_intent = name in {"propose_institution", "approve_institution"} or str(payload.get("intent_type") or "") in {"propose_institution", "approve_institution"}
    selected_by_choice = next((item for item in behaviors if str(item.get("choice") or item.get("name", "")).casefold() == str(choice or "").casefold()), None)
    behavior_ref = payload.get("behavior_ref") or turn.result.get("behavior_ref") or (selected_by_choice or {}).get("ref")
    if behavior_ref and not str(behavior_ref).startswith("behavior:"):
        behavior_ref = f"behavior:{behavior_ref}"
    return _DecisionChoice(behaviors, refs, wait_choice, platform_guarded, interaction, institution_intent, selected_by_choice, behavior_ref)


def _decision_checks(turn: AgentTurn, view: dict[str, Any], name: str, payload: dict[str, Any], checks: list[dict[str, Any]]) -> _DecisionChoice:
    """Checks for a character decision: an offered behavior, a bounded reason and visible grounding."""

    selection = _select_decision(turn, view, name, payload)
    checks.append(_check("behavior_is_offered", selection.wait_choice or selection.platform_guarded or selection.interaction or selection.institution_intent or selection.behavior_ref in selection.refs, f"selected={selection.behavior_ref}; offered={sorted(selection.refs)}"))
    if selection.institution_intent:
        _institution_checks(turn, view, payload, checks)
    reason = str(payload.get("reason") or "")
    checks.append(_check("decision_reason_advisory", True, f"reason_length={len(reason.strip())}; detailed rationale is optional and reviewed by Reflection"))
    parameters = payload.get("parameters") or {}
    checks.append(_check("decision_does_not_fill_execution_costs", not any(key in parameters for key in {"duration", "energy", "exposure", "cost", "effects"}), f"parameter_keys={sorted(parameters)}"))
    selection.alignment = _grounding_checks(turn, view, selection, reason, checks)
    return selection


def _institution_checks(turn: AgentTurn, view: dict[str, Any], payload: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    """A decision that proposes or approves an institution must name it and cite visible evidence."""

    institution_name = str(payload.get("name") or payload.get("institution") or "").strip()
    evidence = str(payload.get("evidence") or payload.get("reason") or "").strip()
    purpose = str(payload.get("purpose") or "").strip()
    checks.append(_check("institution_name_is_specific", len(institution_name) >= 2, f"name_length={len(institution_name)}"))
    checks.append(_check("institution_platform_applied", turn.result.get("status") in {"accepted", "wait"}, f"status={turn.result.get('status')}"))
    context_terms = _context_evidence(view)
    proposal_terms = _terms(" ".join((institution_name, purpose, evidence)))
    grounded = len(evidence) >= 12 and len(purpose) >= 12 and bool(proposal_terms & context_terms)
    checks.append(_check("institution_evidence_is_specific", grounded, f"evidence_length={len(evidence)}; purpose_length={len(purpose)}; context_overlap={bool(proposal_terms & context_terms)}"))


def _grounding_checks(turn: AgentTurn, view: dict[str, Any], selection: _DecisionChoice, reason: str, checks: list[dict[str, Any]]) -> set[Any]:
    """Check that the choice or its reason rests on visible goals, drives and evidence; return the goal/drive alignment."""

    behaviors, refs, behavior_ref, selected_by_choice = selection.behaviors, selection.refs, selection.behavior_ref, selection.selected_by_choice
    wait_choice, platform_guarded, interaction, institution_intent = selection.wait_choice, selection.platform_guarded, selection.interaction, selection.institution_intent
    selected = next((item for item in behaviors if item.get("ref") == behavior_ref), selected_by_choice or {})
    # The view orders drives by current urgency, but a valid choice may
    # serve a lower-urgency configured drive (for example inspecting a
    # pact record for a scholar).  Grounding must consider the complete
    # bounded drive list, not only the top three display entries.
    drive_ids = {item.get("id") for item in (view.get("drives") or [])}
    goal_tags = set(view.get("goal_tags") or [])
    alignment = (goal_tags & set(selected.get("goal_tags") or [])) | (drive_ids & set(selected.get("drive_tags") or []))
    reason_terms = _terms(reason)
    evidence_terms = set().union(
        _terms(selected.get("name", "")), _terms(selected.get("summary", "")),
        _terms(selected.get("purpose", "")), _terms(selected.get("goal_tags", [])),
        _terms(selected.get("supports_drive_tags", [])),
        _terms(view.get("identity", {}).get("capabilities", {})), _terms(view.get("goal_tags", [])),
        _terms(view.get("self", {}).get("goal", "")), _terms(view.get("observation", {}).get("facts", [])),
        _terms(view.get("known_facts", [])), _terms(view.get("memories", [])),
    )
    # Decision tools intentionally allow a short choice-only response.
    # A missing rationale is acceptable when the selected BT is grounded
    # in visible goals/drives; a supplied rationale still must overlap
    # visible evidence.
    # A valid catalog choice still needs visible grounding.  The catalog
    # proves that Platform can execute it; goals, drives, or a concise
    # reason prove that the Agent selected it for a coherent purpose.
    choice_only_grounded = turn.result.get("status") == "accepted" and behavior_ref in refs and not reason.strip() and bool(alignment)
    institution_grounded = institution_intent and next((item.get("passed") for item in checks if item.get("name") == "institution_evidence_is_specific"), False)
    reason_grounded = wait_choice or platform_guarded or interaction or institution_grounded or choice_only_grounded or bool(reason_terms & evidence_terms) or bool(alignment)
    semantic_ok = wait_choice or platform_guarded or interaction or institution_grounded or choice_only_grounded or bool(alignment) or bool(reason_terms & evidence_terms)
    checks.append(_check("decision_semantic_signal", semantic_ok, f"alignment={sorted(alignment)}; reason_overlap={bool(reason_terms & evidence_terms)}; candidates={len(behaviors)}"))
    reason_detail = "reason overlaps visible evidence" if reason_terms & evidence_terms else ("choice aligned with visible goals/drives; explanation language is advisory" if reason.strip() and alignment else ("choice-only response selected a currently executable Platform candidate; explanation is optional" if choice_only_grounded else ("choice aligned with visible goals/drives; no explanation emitted" if not reason.strip() and alignment else "no overlap with selected behavior, goals, drives or capabilities")))
    checks.append(_check("decision_reason_grounding", reason_grounded, reason_detail))
    return alignment


# --- reflection ---


def _reflection_checks(turn: AgentTurn, view: dict[str, Any], name: str, payload: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    """Checks for a reflection: a specific, bounded lesson or an update that Platform applied."""

    if name == "wait" or payload.get("intent_type") == "wait" or turn.result.get("status") == "wait":
        checks.append(_check("reflection_can_decline_low_value_memory", True, "no new durable lesson was submitted"))
    elif name == "record_experience" or payload.get("intent_type") == "record_experience":
        _experience_checks(view, payload, checks)
    elif name == "update_capability" or payload.get("intent_type") == "update_capability":
        _capability_update_checks(turn, payload, checks)
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


def _experience_checks(view: dict[str, Any], payload: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    """A recorded lesson is specific, bounded and cites one recent outcome."""

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


def _capability_update_checks(turn: AgentTurn, payload: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    """A capability update cites evidence, stays within the allowed delta and was applied."""

    evidence = str(payload.get("evidence") or "")
    delta = payload.get("delta")
    checks.append(_check("reflection_capability_evidence_is_specific", len(evidence.strip()) >= 12, f"evidence_length={len(evidence)}"))
    checks.append(_check("reflection_capability_delta_is_bounded", isinstance(delta, (int, float)) and 0 < delta <= 0.2, f"delta={delta}"))
    checks.append(_check("reflection_capability_update_applied", turn.result.get("status") == "accepted" and turn.result.get("capability") is not None, f"status={turn.result.get('status')}; capability={turn.result.get('capability')}"))


# --- God ---


def _god_checks(turn: AgentTurn, view: dict[str, Any], name: str, payload: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    """Checks for God: progress, institutions, entities, definitions and world changes."""

    if name == "wait" or payload.get("intent_type") == "wait" or turn.result.get("status") == "wait":
        checks.append(_check("god_can_wait_without_supported_proposal", True, "no evidence-backed world proposal was needed for this signal"))
    elif name == "record_world_progress" or payload.get("intent_type") == "record_world_progress":
        evidence = str(payload.get("evidence") or "")
        checks.append(_check("god_progress_evidence_is_specific", len(evidence.strip()) >= 12, f"evidence_length={len(evidence)}"))
        checks.append(_check("god_progress_update_applied", turn.result.get("status") == "accepted" and bool(turn.result.get("progress")), f"status={turn.result.get('status')}; progress={bool(turn.result.get('progress'))}"))
        checks.append(_check("god_progress_source_matches_definition", bool(turn.result.get("source_event_type") or turn.result.get("source_behavior_id")), f"source_event={turn.result.get('source_event_type')}; source_behavior={turn.result.get('source_behavior_id')}"))
    elif name == "approve_institution":
        checks.append(_check("god_institution_platform_applied", turn.result.get("status") == "accepted" and str((turn.result.get("institution") or {}).get("status", "")) == "active", f"status={turn.result.get('status')}; institution_status={(turn.result.get('institution') or {}).get('status')}"))
    elif name == "propose_institution":
        checks.append(_check("god_institution_platform_applied", turn.result.get("status") == "accepted" and bool(turn.result.get("institution")), f"status={turn.result.get('status')}; institution={bool(turn.result.get('institution'))}"))
    elif name == "revise_institution":
        checks.append(_check("god_institution_revision_applied", turn.result.get("status") == "accepted" and int((turn.result.get("institution") or {}).get("revision", 0) or 0) >= 1, f"status={turn.result.get('status')}; revision={(turn.result.get('institution') or {}).get('revision')}"))
        checks.append(_check("god_institution_revision_reason_is_specific", len(str(payload.get("reason") or "").strip()) >= 12, f"reason_length={len(str(payload.get('reason') or ''))}"))
    elif name == "propose_entity":
        checks.append(_check("god_entity_has_type_and_name", bool(payload.get("entity_type")) and len(str(payload.get("name", "")).strip()) >= 2, f"type={payload.get('entity_type')}; name={payload.get('name')}"))
        checks.append(_check("god_entity_platform_created", bool(turn.result.get("entity_id")), f"entity_id={turn.result.get('entity_id')}"))
    elif name == "propose_definition":
        _definition_checks(turn, payload, checks)
    elif name == "propose_world_change":
        _world_change_checks(turn, view, payload, checks)
    else:
        checks.append(_check("god_submission_is_known", name in {"propose_entity", "propose_definition", "propose_world_change"}, f"submission={name}"))


def _definition_checks(turn: AgentTurn, payload: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    """A definition proposal has a known type and a name, stays declarative and was registered."""

    definition_type = str(payload.get("definition_type") or "")
    definition = payload.get("definition") if isinstance(payload.get("definition"), dict) else {}
    allowed_types = {"attribute", "world_property", "capability", "action", "behavior", "recipe"}
    checks.append(_check("god_definition_type_known", definition_type in allowed_types, f"definition_type={definition_type}"))
    checks.append(_check("god_definition_has_name", len(str(payload.get("name") or "").strip()) >= 2, f"name={payload.get('name')}"))
    checks.append(_check("god_definition_is_declarative", not any(key in definition for key in {"handler", "python", "script", "sql", "code"}), "definition contains only declarative fields"))
    checks.append(_check("god_definition_registered", turn.result.get("status") == "accepted" and bool(turn.result.get("registered")), f"status={turn.result.get('status')}; registered={turn.result.get('registered')}"))


def _world_change_checks(turn: AgentTurn, view: dict[str, Any], payload: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    """A world change has a summary, was applied, cites visible evidence and keeps its type's contract."""

    checks.append(_check("god_world_change_has_summary", len(str(payload.get("summary", "")).strip()) >= 8, f"summary_length={len(str(payload.get('summary', '')))}"))
    checks.append(_check("god_world_change_applied", turn.result.get("status") == "accepted" and bool(turn.result.get("applied")), f"applied={turn.result.get('applied')}; effects={turn.result.get('applied_effects')}"))
    proposal_text = " ".join(str(payload.get(key, "")) for key in ("summary", "scope", "conditions", "effects", "expected_effects", "reason"))
    evidence_terms = _context_evidence(view)
    proposal_terms = _terms(proposal_text)
    condition_terms = _terms(payload.get("conditions"))
    grounded = bool(proposal_terms & evidence_terms) or bool(condition_terms & evidence_terms)
    checks.append(_check("god_proposal_is_evidence_grounded", grounded, "proposal overlaps visible world evidence" if grounded else "proposal does not cite visible world evidence"))
    change_type = str(payload.get("change_type", ""))
    if change_type == "event" and not payload.get("property"):
        _narrative_event_checks(payload, checks)
    if change_type == "rule":
        _rule_checks(turn, payload, checks)


def _narrative_event_checks(payload: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    """An event without a property is narrative only: it must not claim a state change."""

    mutation_types = {"state_delta", "need_delta", "attribute_delta", "attribute_set", "inventory_add", "inventory_remove", "world_property_delta", "world_property_set", "relationship_delta", "entity_state_set", "catalog_action"}
    direct_claim = any(isinstance(item, dict) and str(item.get("type") or item.get("op") or "") in mutation_types for item in (payload.get("effects") or []))
    checks.append(_check("god_event_does_not_claim_unapplied_state_change", not direct_claim, "event is narrative only" if not direct_claim else "direct state mutation requires a structured property/rule/relationship/faction proposal"))
    checks.append(_check("god_event_is_valid_narrative_signal", not direct_claim, "narrative event can be handled by a configured world rule" if not direct_claim else "event effects claim a direct state change"))


def _rule_checks(turn: AgentTurn, payload: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    """A rule is declarative, names a follow-up behavior and claims only the enforcement its effect has."""

    checks.append(_check("god_rule_has_behavior_followup", bool(turn.result.get("followup_verified")), "rule is stored; a later behavior outcome must verify its effect" if not turn.result.get("followup_verified") else "a later behavior outcome verified the rule"))
    rule = payload.get("rule") if isinstance(payload.get("rule"), dict) else {}
    condition = str(rule.get("condition") or rule.get("when") or "")
    effect = rule.get("effect")
    checks.append(_check("god_rule_is_declarative", len(condition.strip()) >= 5 and bool(effect), "condition and effect are explicit" if len(condition.strip()) >= 5 and bool(effect) else "rule needs an explicit condition and effect"))
    followup = rule.get("behavior_names") or rule.get("behavior_ids") or rule.get("action_names") or []
    checks.append(_check("god_rule_names_followup_behavior", bool(followup), "a later configured behavior is named" if followup else "rule has no configured behavior or action followup"))
    effect_text = str(rule.get("effect") or "").casefold()
    effect_is_denial = effect_text in {"deny", "deny_access", "deny_action", "forbid"} or (isinstance(rule.get("effect"), dict) and str(rule["effect"].get("type", "")).casefold() in {"deny", "deny_access", "deny_action", "forbid"})
    checks.append(_check("god_rule_expected_effect_matches", True, "Platform uses the typed effect as the enforcement contract" if effect_is_denial else "signal text remains observational; no prose is treated as enforcement"))
    typed_enforcement = str((rule.get("effect") or {}).get("enforcement") or "").casefold() if isinstance(rule.get("effect"), dict) else ""
    enforcement_consistent = not typed_enforcement or typed_enforcement in {"none", "signal", str((rule.get("effect") or {}).get("type") or "signal").casefold()}
    checks.append(_check("god_rule_claims_match_enforcement", enforcement_consistent, "typed enforcement matches the effect type" if enforcement_consistent else "typed enforcement does not match the submitted effect type"))
    _declared_condition_checks(payload, rule, checks)


def _declared_condition_checks(payload: dict[str, Any], rule: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    """Every machine field named in ``conditions`` appears in the rule condition."""

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


# --- Story ---


def _story_checks(turn: AgentTurn, view: dict[str, Any], name: str, payload: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    """Checks for Story: progress, a wait or a beat grounded in visible story evidence."""

    if name == "record_world_progress" or payload.get("intent_type") == "record_world_progress":
        evidence = str(payload.get("evidence") or "")
        checks.append(_check("story_progress_evidence_is_specific", len(evidence.strip()) >= 12, f"evidence_length={len(evidence)}"))
        checks.append(_check("story_progress_update_applied", turn.result.get("status") == "accepted" and bool(turn.result.get("progress")), f"status={turn.result.get('status')}; progress={bool(turn.result.get('progress'))}"))
        checks.append(_check("story_progress_source_matches_definition", bool(turn.result.get("source_event_type") or turn.result.get("source_behavior_id")), f"source_event={turn.result.get('source_event_type')}; source_behavior={turn.result.get('source_behavior_id')}"))
    elif name == "wait" or payload.get("intent_type") == "wait" or turn.result.get("status") == "wait":
        checks.append(_check("story_can_wait_without_unlocked_beat", True, "no configured continuation was selected"))
    elif name == "propose_story_beat" or payload.get("intent_type") == "propose_story_beat":
        _story_beat_checks(turn, view, payload, checks)
    else:
        checks.append(_check("story_submission_is_known", False, f"submission={name}"))


def _story_beat_checks(turn: AgentTurn, view: dict[str, Any], payload: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    """A Story beat is a configured choice or an emergent summary, cites visible evidence and was activated."""

    summary = str(payload.get("summary") or "")
    configured = bool(payload.get("beat"))
    checks.append(_check("story_choice_or_emergent_summary", configured or len(summary) >= 8, f"configured={configured}; summary_length={len(summary)}"))
    story_text = " ".join(str(payload.get(key, "")) for key in ("beat", "summary", "fact", "goal_hint", "reason", "audience"))
    evidence_terms = _context_evidence(view)
    story_terms = _terms(story_text)
    audience = {str(item.get("name", "")).casefold() for item in (view.get("audience_candidates") or []) if isinstance(item, dict)}
    audience_match = any(str(item).casefold() in story_text.casefold() for item in audience)
    grounded = bool(story_terms & evidence_terms) or audience_match or configured
    checks.append(_check("story_content_is_evidence_grounded", grounded, "beat overlaps visible story evidence" if grounded else "beat does not cite visible story evidence"))
    checks.append(_check("story_platform_applied", turn.result.get("status") == "accepted" and bool(turn.result.get("activated")), f"activated={turn.result.get('activated')}"))
