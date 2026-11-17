"""The deterministic week-liveness check keeps a refused or quiet world reachable and evolving."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
_SPEC = importlib.util.spec_from_file_location("sim2_verify_week_liveness", ROOT / "scripts" / "verify_week_liveness.py")
assert _SPEC is not None and _SPEC.loader is not None
liveness = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(liveness)

CHARACTER_CONTEXT = """<executable_behaviors>
  <behavior>
    parameters: source_id (required string): Exact visible source name.
    parameter_options: {'source_id': [{'name': 'Signal Archive', 'location': 'Relay Lab'}]}
    choice: Study Configured Source
  </behavior>
</executable_behaviors>"""
GOD_TOOLS = [{"function": {"name": "propose_entity"}}, {"function": {"name": "wait"}}]


def test_scripted_character_fills_a_required_parameter_from_the_offered_options() -> None:
    agents = liveness.ScriptedAgents(0, "Mira", "Relay Lab")

    response = agents.complete_json(system="You are the decision phase of one individual Agent.", context=CHARACTER_CONTEXT)

    assert response["choice"] == "Study Configured Source"
    assert response["parameters"] == {"source_id": "Signal Archive"}


def test_scripted_character_waits_when_a_required_parameter_has_no_option() -> None:
    agents = liveness.ScriptedAgents(0, "Mira", "Relay Lab")
    context = CHARACTER_CONTEXT.replace("parameter_options: {'source_id': [{'name': 'Signal Archive', 'location': 'Relay Lab'}]}", "parameter_options: {}")

    assert agents.complete_json(system="decision", context=context)["intent_type"] == "wait"


def test_scripted_god_declines_first_then_proposes_sources_that_share_no_words() -> None:
    agents = liveness.ScriptedAgents(1, "Mira", "Relay Lab")
    system = "You are the world-level evolution Agent."

    declined = agents.complete_json(system=system, context="<world/>", tools=GOD_TOOLS)
    first = agents.complete_json(system=system, context="<world/>", tools=GOD_TOOLS)
    second = agents.complete_json(system=system, context="<world/>", tools=GOD_TOOLS)

    assert declined["intent_type"] == "wait"
    assert first["name"] == second["name"] == "propose_entity"
    words = [set(" ".join(str(item) for item in call["arguments"]["state"]["research_discoveries"]).split()) for call in (first, second)]
    assert not words[0] & words[1]
    assert first["arguments"]["state"]["research_assignees"] == ["Mira"]
    assert first["arguments"]["location"] == "Relay Lab"


def test_scripted_god_waits_when_the_wake_does_not_offer_propose_entity() -> None:
    agents = liveness.ScriptedAgents(0, "Mira", "Relay Lab")

    response = agents.complete_json(system="You are the world-level evolution Agent.", context="<world/>", tools=[{"function": {"name": "wait"}}])

    assert response["intent_type"] == "wait"


def _week(progress: list[int], *, stagnation: list[int] | None = None, accepted: list[int] | None = None, max_interval: int = 0) -> dict[str, Any]:
    return {
        "days": [{"progress": count} for count in progress],
        "stagnation": stagnation or [],
        "accepted": accepted or [],
        "max_interval": max_interval,
    }


@pytest.mark.parametrize(
    ("kind", "week", "expected"),
    [
        ("accepts", _week([3, 2, 1]), []),
        ("accepts", _week([3, 0, 1]), ["day 2 has no progress event"]),
        ("declines_two", _week([2, 0, 5], stagnation=[2880], accepted=[2880]), []),
        ("declines_two", _week([2, 0, 5]), ["no world.stagnation event"]),
        ("declines_two", _week([2, 0, 5], stagnation=[2880], accepted=[10]), ["God's source was not accepted after the first stagnation event"]),
        ("declines_two", _week([2, 0, 0], stagnation=[2880], accepted=[2880]), ["the last day has no progress event"]),
        ("declines_all", _week([0], stagnation=[2880, 4320, 7200], max_interval=10080), []),
        ("declines_all", _week([0], stagnation=[2880]), ["fewer than two stagnation events in the week"]),
        ("declines_all", _week([0], stagnation=[1000, 3000, 4000]), ["the asks come at shrinking intervals"]),
        ("declines_all", _week([0], stagnation=[1000, 9000], max_interval=5000), ["an interval exceeds max_interval 5000"]),
    ],
)
def test_verdicts_name_what_is_wrong_with_a_week(kind: str, week: dict[str, Any], expected: list[str]) -> None:
    assert liveness.problems(kind, week) == expected


def test_a_refusing_god_is_asked_again_and_the_world_resumes_when_it_accepts() -> None:
    week = liveness.run_week(ROOT / "worlds" / "aster_relay", declines=2, minutes=4 * liveness.DAY)

    assert liveness.problems("declines_two", week) == []
    asked_at = week["stagnation"][0]
    assert any(minute > asked_at for minute in week["progress"]), "no research progress followed the stagnation ask"
    assert week["errors"]["llm_errors"] == 0
    assert set(week["rejections"]) <= {"entity_unreachable"}, "the scripted God may only meet the retryable reachability check"
