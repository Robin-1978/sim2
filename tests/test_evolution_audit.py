"""Tests for the world-neutral evolution audit harness."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts.audit_dynamic_evolution_loop import (
    IDLE_LIMIT_MINUTES,
    _advance_idle,
    _can_continue,
    _pending_structural_tasks,
    _probe_template,
    _quality_flags,
    _ready,
)
from sim2.bootstrap.service import create_instance_from_template
from sim2.platform.runtime import Platform

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


# --- the probe's liveness fallback and its outcome contract ---------------------------

SHIPPED_EVOLUTION = Path(__file__).parents[1] / "worlds" / "aster_relay" / "evolution.yaml"


def _wake(wakes: list[dict], wake_id: str) -> dict:
    return next(item for item in wakes if item.get("id") == wake_id)


@pytest.mark.parametrize("wake_id", ["research_synthesis", "relay_stability_review"])
@pytest.mark.parametrize("source", ["probe", "shipped"])
def test_wakes_that_create_sources_state_the_outcome_threshold(source: str, wake_id: str) -> None:
    """A source God creates has to reach the wake that follows it.

    Real runs: all 8 sources God created at the review, and the second source
    one run created at a synthesis wake, had one stage and ``research_required``
    1.  They produced one research outcome and never reached the synthesis wake
    that needs two, so the world stopped evolving.
    """

    if source == "probe":
        wakes = _probe_template()["evolution"]["wakes"]
    else:
        wakes = yaml.safe_load(SHIPPED_EVOLUTION.read_text(encoding="utf-8"))["wakes"]
    threshold = _wake(wakes, "research_synthesis")["count"]

    text = _wake(wakes, wake_id)["result"]

    assert f"at least {threshold} verified research outcomes" in text
    assert f"only after {threshold} verified research outcomes" in text


def test_probe_declares_a_stagnation_fallback_unless_the_control_is_requested() -> None:
    evolution = _probe_template()["evolution"]
    wake = _wake(evolution["wakes"], "god_on_stagnation")

    assert evolution["stagnation"]["window"] and evolution["stagnation"]["max_interval"]
    assert wake["on"] == "world.stagnation" and wake["for"] == "god"
    assert {"query_history", "propose_entity", "wait"} <= set(wake["tools"])

    control = _probe_template(fallback=False)["evolution"]
    assert "stagnation" not in control
    assert all(item.get("on") != "world.stagnation" for item in control["wakes"])


def _probe_platform(tmp_path: Path, *, fallback: bool) -> Platform:
    name = "probe-fallback" if fallback else "probe-control"
    return Platform(create_instance_from_template(_probe_template(fallback=fallback), name, tmp_path / "runtime"))


def _minute(platform: Platform) -> int:
    return int(platform.store.repositories.metadata.get("world_minute", 0))


def test_idle_probe_world_wakes_god_through_the_stagnation_fallback(tmp_path: Path) -> None:
    platform = _probe_platform(tmp_path, fallback=True)
    try:
        assert _pending_structural_tasks(platform) == []

        assert _advance_idle(platform, limit_minutes=1440) is True

        assert [item["task_type"] for item in _pending_structural_tasks(platform)] == ["event_group:god_on_stagnation"]
    finally:
        platform.close()


def test_idle_probe_control_without_the_fallback_stays_absorbed(tmp_path: Path) -> None:
    platform = _probe_platform(tmp_path, fallback=False)
    try:
        assert _advance_idle(platform, limit_minutes=1440) is False
        assert _pending_structural_tasks(platform) == []
    finally:
        platform.close()


def test_provider_failure_ends_the_run_even_when_a_fallback_exists(tmp_path: Path) -> None:
    platform = _probe_platform(tmp_path, fallback=True)
    try:
        assert _can_continue(platform, {"status": "provider_failure"}, fallback=True) is False
        assert _minute(platform) == 0
    finally:
        platform.close()


def test_a_completed_npc_round_continues_without_passing_idle_time(tmp_path: Path) -> None:
    platform = _probe_platform(tmp_path, fallback=True)
    try:
        assert _can_continue(platform, {"status": "completed"}, fallback=True) is True
        assert _minute(platform) == 0
    finally:
        platform.close()


def test_an_idle_npc_ends_the_control_run_but_passes_time_when_a_fallback_exists(tmp_path: Path) -> None:
    control = _probe_platform(tmp_path, fallback=False)
    try:
        assert _can_continue(control, {"status": "no_npc_behavior"}, fallback=False) is False
        assert _minute(control) == 0
    finally:
        control.close()

    platform = _probe_platform(tmp_path, fallback=True)
    try:
        assert _can_continue(platform, {"status": "no_npc_behavior"}, fallback=True) is True
        assert 0 < _minute(platform) <= IDLE_LIMIT_MINUTES
        assert _pending_structural_tasks(platform)
    finally:
        platform.close()
