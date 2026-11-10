from pathlib import Path

from sim2.agents.engine import AgentEngine
from sim2.agents.tools import parse_tool_call
from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform
from sim2.prompting.renderer import render_context
from sim2.simulation.runner import SimulationReport, SimulationRunner
from sim2.actions.capacity import check_action_capacity
from sim2.actions.conditions import ConditionEngine
from sim2.actions.effects import EffectPipeline


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


def test_prompt_formats_float_state_without_binary_noise():
    prompt = render_context(
        {
            "role": "agent_decision",
            "world_time": {"minute": 1, "label": "Day 1, 00:01"},
            "self": {"attributes": [{"label": "Satiety", "value": 81.200000000004}]},
            "drives": [{"id": "safety", "urgency": 0.020000000000000004, "reason": "test"}],
            "executable_behaviors": [],
        },
        "Choose one.",
    )
    assert "81.2" in prompt
    assert "0.02" in prompt
    assert "81.200000000004" not in prompt
    assert "0.020000000000000004" not in prompt


def test_world_defined_attributes_do_not_require_health_or_energy_names():
    attributes = [
        {"id": "vitality", "kind": "state", "min": 0, "max": 10, "default": 10, "vital": True},
        {"id": "focus", "kind": "state", "min": 0, "max": 20, "default": 20, "capacity": True, "cost_mode": "decrease"},
    ]
    rules = {"vital_attribute": "vitality", "capacity_attribute": "focus"}
    agent = {"initial_state": {"vitality": 10, "focus": 5, "conditions": []}, "needs": {}, "inventory": {}}
    conditions = ConditionEngine(rules)
    assert conditions.evaluate("actor.alive", agent=agent)
    assert conditions.evaluate("actor.exhausted", agent={**agent, "initial_state": {"vitality": 10, "focus": 0, "conditions": []}})
    result = EffectPipeline(attributes).apply(agent, {"type": "attribute_delta", "attribute": "vitality", "delta": -3})
    assert result["value"] == 7
    ok, reason = check_action_capacity(agent, {"costs": {"per_minute": {"focus": 1}}}, 5, attributes)
    assert ok is True, reason


def test_experience_records_claim_provenance(tmp_path: Path):
    platform = Platform(create_instance(_config(), "experience-provenance", tmp_path / "runtime"))
    try:
        result = platform.submit_experience("yu_ge", "event:dialogue", "The participant promised to bring a route note.", 0.6, "promised")
        assert result["status"] == "accepted"
        memory = platform.store.list_memories("yu_ge", limit=5)[0]
        assert memory["claim_status"] == "promised"
    finally:
        platform.close()


def test_social_institution_is_absent_until_proposed_and_activated(tmp_path: Path):
    platform = Platform(create_instance(_config(), "emergent-institution", tmp_path / "runtime"))
    try:
        assert platform.store.list_institutions() == []
        assert platform.store.connection.execute("SELECT COUNT(*) AS n FROM institution_ledger_entry").fetchone()["n"] == 0
        proposed = platform.submit_institution_proposal(
            "liang_he",
            {
                "name": "Public Work Ledger",
                "purpose": "Record verified public work before deciding how to distribute scarce food.",
                "scope": "village",
                "ledger": {"period": "week", "selectors": [{"event_types": ["behavior.completed"], "category": "public_work", "points": 1}]},
                "policy": {"allocation": {"minimum_per_subject": 1, "contribution_share": 0.5}},
            },
        )
        assert proposed["status"] == "accepted"
        assert platform.store.list_institutions(status="active") == []
        activated = platform.activate_institution("god", "Public Work Ledger", "The proposal has an observed food-allocation problem.")
        assert activated["status"] == "accepted"
        active = platform.store.list_institutions(status="active")[0]
        before = platform.calculate_institution_allocation("Public Work Ledger", ["liang_he", "lin_bo"], 10, "week:0")
        assert before["status"] == "accepted"
        assert all(row["total"] == 1 for row in before["allocations"])
        event_id = platform.store.append_event("behavior.completed", "liang_he", 20, {"behavior_id": "gather_resource", "result": "resource_collected"})
        platform._record_active_institution_entries(event_id, "behavior.completed", "liang_he", 20, {"behavior_id": "gather_resource", "result": "resource_collected"})
        totals = platform.store.institution_ledger_totals(active["institution_id"])
        assert totals[0]["subject_id"] == "liang_he"
        assert totals[0]["total"] == 1
    finally:
        platform.close()


def test_story_rejects_unverified_public_movement_claim(tmp_path: Path):
    platform = Platform(create_instance(_config(), "story-evidence", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            platform.store.append_event(
                "object.recovered",
                "lu_xingzhou",
                10,
                {"object_id": "caravan_ledger", "fact": "The ledger confirms a safe northern boundary is required."},
            )
        result = platform.submit_story_beat(
            "story",
            {
                "summary": "The recovered ledger is brought to the north gate and read aloud.",
                "fact": "The ledger confirms a safe northern boundary is required.",
                "goal_hint": "Check the northern boundary.",
                "audience": ["陆行舟"],
            },
        )
        assert result["status"] == "rejected"
        assert result["failed_check"] == "story_unverified_action_claim"
    finally:
        platform.close()


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
