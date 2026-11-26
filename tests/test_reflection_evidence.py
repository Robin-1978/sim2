"""Both reflection gates accept the same evidence: an outcome, a memory or one meaningful event."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sim2.agents.intent_gateway import AgentIntentGateway
from sim2.agents.reflection_policy import MEANINGFUL_REFLECTION_EVENTS
from sim2.agents.tool_executor import AgentToolExecutor
from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform
from sim2.prompting.renderer import render_context

EXPECTED_EVENTS = {
    "behavior.completed",
    "behavior.failed",
    "behavior.interrupted",
    "dialogue.completed",
    "trade.completed",
    "trade.offer_created",
    "trade.countered",
    "exploration.completed",
    "research.completed",
    "object.recovered",
    "relationship.changed",
    "world.change.world_property",
}
# Events a world's own actions record, or a ledger change: not evidence of an experience.
OTHER_EVENTS = ("gate.inspected", "resource.changed")


def _tool_result(*, event_type: str | None = None, outcomes: bool = False, memories: bool = False) -> dict[str, Any]:
    """Call record_experience as a reflecting Agent whose recent history is exactly the given evidence."""

    events = [{"event_type": event_type}] if event_type else []
    repositories = SimpleNamespace(
        events=SimpleNamespace(
            recent_outcomes_for_actor=lambda actor_id, limit: [{"result": "done"}] if outcomes else [],
            recent_events_for_actor=lambda actor_id, limit: events,
        ),
        memory=SimpleNamespace(list_memories=lambda actor_id, limit: [{"id": 1}] if memories else []),
    )
    platform = SimpleNamespace(
        store=SimpleNamespace(repositories=repositories),
        submit_experience=lambda *args: {"status": "accepted"},
    )
    executor = AgentToolExecutor(platform, role="agent_reflection", actor_id="a")
    return executor.execute("record_experience", {"summary": "Waiting for the tide was right."})


def _view_has_evidence(**context: Any) -> bool:
    return AgentIntentGateway.reflection_has_evidence(context)


def test_the_meaningful_reflection_events_are_the_documented_set():
    assert MEANINGFUL_REFLECTION_EVENTS == EXPECTED_EVENTS


def test_both_gates_accept_every_meaningful_event():
    for event_type in sorted(EXPECTED_EVENTS):
        assert _tool_result(event_type=event_type)["status"] == "accepted", event_type
        assert _view_has_evidence(recent_actions=[{"event_type": event_type}]), event_type


def test_both_gates_refuse_other_events_and_say_why():
    for event_type in OTHER_EVENTS:
        refused = _tool_result(event_type=event_type)
        assert refused["status"] == "rejected" and refused["failed_check"] == "reflection_no_observed_result", event_type
        assert not _view_has_evidence(recent_actions=[{"event_type": event_type}]), event_type
    assert _tool_result()["status"] == "rejected"
    assert not _view_has_evidence()


def test_an_outcome_or_a_memory_is_enough_for_both_gates():
    assert _tool_result(outcomes=True)["status"] == "accepted"
    assert _tool_result(memories=True)["status"] == "accepted"
    assert _view_has_evidence(recent_outcomes=[{"result": "done"}])
    assert _view_has_evidence(memories=[{"id": 1}])


def test_reflection_can_cite_an_old_visible_outcome_by_id(tmp_path: Path) -> None:
    world = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(world, "reflection-event-reference", tmp_path / "runtime"))
    try:
        events = platform.store.repositories.events
        with platform.store.transaction():
            event_id = events.append_event("behavior.completed", "lu_xingzhou", 0, {"result": "Water tested."})
            hidden_id = events.append_event("behavior.completed", "fei_lan", 0, {"result": "Private review."})
            for minute in range(1, 502):
                events.append_event("world_minute.advanced", None, minute, {"minute": minute})

        assert all(event["event_id"] != event_id for event in events.recent_events(limit=500))
        view = platform.view.build_decision_view("lu_xingzhou", role="agent_reflection")
        assert f"event:{event_id}" in render_context(view, "Reflect on an outcome.")
        summary = "Testing the water confirmed a result worth recalling."
        hidden = platform.submit_experience("lu_xingzhou", f"event:{hidden_id}", summary, 0.9, "confirmed")
        assert hidden["failed_check"] == "claim_evidence"
        visible = platform.submit_experience("lu_xingzhou", f"event:{event_id}", summary, 0.9, "confirmed")
        assert visible["status"] == "accepted"
        assert platform.store.repositories.memory.list_memories("lu_xingzhou", limit=1)[0]["source_event_id"] == event_id
    finally:
        platform.close()
