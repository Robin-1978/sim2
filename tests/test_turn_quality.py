"""Pin the turn quality guardrails: each check, its order and the grade it leads to."""

from __future__ import annotations

import pytest

from sim2.agents.engine import AgentTurn
from sim2.agents.quality import evaluate_turn

SESSION_CHECKS = ["session_finished", "bounded_session", "platform_feedback_visible"]


def make_turn(role, name, arguments=None, status="accepted", result=None, iterations=1, transcript=None):
    step = {"step": "tool", "name": name, "arguments": arguments or {}, "result": {"status": status}}
    return AgentTurn(
        role=role,
        task_type="quality",
        response={},
        result={"status": status, **(result or {})},
        iterations=iterations,
        transcript=[step] if transcript is None else transcript,
    )


def checks_of(quality):
    return {item["name"]: item for item in quality["checks"]}


def names_of(quality):
    return [item["name"] for item in quality["checks"]]


def failed_of(quality):
    return [item["name"] for item in quality["checks"] if not item["passed"]]


def behavior(index, **fields):
    return {"ref": f"behavior:b{index}", "choice": f"Choice {index}", **fields}


def decision_view(*behaviors, goal_tags=(), drives=(), facts=()):
    return {
        "executable_behaviors": list(behaviors),
        "goal_tags": list(goal_tags),
        "drives": [{"id": item} for item in drives],
        "observation": {"facts": list(facts)},
    }


def decide(arguments, view, name="request_behavior", **turn_fields):
    return evaluate_turn(make_turn("agent_decision", name, arguments, **turn_fields), view)


# --- shared session checks and provider failure ---


def test_every_turn_starts_with_the_shared_session_checks_in_order():
    quality = evaluate_turn(make_turn("story", "wait", status="wait"), {})
    assert names_of(quality)[:3] == SESSION_CHECKS
    assert set(quality) == {"role", "iterations", "checks", "grade", "passed"}
    assert quality["role"] == "story" and quality["iterations"] == 1


@pytest.mark.parametrize(("status", "expected"), [("accepted", True), ("wait", True), ("skipped", True), ("rejected", False), ("error", False)])
def test_session_finishes_only_with_a_closing_status(status, expected):
    quality = evaluate_turn(make_turn("observer", "noop", status=status), {})
    check = checks_of(quality)["session_finished"]
    assert check["passed"] is expected
    assert check["detail"] == f"status={status}"
    assert quality["passed"] is expected
    assert quality["grade"] == ("grounded" if expected else "ungrounded_or_invalid")


@pytest.mark.parametrize(("iterations", "expected"), [(0, True), (6, True), (7, False), (-1, False)])
def test_session_is_bounded_to_six_iterations(iterations, expected):
    quality = evaluate_turn(make_turn("observer", "noop", iterations=iterations), {})
    check = checks_of(quality)["bounded_session"]
    assert check["passed"] is expected
    assert check["detail"] == f"iterations={iterations}"


def test_a_rejected_step_needs_visible_platform_feedback():
    rejected = {"step": "model", "result": {"status": "rejected"}}
    tool = {"step": "tool", "name": "noop", "arguments": {}, "result": {"status": "accepted"}}
    silent = checks_of(evaluate_turn(make_turn("observer", "noop", transcript=[rejected]), {}))["platform_feedback_visible"]
    shown = checks_of(evaluate_turn(make_turn("observer", "noop", transcript=[rejected, tool]), {}))["platform_feedback_visible"]
    clean = checks_of(evaluate_turn(make_turn("observer", "noop"), {}))["platform_feedback_visible"]
    assert (silent["passed"], silent["detail"]) == (False, "rejections=1")
    assert (shown["passed"], shown["detail"]) == (True, "rejections=1")
    assert (clean["passed"], clean["detail"]) == (True, "rejections=0")


@pytest.mark.parametrize("role", ["agent_decision", "agent_reflection", "god", "story", "observer"])
def test_provider_failure_is_reported_before_any_role_check(role):
    turn = make_turn(role, "noop", status="rejected", result={"failed_check": "llm_response", "message": "HTTP 429"})
    quality = evaluate_turn(turn, {})
    assert names_of(quality) == [*SESSION_CHECKS, "provider_response_available"]
    assert checks_of(quality)["provider_response_available"] == {"name": "provider_response_available", "passed": False, "detail": "HTTP 429"}
    assert (quality["grade"], quality["passed"], quality["role"]) == ("provider_error", False, role)


def test_provider_failure_without_a_message_has_a_readable_detail():
    quality = evaluate_turn(make_turn("god", "noop", status="rejected", result={"failed_check": "llm_response", "message": ""}), {})
    assert checks_of(quality)["provider_response_available"]["detail"] == "provider returned no usable response"


def test_each_role_runs_only_its_own_checks():
    beat = {"beat": "harvest"}
    assert failed_of(evaluate_turn(make_turn("story", "propose_world_change", {}), {})) == ["story_submission_is_known"]
    assert failed_of(evaluate_turn(make_turn("god", "propose_story_beat", beat), {})) == ["god_submission_is_known"]
    assert failed_of(evaluate_turn(make_turn("agent_reflection", "propose_world_change", {}), {})) == ["reflection_intent_is_known"]
    assert names_of(evaluate_turn(make_turn("observer", "propose_world_change", {}), {})) == SESSION_CHECKS


# --- character decisions ---


def test_a_guarded_decision_is_graded_platform_guarded():
    quality = decide({"choice": "Choice 0"}, decision_view(behavior(0)), status="skipped")
    assert quality["passed"] is True
    assert quality["grade"] == "platform_guarded"


def test_a_guarded_decision_keeps_its_grade_even_when_the_protocol_checks_fail():
    quality = decide({"choice": "Choice 0"}, decision_view(behavior(0)), status="skipped", iterations=9)
    assert checks_of(quality)["bounded_session"]["passed"] is False
    assert (quality["passed"], quality["grade"]) == (False, "platform_guarded")


def test_a_choice_outside_the_offered_candidates_is_an_invalid_protocol():
    quality = decide({"choice": "Unknown"}, decision_view(behavior(0)))
    assert checks_of(quality)["behavior_is_offered"]["detail"] == "selected=None; offered=['behavior:b0']"
    assert failed_of(quality) == ["behavior_is_offered", "decision_semantic_signal", "decision_reason_grounding"]
    assert (quality["passed"], quality["grade"]) == (False, "invalid_protocol")


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("send_message", {}),
        ("create_trade_offer", {}),
        ("accept_trade_offer", {}),
        ("counter_trade_offer", {}),
        ("reject_trade_offer", {}),
        ("request_behavior", {"intent_type": "send_message"}),
    ],
)
def test_interactions_are_graded_without_a_behavior_choice(name, arguments):
    quality = decide(arguments, decision_view(behavior(0)), name=name)
    assert (quality["passed"], quality["grade"]) == (True, "grounded_interaction")


def test_an_interaction_is_graded_as_an_interaction_even_when_it_also_waits():
    quality = decide({}, decision_view(behavior(0)), name="send_message", status="wait")
    assert (quality["passed"], quality["grade"]) == (True, "grounded_interaction")


@pytest.mark.parametrize(
    ("name", "arguments", "status"),
    [("wait", {}, "wait"), ("wait", {}, "accepted"), ("request_behavior", {"intent": "wait"}, "accepted"), ("request_behavior", {}, "wait")],
)
def test_waiting_is_graded_as_a_grounded_wait(name, arguments, status):
    quality = decide(arguments, decision_view(behavior(0)), name=name, status=status)
    assert (quality["passed"], quality["grade"]) == (True, "grounded_wait")


def test_a_choice_aligned_with_a_goal_is_comparative_when_there_are_alternatives():
    view = decision_view(behavior(0, goal_tags=["harvest"]), behavior(1), goal_tags=["harvest"])
    quality = decide({"choice": "Choice 0"}, view)
    assert (quality["passed"], quality["grade"]) == (True, "comparative_grounded_choice")
    assert checks_of(quality)["decision_semantic_signal"]["detail"] == "alignment=['harvest']; reason_overlap=False; candidates=2"


def test_a_choice_aligned_with_a_goal_is_forced_when_it_is_the_only_candidate():
    view = decision_view(behavior(0, goal_tags=["harvest"]), goal_tags=["harvest"])
    quality = decide({"choice": "Choice 0"}, view)
    assert (quality["passed"], quality["grade"]) == (True, "grounded_forced_choice")


def test_alignment_considers_every_configured_drive_not_only_the_top_three():
    drives = ("rest", "food", "safety", "curiosity")
    view = decision_view(behavior(0, drive_tags=["curiosity"]), behavior(1), drives=drives)
    quality = decide({"choice": "Choice 0"}, view)
    assert checks_of(quality)["decision_semantic_signal"]["detail"].startswith("alignment=['curiosity']")
    assert quality["grade"] == "comparative_grounded_choice"


def test_a_reason_grounded_in_visible_evidence_still_needs_reflection_without_alignment():
    view = decision_view(behavior(0), behavior(1), facts=["the bridge is flooding"])
    quality = decide({"choice": "Choice 0", "reason": "Check the flooding bridge"}, view)
    assert (quality["passed"], quality["grade"]) == (True, "accepted_needs_reflection")
    assert checks_of(quality)["decision_semantic_signal"]["detail"] == "alignment=[]; reason_overlap=True; candidates=2"


@pytest.mark.parametrize(
    ("arguments", "status", "passed", "detail"),
    [
        ({"choice": "Choice 0", "reason": "bridge flooding"}, "accepted", True, "reason overlaps visible evidence"),
        ({"choice": "Choice 0", "reason": "entirely unrelated words"}, "accepted", True, "choice aligned with visible goals/drives; explanation language is advisory"),
        ({"choice": "Choice 0"}, "accepted", True, "choice-only response selected a currently executable Platform candidate; explanation is optional"),
        ({"choice": "Choice 0"}, "wait", True, "choice aligned with visible goals/drives; no explanation emitted"),
    ],
)
def test_reason_grounding_explains_why_the_choice_is_accepted(arguments, status, passed, detail):
    view = decision_view(behavior(0, goal_tags=["harvest"]), goal_tags=["harvest"], facts=["bridge flooding"])
    check = checks_of(decide(arguments, view, status=status))["decision_reason_grounding"]
    assert (check["passed"], check["detail"]) == (passed, detail)


def test_reason_grounding_fails_without_alignment_or_overlap():
    quality = decide({"choice": "Choice 0", "reason": "zzz qqq"}, decision_view(behavior(0), behavior(1)))
    check = checks_of(quality)["decision_reason_grounding"]
    assert (check["passed"], check["detail"]) == (False, "no overlap with selected behavior, goals, drives or capabilities")
    assert quality["grade"] == "invalid_protocol"


def test_the_reason_is_advisory_and_only_its_length_is_reported():
    check = checks_of(decide({"choice": "Choice 0", "reason": "  because  "}, decision_view(behavior(0))))["decision_reason_advisory"]
    assert check["passed"] is True
    assert check["detail"].startswith("reason_length=7;")


def test_a_decision_must_not_fill_in_execution_costs():
    view = decision_view(behavior(0, goal_tags=["harvest"]), goal_tags=["harvest"])
    quality = decide({"choice": "Choice 0", "parameters": {"duration": 3, "target": "field"}}, view)
    assert checks_of(quality)["decision_does_not_fill_execution_costs"] == {
        "name": "decision_does_not_fill_execution_costs",
        "passed": False,
        "detail": "parameter_keys=['duration', 'target']",
    }
    assert quality["grade"] == "invalid_protocol"
    harmless = decide({"choice": "Choice 0", "parameters": {"target": "field"}}, view)
    assert checks_of(harmless)["decision_does_not_fill_execution_costs"]["passed"] is True


@pytest.mark.parametrize(
    "arguments",
    [{"choice": "choice 0"}, {"behavior_ref": "b0"}, {"behavior_ref": "behavior:b0"}, {"choice": "nothing", "behavior_ref": "b0"}],
)
def test_choices_and_references_resolve_to_the_offered_behavior_ref(arguments):
    check = checks_of(decide(arguments, decision_view(behavior(0))))["behavior_is_offered"]
    assert (check["passed"], check["detail"]) == (True, "selected=behavior:b0; offered=['behavior:b0']")


def test_a_choice_resolves_through_the_behavior_name_when_it_has_no_choice_label():
    view = decision_view({"ref": "behavior:mend", "name": "Mend Nets"})
    assert checks_of(decide({"choice": "mend nets"}, view))["behavior_is_offered"]["passed"] is True


def test_the_platform_result_supplies_the_behavior_ref_when_the_model_gave_none():
    check = checks_of(decide({}, decision_view(behavior(0)), result={"behavior_ref": "behavior:b0"}))["behavior_is_offered"]
    assert (check["passed"], check["detail"]) == (True, "selected=behavior:b0; offered=['behavior:b0']")


def test_candidates_fall_back_to_available_behaviors_when_none_are_executable():
    only_available = {"available_behaviors": [behavior(0)]}
    assert checks_of(decide({"choice": "Choice 0"}, only_available))["behavior_is_offered"]["passed"] is True
    empty_executable = {"executable_behaviors": [], "available_behaviors": [behavior(0)]}
    assert checks_of(decide({"choice": "Choice 0"}, empty_executable))["behavior_is_offered"]["passed"] is True
    both = {"executable_behaviors": [behavior(1)], "available_behaviors": [behavior(0)]}
    assert checks_of(decide({"choice": "Choice 0"}, both))["behavior_is_offered"]["passed"] is False


INSTITUTION = {
    "name": "Grain Hall",
    "purpose": "Share the grain stores fairly",
    "evidence": "granary stores are thin after the flood",
}


@pytest.mark.parametrize("name", ["propose_institution", "approve_institution"])
def test_an_institution_decision_needs_a_named_evidenced_proposal(name):
    view = decision_view(behavior(0), facts=["granary stores are thin after the flood"])
    quality = decide(INSTITUTION, view, name=name)
    assert names_of(quality)[3:9] == [
        "behavior_is_offered",
        "institution_name_is_specific",
        "institution_platform_applied",
        "institution_evidence_is_specific",
        "decision_reason_advisory",
        "decision_does_not_fill_execution_costs",
    ]
    assert (quality["passed"], quality["grade"]) == (True, "accepted_needs_reflection")


def test_an_institution_decision_without_specific_evidence_fails_the_semantic_signal():
    view = decision_view(behavior(0), facts=["granary stores are thin after the flood"])
    quality = decide({**INSTITUTION, "evidence": "short"}, view, name="propose_institution")
    assert "institution_evidence_is_specific" in failed_of(quality)
    assert "decision_semantic_signal" in failed_of(quality)
    assert quality["grade"] == "invalid_protocol"


def test_an_institution_proposed_through_intent_type_is_recognised():
    view = decision_view(behavior(0), facts=["granary stores are thin after the flood"])
    quality = decide({**INSTITUTION, "intent_type": "propose_institution"}, view)
    assert checks_of(quality)["institution_name_is_specific"]["passed"] is True
    assert quality["passed"] is True


def test_an_institution_needs_a_specific_name_and_an_applied_platform_result():
    view = decision_view(behavior(0), facts=["granary stores are thin after the flood"])
    quality = decide({**INSTITUTION, "name": "G"}, view, name="propose_institution", status="wait")
    assert checks_of(quality)["institution_name_is_specific"]["detail"] == "name_length=1"
    assert failed_of(quality) == ["institution_name_is_specific"]
    assert checks_of(quality)["institution_platform_applied"]["passed"] is True


# --- reflection ---


def reflect(name, arguments, view=None, **turn_fields):
    return evaluate_turn(make_turn("agent_reflection", name, arguments, **turn_fields), view or {})


RECENT_OUTCOME = {
    "event_type": "patrol_finished",
    "behavior_id": "patrol_walls",
    "outcome": {"result": "walls inspected and sound", "expected": {"summary": "walls hold", "goal_tags": ["safety"]}},
}


@pytest.mark.parametrize(
    "call",
    [
        {"name": "wait", "arguments": {}},
        {"name": "record_experience", "arguments": {"intent_type": "wait"}},
        {"name": "record_experience", "arguments": {}, "status": "wait"},
    ],
)
def test_reflection_may_decline_to_store_a_lesson(call):
    quality = reflect(**call)
    assert names_of(quality)[3:] == ["reflection_can_decline_low_value_memory"]
    assert (quality["passed"], quality["grade"]) == (True, "grounded")


def test_an_unknown_reflection_submission_fails_the_intent_check():
    quality = reflect("rewrite_history", {})
    assert checks_of(quality)["reflection_intent_is_known"] == {"name": "reflection_intent_is_known", "passed": False, "detail": "submission=rewrite_history"}
    assert (quality["passed"], quality["grade"]) == (False, "ungrounded_or_invalid")


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        ("short", False),
        ("four words are enough", True),
        ("abcdefghijklmnop", True),
        ("abcdefghijklmno", False),
        ("a " * 250, True),
        ("a " * 250 + "b", False),
    ],
)
def test_a_lesson_must_be_specific_and_bounded(summary, expected):
    check = checks_of(reflect("record_experience", {"summary": summary}))["reflection_experience_is_specific_and_bounded"]
    assert check["passed"] is expected
    assert check["detail"] == f"summary_length={len(summary)}"


def test_a_lesson_is_not_matched_against_outcomes_when_none_are_visible():
    assert names_of(reflect("record_experience", {"summary": "walls were inspected and sound"}))[3:] == ["reflection_experience_is_specific_and_bounded"]


def test_a_lesson_must_cite_a_recent_outcome():
    view = {"recent_outcomes": [RECENT_OUTCOME, "not an outcome"]}
    cited = checks_of(reflect("record_experience", {"summary": "The inspected walls are sound"}, view))["reflection_experience_matches_outcome"]
    assert (cited["passed"], cited["detail"]) == (True, "summary matches recent outcome(s): ['patrol_walls']")
    unrelated = checks_of(reflect("record_experience", {"summary": "Bread is best when fresh and warm"}, view))["reflection_experience_matches_outcome"]
    assert (unrelated["passed"], unrelated["detail"]) == (False, "summary does not cite any recent observed outcome")


def test_a_capability_update_needs_evidence_a_bounded_delta_and_a_platform_result():
    good = {"evidence": "Two repairs finished without help", "delta": 0.2}
    quality = reflect("update_capability", good, result={"capability": {"id": "repair"}})
    assert names_of(quality)[3:] == [
        "reflection_capability_evidence_is_specific",
        "reflection_capability_delta_is_bounded",
        "reflection_capability_update_applied",
    ]
    assert (quality["passed"], quality["grade"]) == (True, "grounded")
    assert failed_of(reflect("update_capability", good)) == ["reflection_capability_update_applied"]
    assert failed_of(reflect("update_capability", {**good, "evidence": "too short"}, result={"capability": {"id": "r"}})) == ["reflection_capability_evidence_is_specific"]


def test_a_rejected_submission_is_invisible_to_the_role_checks():
    quality = reflect("update_capability", {"evidence": "Two repairs finished without help", "delta": 0.1}, status="rejected", result={"capability": {"id": "repair"}})
    assert failed_of(quality) == ["session_finished", "reflection_intent_is_known"]
    assert checks_of(quality)["reflection_intent_is_known"]["detail"] == "submission="


@pytest.mark.parametrize(("delta", "expected"), [(0.2, True), (0.05, True), (0.21, False), (0, False), (-0.1, False), ("0.1", False), (None, False)])
def test_a_capability_delta_is_bounded_to_a_small_positive_step(delta, expected):
    quality = reflect("update_capability", {"evidence": "Two repairs finished without help", "delta": delta}, result={"capability": {"id": "r"}})
    check = checks_of(quality)["reflection_capability_delta_is_bounded"]
    assert check["passed"] is expected
    assert check["detail"] == f"delta={delta}"


@pytest.mark.parametrize(("statement", "expected"), [("Map every road", True), ("Map roads", False), ("", False)])
def test_a_goal_update_needs_a_specific_statement(statement, expected):
    check = checks_of(reflect("update_goal", {"statement": statement}))["reflection_goal_is_specific"]
    assert (check["passed"], check["detail"]) == (expected, f"statement_length={len(statement)}")


@pytest.mark.parametrize(
    ("arguments", "specific", "valid"),
    [
        ({"statement": "Rain follows the east wind", "confidence": 0.6}, True, True),
        ({"belief": "Rain follows the east wind", "confidence": 1}, True, True),
        ({"statement": "Rain follows the east wind", "confidence": 0}, True, True),
        ({"statement": "Rain follows the east wind", "confidence": 1.5}, True, False),
        ({"statement": "Rain follows the east wind", "confidence": "high"}, True, False),
        ({"statement": "Rain follows the east wind"}, True, False),
        ({"statement": "Rain", "confidence": 0.5}, False, True),
    ],
)
def test_a_belief_update_needs_a_specific_statement_and_a_valid_confidence(arguments, specific, valid):
    checks = checks_of(reflect("update_belief", arguments))
    assert checks["reflection_belief_is_specific"]["passed"] is specific
    assert checks["reflection_belief_confidence_is_valid"]["passed"] is valid


@pytest.mark.parametrize(("arguments", "expected"), [({"action_ids": ["walk"]}, True), ({"actions": ["walk"]}, True), ({"action_ids": []}, False), ({}, False)])
def test_a_behavior_proposal_needs_actions(arguments, expected):
    check = checks_of(reflect("propose_behavior", arguments))["reflection_behavior_proposal_has_actions"]
    assert check["passed"] is expected


def test_a_reflection_submitted_as_an_intent_response_uses_its_intent_type():
    step = {"step": "intent", "response": {"intent_type": "update_goal", "statement": "Map every road of the valley"}, "result": {"status": "accepted"}}
    quality = evaluate_turn(make_turn("agent_reflection", "unused", transcript=[step]), {})
    assert names_of(quality)[3:] == ["reflection_goal_is_specific"]
    assert quality["passed"] is True


# --- God ---


def divine(name, arguments, view=None, **turn_fields):
    return evaluate_turn(make_turn("god", name, arguments, **turn_fields), view or {})


@pytest.mark.parametrize(
    "call",
    [
        {"name": "wait", "arguments": {}},
        {"name": "propose_entity", "arguments": {"intent_type": "wait"}},
        {"name": "propose_entity", "arguments": {}, "status": "wait"},
    ],
)
def test_god_may_wait_when_no_proposal_is_supported(call):
    quality = divine(**call)
    assert names_of(quality)[3:] == ["god_can_wait_without_supported_proposal"]
    assert (quality["passed"], quality["grade"]) == (True, "grounded")


PROGRESS = {"progress": {"id": "p"}, "source_event_type": "harvest_done"}


@pytest.mark.parametrize("role", ["god", "story"])
def test_progress_records_need_evidence_an_applied_update_and_a_source(role):
    arguments = {"evidence": "Harvest finished before the first frost"}
    good = evaluate_turn(make_turn(role, "record_world_progress", arguments, result=PROGRESS), {})
    prefix = "god" if role == "god" else "story"
    assert names_of(good)[3:] == [f"{prefix}_progress_evidence_is_specific", f"{prefix}_progress_update_applied", f"{prefix}_progress_source_matches_definition"]
    assert (good["passed"], good["grade"]) == (True, "grounded")
    unapplied = evaluate_turn(make_turn(role, "record_world_progress", arguments, result={"source_behavior_id": "b"}), {})
    assert failed_of(unapplied) == [f"{prefix}_progress_update_applied"]
    unsourced = evaluate_turn(make_turn(role, "record_world_progress", {"evidence": "short"}, result={"progress": {"id": "p"}}), {})
    assert failed_of(unsourced) == [f"{prefix}_progress_evidence_is_specific", f"{prefix}_progress_source_matches_definition"]
    assert checks_of(unsourced)[f"{prefix}_progress_source_matches_definition"]["detail"] == "source_event=None; source_behavior=None"


def test_approving_an_institution_requires_an_active_institution():
    active = divine("approve_institution", {}, result={"institution": {"status": "active"}})
    draft = divine("approve_institution", {}, result={"institution": {"status": "draft"}})
    assert names_of(active)[3:] == ["god_institution_platform_applied"]
    assert (active["passed"], draft["passed"]) == (True, False)
    assert checks_of(draft)["god_institution_platform_applied"]["detail"] == "status=accepted; institution_status=draft"


def test_proposing_an_institution_requires_a_stored_institution():
    assert divine("propose_institution", {}, result={"institution": {"status": "draft"}})["passed"] is True
    missing = divine("propose_institution", {})
    assert checks_of(missing)["god_institution_platform_applied"]["detail"] == "status=accepted; institution=False"
    assert missing["passed"] is False


@pytest.mark.parametrize(
    ("institution", "reason", "expected"),
    [
        ({"revision": 1}, "The council asked for a clearer charter", [True, True]),
        ({"revision": 0}, "The council asked for a clearer charter", [False, True]),
        ({}, "The council asked for a clearer charter", [False, True]),
        ({"revision": 2}, "short", [True, False]),
    ],
)
def test_revising_an_institution_needs_a_revision_and_a_reason(institution, reason, expected):
    quality = divine("revise_institution", {"reason": reason}, result={"institution": institution})
    assert names_of(quality)[3:] == ["god_institution_revision_applied", "god_institution_revision_reason_is_specific"]
    assert [item["passed"] for item in quality["checks"][3:]] == expected


def test_an_entity_proposal_needs_a_type_a_name_and_a_created_entity():
    created = divine("propose_entity", {"entity_type": "location", "name": "Ford"}, result={"entity_id": "ford"})
    assert names_of(created)[3:] == ["god_entity_has_type_and_name", "god_entity_platform_created"]
    assert (created["passed"], created["grade"]) == (True, "grounded")
    assert failed_of(divine("propose_entity", {"entity_type": "location", "name": "F"}, result={"entity_id": "f"})) == ["god_entity_has_type_and_name"]
    assert failed_of(divine("propose_entity", {"name": "Ford"}, result={"entity_id": "ford"})) == ["god_entity_has_type_and_name"]
    assert failed_of(divine("propose_entity", {"entity_type": "location", "name": "Ford"})) == ["god_entity_platform_created"]


DEFINITION = {"definition_type": "action", "name": "Ford crossing", "definition": {"duration": 5}}


def test_a_definition_proposal_needs_a_known_type_a_name_declarative_content_and_registration():
    registered = divine("propose_definition", DEFINITION, result={"registered": True})
    assert names_of(registered)[3:] == ["god_definition_type_known", "god_definition_has_name", "god_definition_is_declarative", "god_definition_registered"]
    assert (registered["passed"], registered["grade"]) == (True, "grounded")
    assert failed_of(divine("propose_definition", DEFINITION)) == ["god_definition_registered"]
    assert failed_of(divine("propose_definition", {**DEFINITION, "definition_type": "teleport"}, result={"registered": True})) == ["god_definition_type_known"]
    assert failed_of(divine("propose_definition", {**DEFINITION, "name": "F"}, result={"registered": True})) == ["god_definition_has_name"]


@pytest.mark.parametrize("key", ["handler", "python", "script", "sql", "code"])
def test_a_definition_must_stay_declarative(key):
    quality = divine("propose_definition", {**DEFINITION, "definition": {key: "run()"}}, result={"registered": True})
    assert failed_of(quality) == ["god_definition_is_declarative"]


def test_a_definition_that_is_not_a_mapping_has_no_forbidden_fields():
    quality = divine("propose_definition", {**DEFINITION, "definition": "text"}, result={"registered": True})
    assert quality["passed"] is True


FEVER = {"known_facts": ["clinic fever is spreading in the lanes"]}
APPLIED = {"applied": True, "applied_effects": ["noted"]}


def change(**fields):
    return {"change_type": "event", "summary": "Mobilize a clinic response today", "effects": ["People notice the fever"], **fields}


def test_a_world_change_needs_a_summary_an_applied_result_and_visible_evidence():
    quality = divine("propose_world_change", change(), FEVER, result=APPLIED)
    assert names_of(quality)[3:] == [
        "god_world_change_has_summary",
        "god_world_change_applied",
        "god_proposal_is_evidence_grounded",
        "god_event_does_not_claim_unapplied_state_change",
        "god_event_is_valid_narrative_signal",
    ]
    assert (quality["passed"], quality["grade"]) == (True, "grounded")
    assert failed_of(divine("propose_world_change", change(summary="Short"), FEVER, result=APPLIED)) == ["god_world_change_has_summary"]
    assert failed_of(divine("propose_world_change", change(), FEVER)) == ["god_world_change_applied"]
    ungrounded = divine("propose_world_change", change(summary="Plant sunflowers everywhere soon", effects=["Seeds scatter"]), FEVER, result=APPLIED)
    assert failed_of(ungrounded) == ["god_proposal_is_evidence_grounded"]
    assert checks_of(ungrounded)["god_proposal_is_evidence_grounded"]["detail"] == "proposal does not cite visible world evidence"


def test_a_narrative_event_must_not_claim_a_state_change():
    claimed = divine("propose_world_change", change(effects=[{"type": "state_delta", "amount": 1}]), FEVER, result=APPLIED)
    assert failed_of(claimed) == ["god_event_does_not_claim_unapplied_state_change", "god_event_is_valid_narrative_signal"]
    assert checks_of(claimed)["god_event_is_valid_narrative_signal"]["detail"] == "event effects claim a direct state change"
    via_op = divine("propose_world_change", change(effects=[{"op": "inventory_add"}]), FEVER, result=APPLIED)
    assert "god_event_is_valid_narrative_signal" in failed_of(via_op)
    benign = divine("propose_world_change", change(effects=[{"type": "note"}, "plain text"]), FEVER, result=APPLIED)
    assert benign["passed"] is True
    assert checks_of(benign)["god_event_does_not_claim_unapplied_state_change"]["detail"] == "event is narrative only"


def test_an_event_with_a_property_and_other_change_types_skip_the_narrative_checks():
    structured = divine("propose_world_change", change(property="fog_level", effects=[{"type": "state_delta"}]), FEVER, result=APPLIED)
    assert names_of(structured)[-1] == "god_proposal_is_evidence_grounded"
    relationship = divine("propose_world_change", change(change_type="relationship"), FEVER, result=APPLIED)
    assert names_of(relationship)[-1] == "god_proposal_is_evidence_grounded"


RULE = {
    "change_type": "rule",
    "summary": "Record the clinic fever response rule",
    "rule": {"condition": "event.recent.clinic.fever == true", "effect": "deny_access", "behavior_names": ["treat_fever"]},
    "conditions": ["event.recent.clinic.fever == true"],
}
RULE_RESULT = {**APPLIED, "followup_verified": True}
RULE_CHECKS = [
    "god_rule_has_behavior_followup",
    "god_rule_is_declarative",
    "god_rule_names_followup_behavior",
    "god_rule_expected_effect_matches",
    "god_rule_claims_match_enforcement",
    "god_rule_conditions_match",
]


def rule_with(**rule_fields):
    return {**RULE, "rule": {**RULE["rule"], **rule_fields}}


def test_a_verified_declarative_rule_passes_every_rule_check_in_order():
    quality = divine("propose_world_change", RULE, FEVER, result=RULE_RESULT)
    assert names_of(quality)[6:] == RULE_CHECKS
    assert (quality["passed"], quality["grade"]) == (True, "grounded")


def test_a_rule_waits_for_a_later_behavior_outcome_to_verify_it():
    quality = divine("propose_world_change", RULE, FEVER, result=APPLIED)
    assert failed_of(quality) == ["god_rule_has_behavior_followup"]
    assert checks_of(quality)["god_rule_has_behavior_followup"]["detail"] == "rule is stored; a later behavior outcome must verify its effect"
    assert (quality["passed"], quality["grade"]) == (False, "accepted_needs_world_followup")
    verified = checks_of(divine("propose_world_change", RULE, FEVER, result=RULE_RESULT))["god_rule_has_behavior_followup"]
    assert verified["detail"] == "a later behavior outcome verified the rule"


@pytest.mark.parametrize(
    "rule_fields",
    [{"condition": "x"}, {"condition": ""}, {"effect": ""}, {"effect": None}],
)
def test_a_rule_needs_an_explicit_condition_and_effect(rule_fields):
    quality = divine("propose_world_change", rule_with(**rule_fields), FEVER, result=RULE_RESULT)
    assert "god_rule_is_declarative" in failed_of(quality)
    assert checks_of(quality)["god_rule_is_declarative"]["detail"] == "rule needs an explicit condition and effect"


def test_a_rule_may_state_its_condition_as_when():
    rule = {"when": "event.recent.clinic.fever == true", "effect": "deny", "behavior_names": ["treat_fever"]}
    quality = divine("propose_world_change", {**RULE, "rule": rule}, FEVER, result=RULE_RESULT)
    assert checks_of(quality)["god_rule_is_declarative"]["passed"] is True


@pytest.mark.parametrize("field", ["behavior_names", "behavior_ids", "action_names"])
def test_a_rule_names_its_followup_behavior_in_any_configured_field(field):
    rule = {"condition": "event.recent.clinic.fever == true", "effect": "deny", field: ["treat_fever"]}
    quality = divine("propose_world_change", {**RULE, "rule": rule}, FEVER, result=RULE_RESULT)
    assert checks_of(quality)["god_rule_names_followup_behavior"]["passed"] is True
    missing = divine("propose_world_change", rule_with(behavior_names=[]), FEVER, result=RULE_RESULT)
    assert checks_of(missing)["god_rule_names_followup_behavior"]["detail"] == "rule has no configured behavior or action followup"


@pytest.mark.parametrize(
    ("effect", "detail"),
    [
        ("deny", "Platform uses the typed effect as the enforcement contract"),
        ("Forbid", "Platform uses the typed effect as the enforcement contract"),
        ({"type": "deny_action"}, "Platform uses the typed effect as the enforcement contract"),
        ("open the clinic", "signal text remains observational; no prose is treated as enforcement"),
        ({"type": "signal"}, "signal text remains observational; no prose is treated as enforcement"),
    ],
)
def test_only_denial_effects_are_described_as_enforcement(effect, detail):
    quality = divine("propose_world_change", rule_with(effect=effect), FEVER, result=RULE_RESULT)
    check = checks_of(quality)["god_rule_expected_effect_matches"]
    assert (check["passed"], check["detail"]) == (True, detail)


@pytest.mark.parametrize(
    ("effect", "consistent"),
    [
        ("deny", True),
        ({"type": "deny"}, True),
        ({"type": "deny", "enforcement": "deny"}, True),
        ({"type": "deny", "enforcement": "NONE"}, True),
        ({"type": "deny", "enforcement": "signal"}, True),
        ({"type": "signal", "enforcement": "deny"}, False),
        ({"enforcement": "deny"}, False),
        ({"enforcement": "signal"}, True),
    ],
)
def test_a_rule_may_not_claim_more_enforcement_than_its_effect_type(effect, consistent):
    quality = divine("propose_world_change", rule_with(effect=effect), FEVER, result=RULE_RESULT)
    check = checks_of(quality)["god_rule_claims_match_enforcement"]
    assert check["passed"] is consistent
    assert check["detail"] == ("typed enforcement matches the effect type" if consistent else "typed enforcement does not match the submitted effect type")


@pytest.mark.parametrize(
    ("condition", "declared", "missing"),
    [
        ("world.fog_level > 0.2", ["world.fog_level > 0.2"], []),
        ('world["Fog Level"] > 0.2', ["world.fog_level > 0.2"], []),
        ("world['Fog Level'] > 0.2", ["world.fog_level"], []),
        ("fog is high", ["world.fog_level > 0.2"], ["world.fog_level"]),
        ("actor.alive == true", ["actor.alive", "target.state.health < 3", "resource.stock.level"], ["resource.stock.level", "target.state.health"]),
        ("anything", ["plain words only", "foo.bar"], []),
        ("anything", None, []),
    ],
)
def test_declared_machine_fields_must_appear_in_the_rule_condition(condition, declared, missing):
    payload = {**RULE, "rule": {**RULE["rule"], "condition": condition}}
    if declared is None:
        del payload["conditions"]
    else:
        payload["conditions"] = declared
    check = checks_of(divine("propose_world_change", payload, FEVER, result=RULE_RESULT))["god_rule_conditions_match"]
    assert check["passed"] is (not missing)
    expected = "declared machine fields are represented in the rule condition" if not missing else f"declared condition fields are absent from rule condition: {missing}"
    assert check["detail"] == expected


def test_an_unknown_god_submission_fails_the_known_submission_check():
    quality = divine("summon_rain", {})
    assert checks_of(quality)["god_submission_is_known"] == {"name": "god_submission_is_known", "passed": False, "detail": "submission=summon_rain"}
    assert (quality["passed"], quality["grade"]) == (False, "ungrounded_or_invalid")


def test_only_an_accepted_failing_world_change_needs_a_followup():
    failing = change(summary="Short")
    accepted = divine("propose_world_change", failing, FEVER, result=APPLIED)
    rejected = divine("propose_world_change", failing, FEVER, status="rejected", result=APPLIED)
    other = divine("propose_entity", {"entity_type": "location", "name": "F"}, result={"entity_id": "f"})
    assert accepted["grade"] == "accepted_needs_world_followup"
    assert rejected["grade"] == "ungrounded_or_invalid"
    assert other["grade"] == "ungrounded_or_invalid"
    story = evaluate_turn(make_turn("story", "propose_world_change", failing, result=APPLIED), FEVER)
    assert story["grade"] == "ungrounded_or_invalid"


def test_a_world_change_that_only_waited_needs_no_world_followup():
    quality = divine("propose_world_change", change(), FEVER, status="wait", iterations=9)
    assert names_of(quality)[3:] == ["god_can_wait_without_supported_proposal"]
    assert (quality["passed"], quality["grade"]) == (False, "ungrounded_or_invalid")


# --- Story ---


def narrate(name, arguments, view=None, **turn_fields):
    return evaluate_turn(make_turn("story", name, arguments, **turn_fields), view or {})


def test_story_may_wait_when_no_beat_is_unlocked():
    quality = narrate("wait", {}, status="wait")
    assert names_of(quality)[3:] == ["story_can_wait_without_unlocked_beat"]
    assert narrate("propose_story_beat", {"intent_type": "wait"})["grade"] == "grounded"


def test_an_unknown_story_submission_always_fails():
    quality = narrate("tell_everything", {})
    assert checks_of(quality)["story_submission_is_known"] == {"name": "story_submission_is_known", "passed": False, "detail": "submission=tell_everything"}
    assert (quality["passed"], quality["grade"]) == (False, "ungrounded_or_invalid")


def test_a_configured_beat_is_grounded_by_its_configuration():
    quality = narrate("propose_story_beat", {"beat": "harvest_festival"}, result={"activated": True})
    assert names_of(quality)[3:] == ["story_choice_or_emergent_summary", "story_content_is_evidence_grounded", "story_platform_applied"]
    assert checks_of(quality)["story_choice_or_emergent_summary"]["detail"] == "configured=True; summary_length=0"
    assert (quality["passed"], quality["grade"]) == (True, "grounded")


def test_an_emergent_beat_needs_a_summary_and_visible_story_evidence():
    view = {"story_beats": ["ferry strike ends at dawn"]}
    good = narrate("propose_story_beat", {"summary": "The ferry crews celebrate at dawn"}, view, result={"activated": True})
    assert (good["passed"], good["grade"]) == (True, "grounded")
    short = narrate("propose_story_beat", {"summary": "Short"}, view, result={"activated": True})
    assert "story_choice_or_emergent_summary" in failed_of(short)
    ungrounded = narrate("propose_story_beat", {"summary": "Someone remembers an unrelated song"}, view, result={"activated": True})
    assert failed_of(ungrounded) == ["story_content_is_evidence_grounded"]
    assert checks_of(ungrounded)["story_content_is_evidence_grounded"]["detail"] == "beat does not cite visible story evidence"


def test_a_beat_that_names_a_visible_audience_member_is_grounded():
    view = {"audience_candidates": [{"name": "Mira"}, "not a candidate"]}
    quality = narrate("propose_story_beat", {"summary": "Everyone hears Mira singing again"}, view, result={"activated": True})
    assert checks_of(quality)["story_content_is_evidence_grounded"]["detail"] == "beat overlaps visible story evidence"
    assert quality["passed"] is True


def test_a_story_beat_must_be_activated_by_the_platform():
    quality = narrate("propose_story_beat", {"beat": "harvest_festival"})
    assert failed_of(quality) == ["story_platform_applied"]
    assert checks_of(quality)["story_platform_applied"]["detail"] == "activated=None"
