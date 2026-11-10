from pathlib import Path

from sim2.agents.engine import AgentEngine
from sim2.agents.tools import parse_tool_call
from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform
from sim2.simulation.runner import SimulationReport, SimulationRunner


class WaitProvider:
    def __init__(self):
        self.calls = []

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.calls.append({"system": system, "context": context, "tools": tools, "messages": messages})
        return {"intent_type": "wait", "reason": "The message adds no new decision."}


def _config() -> Path:
    return Path(__file__).parents[1] / "worlds" / "starter_village"


def test_flattened_function_call_is_normalized():
    assert parse_tool_call({"name": "wait", "arguments": {"reason": "No new work."}}) == (
        "wait",
        {"reason": "No new work."},
    )


def test_unbound_context_reference_does_not_leak_into_world_text():
    assert Platform._resolve_parameters({"topic": "context.topic"}, {}) == {"topic": None}


def test_world_relationship_proposal_uses_visible_names(tmp_path: Path):
    platform = Platform(create_instance(_config(), "relationship-proposal", tmp_path / "runtime"))
    try:
        result = platform.submit_world_change(
            "god",
            {
                "change_type": "relationship",
                "summary": "A verified exchange strengthens cooperation.",
                "relationship": {"from": "费兰", "to": "余歌", "type": "trust", "description": "Verified information exchange."},
            },
        )
        assert result["status"] == "accepted"
        assert any(item["from"] == "fei_lan" and item["to"] == "yu_ge" and item["type"] == "trust" for item in platform.store.list_relationships())
    finally:
        platform.close()


def test_instant_dialogue_view_is_compact_and_explicit(tmp_path: Path):
    platform = Platform(create_instance(_config(), "compact-dialogue", tmp_path / "runtime"))
    provider = WaitProvider()
    try:
        turn = AgentEngine(platform, provider).run(
            role="agent_decision",
            actor_id="yu_ge",
            task_type="instant:dialogue.completed",
            interaction={"from": "费兰", "topic": "route", "message": "The western road is open before dusk."},
        )
        prompt = turn.prompt_history[0]
        assert "<incoming_interaction>" in prompt
        assert "The western road is open before dusk." in prompt
        assert "<executable_behaviors>\n    - none; return wait" in prompt
        assert len(prompt) < 7000
        assert provider.calls[0]["tools"]
        assert {item["function"]["name"] for item in provider.calls[0]["tools"]} == {"send_message"}
    finally:
        platform.close()


def test_repeated_dialogue_message_is_suppressed_by_thread_scheduler(tmp_path: Path):
    platform = Platform(create_instance(_config(), "dialogue-thread", tmp_path / "runtime"))
    provider = WaitProvider()
    runner = SimulationRunner(platform, {"agent_decision": provider})
    report = SimulationReport(0, 0)
    try:
        first = platform.submit_dialogue("fei_lan", "yu_ge", "route", "The western road is open before dusk.")
        assert first["status"] == "accepted"
        runner._run_instant_interactions(report)
        assert len(provider.calls) == 1

        second = platform.submit_dialogue("fei_lan", "yu_ge", "route", "The western road is open before dusk.")
        assert second["status"] == "accepted"
        runner._run_instant_interactions(report)
        assert len(provider.calls) == 1
        assert report.interaction_replies_suppressed == 1
    finally:
        platform.close()
