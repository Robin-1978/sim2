"""Tests for the world-neutral evolution audit harness."""

from __future__ import annotations

import pytest

from scripts.audit_dynamic_evolution_loop import _pending_structural_tasks, _quality_flags, _ready

ABSORBING = "absorbing_state:three_rounds_without_structural_or_story_progress"


class _TaskSource:
    def __init__(self, tasks: list[dict]) -> None:
        self.tasks = tasks

    def ready_evolution_tasks(self) -> list[dict]:
        return list(self.tasks)


def test_pending_structural_tasks_prevent_false_absorbing_state() -> None:
    source = _TaskSource([
        {"task_id": "god:event_group:relay_stability_review:7", "role": "god", "task_type": "event_group:relay_stability_review"},
        {"task_id": "agent_decision:event:7:mira", "role": "agent_decision", "task_type": "agent_plan_selection"},
    ])

    pending = _pending_structural_tasks(source)

    assert [item["task_type"] for item in pending] == ["event_group:relay_stability_review"]


def test_ready_selects_any_configured_god_task_without_event_family_literal() -> None:
    source = _TaskSource([
        {
            "task_id": "god:event_group:relay_stability_review:7",
            "role": "god",
            "task_type": "event_group:relay_stability_review",
        },
        {
            "task_id": "god:event_group:research_synthesis:3",
            "role": "god",
            "task_type": "event_group:research_synthesis",
        },
    ])

    selected = _ready(source, role="god")

    assert selected is not None
    assert selected["task_type"] == "event_group:relay_stability_review"


def test_ready_can_still_filter_the_initial_task_family() -> None:
    source = _TaskSource([
        {
            "task_id": "god:event_group:resource_safety:2",
            "role": "god",
            "task_type": "event_group:resource_safety",
        },
        {
            "task_id": "god:event_group:research_synthesis:3",
            "role": "god",
            "task_type": "event_group:research_synthesis",
        },
    ])

    selected = _ready(source, role="god", task_prefix="event_group:resource_safety")

    assert selected is not None
    assert selected["task_type"] == "event_group:resource_safety"


def _round(
    *,
    objects: int = 0,
    actions: int = 0,
    behaviors: int = 0,
    story: tuple[str, ...] = (),
    choice: str | None = "Inspect Archive",
) -> dict:
    """One row in the shape ``main`` writes to ``evolution-audit.jsonl``.

    ``choice=None`` is a waiting round: the NPC calls ``wait`` with only a
    ``reason`` and the probe records ``no_npc_behavior``, as a real run does.
    """

    if choice is None:
        npc = {
            "status": "no_npc_behavior",
            "decision": {"status": "wait", "calls": [{"name": "wait", "arguments": {"reason": "nothing new"}}]},
        }
    else:
        npc = {
            "status": "completed",
            "decision": {
                "status": "accepted",
                "calls": [{"name": "request_behavior", "arguments": {"choice": choice, "confidence": 0.8, "reason": "r"}}],
            },
        }
    return {
        "new_objects": [{"id": f"object_{index}"} for index in range(objects)],
        "registered_actions": [f"action_{index}" for index in range(actions)],
        "registered_behaviors": [f"behavior_{index}" for index in range(behaviors)],
        "story": [{"status": status} for status in story],
        "npc": npc,
    }


def _flags(rows: list[dict], *, requested: int | None = None, failures: tuple[str, ...] = (), pending: tuple[dict, ...] = ()) -> list[str]:
    return _quality_flags(
        rows,
        requested_rounds=len(rows) if requested is None else requested,
        turn_failures=list(failures),
        pending_structural_tasks=list(pending),
    )


def test_three_rounds_repeating_one_choice_without_progress_are_flagged() -> None:
    rows = [_round(objects=1), _round(), _round(), _round()]

    assert _flags(rows) == [ABSORBING]


def test_waiting_final_round_does_not_hide_a_stalled_tail() -> None:
    # A waiting round has no "choice" argument.  The real run of 2026-11-17
    # repeated "Inspect Archive" for five rounds and then waited; counting the
    # missing choice as a different choice made the audit report "passed".
    rows = [_round(objects=1), _round(), _round(), _round(), _round(choice=None)]

    assert _flags(rows) == [ABSORBING]


def test_alternating_choices_without_progress_are_flagged() -> None:
    rows = [
        _round(objects=1),
        _round(choice="Inspect Archive"),
        _round(choice="Study Source"),
        _round(choice="Inspect Archive"),
    ]

    assert _flags(rows) == [ABSORBING]


@pytest.mark.parametrize(
    "progress",
    [{"objects": 1}, {"actions": 1}, {"behaviors": 1}, {"story": ("accepted",)}, {"story": ("rejected", "accepted")}],
    ids=["object", "action", "behavior", "accepted-beat", "accepted-beat-after-rejected"],
)
def test_progress_in_any_round_of_the_window_clears_the_flag(progress: dict) -> None:
    rows = [_round(), _round(**progress), _round()]

    assert _flags(rows) == []


def test_story_beats_that_were_not_accepted_are_not_progress() -> None:
    rows = [_round(story=("rejected",)), _round(story=("wait",)), _round(story=("rejected", "wait"))]

    assert _flags(rows) == [ABSORBING]


def test_progress_older_than_the_window_does_not_clear_the_flag() -> None:
    rows = [_round(objects=2), _round(story=("accepted",)), _round(), _round(), _round()]

    assert _flags(rows) == [ABSORBING]


def test_queued_structural_work_defers_the_stall_judgement() -> None:
    rows = [_round(objects=1), _round(), _round(), _round()]
    pending = ({"task_id": "god:event_group:relay_stability_review:9", "role": "god"},)

    assert _flags(rows, pending=pending) == []


def test_runs_shorter_than_the_window_are_not_judged() -> None:
    assert _flags([_round(), _round()]) == []


def test_turn_failures_are_reported_once_instead_of_a_stall() -> None:
    rows = [_round(), _round(), _round()]

    assert _flags(rows, failures=("god:schema", "story:rejected", "god:schema")) == ["god:schema", "story:rejected"]


def test_incomplete_runs_are_flagged() -> None:
    rows = [_round(objects=1), _round(story=("accepted",))]

    assert _flags(rows, requested=10) == ["incomplete_rounds:2/10"]
