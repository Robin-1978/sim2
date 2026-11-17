from __future__ import annotations

from typing import Any

from sim2.agents.session import ReactSession
from sim2.agents.tool_guards import read_signature, screen_tool_call


def make_session(**overrides: Any) -> ReactSession:
    values: dict[str, Any] = {
        "role": "agent_decision",
        "actor_id": "actor",
        "task_type": "decision",
        "task": "",
        "system_policy": "",
        "context": {},
        "initial_context": {},
        "executor": None,
        "event_ids": [],
        "state_attributes": [],
        "interaction_tools": None,
        "session_tools": None,
        "messages": [],
        "max_tool_calls": 3,
        "max_repeated_tool_calls": 2,
        "read_budget": 2,
    }
    values.update(overrides)
    return ReactSession(**values)


def screen(session: ReactSession, tool: str, arguments: dict[str, Any], *, is_read_tool: bool = False) -> dict[str, Any] | None:
    repeats = session.register_call(tool, arguments, is_read_tool=is_read_tool)
    return screen_tool_call(session, tool, arguments, is_read_tool=is_read_tool, repeats=repeats)


def test_register_call_counts_every_request_reads_and_exact_repeats():
    session = make_session()

    assert session.register_call("observe_nearby", {}, is_read_tool=True) == 1
    assert session.register_call("observe_nearby", {}, is_read_tool=True) == 2
    assert session.register_call("wait", {"reason": "rest"}, is_read_tool=False) == 1
    assert session.register_call("wait", {"reason": "other"}, is_read_tool=False) == 1

    assert session.tool_calls_used == 4
    assert session.read_tool_calls == 2


def test_guards_reject_in_the_documented_order():
    session = make_session(
        max_tool_calls=1, max_repeated_tool_calls=1, read_budget=0, interaction_tools=["wait"], session_tools=["wait"],
        read_signatures={read_signature("observe_nearby", {})},
    )
    session.register_call("observe_nearby", {}, is_read_tool=True)
    session.register_call("observe_nearby", {}, is_read_tool=True)

    def failed_check() -> str | None:
        result = screen_tool_call(session, "observe_nearby", {}, is_read_tool=True, repeats=session.repeated["observe_nearby:[]"])
        return result["failed_check"] if result else None

    assert failed_check() == "tool_budget"
    session.max_tool_calls = 10
    assert failed_check() == "read_budget"
    session.read_budget = 10
    assert failed_check() == "repeated_read"
    session.read_signatures.clear()
    assert failed_check() == "repeated_tool_call"
    session.max_repeated_tool_calls = 10
    assert failed_check() == "interaction_tool"
    session.interaction_tools = None
    assert failed_check() == "task_tool"
    session.session_tools = None
    assert failed_check() is None


def test_read_signature_ignores_wording_and_questions():
    assert read_signature("inspect_entity", {"entity": "  Old Well "}) == read_signature("inspect_entity", {"entity": "old well", "question": "why?"})
    assert read_signature("observe_nearby", {"question": "a"}) == read_signature("observe_nearby", {"question": "b"})
    assert read_signature("recall_memory", {"query": "Water  Supply", "question": "x"}) == read_signature("recall_memory", {"query": "water supply"})
    assert read_signature("inspect_entity", {"entity": "well"}) != read_signature("inspect_entity", {"entity": "road"})


def test_event_review_cannot_rerecord_its_triggering_event():
    narration = {"change_type": "event", "summary": "It happened."}
    typed = {"change_type": "event", "property": "supply", "summary": "Supply changed."}

    god_event = make_session(role="god", task_type="event:review")
    assert screen(god_event, "propose_world_change", narration)["failed_check"] == "event_already_recorded"
    assert screen(god_event, "propose_world_change", typed) is None
    assert screen(make_session(role="god", task_type="trigger:food_crisis"), "propose_world_change", narration)["failed_check"] == "event_already_recorded"
    assert screen(make_session(role="god", task_type="world_review"), "propose_world_change", narration) is None
    assert screen(make_session(role="story", task_type="event:review"), "propose_world_change", narration) is None


def test_finish_defaults_to_the_last_readable_response():
    session = make_session()
    session.last_response = {"readable": True}

    assert session.finish({"status": "wait"}, iterations=2).response == {"readable": True}
    assert session.finish({"status": "wait"}, iterations=2, response={"raw": 1}).response == {"raw": 1}
    assert session.finish({"status": "wait"}, iterations=3).iterations == 3
