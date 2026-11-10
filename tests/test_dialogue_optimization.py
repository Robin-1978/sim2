from pathlib import Path
import json

from sim2.agents.engine import AgentEngine
from sim2.agents.tools import parse_tool_call
from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform
from sim2.prompting.renderer import render_context
from sim2.simulation.runner import SimulationReport, SimulationRunner
from sim2.intents.models import BehaviorIntent
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


def test_compatible_tool_use_envelopes_are_normalized():
    assert parse_tool_call({"tool": "wait", "parameters": {"reason": "No new work."}}) == (
        "wait",
        {"reason": "No new work."},
    )
    assert parse_tool_call({"tool_uses": [{"tool": "observe_nearby", "arguments": {"question": "What is here?"}}]}) == (
        "observe_nearby",
        {"question": "What is here?"},
    )
    assert parse_tool_call({"tool": "record_world_progress", "params": {"progress_key": "security", "delta": 0.2}}) == (
        "record_world_progress",
        {"progress_key": "security", "delta": 0.2},
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


def test_starter_bell_installation_closes_entity_and_inventory_loop(tmp_path: Path):
    platform = Platform(create_instance(_config(), "bell-install-loop", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            agent = platform.store.get_agent("tian_yan")
            agent["inventory"]["dawn_bell"] = 1
            platform.store.update_agent("tian_yan", agent)
        result = platform.submit_behavior(BehaviorIntent("tian_yan", "behavior:install_dawn_bell", {}, {}, "Install the completed warning artifact.", 0.9, "bell-install"))
        assert result["status"] == "accepted"
        for _ in range(result["remaining_minutes"]):
            platform.step()
        assert platform.store.get_agent("tian_yan")["inventory"].get("dawn_bell", 0) == 0
        assert platform.store.get_entity("dawn_bell")["initial_state"]["installed"] is True
        assert any("installed at" in str(item.get("fact", "")) for item in platform.store.list_knowledge_facts("tian_yan"))
        assert any(item["event_type"] == "entity.installed" for item in platform.store.timeline_events(limit=100))
        tasks = [row["task_type"] for row in platform.store.connection.execute("SELECT task_type FROM evolution_task")]
        assert tasks.count("event:entity.installed") == 1  # Story wake
        assert "event_group:public_incident" in tasks  # one causal world-level wake
        assert "event:warning.artifact_installed" not in tasks
    finally:
        platform.close()


def test_starter_bell_enables_alarm_only_when_threat_is_configured(tmp_path: Path):
    platform = Platform(create_instance(_config(), "bell-alarm-loop", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            agent = platform.store.get_agent("tian_yan")
            agent["inventory"]["dawn_bell"] = 1
            platform.store.update_agent("tian_yan", agent)
        install = platform.submit_behavior(BehaviorIntent("tian_yan", "behavior:install_dawn_bell", {}, {}, "Install the warning artifact.", 0.9, "bell-alarm-install"))
        assert install["status"] == "accepted"
        for _ in range(install["remaining_minutes"]):
            platform.step()
        assert platform.store.get_meta("world_properties")["north_gate_alarm_ready"] == 1
        assert not any(item["ref"] == "behavior:sound_alarm" for item in platform.decision_view("he_yan")["executable_behaviors"])

        with platform.store.transaction():
            properties = dict(platform.store.get_meta("world_properties"))
            properties["north_gate_threat_level"] = 0.8
            platform.store.set_meta("world_properties", properties)
        assert any(item["ref"] == "behavior:sound_alarm" for item in platform.decision_view("he_yan")["executable_behaviors"])
        alarm = platform.submit_behavior(BehaviorIntent("he_yan", "behavior:sound_alarm", {}, {}, "Raise the installed north gate alarm for the detected threat.", 0.9, "bell-alarm-raise"))
        assert alarm["status"] == "accepted"
        for _ in range(alarm["remaining_minutes"]):
            platform.step()
        assert any(item["event_type"] == "alarm.raised" for item in platform.store.timeline_events(limit=100))
    finally:
        platform.close()


def test_food_behaviors_are_available_only_when_satiety_is_low(tmp_path: Path):
    platform = Platform(create_instance(_config(), "food-hunger-gate", tmp_path / "runtime"))
    try:
        view = platform.decision_view("fei_lan")
        refs = {item["ref"] for item in view["executable_behaviors"]}
        assert "behavior:eat_food" not in refs
        assert "behavior:get_food" not in refs

        with platform.store.transaction():
            agent = platform.store.get_agent("fei_lan")
            agent["needs"]["satiety"] = 30
            platform.store.update_agent("fei_lan", agent)
        view = platform.decision_view("fei_lan")
        refs = {item["ref"] for item in view["executable_behaviors"]}
        assert "behavior:eat_food" in refs
    finally:
        platform.close()


def test_god_cannot_acknowledge_an_already_recorded_event_as_new_world_change(tmp_path: Path):
    platform = Platform(create_instance(_config(), "god-event-guard", tmp_path / "runtime"))
    try:
        engine = AgentEngine(platform, WaitProvider())
        result = engine._submit_response(
            "god",
            "god",
            {"change_type": "event", "summary": "Repeat the recorded event", "effects": [], "expected_effects": []},
            context={},
            task_type="event:behavior.completed",
        )
        assert result["status"] == "rejected"
        assert result["failed_check"] == "event_already_recorded"
    finally:
        platform.close()


def test_model_tool_view_compacts_nested_entity_state():
    result = AgentEngine._model_tool_result(
        "inspect_entity",
        {
            "status": "ok",
            "entity": {
                "id": "internal-id",
                "name": "A person",
                "initial_state": {"status": "idle", "location_id": "square", "last_outcome": {"history": "x" * 20000}},
                "needs": {"satiety": 80},
                "inventory": {"rations": 2},
                "capabilities": {"research": 0.8},
            },
            "event_id": "internal-event",
        },
    )
    encoded = json.dumps(result, ensure_ascii=False)
    assert len(encoded) < 6000
    assert "internal-id" not in encoded
    assert "last_outcome" not in encoded
    assert result["entity"]["name"] == "A person"


def test_decision_wait_alias_is_a_valid_terminal_intent(tmp_path: Path):
    platform = Platform(create_instance(_config(), "decision-wait-alias", tmp_path / "runtime"))
    try:
        engine = AgentEngine(platform, WaitProvider())
        result = engine._submit_response(
            "god", "god", {"decision": "wait", "reason": "No new typed change is supported."},
            context={}, task_type="event:security.marks_classified",
        )
        assert result["status"] == "wait"
    finally:
        platform.close()


def test_starter_water_pact_and_mark_followups_change_world_or_knowledge(tmp_path: Path):
    platform = Platform(create_instance(_config(), "followup-loops", tmp_path / "runtime"))
    try:
        cases = [
            ("qiao_ning", "behavior:mitigate_water", "water_quality", 0.80, "water.mitigation.applied"),
            ("su_wu", "behavior:stabilize_pact", "root_gate_stability", 0.72, "pact.stability_improved"),
            ("he_yan", "behavior:investigate_north_gate_marks", None, None, "security.marks_classified"),
        ]
        for actor, behavior, property_name, expected_value, event_type in cases:
            intent_id = f"{actor}-followup"
            result = platform.submit_behavior(BehaviorIntent(actor, behavior, {}, {}, "Complete the configured follow-up.", 0.9, intent_id))
            assert result["status"] == "accepted"
            for _ in range(260):
                platform.step()
            if property_name:
                assert platform.store.get_meta("world_properties")[property_name] >= expected_value - 1e-6
            assert any(item["event_type"] == event_type for item in platform.store.timeline_events(limit=300))
        assert any(item["fact_key"] == "security:north_gate_marks" for item in platform.store.list_knowledge_facts("he_yan", limit=20))
    finally:
        platform.close()


def test_research_wakes_story_and_god_only_after_configured_rounds(tmp_path: Path):
    platform = Platform(create_instance(_config(), "research-rounds", tmp_path / "runtime"))
    try:
        agent = platform.store.get_agent("qiao_ning")
        action = next(item for item in platform.store.get_definitions("action_def") if item["id"] == "research_source")
        first = platform._advance_information_progress(agent, action, {"source_id": "clean_water"}, 100)
        assert first["completed"] is False
        assert platform.store.connection.execute("SELECT COUNT(*) AS n FROM evolution_task WHERE task_type LIKE 'event:%'").fetchone()["n"] == 0
        second = platform._advance_information_progress(agent, action, {"source_id": "clean_water"}, 200)
        assert second["completed"] is True
        rows = platform.store.connection.execute("SELECT role,task_type FROM evolution_task WHERE task_type='event:research.completed'").fetchall()
        assert {row["role"] for row in rows} == {"story"}
        # One completed source is not enough for a world-level synthesis.
        # The starter package requires three independent research results;
        # content packs can choose a longer or shorter evidence threshold.
        for minute in (201, 202):
            with platform.store.transaction():
                event_id = platform.store.append_event("research.completed", "qiao_ning", minute, {"summary": "Another validated research result."})
            platform._queue_event_consumer_task("god", event_id, "research.completed", "qiao_ning", minute, task_type="event:research.completed")
        assert any(item["role"] == "god" and str(item["task_type"]).startswith("event_group:research_synthesis") for item in platform.ready_evolution_tasks())
        assert platform.store.connection.execute("SELECT COUNT(*) AS n FROM event_log WHERE event_type='research.progressed'").fetchone()["n"] == 1
    finally:
        platform.close()


def test_legacy_attribute_effects_are_rejected_in_development(tmp_path: Path):
    platform = Platform(create_instance(_config(), "no-legacy-effects", tmp_path / "runtime"))
    try:
        agent = platform.store.get_agent("qiao_ning")
        before = dict(agent["initial_state"])
        result = platform.effects.apply(agent, {"type": "health_restore", "amount": 5})
        assert result["applied"] is False
        assert result["error"] == "unsupported effect type: health_restore"
        assert agent["initial_state"] == before
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


def test_story_accepts_configured_story_source_event_without_hardcoded_event_name(tmp_path: Path):
    platform = Platform(create_instance(_config(), "story-source-event", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            platform.store.append_event(
                "security.investigation_completed",
                "he_yan",
                10,
                {"story_source": True, "summary": "The boundary marks were investigated and classified."},
            )
        result = platform.submit_story_beat(
            "story",
            {
                "summary": "The north gate investigation is now part of the public security record.",
                "fact": "The boundary marks were investigated and classified.",
                "goal_hint": "Continue monitoring the north gate.",
                "audience": ["何砚"],
            },
        )
        assert result["status"] == "accepted"
    finally:
        platform.close()


def test_story_task_uses_its_source_event_when_newer_events_exist(tmp_path: Path):
    platform = Platform(create_instance(_config(), "story-source-boundary", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            source_id = platform.store.append_event("research.completed", "qiao_ning", 5, {"summary": "Water source was confirmed."})
            platform.store.append_event("relationship.changed", "lin_bo", 6, {"summary": "A later social change was recorded."})
        view = platform.view.build_decision_view("story", role="story", source_event_id=source_id)
        assert view["validated_source"]["event_type"] == "research.completed"
        assert view["validated_source"]["world_minute"] == 5
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
        assert {item["function"]["name"] for item in provider.calls[0]["tools"]} == {"send_message", "wait"}
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
