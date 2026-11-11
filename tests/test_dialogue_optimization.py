from pathlib import Path
import json

from sim2.agents.engine import AgentEngine, AgentTurn
from sim2.agents.quality import evaluate_turn
from sim2.agents.tools import parse_tool_call, tool_schemas_for_role
from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform
from sim2.prompting.renderer import render_context
from sim2.simulation.runner import SimulationReport, SimulationRunner
from sim2.intents.models import BehaviorIntent
from sim2.actions.capacity import check_action_capacity
from sim2.actions.conditions import ConditionEngine
from sim2.actions.effects import EffectPipeline
from sim2.llm.adapters import IcemanAdapter, LongCatAdapter, resolve_adapter
from sim2.llm.gateway import _json_from_text


class WaitProvider:
    def __init__(self):
        self.calls = []

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.calls.append({"system": system, "context": context, "tools": tools, "messages": messages})
        return {"intent_type": "wait", "reason": "The message adds no new decision."}


class ReplyChainProvider:
    def __init__(self):
        self.calls = []

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.calls.append({"system": system, "context": context, "tools": tools, "messages": messages})
        recipient = "费兰" if "from: 费兰" in context else "余歌"
        index = len(self.calls)
        return {
            "tool_calls": [{
                "id": f"reply-{index}",
                "type": "function",
                "function": {
                    "name": "send_message",
                    "arguments": {
                        "recipient": recipient,
                        "topic": f"rephrased topic {index}",
                        "message": f"Reply {index} continues the same bounded exchange.",
                    },
                },
            }],
        }


def test_agent_sessions_reuse_platform_view_and_embedding_cache(tmp_path: Path):
    platform = Platform(create_instance(_config(), "shared-view-cache", tmp_path / "runtime"))
    try:
        first = AgentEngine(platform, WaitProvider())
        second = AgentEngine(platform, WaitProvider())
        assert first.view is platform.view
        assert second.view is platform.view
        assert first.view.memory is second.view.memory
    finally:
        platform.close()


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
    assert parse_tool_call({"tool": "preview_behavior", "choice": "Explore Greywood"}) == (
        "preview_behavior",
        {"choice": "Explore Greywood"},
    )
    assert parse_tool_call({"tool_uses": [{"function": "request_behavior", "arguments": {"choice": "Research Water"}}]}) == (
        "request_behavior",
        {"choice": "Research Water"},
    )


def test_provider_intent_wrapper_uses_visible_behavior_choice():
    normalized = AgentEngine._normalize_provider_intent(
        "agent_decision",
        {
            "choice": "request_behavior",
            "behavior": "Explore Greywood",
            "target": "Greywood edge",
        },
    )
    assert normalized["choice"] == "Explore Greywood"
    assert normalized["target"] == {"name": "Greywood edge"}

    detailed = AgentEngine._normalize_provider_intent(
        "agent_decision",
        {
            "choice": "request_behavior",
            "choice_detail": {"behavior_name": "Manage Ration"},
            "target": "粮仓",
            "reason": "Review winter allocation.",
        },
    )
    assert detailed["choice"] == "Manage Ration"
    assert detailed["target"] == {"name": "粮仓"}

    nested = AgentEngine._normalize_provider_intent(
        "agent_decision",
        {
            "choice": "request_behavior",
            "parameters": {"choice": "Manage Ration", "target": "粮仓", "reason": "Review winter allocation.", "confidence": 0.9},
        },
    )
    assert nested["choice"] == "Manage Ration"
    assert nested["target"] == {"name": "粮仓"}
    assert nested["reason"] == "Review winter allocation."
    assert nested["confidence"] == 0.9
    assert nested["parameters"] == {}


def test_longcat_bare_function_object_is_unwrapped_at_adapter_boundary():
    adapter = resolve_adapter("openai_compatible", "https://example.test", "longcat-2.5-free")
    assert isinstance(adapter, LongCatAdapter)
    normalized = adapter.normalize_response(
        {"request_behavior": {"choice": "Patrol Route", "target": "北门", "reason": "The watch route is the current duty."}}
    )
    assert normalized["intent_type"] == "select_behavior"
    assert normalized["choice"] == "Patrol Route"
    assert normalized["target"] == "北门"


def test_provider_target_alias_is_mapped_to_visible_name():
    normalized = AgentEngine._normalize_provider_intent(
        "agent_decision",
        {"choice": "Exchange information with another character.", "target": {"Participant": "余歌"}},
    )
    assert normalized["target"]["name"] == "余歌"


def test_iceman_adapter_keeps_tools_but_removes_unsupported_json_mode():
    adapter = resolve_adapter("openai_compatible", "https://example.test", "iceman-contributor-free")
    assert isinstance(adapter, IcemanAdapter)
    payload = adapter.prepare_payload({"tools": [{"type": "function"}], "response_format": {"type": "json_object"}})
    assert "tools" in payload
    assert "response_format" not in payload
    assert payload["tool_choice"] == "required"


def test_bare_wait_is_the_only_plain_text_intent_normalized():
    assert _json_from_text("wait") == {"intent_type": "wait", "reason": "No world change is needed now."}


def test_grouped_world_evidence_is_rendered_in_god_prompt(tmp_path: Path):
    platform = Platform(create_instance(_config(), "group-evidence", tmp_path / "runtime"))
    provider = WaitProvider()
    try:
        first = platform.inject_event("research.completed", {"summary": "First verified sample."})["event_id"]
        second = platform.inject_event("research.completed", {"summary": "Second verified sample."})["event_id"]
        turn = AgentEngine(platform, provider).run(
            role="god",
            actor_id="god",
            task_type="event_group:research_synthesis",
            source_events=[{"event_id": first}, {"event_id": second}],
        )
        assert turn.result["status"] == "wait"
        prompt = provider.calls[0]["context"]
        assert "<triggering_evidence>" in prompt
        assert "First verified sample." in prompt
        assert "Second verified sample." in prompt
    finally:
        platform.close()


def test_dynamic_signal_cannot_wake_its_proposer_role(tmp_path: Path):
    platform = Platform(create_instance(_config(), "self-wake-rule", tmp_path / "runtime"))
    try:
        result = platform.submit_world_change(
            "god",
            {
                "change_type": "rule",
                "summary": "Recheck the same report.",
                "rule": {
                    "id": "self-review",
                    "condition": "event.recent.security.marks_classified == true",
                    "effect": {"type": "signal", "name": "security.review"},
                    "wake": ["god", "story"],
                },
            },
            origin_role="god",
        )
        assert result["status"] == "rejected"
        assert result["failed_check"] == "rule_self_wake"
        assert not any(item.get("id") == "self-review" for item in platform.store.get_meta("rules", {}).get("world_rules", []))
    finally:
        platform.close()


def test_native_tool_arguments_are_readable_in_audit_copy():
    raw = {"tool_calls": [{"id": "provider-call", "function": {"name": "send_message", "arguments": '{"recipient":"余歌"}'}}]}
    readable = AgentEngine._readable_model_response(raw)
    assert readable["tool_calls"][0]["function"]["arguments"] == {"recipient": "余歌"}
    assert isinstance(raw["tool_calls"][0]["function"]["arguments"], str)


def test_instant_message_prompt_states_transaction_boundary(tmp_path: Path):
    platform = Platform(create_instance(_config(), "message-boundary", tmp_path / "runtime"))
    try:
        context = platform.decision_view("yu_ge")
        AgentEngine._compact_interaction_context(context, "instant:dialogue.completed", ["send_message", "wait"])
        prompt = render_context(context, "Reply to the incoming message.")
        assert "send_message records testimony only" in prompt
        assert "cannot transfer items or currency" in prompt
        schema = next(item for item in tool_schemas_for_role("agent_decision", ["send_message", "wait"]) if item["function"]["name"] == "send_message")
        assert "never transfers items" in schema["function"]["description"]
    finally:
        platform.close()


def test_react_message_compaction_keeps_native_tool_groups_intact():
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    for turn in range(3):
        messages.append({
            "role": "assistant",
            "tool_calls": [
                {"id": f"call-{turn}-a", "type": "function", "function": {"name": "listen", "arguments": "{}"}},
                {"id": f"call-{turn}-b", "type": "function", "function": {"name": "recall_memory", "arguments": "{}"}},
            ],
        })
        messages.append({"role": "tool", "tool_call_id": f"call-{turn}-a", "content": "{}"})
        messages.append({"role": "tool", "tool_call_id": f"call-{turn}-b", "content": "{}"})
    compacted = AgentEngine._trim_conversation_messages(messages, max_tail=6)
    assert compacted[:2] == messages[:2]
    assert compacted[2]["role"] == "assistant"
    declared = set()
    for message in compacted[2:]:
        if message.get("role") == "assistant":
            declared.update(call["id"] for call in message.get("tool_calls") or [])
        if message.get("role") == "tool":
            assert message["tool_call_id"] in declared


def test_quality_separates_provider_failure_from_agent_content_failure():
    turn = AgentTurn(
        role="agent_decision",
        task_type="agent_plan_selection",
        response={},
        result={"status": "rejected", "failed_check": "llm_response", "message": "HTTP 429"},
        iterations=1,
        transcript=[{"step": "llm_error", "error": "HTTP 429"}],
    )
    quality = evaluate_turn(turn, {"executable_behaviors": [{"ref": "behavior:test"}]})
    assert quality["passed"] is False
    assert quality["grade"] == "provider_error"
    assert not any(item["name"] == "behavior_is_offered" for item in quality["checks"])


def test_choice_only_quality_requires_visible_goal_or_drive_grounding():
    turn = AgentTurn(
        role="agent_decision",
        task_type="agent_plan_selection",
        response={"tool_calls": [{"function": {"name": "request_behavior", "arguments": {"choice": "Do the thing"}}}]},
        result={"status": "accepted", "behavior_ref": "behavior:thing"},
        iterations=1,
        transcript=[{"step": "tool", "name": "request_behavior", "arguments": {"choice": "Do the thing"}, "result": {"status": "accepted"}}],
    )
    quality = evaluate_turn(turn, {
        "executable_behaviors": [{"ref": "behavior:thing", "choice": "Do the thing", "goal_tags": ["unrelated"], "drive_tags": ["other"]}],
        "goal_tags": ["current_goal"],
        "drives": [{"id": "current_drive"}],
    })
    assert quality["passed"] is False
    assert any(item["name"] == "decision_semantic_signal" and not item["passed"] for item in quality["checks"])


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


def test_registered_world_rules_show_current_condition_status(tmp_path: Path):
    platform = Platform(create_instance(_config(), "rule-status-view", tmp_path / "runtime"))
    try:
        view = platform.decision_view("liang_he")
        ration = next(item for item in view["active_world_rules"] if "food" in item["condition"].casefold())
        assert ration["current_status"] == "inactive"
        prompt = render_context(view, "Choose one behavior.")
        assert "<world_rules>" in prompt
        assert "currently inactive" in prompt
        assert "<active_world_rules>" not in prompt

        platform._set_world_property("village_food", 100)
        changed = platform.decision_view("liang_he")
        ration = next(item for item in changed["active_world_rules"] if "food" in item["condition"].casefold())
        assert ration["current_status"] == "active"
    finally:
        platform.close()
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
        installed_facts = [str(item.get("fact", "")) for item in platform.store.list_knowledge_facts("tian_yan") if "installed at" in str(item.get("fact", ""))]
        assert installed_facts
        assert all("{" not in fact and "}" not in fact for fact in installed_facts)
        assert any(item["event_type"] == "entity.installed" for item in platform.store.timeline_events(limit=100))
        tasks = [row["task_type"] for row in platform.store.connection.execute("SELECT task_type FROM evolution_task")]
        assert "event:entity.installed" not in tasks  # no redundant Story goal for a closed installation
        assert tasks.count("event_group:public_incident") == 1  # one causal world-level wake
        assert "event:warning.artifact_installed" not in tasks
    finally:
        platform.close()


def test_classified_boundary_marks_remove_repeat_investigation(tmp_path: Path):
    platform = Platform(create_instance(_config(), "classified-marks-closed", tmp_path / "runtime"))
    try:
        before = {item["ref"] for item in platform.decision_view("he_yan")["executable_behaviors"]}
        assert "behavior:investigate_north_gate_marks" in before
        result = platform.submit_behavior(BehaviorIntent("he_yan", "behavior:investigate_north_gate_marks", {}, {}, "Classify the marks once.", 0.9, "classify-once"))
        assert result["status"] == "accepted"
        for _ in range(result["remaining_minutes"]):
            platform.step()
        assert any(item["fact_key"] == "security:north_gate_marks" for item in platform.store.list_knowledge_facts("he_yan", limit=50))
        after = {item["ref"] for item in platform.decision_view("he_yan")["executable_behaviors"]}
        assert "behavior:investigate_north_gate_marks" not in after
    finally:
        platform.close()


def test_world_calendar_effects_advance_fog_and_winter_countdown(tmp_path: Path):
    platform = Platform(create_instance(_config(), "calendar-world-effects", tmp_path / "runtime"))
    try:
        for _ in range(1440):
            platform.step()
        properties = platform.store.get_meta("world_properties")
        assert properties["winter_days_remaining"] == 29
        assert round(properties["fog_level"], 2) == 0.6
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
        assert not any(task["role"] == "story" for task in platform.ready_evolution_tasks())
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
        heard = platform.perception.listen("lin_bo", "Was an alarm raised?", 10)
        assert any(item["event_type"] == "alarm.raised" for item in heard["events"])

        response_choices = {item["ref"] for item in platform.decision_view("he_yan")["executable_behaviors"]}
        assert "behavior:sound_alarm" not in response_choices
        assert "behavior:defend_village" in response_choices
        before = platform.store.get_meta("world_properties")["north_gate_threat_level"]
        defense = platform.submit_behavior(BehaviorIntent(
            "he_yan", "behavior:defend_village", {}, {},
            "Respond to the verified alarm and secure the northern boundary.", 0.95, "bell-alarm-response",
        ))
        assert defense["status"] == "accepted"
        for _ in range(defense["remaining_minutes"]):
            platform.step()
        after = platform.store.get_meta("world_properties")["north_gate_threat_level"]
        assert after < before
        assert after < 0.7
        assert any(item["event_type"] == "defense.village_response" for item in platform.store.timeline_events(limit=200))
        assert "behavior:defend_village" not in {
            item["ref"] for item in platform.decision_view("he_yan")["executable_behaviors"]
        }
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


def test_starter_decision_tool_policy_omits_redundant_behavior_preview(tmp_path: Path):
    platform = Platform(create_instance(_config(), "decision-tool-surface", tmp_path / "runtime"))
    try:
        names = {item["name"] for item in platform.decision_view("yu_ge")["tools"]}
        assert "request_behavior" in names
        assert "preview_behavior" not in names
    finally:
        platform.close()


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
        with platform.store.transaction():
            platform.store.merge_knowledge_fact("qiao_ning", "researched:clean_water:contamination", {
                "fact": "River contamination was verified.", "source_type": "research", "confidence": 1.0,
            })
            platform.store.merge_knowledge_fact("su_wu", "researched:old_pact_tablet:origin", {
                "fact": "The old pact's stabilizing purpose was verified.", "source_type": "research", "confidence": 1.0,
            })
        cases = [
            ("qiao_ning", "behavior:mitigate_water", "water_quality", 0.80, "water.mitigation.applied"),
            ("su_wu", "behavior:stabilize_pact", "root_gate_stability", 0.72, "pact.stability.improved"),
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
        # The starter package requires five independent research results;
        # content packs can choose a longer or shorter evidence threshold.
        for minute in (201, 202, 203, 204):
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


def test_story_visible_names_strip_model_wrapping_quotes(tmp_path: Path):
    platform = Platform(create_instance(_config(), "story-quote-cleanup", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            platform.store.append_event("security.investigation_completed", "he_yan", 10, {"story_source": True, "summary": "The boundary marks were investigated and classified."})
        result = platform.submit_story_beat(
            "story",
            {
                "summary": '"The north gate investigation is recorded."',
                "fact": '"The boundary marks were investigated and classified."',
                "goal_hint": '"Continue monitoring the north gate."',
                "audience": ['"何砚"'],
            },
        )
        assert result["status"] == "accepted"
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


def test_dialogue_round_limit_uses_pair_chain_not_model_topic_text(tmp_path: Path):
    platform = Platform(create_instance(_config(), "dialogue-pair-chain", tmp_path / "runtime"))
    provider = WaitProvider()
    runner = SimulationRunner(platform, {"agent_decision": provider}, max_interaction_rounds=3)
    report = SimulationReport(0, 0)
    try:
        for index, topic in enumerate(("lost ledger", "north gate marks", "bridge safety", "ledger follow-up")):
            sent = platform.submit_dialogue(
                "fei_lan", "yu_ge", topic,
                f"Distinct message {index} about the same ongoing exchange.",
            )
            assert sent["status"] == "accepted"

        runner._run_instant_interactions(report)

        assert len(provider.calls) == 3
        assert report.interaction_replies_suppressed == 1
        assert report.interaction_threads_closed == 1
        state = platform.store.get_meta("runtime_scheduler_state")
        pair_thread = next(item for item in state["interaction_rounds"] if item["key"][0] == "dialogue")
        assert pair_thread["rounds"] == 3
        assert len(pair_thread["key"]) == 3
    finally:
        platform.close()


def test_dialogue_reply_chain_is_drained_without_advancing_world_time(tmp_path: Path):
    platform = Platform(create_instance(_config(), "dialogue-same-minute-chain", tmp_path / "runtime"))
    provider = ReplyChainProvider()
    runner = SimulationRunner(platform, {"agent_decision": provider}, max_interaction_rounds=3)
    report = SimulationReport(0, 0)
    try:
        sent = platform.submit_dialogue(
            "fei_lan", "yu_ge", "opening topic",
            "This message opens one bounded exchange.",
        )
        assert sent["status"] == "accepted"

        runner._run_instant_interactions(report)

        assert len(provider.calls) == 3
        assert platform.store.get_meta("world_minute") == 0
        rows = platform.store.connection.execute(
            "SELECT world_minute,topic FROM dialogue_exchange ORDER BY id"
        ).fetchall()
        assert len(rows) == 4  # opening message plus three accepted replies
        assert {int(row["world_minute"]) for row in rows} == {0}
        assert report.interaction_replies_suppressed == 1
        assert report.interaction_threads_closed == 1
    finally:
        platform.close()


def test_pending_interaction_blocks_tick_and_resumes_from_persisted_event(tmp_path: Path):
    platform = Platform(create_instance(_config(), "dialogue-blocking-resume", tmp_path / "runtime"))
    try:
        sent = platform.submit_dialogue(
            "fei_lan", "yu_ge", "route evidence",
            "Please review this route report before world time advances.",
        )
        assert sent["status"] == "accepted"

        blocked = SimulationRunner(platform, {}).run(1)
        assert blocked.stop_reason == "interaction_pending"
        assert platform.store.get_meta("world_minute") == 0

        provider = WaitProvider()
        resumed = SimulationRunner(platform, {"agent_decision": provider}).run(1)
        assert resumed.stop_reason is None
        assert provider.calls
        assert "<incoming_interaction>" in provider.calls[0]["context"]
        assert platform.store.get_meta("world_minute") == 1
        state = platform.store.get_meta("runtime_scheduler_state")
        assert sent["event_id"] in state["interaction_events_seen"]
    finally:
        platform.close()


def test_interaction_dispatch_cursor_survives_runner_restart(tmp_path: Path):
    platform = Platform(create_instance(_config(), "dialogue-resume", tmp_path / "runtime"))
    provider = WaitProvider()
    try:
        sent = platform.submit_dialogue("fei_lan", "yu_ge", "route", "The western road is open before dusk.")
        assert sent["status"] == "accepted"
        first = SimulationRunner(platform, {"agent_decision": provider})
        first._run_instant_interactions(SimulationReport(0, 0))
        assert len(provider.calls) == 1

        resumed = SimulationRunner(platform, {"agent_decision": provider})
        resumed._run_instant_interactions(SimulationReport(0, 0))
        assert len(provider.calls) == 1
        state = platform.store.get_meta("runtime_scheduler_state")
        assert sent["event_id"] in state["interaction_events_seen"]
    finally:
        platform.close()


def test_wait_decisions_back_off_and_configured_event_wakes_one_agent(tmp_path: Path):
    platform = Platform(create_instance(_config(), "decision-backoff", tmp_path / "runtime"))
    provider = WaitProvider()
    try:
        rules = dict(platform.store.get_meta("rules") or {})
        rules["decision_policy"] = {"wait_backoff_minutes": [5, 10, 20], "wake_events": ["test.wake"]}
        with platform.store.transaction():
            platform.store.set_meta("rules", rules)
        runner = SimulationRunner(platform, {"agent_decision": provider})
        runner.run(1)
        initial_calls = len(provider.calls)
        assert initial_calls == len(platform.store.list_agents())
        runner.run(4)
        assert len(provider.calls) == initial_calls
        runner.run(1)
        assert len(provider.calls) == initial_calls * 2

        event = platform.inject_event("test.wake", {"summary": "A configured wake signal."}, "fei_lan")
        assert event["status"] == "accepted"
        before = len(provider.calls)
        runner.run(1)
        assert len(provider.calls) == before + 1
        assert runner._wait_streak.get("fei_lan") == 1
    finally:
        platform.close()
