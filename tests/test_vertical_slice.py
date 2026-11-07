from pathlib import Path
import pytest
from sandbox.intents.models import BehaviorIntent

from sandbox.agents.engine import AgentEngine
from sandbox.agents.tools import AgentToolExecutor
from sandbox.bootstrap.service import create_instance, validate_config
from sandbox.platform.runtime import Platform
from sandbox.prompting.renderer import render_context


class FakeProvider:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete_json(self, *, system, context):
        self.calls.append((system, context))
        return dict(self.response)


class SequenceProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete_json(self, *, system, context, tools=None):
        self.calls.append({"system": system, "context": context, "tools": tools})
        return dict(self.responses.pop(0))


def test_bootstrap_and_agent_intent(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "test-world", tmp_path / "runtime")
    platform = Platform(db_path)
    provider = FakeProvider(
        {
            "actor_id": "lu_xingzhou",
            "behavior_ref": "behavior:explore_greywood",
            "target": {},
            "parameters": {},
            "reason": "The area is not fully mapped.",
            "confidence": 0.9,
        }
    )
    try:
        turn = AgentEngine(platform, provider).run(role="agent_decision", actor_id="lu_xingzhou")
        assert turn.result["status"] == "accepted"
        assert turn.result["behavior_ref"] == "behavior:explore_greywood"
        assert turn.iterations == 1
        assert provider.calls
        prompt = provider.calls[0][1]
        assert "# OPTIONS" in prompt
        assert "collect" not in prompt.lower()
        assert "actor.can" not in prompt
    finally:
        platform.close()


def test_context_uses_role_tools_and_plain_sections():
    class View:
        pass

    context = {
        "self": {"name": "A", "role": "watcher", "status": "idle", "location": "gate", "goal": "protect"},
        "drives": [],
        "observation": {"facts": ["A fresh mark is visible."], "source": "direct sight"},
        "direct_relationships": [],
        "known_facts": [],
        "active_task": {},
        "available_behaviors": [{"ref": "behavior:patrol_route", "name": "Patrol Route", "when": ["A route needs inspection."], "time": "96 minutes", "result": ["A report is recorded."]}],
        "tools": [{"name": "request_behavior", "purpose": "Ask Platform to start one offered behavior."}],
    }
    prompt = render_context(context, "Choose a behavior.")
    assert "# TOOLS" in prompt
    assert "request_behavior" in prompt
    assert "A fresh mark is visible." in prompt


def test_react_session_reads_tool_result_before_selecting_behavior(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "react-world", tmp_path / "runtime")
    platform = Platform(db_path)
    provider = SequenceProvider([
        {"tool_call": {"name": "observe_nearby", "arguments": {"question": "Who is nearby?"}}},
        {
            "behavior_ref": "behavior:explore_greywood",
            "target": {},
            "parameters": {},
            "reason": "The local view confirms exploration is useful.",
            "confidence": 0.8,
        },
    ])
    try:
        turn = AgentEngine(platform, provider).run(role="agent_decision", actor_id="lu_xingzhou")
        assert turn.result["status"] == "accepted"
        assert turn.iterations == 2
        assert "TOOL RESULTS" in provider.calls[1]["context"]
        assert provider.calls[0]["tools"]
        assert provider.calls[0]["tools"][0]["function"]["name"] == "observe_nearby"
        assert any(step["step"] == "tool" for step in turn.transcript)
    finally:
        platform.close()


def test_react_session_retries_after_platform_rejection(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "retry-world", tmp_path / "runtime")
    platform = Platform(db_path)
    provider = SequenceProvider([
        {"behavior_ref": "behavior:manage_ration", "reason": "Not available to this character."},
        {"behavior_ref": "behavior:explore_greywood", "reason": "Use an offered behavior."},
    ])
    try:
        turn = AgentEngine(platform, provider).run(role="agent_decision", actor_id="lu_xingzhou")
        assert turn.result["status"] == "accepted"
        assert turn.iterations == 2
        assert "not available" in provider.calls[1]["context"].lower()
    finally:
        platform.close()


def test_reflection_intent_is_committed_by_platform(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "reflection-world", tmp_path / "runtime")
    platform = Platform(db_path)
    provider = SequenceProvider([
        {
            "intent_type": "record_experience",
            "event_ref": "event:test",
            "summary": "The first observation was confirmed by a nearby witness.",
            "confidence": 0.7,
        }
    ])
    try:
        turn = AgentEngine(platform, provider).run(role="agent_reflection", actor_id="lu_xingzhou", task_type="daily_reflection")
        assert turn.result["status"] == "accepted"
        memories = platform.store.list_memories("lu_xingzhou")
        assert memories[0]["summary"].startswith("The first observation")
    finally:
        platform.close()


def test_decision_session_does_not_call_llm_while_behavior_is_active(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "active-world", tmp_path / "runtime")
    platform = Platform(db_path)
    first = SequenceProvider([{"behavior_ref": "behavior:explore_greywood"}])
    second = SequenceProvider([{"behavior_ref": "behavior:patrol_route"}])
    try:
        accepted = AgentEngine(platform, first).run(role="agent_decision", actor_id="lu_xingzhou")
        skipped = AgentEngine(platform, second).run(role="agent_decision", actor_id="lu_xingzhou")
        assert accepted.result["status"] == "accepted"
        assert skipped.result["status"] == "skipped"
        assert skipped.iterations == 0
        assert second.calls == []
    finally:
        platform.close()


def test_behavior_intent_is_idempotent(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "idempotent-world", tmp_path / "runtime")
    platform = Platform(db_path)
    intent = BehaviorIntent(
        actor_id="lu_xingzhou",
        behavior_ref="behavior:explore_greywood",
        target={},
        parameters={},
        reason="Explore the unknown area.",
        confidence=0.8,
        intent_id="stable-intent",
    )
    try:
        first = platform.submit_behavior(intent)
        second = platform.submit_behavior(intent)
        assert first["status"] == "accepted"
        assert second == first
    finally:
        platform.close()


def test_read_tool_respects_agent_visibility(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "visibility-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        executor = AgentToolExecutor(platform, role="agent_decision", actor_id="lu_xingzhou")
        result = executor.execute("inspect_entity", {"entity_id": "old_pact_tablet"})
        assert result["status"] == "rejected"
        assert result["failed_check"] == "visibility"
    finally:
        platform.close()


def test_bootstrap_imports_generic_relationships_and_knowledge(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "import-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        assert platform.store.get_entity("village_council")["entity_type"] == "faction"
        assert any(row["person"] == "he_yan" for row in platform.store.get_relationships("lin_bo"))
        facts = platform.store.list_knowledge_facts("lu_xingzhou")
        assert any(fact["fact_key"] == "public:village" for fact in facts)
        assert platform.store.get_meta("config_version") == "starter-village-0.1.0"
    finally:
        platform.close()


def test_invalid_reference_fails_before_sqlite_creation(tmp_path: Path):
    source = Path(__file__).parents[1] / "worlds" / "starter_village"
    copied = tmp_path / "invalid-world"
    copied.mkdir()
    for path in source.iterdir():
        if path.is_file():
            copied.joinpath(path.name).write_bytes(path.read_bytes())
    behavior_file = copied / "behavior_trees.yaml"
    behavior_file.write_text(behavior_file.read_text().replace("action_id: explore_area", "action_id: missing_action", 1), encoding="utf-8")
    report = validate_config(copied)
    assert report["valid"] is False
    assert any("missing_action" in error for error in report["errors"])
    with pytest.raises(ValueError):
        create_instance(copied, "invalid", tmp_path / "runtime")
    assert not (tmp_path / "runtime" / "instances" / "invalid" / "world.sqlite3").exists()


def test_platform_advances_persisted_action_progress_without_llm(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "progress-world", tmp_path / "runtime")
    platform = Platform(db_path)
    intent = BehaviorIntent(
        actor_id="lu_xingzhou",
        behavior_ref="behavior:explore_greywood",
        target={},
        parameters={},
        reason="Map the unknown area.",
        confidence=0.9,
        intent_id="progress-intent",
    )
    try:
        assert platform.submit_behavior(intent)["status"] == "accepted"
        first = platform.advance(1)
        assert first["to_minute"] == 1
        state = platform.store.get_agent("lu_xingzhou")["initial_state"]
        assert state["status"] == "executing"
        assert state["active_action_index"] == 1
        platform.advance(1)
        state = platform.store.get_agent("lu_xingzhou")["initial_state"]
        assert state["active_action"] == "explore_area"
        assert state["remaining_minutes"] == 239
        row = platform.store.connection.execute("SELECT status,remaining_minutes FROM action_instance WHERE execution_id=? AND action_id=?", ("progress-intent", "explore_area")).fetchone()
        assert row["status"] == "running"
        assert row["remaining_minutes"] == 239
    finally:
        platform.close()


def test_platform_interrupt_updates_bt_and_action_instances(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "interrupt-world", tmp_path / "runtime")
    platform = Platform(db_path)
    intent = BehaviorIntent(
        actor_id="lu_xingzhou", behavior_ref="behavior:explore_greywood", target={}, parameters={},
        reason="Explore", confidence=0.9, intent_id="interrupt-intent",
    )
    try:
        platform.submit_behavior(intent)
        platform.advance(2)
        paused = platform.interrupt_behavior("lu_xingzhou", "urgent boundary signal", hard=False)
        assert paused["status"] == "paused"
        state = platform.store.get_agent("lu_xingzhou")["initial_state"]
        assert state["status"] == "paused"
        row = platform.store.connection.execute("SELECT status FROM action_instance WHERE execution_id=? AND status=?", ("interrupt-intent", "paused")).fetchone()
        assert row is not None
        cancelled = platform.interrupt_behavior("lu_xingzhou", "threat confirmed", hard=True)
        assert cancelled["status"] == "cancelled"
        assert platform.store.get_agent("lu_xingzhou")["initial_state"]["status"] == "idle"
    finally:
        platform.close()
