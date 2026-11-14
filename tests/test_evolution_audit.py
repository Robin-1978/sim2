"""Tests for the world-neutral evolution audit harness."""

from __future__ import annotations

from scripts.audit_dynamic_evolution_loop import _ready


class _TaskSource:
    def __init__(self, tasks: list[dict]) -> None:
        self.tasks = tasks

    def ready_evolution_tasks(self) -> list[dict]:
        return list(self.tasks)


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
