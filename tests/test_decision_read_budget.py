from __future__ import annotations

from pathlib import Path

from sim2.agents.engine import AgentEngine
from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform


class ReadThenChooseProvider:
    max_agent_iterations = 5
    max_read_tool_calls = 3

    def __init__(self, choice: str):
        self.choice = choice
        self.calls: list[dict] = []
        self.responses = [
            {"tool_call": {"name": "observe_nearby", "arguments": {"question": "What is visible here?"}}},
            {"tool_call": {"name": "inspect_entity", "arguments": {"entity": "旧商路", "question": "What is publicly visible?"}}},
            {"tool_call": {"name": "preview_behavior", "arguments": {"choice": choice}}},
            {"tool_call": {"name": "request_behavior", "arguments": {"choice": choice, "reason": "The listed behavior is executable and advances the current goal."}}},
        ]

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.calls.append({"context": context, "tools": tools or []})
        return self.responses.pop(0)


class ReasonOnlyWaitProvider:
    max_agent_iterations = 3
    max_read_tool_calls = 3

    def complete_json(self, *, system, context, tools=None, messages=None):
        return {"reason": "No executable behaviors are available; wait for the world to change."}


def test_decision_can_read_several_distinct_facts_then_must_choose(tmp_path: Path) -> None:
    world = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(world, "read-then-choose", tmp_path / "runtime"))
    try:
        choice = platform.view.build_decision_view("luo_shan", role="agent_decision")["executable_behaviors"][0]["choice"]
        provider = ReadThenChooseProvider(choice)
        turn = AgentEngine(platform, provider).run(
            role="agent_decision",
            actor_id="luo_shan",
            task_type="agent_plan_selection",
        )
        assert turn.result["status"] == "accepted", turn.result
        assert turn.iterations == 4
        assert "Decision guidance:" in provider.calls[1]["context"]
        final_tools = {item["function"]["name"] for item in provider.calls[-1]["tools"]}
        assert "request_behavior" in final_tools
        assert "observe_nearby" not in final_tools
        assert "inspect_entity" not in final_tools
        assert "preview_behavior" not in final_tools
    finally:
        platform.close()


def test_reason_only_no_behavior_response_closes_as_wait(tmp_path: Path) -> None:
    world = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(world, "reason-only-wait", tmp_path / "runtime"))
    try:
        engine = AgentEngine(platform, ReasonOnlyWaitProvider())
        result = engine._submit_response(
            "agent_decision",
            "luo_shan",
            {"reason": "No executable behaviors are available; wait for the world to change."},
            context={"executable_behaviors": []},
            task_type="agent_plan_selection",
        )
        assert result["status"] == "wait"
    finally:
        platform.close()

