"""Both reflection gates accept the same evidence: an outcome, a memory or one meaningful event."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from sim2.agents.intent_gateway import AgentIntentGateway
from sim2.agents.reflection_policy import MEANINGFUL_REFLECTION_EVENTS
from sim2.agents.tool_executor import AgentToolExecutor

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
