from pathlib import Path
import pytest
import json
import threading
from urllib.request import Request, urlopen
from sandbox.intents.models import BehaviorIntent

from sandbox.agents.engine import AgentEngine
from sandbox.agents.tools import AgentToolExecutor
from sandbox.actions.conditions import ConditionEngine
from sandbox.actions.effects import EffectPipeline
from sandbox.api.service import ControlService
from sandbox.api.http import ControlHTTPServer
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
    platform.submit_experience("lu_xingzhou", "event:prior", "A prior route report was recorded.", 0.8)
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
        assert "Recent experience" in provider.calls[0]["context"]
    finally:
        platform.close()


def test_reflection_can_update_goal_through_platform(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "goal-world", tmp_path / "runtime")
    platform = Platform(db_path)
    provider = SequenceProvider([{"intent_type": "update_goal", "statement": "Confirm the safest route before the next fog cycle.", "priority": 0.9}])
    try:
        turn = AgentEngine(platform, provider).run(role="agent_reflection", actor_id="lu_xingzhou", task_type="goal_review")
        assert turn.result["status"] == "accepted"
        assert platform.store.get_agent("lu_xingzhou")["ultimate_goal"].startswith("Confirm the safest route")
    finally:
        platform.close()


def test_memory_fts_and_bounded_compaction(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "memory-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        for index in range(30):
            platform.submit_experience("lu_xingzhou", f"event:{index}", f"The river clue {index} was recorded.", 0.8)
        recalled = platform.memory.recall("lu_xingzhou", "river clue", limit=8)
        assert recalled["memories"]
        compacted = platform.memory.compact("lu_xingzhou", keep=10)
        assert compacted["status"] == "compacted"
        assert any(item["type"] == "summary" for item in platform.store.list_memories("lu_xingzhou", limit=50))
        forgotten = platform.memory.forget("lu_xingzhou", maximum=12)
        assert forgotten["status"] in {"unchanged", "forgotten"}
    finally:
        platform.close()


def test_successful_recipe_records_capability_history(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "capability-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        agent = platform.store.get_agent("liang_he")
        agent["inventory"].update({"grain": 1, "clean_water": 1})
        with platform.store.transaction():
            platform.store.update_agent("liang_he", agent)
        intent = BehaviorIntent("liang_he", "behavior:craft_meal", {}, {}, "Prepare food", 0.8, "capability-intent")
        assert platform.submit_behavior(intent)["status"] == "accepted"
        platform.advance(60)
        history = platform.store.list_capability_progress("liang_he")
        assert any(item["capability"] == "cooking" and item["delta"] > 0 for item in history)
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


def test_generic_condition_and_effect_components():
    agent = {"role": "pathfinder", "capabilities": {"exploration": 0.8}, "initial_state": {"health": 100, "stamina": 20, "conditions": []}, "inventory": {}}
    conditions = ConditionEngine()
    assert conditions.evaluate("actor.alive and actor.can_explore", agent=agent)
    assert not conditions.evaluate("actor.can_fly", agent=agent)
    agent["faction_ids"] = ["village_watch"]
    assert conditions.evaluate("actor.faction_id not_in [root_keepers]", agent=agent)
    effects = EffectPipeline()
    result = effects.apply(agent, {"type": "inventory_add", "item": "map", "quantity": 1})
    assert result["applied"] is True
    assert agent["inventory"]["map"] == 1
    effects.apply(agent, {"type": "need_delta", "need": "fatigue", "delta": 5})
    assert agent["initial_state"]["fatigue"] == 5


def test_exploration_completion_persists_result_and_knowledge(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "knowledge-world", tmp_path / "runtime")
    platform = Platform(db_path)
    intent = BehaviorIntent(
        actor_id="lu_xingzhou", behavior_ref="behavior:explore_greywood", target={}, parameters={},
        reason="Survey the area", confidence=0.9, intent_id="knowledge-intent",
    )
    try:
        platform.submit_behavior(intent)
        report = platform.advance(241)
        assert report["to_minute"] == 241
        fact = platform.store.connection.execute("SELECT area_id FROM exploration_result WHERE agent_id=?", ("lu_xingzhou",)).fetchone()
        assert fact["area_id"] == "greywood_edge"
        assert any(item["fact_key"] == "explored:greywood_edge" for item in platform.store.list_knowledge_facts("lu_xingzhou"))
    finally:
        platform.close()


def test_evolution_windows_are_scheduled_without_llm_calls(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "evolution-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        platform.store.connection.execute("UPDATE evolution_task SET due_minute=2 WHERE task_id='reflection:lu_xingzhou'")
        platform.store.connection.commit()
        assert platform.ready_evolution_tasks() == []
        platform.advance(2)
        tasks = platform.ready_evolution_tasks()
        task = next(item for item in tasks if item["task_id"] == "reflection:lu_xingzhou")
        assert task["task_type"] == "daily_reflection"
        claimed = platform.claim_evolution_task(task["task_id"])
        assert claimed["task_id"] == task["task_id"]
        platform.complete_evolution_task(task["task_id"])
        assert all(item["task_id"] != task["task_id"] for item in platform.ready_evolution_tasks())
    finally:
        platform.close()


def test_god_proposal_creates_entity_only_through_platform(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "god-world", tmp_path / "runtime")
    platform = Platform(db_path)
    provider = SequenceProvider([
        {"tool_call": {"name": "propose_entity", "arguments": {"entity_type": "resource_node", "name": "New Spring", "location_id": "river_bank", "description": "A newly revealed water source."}}}
    ])
    try:
        turn = AgentEngine(platform, provider).run(role="god", actor_id="god")
        assert turn.result["status"] == "accepted"
        entity = platform.store.get_entity(turn.result["entity_id"])
        assert entity["entity_type"] == "resource_node"
    finally:
        platform.close()


def test_world_builder_validates_then_submits_template(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "builder-world", tmp_path / "runtime")
    platform = Platform(db_path)
    template = {
        "world": {"id": "small_world", "title": "Small World"},
        "actions": [], "behavior_trees": [], "agents": [],
    }
    provider = SequenceProvider([
        {"tool_call": {"name": "validate_world_template", "arguments": {"template": template}}},
        {"tool_call": {"name": "submit_world_template", "arguments": {"template": template}}},
    ])
    try:
        turn = AgentEngine(platform, provider).run(role="world_builder", actor_id="world_builder")
        assert turn.result["status"] == "accepted"
        assert turn.iterations == 2
    finally:
        platform.close()


def test_validated_template_creates_a_separate_instance(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    source_db = create_instance(config, "template-source", tmp_path / "runtime")
    platform = Platform(source_db)
    template = {
        "template_version": "test-template-1",
        "world": {"id": "generated_world", "title": "Generated World", "premise": "A small test world."},
        "locations": [{"id": "entry", "name": "Entry", "routes": []}],
        "actions": [], "behavior_trees": [], "agents": [], "factions": [],
    }
    try:
        result = platform.bootstrap_world_from_template(template, "generated-instance", tmp_path / "generated")
        assert result["status"] == "accepted"
        generated = Platform(Path(result["db_path"]))
        try:
            assert generated.store.get_meta("world_id") == "generated_world"
            assert generated.store.get_meta("config_version") == "test-template-1"
            assert generated.store.get_entity("entry")["entity_type"] == "location"
        finally:
            generated.close()
    finally:
        platform.close()


def test_control_service_pause_step_snapshot_and_audit(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "control-world", tmp_path / "runtime")
    service = ControlService(db_path)
    snapshot = tmp_path / "snapshot.sqlite3"
    try:
        assert service.status()["status"] == "running"
        service.pause("test")
        assert service.advance(2)["status"] == "paused"
        assert service.step()["to_minute"] == 1
        service.resume("test")
        injected = service.inject("operator.signal", {"kind": "test"})
        assert injected["status"] == "accepted"
        metrics = service.metrics()
        assert metrics["world_minute"] == 1
        service.snapshot(snapshot)
        assert snapshot.exists()
        audit = service.audit()
        assert any(event["event_type"] == "operator.signal" for event in audit["events"])
    finally:
        service.close()


def test_player_entry_and_snapshot_restore_use_platform(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "player-world", tmp_path / "runtime")
    platform = Platform(db_path)
    snapshot = tmp_path / "player-snapshot.sqlite3"
    try:
        entered = platform.enter_player("player_one", "courier", name="Ari")
        assert entered["status"] == "accepted"
        assert platform.store.get_agent("player_one")["initial_state"]["location_id"] == "river_bridge"
        platform.snapshot(snapshot)
        platform.advance(3)
        assert platform.status()["world_minute"] == 3
        restored = platform.restore_snapshot(snapshot)
        assert restored["world_minute"] == 0
        assert platform.store.get_agent("player_one")["name"] == "Ari"
    finally:
        platform.close()


def test_local_http_control_adapter_uses_platform(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "http-world", tmp_path / "runtime")
    server = ControlHTTPServer(("127.0.0.1", 0), db_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/status") as response:
            status = json.loads(response.read())
        assert status["status"] == "running"
        request = Request(base + "/pause", data=b'{"reason":"test"}', headers={"content-type": "application/json"}, method="POST")
        with urlopen(request) as response:
            paused = json.loads(response.read())
        assert paused["status"] == "paused"
    finally:
        server.close()
        thread.join(timeout=2)


def test_same_config_and_intent_replay_same_event_sequence(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    first_db = create_instance(config, "replay-a", tmp_path / "runtime")
    second_db = create_instance(config, "replay-b", tmp_path / "runtime")
    first = Platform(first_db)
    second = Platform(second_db)
    intent_data = dict(actor_id="lu_xingzhou", behavior_ref="behavior:explore_greywood", target={}, parameters={}, reason="Replay", confidence=0.8, intent_id="replay-intent")
    try:
        first.submit_behavior(BehaviorIntent(**intent_data))
        second.submit_behavior(BehaviorIntent(**intent_data))
        first.advance(3)
        second.advance(3)
        select = "SELECT event_type,actor_id,world_minute,data_json FROM event_log ORDER BY event_id"
        first_events = [tuple(row) for row in first.store.connection.execute(select).fetchall()]
        second_events = [tuple(row) for row in second.store.connection.execute(select).fetchall()]
        assert first_events == second_events
    finally:
        first.close()
        second.close()
