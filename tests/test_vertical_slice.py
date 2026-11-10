from pathlib import Path
import pytest
import json
import threading
import httpx
from urllib.request import Request, urlopen
from sim2.intents.models import BehaviorIntent

from sim2.agents.engine import AgentEngine
from sim2.agents.tools import AgentToolExecutor
from sim2.agents.tools import tool_schemas_for_role
from sim2.agents.quality import evaluate_turn
from sim2.agent_view.validator import validate_agent_view
from sim2.actions.conditions import ConditionEngine
from sim2.actions.effects import EffectPipeline
from sim2.world.drives import DriveCalculator
from sim2.api.service import ControlService
from sim2.api.http import ControlHTTPServer
from sim2.api.text import TextWorldSession
from sim2.bootstrap.service import create_instance, validate_config
from sim2.platform.runtime import Platform
from sim2.prompting.renderer import render_context
from sim2.simulation.runner import SimulationRunner
from sim2.llm.config import ProviderConfig
from sim2.llm.gateway import OpenAICompatibleProvider
from sim2.memory.similarity import ExperienceSimilarity


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


class MessageProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = []

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.messages.append(list(messages or []))
        return dict(self.responses.pop(0))


class FailingProvider:
    def __init__(self, message="provider unavailable"):
        self.message = message
        self.calls = 0

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.calls += 1
        raise RuntimeError(self.message)


def step_n(platform: Platform, minutes: int):
    result = None
    for _ in range(minutes):
        result = platform.step()
    return result


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
        assert "<executable_behaviors>" in prompt
        assert "actor.can" not in prompt
        assert "nodes" not in prompt.lower()
        assert "preconditions" not in prompt.lower()
    finally:
        platform.close()


def test_agent_selects_human_readable_choice_and_prompt_hides_behavior_ref(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "choice-world", tmp_path / "runtime")
    platform = Platform(db_path)
    provider = FakeProvider(
        {
            "choice": "Explore Greywood",
            "reason": "The area is unknown and exploration supports the map goal.",
            "confidence": 0.9,
        }
    )
    try:
        turn = AgentEngine(platform, provider).run(role="agent_decision", actor_id="lu_xingzhou")
        assert turn.result["status"] == "accepted"
        assert turn.result["behavior_ref"] == "behavior:explore_greywood"
        prompt = provider.calls[0][1]
        assert "choice: Explore Greywood" in prompt
        assert "behavior:explore_greywood" not in prompt
        assert 'ref="' not in prompt
    finally:
        platform.close()


def test_prompt_keeps_stable_sections_before_dynamic_world_state(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "prompt-order-world", tmp_path / "runtime"))
    try:
        prompt = render_context(platform.decision_view("lu_xingzhou"), "Choose one behavior.")
        assert prompt.index("<protocol>") < prompt.index("<platform_rules>") < prompt.index("<allowed_intents_and_tools>") < prompt.index("<world_state>")
    finally:
        platform.close()


def test_agent_view_exposes_calendar_time_not_only_tick_number(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "calendar-context-world", tmp_path / "runtime"))
    try:
        platform.store.set_meta("world_minute", 1530)
        time = platform.decision_view("lu_xingzhou")["world_time"]
        assert time["day"] == 2
        assert time["time_of_day"] == "01:30"
        assert time["season"] == "late_autumn"
        assert time["label"] == "Day 2, 01:30, late_autumn"
    finally:
        platform.close()


def test_agent_view_scene_describes_current_location_and_nearby_participants(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "scene-context-world", tmp_path / "runtime"))
    try:
        scene = platform.decision_view("yu_ge")["observation"]
        assert scene["source"] == "current_state"
        assert any("余歌旅店" in fact for fact in scene["facts"])
        assert any("费兰" in fact for fact in scene["facts"])
    finally:
        platform.close()


def test_context_compaction_preserves_choices_and_task(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "context-compaction-world", tmp_path / "runtime"))
    try:
        view = platform.decision_view("lu_xingzhou")
        view["memories"] = [{"type": "experience", "summary": "Long historical detail " * 80, "confidence": 0.5} for _ in range(30)]
        view["known_facts"] = [{"fact": "Long fact " * 80} for _ in range(30)]
        prompt = render_context(view, "Choose one behavior for the current goal.", max_chars=8000)
        assert len(prompt) <= 8000
        assert "<executable_behaviors>" in prompt
        assert "choice: Explore Greywood" in prompt
        assert "<decision>" in prompt
        if "</interaction_history>" in prompt:
            assert prompt.rindex("<decision>") > prompt.rindex("</interaction_history>")
    finally:
        platform.close()


def test_reflection_and_god_prompts_use_readable_outcome_names(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "readable-outcomes-world", tmp_path / "runtime"))
    try:
        result = platform.submit_behavior(BehaviorIntent(
            "lin_bo", "behavior:rest_and_recover", {}, {}, "Restore readiness.", 0.8, "readable-outcome"
        ))
        for _ in range(result["remaining_minutes"]):
            platform.step()
        reflection_prompt = render_context(platform.decision_view("lin_bo", role="agent_reflection"), "Review the latest outcome.")
        god_prompt = render_context(platform.decision_view("god", role="god"), "Review the world outcome.")
        assert "behavior:rest_and_recover" not in reflection_prompt
        assert "rest_and_recover" not in reflection_prompt
        assert "Recover energy at the current safe location." in reflection_prompt
        assert "behavior:rest_and_recover" not in god_prompt
        assert "lin_bo" not in god_prompt
        assert "林泊" in god_prompt
    finally:
        platform.close()


def test_decision_alias_wait_ends_without_repeated_model_calls(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "wait-alias-world", tmp_path / "runtime"))
    provider = FakeProvider({"intent": "wait", "reason": "No suitable behavior is available now."})
    try:
        turn = AgentEngine(platform, provider).run(role="agent_decision", actor_id="lu_xingzhou")
        assert turn.result["status"] == "wait"
        assert turn.iterations == 1
        assert len(provider.calls) == 1
    finally:
        platform.close()


def test_react_session_reports_budget_exhaustion_explicitly(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "budget-world", tmp_path / "runtime"))
    provider = SequenceProvider([{"intent_type": "not_allowed"}, {"intent_type": "still_not_allowed"}])
    try:
        turn = AgentEngine(platform, provider, max_iterations=2).run(role="agent_decision", actor_id="lu_xingzhou")
        assert turn.result["status"] == "rejected"
        assert turn.result["failed_check"] == "budget_exhausted"
    finally:
        platform.close()


def test_model_tool_schemas_use_visible_names_instead_of_ids():
    for role in ("agent_decision", "agent_reflection", "god"):
        schemas = tool_schemas_for_role(role)
        for schema in schemas:
            properties = schema["function"]["parameters"].get("properties", {})
            assert not any(key == "id" or key.endswith("_id") for key in properties)


def test_god_schema_does_not_offer_direct_world_property_mutation():
    world_change = next(schema for schema in tool_schemas_for_role("god") if schema["function"]["name"] == "propose_world_change")
    parameters = world_change["function"]["parameters"]
    assert parameters["properties"]["change_type"]["enum"] == ["rule", "relationship", "faction", "event"]
    assert "property" not in parameters["properties"]
    assert "value" not in parameters["properties"]


def test_provider_retries_transient_disconnect_without_logging_credentials(monkeypatch):
    responses = [httpx.RemoteProtocolError("disconnect"), httpx.Response(200, request=httpx.Request("POST", "http://example.test"), json={"choices": [{"message": {"content": '{"intent_type":"wait"}'}}]})]
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr("sim2.llm.gateway.httpx.post", fake_post)
    provider = OpenAICompatibleProvider(ProviderConfig("test", "openai_compatible", "http://example.test/v1", "test", max_retries=1, retry_backoff_seconds=0))
    result = provider.complete_json(system="system", context="context")
    assert result["intent_type"] == "wait"
    assert len(calls) == 2


def test_provider_cooldown_stops_repeated_transient_requests(monkeypatch):
    response = httpx.Response(429, request=httpx.Request("POST", "http://example.test"), json={"error": {"message": "rate limited"}})
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        return response

    monkeypatch.setattr("sim2.llm.gateway.httpx.post", fake_post)
    provider = OpenAICompatibleProvider(ProviderConfig("test", "openai_compatible", "http://example.test/v1", "test", max_retries=0, retry_backoff_seconds=0))
    with pytest.raises(RuntimeError, match="LLM HTTP 429"):
        provider.complete_json(system="system", context="context")
    with pytest.raises(RuntimeError, match="cooldown"):
        provider.complete_json(system="system", context="context")
    assert len(calls) == 1


def test_provider_failure_is_bounded_and_backed_off(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "provider-failure-world", tmp_path / "runtime")
    platform = Platform(db_path)
    provider = FailingProvider()
    try:
        report = SimulationRunner(
            platform,
            {"agent_decision": provider},
            wait_cooldown_minutes=30,
            llm_error_cooldown_minutes=60,
        ).run(60)
        # One failed provider call per idle Agent is enough; the ReAct loop
        # and minute loop must not amplify a configuration outage.
        assert provider.calls == len(platform.store.list_agents())
        assert report.llm_errors == provider.calls
        assert report.rejected_turns == provider.calls
    finally:
        platform.close()


def test_platform_run_owns_autonomous_scheduler(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "platform-runtime-world", tmp_path / "runtime")
    platform = Platform(db_path)
    provider = FakeProvider({"intent_type": "wait", "reason": "No urgent work."})
    try:
        report = platform.run(
            1,
            providers={"agent_decision": provider, "agent_reflection": provider, "god": provider},
        )
        assert report.end_minute == 1
        assert platform.status()["world_minute"] == 1
        assert provider.calls
    finally:
        platform.close()


def test_simulation_report_counts_behavior_events_beyond_recent_event_window(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "report-world", tmp_path / "runtime")
    platform = Platform(db_path)
    provider = FakeProvider({"behavior_ref": "behavior:rest_and_recover", "reason": "Recover readiness.", "confidence": 0.8})
    try:
        report = SimulationRunner(platform, {"agent_decision": provider}).run(1)
        assert report.selected_behaviors
        assert report.diversity["unique_behaviors"] >= 1
    finally:
        platform.close()


def test_food_bt_is_available_with_food_and_closes_satiety_loop(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "food-loop-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        agent = platform.store.get_agent("lu_xingzhou")
        agent["needs"]["satiety"] = 40
        with platform.store.transaction():
            platform.store.update_agent("lu_xingzhou", agent)
        view = platform.decision_view("lu_xingzhou")
        assert any(item["ref"] == "behavior:eat_food" for item in view["executable_behaviors"])
        before = platform.store.get_agent("lu_xingzhou")
        result = platform.submit_behavior(BehaviorIntent(
            actor_id="lu_xingzhou",
            behavior_ref="behavior:eat_food",
            target={},
            parameters={},
            reason="Eat carried food before taking another task.",
            confidence=0.9,
            intent_id="eat-food-intent",
        ))
        assert result["status"] == "accepted"
        platform.step()
        after = platform.store.get_agent("lu_xingzhou")
        assert after["inventory"]["rations"] == before["inventory"]["rations"] - 1
        assert before["needs"]["satiety"] == 40
        assert after["needs"]["satiety"] == pytest.approx(min(100, before["needs"]["satiety"] - 0.02 + 30))
        assert after["initial_state"]["status"] == "idle"
    finally:
        platform.close()


def test_food_bt_remains_available_for_agent_choice_at_any_satiety(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "food-trigger-world", tmp_path / "runtime"))
    try:
        agent = platform.store.get_agent("lu_xingzhou")
        agent["needs"]["satiety"] = 79
        with platform.store.transaction():
            platform.store.update_agent("lu_xingzhou", agent)
        view = platform.decision_view("lu_xingzhou")
        assert any(item["ref"] == "behavior:eat_food" for item in view["executable_behaviors"])
        accepted = platform.submit_behavior(BehaviorIntent("lu_xingzhou", "behavior:eat_food", {}, {}, "Choose food before the next task.", 0.8, "early-food"))
        assert accepted["status"] == "accepted"
    finally:
        platform.close()


def test_food_store_bt_supplies_agents_without_carried_food(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "food-store-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        agent = platform.store.get_agent("liang_he")
        agent["needs"]["satiety"] = 40
        with platform.store.transaction():
            platform.store.update_agent("liang_he", agent)
        view = platform.decision_view("liang_he")
        assert any(item["ref"] == "behavior:get_food" for item in view["executable_behaviors"])
        before = platform.store.get_entity("village_food")["amount"]
        result = platform.submit_behavior(BehaviorIntent(
            actor_id="liang_he", behavior_ref="behavior:get_food", target={}, parameters={},
            reason="Withdraw a ration before the next task.", confidence=0.9, intent_id="get-food-intent",
        ))
        assert result["status"] == "accepted"
        assert result["remaining_minutes"] == 11
        for _ in range(11):
            platform.step()
        after = platform.store.get_agent("liang_he")
        assert after["inventory"]["rations"] == 1
        assert platform.store.get_entity("village_food")["amount"] == before - 1
    finally:
        platform.close()


def test_market_food_path_uses_coin_and_market_stock(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "market-food-world", tmp_path / "runtime"))
    try:
        agent = platform.store.get_agent("fei_lan")
        agent["needs"]["satiety"] = 20
        platform.store.update_agent("fei_lan", agent)
        platform.store.connection.commit()
        view = platform.decision_view("fei_lan")
        assert any(item["ref"] == "behavior:buy_food" for item in view["executable_behaviors"])
        before_coin = platform.store.get_agent("fei_lan")["inventory"]["coin"]
        before_rations = platform.store.get_agent("fei_lan")["inventory"].get("rations", 0)
        before_stock = platform.store.get_entity("market_rations")["amount"]
        result = platform.submit_behavior(BehaviorIntent(
            "fei_lan", "behavior:buy_food", {}, {}, "Buy food because carried food is unavailable.", 0.9, "market-food"
        ))
        assert result["status"] == "accepted"
        step_n(platform, 5)
        after = platform.store.get_agent("fei_lan")
        assert after["inventory"]["coin"] == before_coin - 1
        assert after["inventory"]["rations"] == before_rations + 1
        assert platform.store.get_entity("market_rations")["amount"] == before_stock - 1
    finally:
        platform.close()


def test_nearby_communication_behavior_resolves_a_visible_participant(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "nearby-dialogue-world", tmp_path / "runtime"))
    try:
        view = platform.decision_view("yu_ge")
        communicate = next(item for item in view["executable_behaviors"] if item["ref"] == "behavior:communicate")
        assert "费兰" in communicate["target"]
        result = platform.submit_behavior(BehaviorIntent(
            "yu_ge", "behavior:communicate", {}, {}, "Exchange the merchant's route information.", 0.8, "nearby-dialogue"
        ))
        assert result["status"] == "accepted"
        step_n(platform, 6)
        dialogue = next(item for item in platform.store.recent_events(limit=20) if item["event_type"] == "dialogue.completed")
        assert dialogue["topic"] == "general"
        assert platform.store.list_memories("fei_lan", limit=5)
        assert any(item["from"] == "yu_ge" and item["to"] == "fei_lan" and item["type"] == "trust" for item in platform.store.list_relationships())
        assert "trust=0.02" in render_context(platform.decision_view("yu_ge"), "Choose one behavior.")
        assert not any(item["ref"] == "behavior:communicate" for item in platform.decision_view("yu_ge")["executable_behaviors"])
    finally:
        platform.close()


def test_starvation_is_a_configured_world_consequence(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "starvation-world", tmp_path / "runtime"))
    try:
        agent = platform.store.get_agent("fei_lan")
        agent["needs"]["satiety"] = 0
        agent["initial_state"]["health"] = 10
        platform.store.update_agent("fei_lan", agent)
        platform.store.connection.commit()
        step_n(platform, 60)
        after = platform.store.get_agent("fei_lan")
        assert after["initial_state"]["health"] == 8
        assert after["initial_state"]["status"] == "idle"
    finally:
        platform.close()


def test_patrol_report_closes_personal_world_and_knowledge_loop(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "patrol-closure-world", tmp_path / "runtime"))
    try:
        before = platform.store.get_meta("world_properties")["north_gate_threat_level"]
        before_needs = platform.store.get_agent("he_yan")["needs"].copy()
        result = platform.submit_behavior(BehaviorIntent("he_yan", "behavior:patrol_route", {}, {}, "Inspect the known boundary route.", 0.9, "patrol-closure"))
        assert result["status"] == "accepted"
        step_n(platform, 97)
        after = platform.store.get_meta("world_properties")["north_gate_threat_level"]
        assert after == before
        agent = platform.store.get_agent("he_yan")
        assert agent["needs"]["safety"] > before_needs["safety"]
        assert agent["needs"]["esteem"] > before_needs["esteem"]
        assert any(item["fact_key"] == "patrol:north_gate_loop" for item in platform.store.list_knowledge_facts("he_yan", limit=20))
        assert any(item["event_type"] == "security.patrol_reported" for item in platform.store.recent_events(limit=30))
        trends = platform.store.world_trends(current_minute=platform.store.get_meta("world_minute"))["weekly"]
        assert trends["signal_events"]["security.patrol_reported"] == 1
        assert "north_gate_threat_level" not in trends["world_property_deltas"]
        assert any(item["role"] == "god" and item["source_event_type"] == "security.patrol_reported" for item in platform.store.ready_evolution_tasks())
    finally:
        platform.close()


def test_governance_report_closes_personal_world_and_knowledge_loop(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "governance-closure-world", tmp_path / "runtime"))
    try:
        result = platform.submit_behavior(BehaviorIntent("lin_bo", "behavior:govern_village", {}, {}, "Review current reserves and risk.", 0.9, "governance-closure"))
        assert result["status"] == "accepted"
        step_n(platform, 24)
        assert platform.store.get_meta("world_properties")["public_trust"] == 0.55
        facts = platform.store.list_knowledge_facts("lin_bo", limit=20)
        assert any(item["fact_key"] == "governance:latest_review" for item in facts)
        assert any(item["fact_key"] == "inspect_object:village_food" and "320" in item["fact"] for item in facts)
        assert any(item["event_type"] == "governance.reviewed" for item in platform.store.recent_events(limit=30))
        assert any(item["role"] == "god" and item["source_event_type"] == "governance.reviewed" for item in platform.store.ready_evolution_tasks())
    finally:
        platform.close()


def test_satiety_threshold_is_an_observable_signal_not_a_food_choice_gate(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "satiety-signal-world", tmp_path / "runtime"))
    try:
        agent = platform.store.get_agent("lu_xingzhou")
        agent["needs"]["satiety"] = 40.01
        platform.store.update_agent("lu_xingzhou", agent)
        platform.store.connection.commit()
        assert any(item["ref"] == "behavior:eat_food" for item in platform.decision_view("lu_xingzhou")["executable_behaviors"])
        platform.step()
        event = next(item for item in platform.store.recent_events(limit=30) if item["event_type"] == "need.satiety_low")
        assert event["need"] == "satiety"
    finally:
        platform.close()


def test_rest_fallback_disappears_but_food_remains_a_decision_choice(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "satisfied-needs-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        liang = platform.store.get_agent("liang_he")
        liang["needs"]["satiety"] = 100
        platform.store.update_agent("liang_he", liang)
        platform.store.connection.commit()
        offered = {item["ref"] for item in platform.decision_view("liang_he")["executable_behaviors"]}
        assert "behavior:eat_food" not in offered
        assert "behavior:get_food" in offered

        smith = platform.store.get_agent("tian_yan")
        smith["initial_state"]["energy"] = 100
        platform.store.update_agent("tian_yan", smith)
        platform.store.connection.commit()
        offered = {item["ref"] for item in platform.decision_view("tian_yan")["executable_behaviors"]}
        assert "behavior:rest_and_recover" not in offered
    finally:
        platform.close()


def test_attention_capacity_filters_catalog_and_rejects_stale_choice(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "attention-capacity-world", tmp_path / "runtime"))
    try:
        steward = platform.store.get_agent("lin_bo")
        steward["initial_state"]["attention"] = 100
        platform.store.update_agent("lin_bo", steward)
        platform.store.connection.commit()
        view = platform.decision_view("lin_bo")
        assert "behavior:govern_village" not in {item["ref"] for item in view["executable_behaviors"]}
        result = platform.submit_behavior(BehaviorIntent(
            "lin_bo", "behavior:govern_village", {}, {}, "Review village stability.", 0.8, "stale-attention-intent"
        ))
        assert result["status"] == "rejected"
        assert result["failed_check"] == "behavior_unavailable"
    finally:
        platform.close()


def test_research_attention_cost_fits_configured_capacity(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "research-capacity-world", tmp_path / "runtime"))
    try:
        su_wu = platform.store.get_agent("su_wu")
        su_wu["initial_state"]["attention"] = 0
        su_wu["initial_state"]["energy"] = 100
        platform.store.update_agent("su_wu", su_wu)
        platform.store.connection.commit()
        assert any(item["ref"] == "behavior:research_source" for item in platform.decision_view("su_wu")["executable_behaviors"])
    finally:
        platform.close()


def test_inspection_behaviors_have_configured_cooldowns(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "inspection-cooldown-world", tmp_path / "runtime"))
    try:
        intent = BehaviorIntent("su_wu", "behavior:inspect_object", {}, {}, "Check the current source.", 0.8, "inspection-cooldown")
        assert platform.submit_behavior(intent)["status"] == "accepted"
        step_n(platform, 24)
        assert not any(item["ref"] == "behavior:inspect_object" for item in platform.decision_view("su_wu")["executable_behaviors"])
        platform.store.set_meta("world_minute", 1464)
        assert any(item["ref"] == "behavior:inspect_object" for item in platform.decision_view("su_wu")["executable_behaviors"])
    finally:
        platform.close()


def test_decision_context_explains_single_recovery_candidate(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "single-recovery-context", tmp_path / "runtime"))
    try:
        liang = platform.store.get_agent("liang_he")
        liang["initial_state"]["energy"] = 6.5
        liang["needs"]["satiety"] = 100
        platform.store.update_agent("liang_he", liang)
        platform.store.connection.commit()
        view = platform.decision_view("liang_he")
        offered = {item["ref"] for item in view["executable_behaviors"]}
        assert "behavior:recover_at_safe_location" in offered
        assert "behavior:eat_food" not in offered
        assert "behavior:get_food" in offered
        assert any("Satiety is 100/100" in item for item in view["decision_constraints"])
        assert any("Energy is 6.5/100" in item for item in view["decision_constraints"])
        assert any("not safe for rest" in item for item in view["decision_constraints"])
        prompt = render_context(view, "Choose one behavior.")
        assert "<decision_constraints>" in prompt
        assert "Food choices remain available" in prompt
    finally:
        platform.close()


def test_core_gather_bt_travels_to_nearest_resource_node(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "gather-core-world", tmp_path / "runtime"))
    try:
        liang = platform.store.get_agent("liang_he")
        liang["initial_state"]["location_id"] = "village_square"
        liang["initial_state"]["energy"] = 100
        platform.store.update_agent("liang_he", liang)
        platform.store.connection.commit()
        view = platform.decision_view("liang_he")
        assert any(item["ref"] == "behavior:gather_at_known_node" for item in view["executable_behaviors"])
        result = platform.submit_behavior(BehaviorIntent(
            "liang_he", "behavior:gather_at_known_node", {}, {}, "Collect available grain for production.", 0.8, "gather-core-intent"
        ))
        assert result["status"] == "accepted"
        platform.step()
        assert platform.store.get_agent("liang_he")["initial_state"]["location_id"] == "granary"
    finally:
        platform.close()


def test_core_treatment_bt_travels_to_health_service_and_restores_health(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "health-core-world", tmp_path / "runtime"))
    try:
        qiao = platform.store.get_agent("qiao_ning")
        qiao["initial_state"]["location_id"] = "village_square"
        qiao["initial_state"]["health"] = 70
        qiao["inventory"]["medicine"] = 1
        platform.store.update_agent("qiao_ning", qiao)
        platform.store.connection.commit()
        view = platform.decision_view("qiao_ning")
        assert any(item["ref"] == "behavior:seek_treatment" for item in view["executable_behaviors"])
        result = platform.submit_behavior(BehaviorIntent(
            "qiao_ning", "behavior:seek_treatment", {}, {}, "Recover from an injury.", 0.9, "health-core-intent"
        ))
        assert result["status"] == "accepted"
        step_n(platform, 76)
        healed = platform.store.get_agent("qiao_ning")
        assert healed["initial_state"]["health"] == 100
        assert healed["inventory"]["medicine"] == 0
    finally:
        platform.close()


def test_platform_accepts_food_choice_without_a_hard_satiety_gate(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "stale-food-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        liang = platform.store.get_agent("liang_he")
        liang["needs"]["satiety"] = 100
        platform.store.update_agent("liang_he", liang)
        platform.store.connection.commit()
        result = platform.submit_behavior(BehaviorIntent(
            actor_id="liang_he",
            behavior_ref="behavior:get_food",
            target={},
            parameters={},
            reason="Use a stale candidate.",
            confidence=0.5,
            intent_id="stale-food-intent",
        ))
        assert result["status"] == "accepted"
    finally:
        platform.close()


def test_agent_view_filters_unavailable_recipe_and_noop_travel(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "agent-view-filter-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        blacksmith = {item["ref"] for item in platform.decision_view("tian_yan")["executable_behaviors"]}
        assert "behavior:craft_meal" not in blacksmith
        assert "behavior:travel" not in blacksmith
        assert all(platform.decision_view(agent["id"])["executable_behaviors"] for agent in platform.store.list_agents())
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
    assert "<tools>" not in prompt
    assert "request_behavior" in prompt
    assert "arguments:" not in prompt
    assert "A fresh mark is visible." in prompt


def test_context_compacts_raw_memory_messages_and_keeps_provenance():
    context = {
        "self": {"name": "A", "role": "watcher", "status": "idle", "location": "gate", "goal": "protect"},
        "drives": [],
        "known_facts": [],
        "memories": [{"type": "testimony", "message": "A long transcript " * 100, "source_type": "dialogue", "confidence": 0.6}],
        "direct_relationships": [],
        "active_task": {},
        "available_behaviors": [],
    }
    prompt = render_context(context, "Choose a behavior.")
    assert "source=dialogue" in prompt
    assert "confidence=0.60" in prompt
    assert len(prompt.split("memory (testimony):", 1)[1].split("\n", 1)[0]) <= 410


def test_attribute_context_comes_from_config_and_food_stays_inventory(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "attribute-world", tmp_path / "runtime"))
    try:
        view = platform.decision_view("lu_xingzhou")
        labels = {item["label"] for item in view["self"]["attributes"]}
        assert labels == {"Health", "Energy", "Exposure", "Attention load", "Satiety", "Safety", "Belonging", "Esteem", "Self-actualization"}
        assert "food" not in {label.casefold() for label in labels}
        assert "rations" in view["inventory"]
        assert any("Only listed BTs" in rule for rule in view["platform_rules"])
    finally:
        platform.close()


def test_all_configured_needs_have_direct_agent_facing_attribute_meanings(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "attribute-meaning-world", tmp_path / "runtime"))
    try:
        definitions = {item["id"]: item for item in platform.store.get_meta("attributes", [])}
        needs = {item["id"] for item in (platform.store.get_meta("rules", {}) or {}).get("needs", [])}
        assert needs <= set(definitions)
        for need_id in needs:
            meaning = str(definitions[need_id].get("prompt", ""))
            assert "100" in meaning and "0" in meaning
        prompt = render_context(platform.decision_view("liang_he"), "Choose one behavior.")
        assert "Satiety" in prompt and "higher means more full" in prompt
    finally:
        platform.close()


def test_starter_behavior_closure_contract_is_complete(tmp_path: Path):
    """Every registered tree must expose a result and a persisted consequence."""

    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "behavior-closure-contract-world", tmp_path / "runtime"))
    try:
        actions = {item["id"]: item for item in platform.store.get_definitions("action_def")}
        recipes = {item["id"]: item for item in platform.store.get_definitions("recipe_def")}
        for behavior in platform.store.get_definitions("bt_def"):
            action_nodes = list(platform.view._iter_behavior_actions(behavior["id"]))
            configured_actions = [actions.get(str(node.get("action_id")), {}) for node in action_nodes]
            assert behavior.get("success_results") or any(item.get("success", {}).get("result") for item in configured_actions), behavior["id"]
            assert behavior.get("failure_results") or any(item.get("failure", {}).get("result") for item in configured_actions), behavior["id"]
            has_consequence = bool(behavior.get("outcome_effects") or behavior.get("outcome_events") or behavior.get("outcome_facts"))
            for action in configured_actions:
                recipe = recipes.get(str(next((node.get("parameters", {}).get("recipe_id") for node in action_nodes if node.get("action_id") == action.get("id")), "")))
                has_consequence = has_consequence or bool(action.get("effects") or action.get("progress_effects") or action.get("information") or (recipe and recipe.get("outputs")))
            assert has_consequence, behavior["id"]
    finally:
        platform.close()


def test_agent_view_contract_and_native_react_messages(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "message-world", tmp_path / "runtime")
    platform = Platform(db_path)
    provider = MessageProvider([
        {"tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "observe_nearby", "arguments": "{\"question\":\"What is here?\"}"}}]},
        {"behavior_ref": "behavior:explore_greywood", "target": {}, "parameters": {}, "reason": "The goal requires mapping.", "confidence": 0.8},
    ])
    try:
        view = platform.decision_view("lu_xingzhou")
        assert validate_agent_view(view, role="agent_decision") == []
        turn = AgentEngine(platform, provider).run(role="agent_decision", actor_id="lu_xingzhou")
        assert turn.result["status"] == "accepted"
        assert len(provider.messages) == 2
        assert any(message.get("role") == "assistant" and message.get("tool_calls") for message in provider.messages[1])
        assert any(message.get("role") == "tool" and message.get("tool_call_id") == "call-1" for message in provider.messages[1])
        assert provider.messages[1][-1]["role"] == "user"
        assert "<self>" not in provider.messages[1][-1]["content"]
        assert "Tool results:" in provider.messages[1][-1]["content"]
    finally:
        platform.close()


def test_native_multiple_tool_calls_receive_one_result_each(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "multi-tool-world", tmp_path / "runtime")
    platform = Platform(db_path)
    provider = MessageProvider([
        {"tool_calls": [
            {"id": "call-a", "type": "function", "function": {"name": "observe_nearby", "arguments": "{\"question\":\"Who is here?\"}"}},
            {"id": "call-b", "type": "function", "function": {"name": "query_known_fact", "arguments": "{\"query\":\"village\"}"}},
        ]},
        {"behavior_ref": "behavior:explore_greywood", "reason": "The exploration goal requires this behavior.", "confidence": 0.8},
    ])
    try:
        turn = AgentEngine(platform, provider).run(role="agent_decision", actor_id="lu_xingzhou")
        assert turn.result["status"] == "accepted"
        second = provider.messages[1]
        tool_messages = [message for message in second if message.get("role") == "tool"]
        assert {message["tool_call_id"] for message in tool_messages} == {"call-a", "call-b"}
    finally:
        platform.close()


def test_native_batch_stops_after_one_accepted_intent(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "native-one-intent-world", tmp_path / "runtime"))
    provider = MessageProvider([
        {"tool_calls": [
            {"id": "intent-a", "type": "function", "function": {"name": "request_behavior", "arguments": '{"choice":"Explore Greywood"}'}},
            {"id": "intent-b", "type": "function", "function": {"name": "request_behavior", "arguments": '{"choice":"Eat one available food item"}'}},
        ]}
    ])
    try:
        turn = AgentEngine(platform, provider).run(role="agent_decision", actor_id="lu_xingzhou")
        assert turn.result["status"] == "accepted"
        assert platform.store.connection.execute("SELECT COUNT(*) AS n FROM bt_instance WHERE actor_id='lu_xingzhou'").fetchone()["n"] == 1
    finally:
        platform.close()


def test_decision_quality_checks_grounding_against_drive_and_goal(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "quality-world", tmp_path / "runtime")
    platform = Platform(db_path)
    provider = SequenceProvider([{"behavior_ref": "behavior:explore_greywood", "reason": "Exploration capability and the map goal support this behavior.", "confidence": 0.9}])
    try:
        view = platform.decision_view("lu_xingzhou")
        turn = AgentEngine(platform, provider).run(role="agent_decision", actor_id="lu_xingzhou")
        quality = evaluate_turn(turn, view)
        assert quality["passed"] is True
        assert quality["grade"] == "comparative_grounded_choice"
    finally:
        platform.close()


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
        assert "<interaction_history>" in provider.calls[1]["context"]
        assert provider.calls[0]["tools"]
        assert provider.calls[0]["tools"][0]["function"]["name"] == "observe_nearby"
        assert any(step["step"] == "tool" for step in turn.transcript)
    finally:
        platform.close()


def test_metrics_report_prompt_prefix_reuse_and_provider_cache_evidence(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "cache-metrics-world", tmp_path / "runtime")
    platform = Platform(db_path)
    provider = SequenceProvider([
        {"tool_call": {"name": "observe_nearby", "arguments": {"question": "Who is nearby?"}}},
        {"behavior_ref": "behavior:explore_greywood", "reason": "The goal requires exploration.", "confidence": 0.8},
    ])
    try:
        assert AgentEngine(platform, provider).run(role="agent_decision", actor_id="lu_xingzhou").result["status"] == "accepted"
        cache = platform.metrics()["llm_cache"]
        assert cache["calls"] == 2
        assert cache["static_prefix_reuse_rate"] == 0.5
        assert cache["provider_cache_hit_rate"] is None
        assert cache["average_static_prefix_chars"] > 0
        assert cache["average_dynamic_chars"] > 0
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
        assert "memory (experience)" in provider.calls[0]["context"]
    finally:
        platform.close()


def test_reflection_cannot_record_an_experience_without_evidence(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "reflection-evidence-world", tmp_path / "runtime"))
    provider = SequenceProvider([
        {"intent_type": "record_experience", "event_ref": "Explore Greywood completion", "summary": "The map improved by fifteen percent and the sister route is confirmed."},
        {"intent_type": "wait", "reason": "No completed result is available yet."},
    ])
    try:
        assert platform.submit_behavior(BehaviorIntent(
            "lu_xingzhou", "behavior:explore_greywood", {}, {}, "Begin the survey.", 0.9, "reflection-evidence-active"
        ))["status"] == "accepted"
        turn = AgentEngine(platform, provider).run(role="agent_reflection", actor_id="lu_xingzhou", task_type="daily_reflection")
        assert turn.result["status"] == "wait"
        assert len(platform.store.list_memories("lu_xingzhou")) == 0
        assert turn.transcript[0]["step"] == "guard"
    finally:
        platform.close()


def test_reflection_summary_alias_is_committed_once(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "reflection-summary-world", tmp_path / "runtime"))
    try:
        intent = BehaviorIntent("lin_bo", "behavior:rest_and_recover", {}, {}, "Recover readiness.", 0.8, "reflection-summary-outcome")
        assert platform.submit_behavior(intent)["status"] == "accepted"
        step_n(platform, 48)
        provider = FakeProvider({"summary": "Rest restored energy while satiety continued to decline."})
        turn = AgentEngine(platform, provider).run(role="agent_reflection", actor_id="lin_bo", task_type="daily_reflection")
        assert turn.result["status"] == "accepted"
        assert turn.iterations == 1
        assert platform.store.list_memories("lin_bo", limit=1)[0]["type"] == "experience"
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


def test_validated_behavior_proposal_enters_agent_executable_catalog(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "evolving-bt-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        result = platform.submit_behavior_proposal("lu_xingzhou", {"behavior_id": "survey_then_explore", "goal": "Survey and explore the known boundary.", "action_ids": ["explore_area"], "entry_conditions": ["actor.can_explore"], "success_results": ["knowledge_updated"], "failure_results": ["interrupted"]})
        assert result["status"] == "accepted"
        view = platform.decision_view("lu_xingzhou")
        proposed = next(item for item in view["executable_behaviors"] if item["ref"] == "behavior:survey_then_explore")
        assert proposed["registration"] == "platform_validated"
    finally:
        platform.close()


def test_platform_rejects_equivalent_behavior_proposal(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "duplicate-bt-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        result = platform.submit_behavior_proposal(
            "lu_xingzhou",
            {"behavior_id": "new_explore", "agent_label": "Explore Greywood", "summary": "Explore Greywood", "goal": "Map Greywood", "action_ids": ["travel_to", "explore_area"]},
        )
        assert result["status"] == "rejected"
        assert any("equivalent" in error for error in result["errors"])
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


def test_memory_has_short_and_long_tiers_and_task_conditioned_recall(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "memory-tier-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            platform.store.put_memory(
                "lu_xingzhou", "testimony",
                {"summary": "The north gate watch saw fresh marks before dawn.", "importance": 0.8, "pending_decision": True},
                tier="short_term",
            )
            platform.store.put_memory(
                "lu_xingzhou", "experience",
                    {"summary": "Exploring Greywood confirms a safe river route before the fog rises.", "importance": 0.9},
                tier="long_term",
            )
        recalled = platform.memory.recall("lu_xingzhou", "north gate marks", limit=2)
        assert recalled["memories"][0]["tier"] == "short_term"
        assert "north gate" in recalled["memories"][0]["summary"].lower()
        view = platform.decision_view("lu_xingzhou")
        assert view["short_term_memories"]
        assert view["long_term_memories"]
        assert "Greywood" in view["memory_query"] or "north" in view["memory_query"].lower()
    finally:
        platform.close()


def test_memory_and_knowledge_use_structured_retrieval_dimensions(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "memory-dimensions-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            platform.store.put_memory(
                "lu_xingzhou",
                "experience",
                {"summary": "The north gate route is safe before fog.", "location_id": "north_gate", "behavior_ref": "patrol_route", "goal_tags": ["safety"], "importance": 0.9},
                tier="long_term",
            )
            platform.store.put_memory(
                "lu_xingzhou",
                "experience",
                {"summary": "The market sells cheap cloth.", "location_id": "inn", "behavior_ref": "trade", "goal_tags": ["trade"], "importance": 0.9},
                tier="long_term",
            )
            platform.store.put_knowledge_fact(
                "lu_xingzhou",
                "patrol:north_gate",
                {"fact": "Fresh marks were found at the north gate.", "location_id": "north_gate", "behavior": "patrol_route", "source_type": "patrol", "confidence": 0.9},
            )
            platform.store.put_knowledge_fact(
                "lu_xingzhou",
                "market:cloth",
                {"fact": "The market has cloth.", "location_id": "inn", "source_type": "trade", "confidence": 0.9},
            )
        recalled = platform.memory.retrieve(
            "lu_xingzhou",
            query="route north gate",
            limit=1,
            dimensions={"location": ["north_gate"], "behavior": ["patrol_route"]},
        )
        assert recalled[0]["tags"]["location"] == "北门"
        facts = platform.memory.relevant_facts(
            "lu_xingzhou", query="marks north gate", limit=2, dimensions={"location": ["north_gate"]}
        )
        assert facts[0]["fact_key"] == "patrol:north_gate"
        assert facts[0]["tags"]["behavior"] == "patrol_route"
        assert all(item["fact_key"] != "market:cloth" for item in facts)
    finally:
        platform.close()


def test_memory_bge_cache_discovery_matches_fastembed_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    snapshot = tmp_path / "models--Qdrant--bge-small-zh-v1.5" / "snapshots" / "revision"
    snapshot.mkdir(parents=True)
    (snapshot / "model_optimized.onnx").write_bytes(b"placeholder")
    (snapshot / "tokenizer.json").write_text("{}", encoding="utf-8")
    monkeypatch.delenv("SIM2_BGE_MODEL", raising=False)
    monkeypatch.setenv("SIM2_BGE_CACHE", str(tmp_path))
    resolved = ExperienceSimilarity._resolve_model_name({})
    assert resolved == str(snapshot)


def test_multiple_visible_participants_require_explicit_target(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "explicit-target-world", tmp_path / "runtime"))
    try:
        lin_bo = platform.store.get_agent("lin_bo")
        lin_bo["initial_state"]["location_id"] = "inn"
        with platform.store.transaction():
            platform.store.update_agent("lin_bo", lin_bo)
        view = platform.decision_view("fei_lan")
        communicate = next(item for item in view["executable_behaviors"] if item["ref"] == "behavior:communicate")
        assert communicate["target_required"] is True
        assert "Choose one participant explicitly" in communicate["target"]
        rejected = platform.submit_behavior(BehaviorIntent("fei_lan", "behavior:communicate", {}, {}, "Exchange information.", 0.8, "missing-target"))
        assert rejected["status"] == "rejected"
        assert rejected["failed_check"] == "target_required"
        assert set(rejected["available_targets"]) == {"余歌", "林泊"}
    finally:
        platform.close()


def test_nested_bt_target_is_explicit_and_hidden_target_is_rejected(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "nested-target-world", tmp_path / "runtime"))
    try:
        lin_bo = platform.store.get_agent("lin_bo")
        lin_bo["initial_state"]["location_id"] = "inn"
        fei_lan = platform.store.get_agent("fei_lan")
        fei_lan["initial_bt_ids"].append("nested_communicate")
        with platform.store.transaction():
            platform.store.update_agent("lin_bo", lin_bo)
            platform.store.update_agent("fei_lan", fei_lan)
            platform.store.put_definition("bt_def", "nested_communicate", {
                "id": "nested_communicate", "version": 1, "summary": "Nested communication",
                "nodes": [{"type": "sequence", "children": [{"type": "action", "action_id": "communicate", "parameters": {"target_id": "context.target_id", "topic": "general"}}]}],
            })
        view = platform.decision_view("fei_lan")
        nested = next(item for item in view["executable_behaviors"] if item["ref"] == "behavior:nested_communicate")
        assert nested["target_required"] is True
        missing = platform.submit_behavior(BehaviorIntent("fei_lan", "behavior:nested_communicate", {}, {}, "Talk to one visible person.", 0.8, "nested-missing"))
        assert missing["status"] == "rejected"
        assert missing["failed_check"] == "target_required"
        nested_hidden = platform.submit_behavior(BehaviorIntent("fei_lan", "behavior:nested_communicate", {}, {"target_id": "lu_xingzhou"}, "Talk to a hidden person.", 0.8, "nested-hidden"))
        assert nested_hidden["status"] == "rejected"
        assert nested_hidden["failed_check"] == "target_visibility"
        hidden = platform.submit_behavior(BehaviorIntent("fei_lan", "behavior:communicate", {}, {"target_id": "lu_xingzhou"}, "Talk to a hidden person.", 0.8, "hidden-target"))
        assert hidden["status"] == "rejected"
        assert hidden["failed_check"] == "target_visibility"
    finally:
        platform.close()


def test_god_view_contains_weekly_and_monthly_world_trends(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "god-trend-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            platform.store.append_event("resource.changed", "fei_lan", 100, {"resource_id": "village_food", "delta": -12})
            platform.store.append_event("relationship.changed", "fei_lan", 100, {"relation": "trust", "delta": 0.2, "target_id": "yu_ge"})
            platform.store.append_event("goal.updated", "fei_lan", 100, {"statement": "Restore the trade route"})
            platform.store.append_event("behavior.completed", "fei_lan", 100, {"behavior_id": "explore_greywood", "outcome": {"expected": {"goal_tags": ["exploration"]}}})
            platform.store.append_event("resource.changed", "fei_lan", 20000, {"resource_id": "village_food", "delta": -7})
        trends = platform.store.world_trends(current_minute=200)
        assert trends["weekly"]["resource_deltas"]["village_food"] == -12
        assert trends["weekly"]["relationship_trends"]
        assert trends["weekly"]["goal_trends"]["progress_by_tag"]["exploration"] == 1
        view = platform.view.build_decision_view("god", role="god")
        prompt = render_context(view, "Review world trends.")
        assert "<world_trends>" in prompt
        assert "weekly:" in prompt and "monthly:" in prompt
        assert "village_food" not in prompt
        assert "village food" in prompt
        long_window = platform.store.world_trends(current_minute=43200)
        assert long_window["monthly"]["resource_deltas"]["village_food"] == -19
        assert "village_food" not in long_window["weekly"]["resource_deltas"]
    finally:
        platform.close()


def test_world_property_proposal_normalizes_numeric_model_values(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "world-property-type-world", tmp_path / "runtime"))
    try:
        result = platform.submit_world_change(
            "operator",
            {"change_type": "world_property", "summary": "Stabilize the root gate.", "property": "root_gate_stability", "value": "0.75"},
        )
        assert result["status"] == "accepted"
        assert isinstance(result["effect"]["after"], float)
        assert platform.store.get_meta("world_properties")["root_gate_stability"] == 0.75
        rejected = platform.submit_world_change(
            "operator",
            {"change_type": "world_property", "summary": "Break the numeric contract.", "property": "root_gate_stability", "value": "not-a-number"},
        )
        assert rejected["status"] == "rejected"
        assert rejected["failed_check"] == "property_type"
    finally:
        platform.close()


def test_world_property_accepts_readable_label_without_creating_duplicate_key(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "world-property-label-world", tmp_path / "runtime"))
    try:
        result = platform.submit_world_change(
            "operator",
            {"change_type": "world_property", "summary": "Ease the gate pressure.", "property": "north gate threat level", "value": 0.18},
        )
        assert result["status"] == "accepted"
        properties = platform.store.get_meta("world_properties")
        assert properties["north_gate_threat_level"] == 0.18
        assert "north gate threat level" not in properties
    finally:
        platform.close()


def test_successful_recipe_records_capability_evidence_without_auto_upgrade(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "capability-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        agent = platform.store.get_agent("liang_he")
        agent["inventory"].update({"grain": 1})
        with platform.store.transaction():
            platform.store.update_agent("liang_he", agent)
        intent = BehaviorIntent("liang_he", "behavior:craft_meal", {}, {}, "Prepare food", 0.8, "capability-intent")
        assert platform.submit_behavior(intent)["status"] == "accepted"
        step_n(platform, 60)
        history = platform.store.list_capability_progress("liang_he")
        assert history == []
        evidence = platform.store.recent_events_for_actor("liang_he", limit=20)
        assert any(item["event_type"] == "capability.evidence" and item["capability"] == "cooking" for item in evidence)
        assert platform.store.get_entity("clean_water")["amount"] == 99
    finally:
        platform.close()


def test_capability_update_requires_repeated_outcomes_and_is_agent_proposed(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "capability-proposal-world", tmp_path / "runtime"))
    try:
        rejected = platform.submit_capability_update("liang_he", "cooking", 0.04, "One meal completed.")
        assert rejected["status"] == "rejected"
        assert rejected["failed_check"] == "insufficient_evidence"
        for minute in (1, 2, 3):
            platform.store.append_event("behavior.completed", "liang_he", minute, {"behavior_id": "craft_meal", "result": "recipe_completed"})
        platform.store.connection.commit()
        accepted = platform.submit_capability_update("liang_he", "cooking", 0.04, "Three configured meals completed without a failure.")
        assert accepted["status"] == "accepted"
        assert platform.store.get_agent("liang_he")["capabilities"]["cooking"] == 0.49
        assert platform.store.list_capability_progress("liang_he")[0]["source"] == "agent_review"
    finally:
        platform.close()


def test_world_progress_milestone_wakes_story_and_god_from_agent_proposal(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "civilization-progress-world", tmp_path / "runtime"))
    try:
        platform.store.append_event("behavior.completed", "lin_bo", 1, {"behavior_id": "govern_village", "result": "behavior_completed"})
        platform.store.connection.commit()
        result = platform.submit_world_progress("story", "governance_capacity", 5, "Five governance reviews produced public reserve and risk reports.")
        assert result["status"] == "accepted"
        assert result["milestone"] is True
        assert platform.store.get_progress(platform.store.get_meta("world_id"), "governance_capacity")["current"] == 5
        tasks = platform.ready_evolution_tasks()
        assert any(item["role"] == "story" for item in tasks)
        assert any(item["role"] == "god" for item in tasks)
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


def test_perception_channels_are_scoped_and_model_readable(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "perception-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        executor = AgentToolExecutor(platform, role="agent_decision", actor_id="lu_xingzhou")
        listened = executor.execute("listen", {"question": "What happened nearby?"})
        assert listened["status"] == "ok"
        known = executor.execute("query_known_fact", {"query": "village"})
        assert known["status"] == "ok"
        public = executor.execute("read_public_record", {"entity_id": "village_square"})
        assert public["status"] == "ok"
    finally:
        platform.close()


def test_read_tool_projection_hides_internal_execution_fields(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "perception-redaction-world", tmp_path / "runtime"))
    try:
        executor = AgentToolExecutor(platform, role="agent_decision", actor_id="yu_ge")
        result = executor.execute("inspect_entity", {"entity": "费兰"})
        encoded = json.dumps(result, ensure_ascii=False)
        assert result["status"] == "ok"
        assert "active_bt_instance" not in encoded
        assert "behavior:communicate" not in encoded
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
        first = platform.step()
        assert first["to_minute"] == 1
        state = platform.store.get_agent("lu_xingzhou")["initial_state"]
        assert state["status"] == "executing"
        assert state["active_action_index"] == 1
        platform.step()
        state = platform.store.get_agent("lu_xingzhou")["initial_state"]
        assert state["active_action"] == "explore_area"
        assert state["remaining_minutes"] == 239
        row = platform.store.connection.execute("SELECT status,remaining_minutes FROM action_instance WHERE execution_id=? AND action_id=?", ("progress-intent", "explore_area")).fetchone()
        assert row["status"] == "running"
        assert row["remaining_minutes"] == 239
    finally:
        platform.close()


def test_route_duration_is_derived_from_configured_route_distance(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "route-duration-world", tmp_path / "runtime"))
    try:
        result = platform.submit_behavior(BehaviorIntent("lu_xingzhou", "behavior:travel", {"location": "inn"}, {}, "Travel to the inn.", 0.8, "route-duration"))
        assert result["status"] == "accepted"
        assert result["remaining_minutes"] == 3
    finally:
        platform.close()


def test_finite_resource_depletion_hides_gather_until_regeneration(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "resource-cycle-world", tmp_path / "runtime"))
    try:
        grain = platform.store.get_entity("grain")
        grain["initial_state"]["quantity"] = 0
        platform.store.put_entity("grain", "material", "谷物", {key: value for key, value in grain.items() if key not in {"id", "entity_type", "name"}})
        platform.store.connection.commit()
        assert not any(item["ref"] == "behavior:gather_resource" for item in platform.decision_view("liang_he")["executable_behaviors"])
        for _ in range(1440):
            platform.step()
        assert platform.store.get_entity("grain")["initial_state"]["quantity"] == 20.0
        assert any(item["ref"] == "behavior:gather_resource" for item in platform.decision_view("liang_he")["executable_behaviors"])
    finally:
        platform.close()


def test_target_effects_update_target_health_not_actor(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "target-effects-world", tmp_path / "runtime"))
    try:
        attacker = platform.store.get_agent("he_yan")
        target = platform.store.get_agent("lu_xingzhou")
        target["initial_state"]["health"] = 70
        platform.store.update_agent("lu_xingzhou", target)
        platform.store.connection.commit()
        attack = next(item for item in platform.store.get_definitions("action_def") if item["id"] == "attack")
        with platform.store.transaction():
            platform._apply_success_effects(attacker, attack, {"weapon_id": "spear"}, 0, target={"id": "lu_xingzhou", "defense": 4})
        assert platform.store.get_agent("he_yan")["initial_state"]["health"] == 100
        assert platform.store.get_agent("lu_xingzhou")["initial_state"]["health"] == 63
        relationship = next(item for item in platform.store.list_relationships() if item["from"] == "he_yan" and item["to"] == "lu_xingzhou" and item["type"] == "hostility")
        assert relationship["relation_strength"] > 0
    finally:
        platform.close()


def test_public_event_and_world_property_effects_commit_through_platform(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "effect-contract-world", tmp_path / "runtime"))
    try:
        action = {"id": "signal", "effects": [
            {"type": "public_event", "event": "signal.raised"},
            {"type": "world_property_delta", "property": "north_gate_threat_level", "delta": 0.1},
        ]}
        with platform.store.transaction():
            platform._apply_success_effects(platform.store.get_agent("he_yan"), action, {}, 0)
        assert platform.store.get_meta("world_properties")["north_gate_threat_level"] == 0.32
        assert any(item["event_type"] == "signal.raised" for item in platform.store.recent_events(limit=10))
    finally:
        platform.close()


def test_action_failure_condition_is_evaluated_at_completion(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "action-failure-world", tmp_path / "runtime"))
    try:
        action = {
            "id": "stun_sensitive_action",
            "parameters": {},
            "preconditions": ["actor.alive"],
            "duration": {"minutes": 1},
            "costs": {"reserve": {}, "per_minute": {}},
            "success": {"result": "done"},
            "failure": {"condition": "actor.stunned", "result": "stunned"},
        }
        behavior = {
            "id": "stun_sensitive_behavior",
            "version": 1,
            "scope": "personal",
            "entry_conditions": ["actor.alive"],
            "nodes": [{"type": "action", "action_id": "stun_sensitive_action"}],
            "success_results": ["done"],
            "failure_results": ["stunned"],
        }
        platform.store.put_definition("action_def", action["id"], action)
        platform.store.put_definition("bt_def", behavior["id"], behavior)
        agent = platform.store.get_agent("su_wu")
        agent["initial_bt_ids"].append(behavior["id"])
        agent["initial_state"]["conditions"] = ["stunned"]
        platform.store.update_agent("su_wu", agent)
        platform.store.connection.commit()
        result = platform.submit_behavior(BehaviorIntent("su_wu", "behavior:stun_sensitive_behavior", {}, {}, "Try the action.", 0.8, "stun-failure"))
        assert result["status"] == "accepted"
        platform.step()
        assert any(item["event_type"] == "behavior.failed" and item["actor_id"] == "su_wu" for item in platform.store.recent_events(limit=10))
    finally:
        platform.close()


def test_role_goal_paths_produce_medicine_and_dawn_bell(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "goal-production-world", tmp_path / "runtime"))
    try:
        medicine = platform.submit_behavior(BehaviorIntent("qiao_ning", "behavior:craft_medicine", {}, {}, "Prepare medicine for the fever cases.", 0.9, "medicine-goal"))
        bell = platform.submit_behavior(BehaviorIntent("tian_yan", "behavior:craft_dawn_bell", {}, {}, "Forge the warning bell before the fog cycle.", 0.9, "bell-goal"))
        assert medicine["status"] == "accepted"
        assert bell["status"] == "accepted"
        for _ in range(120):
            platform.step()
        assert platform.store.get_agent("qiao_ning")["inventory"].get("medicine", 0) == 1
        assert platform.store.get_agent("tian_yan")["inventory"].get("dawn_bell", 0) == 1
        assert platform.store.get_entity("dawn_bell")["initial_state"]["created"] is True
        assert not any(item["ref"] == "behavior:craft_dawn_bell" for item in platform.decision_view("tian_yan")["executable_behaviors"])
    finally:
        platform.close()


def test_water_research_path_records_a_specific_fact(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "water-research-world", tmp_path / "runtime"))
    try:
        for attempt in range(2):
            if attempt:
                agent = platform.store.get_agent("qiao_ning")
                agent["initial_state"]["attention"] = 0
                agent["initial_state"]["energy"] = 90
                with platform.store.transaction():
                    platform.store.update_agent("qiao_ning", agent)
            result = platform.submit_behavior(BehaviorIntent("qiao_ning", "behavior:research_water", {}, {}, "Test the river water for the fever investigation.", 0.9, f"water-research-{attempt}"))
            assert result["status"] == "accepted"
            for _ in range(result["remaining_minutes"]):
                platform.step()
            if attempt == 0:
                assert not any(task["role"] == "story" for task in platform.ready_evolution_tasks())
            else:
                assert any(task["role"] == "story" for task in platform.ready_evolution_tasks())
        facts = platform.store.list_knowledge_facts("qiao_ning", limit=30)
        assert any(item["fact_key"] == "researched:clean_water:contamination" for item in facts)
    finally:
        platform.close()


def test_story_feedback_runs_at_completion_boundary_and_updates_audience(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "story-boundary-world", tmp_path / "runtime"))
    provider = SequenceProvider([{"intent_type": "propose_story_beat", "beat": "Reconsider the old pact"}])
    try:
        for attempt in range(2):
            agent = platform.store.get_agent("su_wu")
            agent["initial_state"]["attention"] = 0
            agent["initial_state"]["energy"] = 90
            with platform.store.transaction():
                platform.store.update_agent("su_wu", agent)
            result = platform.submit_behavior(BehaviorIntent("su_wu", "behavior:research_source", {}, {}, "Study the pact source.", 0.9, f"story-research-{attempt}"))
            assert result["status"] == "accepted"
            for _ in range(result["remaining_minutes"]):
                platform.step()
        boundary = int(platform.store.get_meta("world_minute", 0))
        report = SimulationRunner(platform, {"story": provider}).run(1)
        assert report.story_sessions == 1
        assert platform.store.get_meta("world_minute") == boundary + 1
        assert platform.status()["status"] == "running"
        state = platform.store.get_meta("story_state")
        assert state["old_pact_understood"]["status"] == "active"
        assert "Decide whether the village should renew, change, or end the old pact." in platform.store.get_agent("lin_bo")["story_goals"]
        events = platform.store.recent_events(limit=4)
        story_event = next(item for item in events if item["event_type"] == "story.beat.activated")
        minute_event = next(item for item in events if item["event_type"] == "world_minute.advanced")
        assert story_event["world_minute"] == boundary
        assert minute_event["world_minute"] == boundary + 1
    finally:
        platform.close()


def test_emergent_story_beat_is_not_repeated_for_one_validated_result(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "story-dedup-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            source_event_id = platform.store.append_event(
                "relationship.changed",
                "fei_lan",
                0,
                {"target_id": "yu_ge", "relation": "trust", "delta": 0.1},
            )
        proposal = {
            "beat": "emergent: route testimony",
            "summary": "A validated testimony changes how the village reads the trade route.",
            "fact": "The trade route testimony was recorded.",
            "goal_hint": "Compare the testimony with the next route report.",
            "audience": ["林泊"],
        }
        first = platform.submit_story_beat("story", proposal)
        second = platform.submit_story_beat("story", {**proposal, "summary": "A second wording for the same validated testimony."})
        assert first["status"] == "accepted"
        assert second["status"] == "wait"
        assert second["reason"].startswith("This validated result already")
        assert platform.store.recent_events(limit=20)[0]["event_type"] == "story.beat.activated"
        saved = platform.store.get_meta("story_state")["emergent_beats"]
        assert saved[-1]["source_event_id"] == source_event_id
    finally:
        platform.close()


def test_step_commits_each_tick_before_a_later_tick_fails(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "tick-commit-world", tmp_path / "runtime")
    platform = Platform(db_path)
    original = platform._apply_minute_effects

    def fail_on_second_tick(minute: int):
        if minute == 2:
            raise RuntimeError("synthetic tick failure")
        return original(minute)

    platform._apply_minute_effects = fail_on_second_tick
    try:
        with pytest.raises(RuntimeError, match="synthetic tick failure"):
            step_n(platform, 3)
        assert platform.store.get_meta("world_minute") == 1
        event = platform.store.recent_events(limit=5)
        assert any(item["event_type"] == "world_minute.advanced" and item["world_minute"] == 1 for item in event)
        assert not any(item["event_type"] == "world_minute.advanced" and item["world_minute"] == 2 for item in event)
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
        starting_rations = platform.store.get_agent("lu_xingzhou")["inventory"]["rations"]
        platform.submit_behavior(intent)
        step_n(platform, 2)
        assert platform.store.get_agent("lu_xingzhou")["inventory"]["rations"] == starting_rations - 1
        paused = platform.interrupt_behavior("lu_xingzhou", "urgent boundary signal", hard=False)
        assert paused["status"] == "paused"
        state = platform.store.get_agent("lu_xingzhou")["initial_state"]
        assert state["status"] == "paused"
        remaining_before_resume = state["remaining_minutes"]
        row = platform.store.connection.execute("SELECT status FROM action_instance WHERE execution_id=? AND status=?", ("interrupt-intent", "paused")).fetchone()
        assert row is not None
        resumed = platform.resume_behavior("lu_xingzhou")
        assert resumed["status"] == "resumed"
        assert platform.store.get_agent("lu_xingzhou")["initial_state"]["remaining_minutes"] == remaining_before_resume
        platform.step()
        assert platform.store.get_agent("lu_xingzhou")["initial_state"]["remaining_minutes"] == remaining_before_resume - 1
        cancelled = platform.interrupt_behavior("lu_xingzhou", "threat confirmed", hard=True)
        assert cancelled["status"] == "cancelled"
        assert platform.store.get_agent("lu_xingzhou")["initial_state"]["status"] == "idle"
        assert platform.store.get_agent("lu_xingzhou")["inventory"]["rations"] == starting_rations
    finally:
        platform.close()


def test_direct_interaction_does_not_interrupt_active_behavior(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "interaction-boundary-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        listener = platform.store.get_agent("qiao_ning")
        listener["initial_state"]["location_id"] = "granary"
        with platform.store.transaction():
            platform.store.update_agent("qiao_ning", listener)
        intent = BehaviorIntent(
            actor_id="liang_he", behavior_ref="behavior:craft_meal", target={}, parameters={},
            reason="Prepare food before trading.", confidence=0.8, intent_id="interaction-boundary-intent",
        )
        assert platform.submit_behavior(intent)["status"] == "accepted"
        platform.step()
        result = platform.submit_dialogue("liang_he", "qiao_ning", "trade", "Can we trade now?")
        assert result["status"] == "rejected"
        assert result["failed_check"] == "participant_busy"
        trade = platform.create_trade_offer("liang_he", "qiao_ning", {"grain": 1}, {"medicine": 1})
        assert trade["status"] == "rejected"
        assert trade["failed_check"] == "participant_busy"
        state = platform.store.get_agent("liang_he")["initial_state"]
        assert state["status"] == "executing"
        assert state["active_bt_instance"] == "interaction-boundary-intent"
    finally:
        platform.close()


def test_behavior_outcome_is_available_to_nightly_reflection(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "outcome-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        intent = BehaviorIntent("lin_bo", "behavior:rest_and_recover", {}, {}, "Recover before the next duty.", 0.8, "outcome-intent")
        assert platform.submit_behavior(intent)["status"] == "accepted"
        step_n(platform, 49)
        events = platform.store.recent_events_for_actor("lin_bo", limit=20)
        outcome_event = next(item for item in events if item["event_type"] == "behavior.completed")
        outcome = outcome_event["outcome"]
        assert outcome["expected"]["summary"]
        assert "actual" in outcome and "delta" in outcome
        assert platform.ready_evolution_tasks() == []
        view = platform.decision_view("lin_bo", role="agent_reflection")
        assert view["recent_outcomes"][0]["event_type"] == "behavior.completed"
        prompt = render_context(view, "Review the latest nightly outcome.")
        assert "<outcome_review>" in prompt
        assert "expected=" in prompt
    finally:
        platform.close()


def test_configured_behavior_outcome_updates_social_and_growth_needs(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "need-outcome-world", tmp_path / "runtime"))
    try:
        before = platform.store.get_agent("lin_bo")["needs"]
        intent = BehaviorIntent("lin_bo", "behavior:govern_village", {}, {}, "Review village stability.", 0.9, "need-outcome-intent")
        assert platform.submit_behavior(intent)["status"] == "accepted"
        step_n(platform, 24)
        after = platform.store.get_agent("lin_bo")["needs"]
        assert after["esteem"] == before["esteem"] + 0.5
        assert after["belonging"] == before["belonging"] + 0.2
    finally:
        platform.close()


def test_parallel_bt_runs_action_instances_concurrently(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "parallel-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        with platform.store.transaction():
            platform.store.put_definition("bt_def", "parallel_test", {"id": "parallel_test", "version": 1, "summary": "Run two independent tasks", "estimated_minutes": 48, "nodes": [{"type": "parallel", "children": [{"type": "action", "action_id": "rest", "parameters": {"location_id": "context.location_id"}}, {"type": "action", "action_id": "travel_to", "parameters": {"location_id": "watchtower"}}]}]})
            agent = platform.store.get_agent("lu_xingzhou")
            agent["initial_state"]["location_id"] = "village_square"
            agent["initial_bt_ids"].append("parallel_test")
            platform.store.update_agent("lu_xingzhou", agent)
        intent = BehaviorIntent("lu_xingzhou", "behavior:parallel_test", {}, {}, "Run parallel work", 0.8, "parallel-intent")
        assert platform.submit_behavior(intent)["status"] == "accepted"
        platform.step()
        rows = platform.store.connection.execute("SELECT action_id,status,remaining_minutes FROM action_instance WHERE execution_id=? ORDER BY action_id", ("parallel-intent",)).fetchall()
        assert len(rows) == 2
        assert rows[0]["status"] == "running" or rows[1]["status"] == "running"
        assert platform.store.get_agent("lu_xingzhou")["initial_state"]["active_action"] == "parallel"
        step_n(platform, 47)
        assert platform.store.get_agent("lu_xingzhou")["initial_state"]["status"] == "idle"
    finally:
        platform.close()


def test_generic_condition_and_effect_components():
    agent = {"role": "pathfinder", "capabilities": {"exploration": 0.8}, "initial_state": {"health": 100, "energy": 20, "conditions": []}, "inventory": {}}
    conditions = ConditionEngine({"capability_aliases": {"explore": ["exploration"]}})
    assert conditions.evaluate("actor.alive and actor.can_explore", agent=agent)
    assert not conditions.evaluate("actor.can_fly", agent=agent)
    agent["faction_ids"] = ["village_watch"]
    assert conditions.evaluate("actor.faction_id not_in [root_keepers]", agent=agent)
    effects = EffectPipeline()
    result = effects.apply(agent, {"type": "inventory_add", "item": "map", "quantity": 1})
    assert result["applied"] is True
    assert agent["inventory"]["map"] == 1
    effects.apply(agent, {"type": "need_delta", "need": "energy", "delta": 5})
    assert agent["initial_state"]["energy"] == 25
    target = {"id": "target", "name": "Target", "entity_type": "agent", "initial_state": {"location_id": "entry", "health": 100}, "combat_stats": {"defense": 2}}
    # TargetResolver is store-backed in production; the formula behavior is
    # validated separately here with the same public projection shape.
    damage = EffectPipeline().apply(agent, {"type": "damage", "formula": "actor.attack_power - target.defense"}, target={"defense": 2})
    assert damage["applied"] is True


def test_hybrid_drive_model_has_zero_baseline_and_separate_components():
    calculator = DriveCalculator({"needs": [{"id": "safety", "target": 100, "gamma": 2, "critical_threshold": 0.8, "critical_gain": 2}]})
    calm = calculator.calculate({"needs": {"safety": 100}})[0]
    pressured = calculator.calculate({"needs": {"safety": 20}, "external_pressure": {"safety": 0.4}, "relation_pressure": {"safety": 0.2}})[0]
    assert calm["urgency"] < 0.01
    assert pressured["urgency"] > calm["urgency"]
    assert set(pressured["components"]) >= {"internal", "external", "social", "novelty", "inhibition"}


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
        report = step_n(platform, 241)
        assert report["to_minute"] == 241
        fact = platform.store.connection.execute("SELECT area_id FROM exploration_result WHERE agent_id=?", ("lu_xingzhou",)).fetchone()
        assert fact["area_id"] == "greywood_edge"
        assert any(item["fact_key"] == "explored:greywood_edge" for item in platform.store.list_knowledge_facts("lu_xingzhou"))
    finally:
        platform.close()


def test_completed_exploration_is_not_offered_again_and_keeps_discoveries(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "exploration-novelty-world", tmp_path / "runtime"))
    try:
        assert platform.submit_behavior(BehaviorIntent(
            "lu_xingzhou", "behavior:explore_greywood", {}, {}, "Survey the unknown edge.", 0.9, "exploration-novelty"
        ))["status"] == "accepted"
        step_n(platform, 241)
        facts = platform.store.list_knowledge_facts("lu_xingzhou", limit=20)
        assert any(item["fact_key"] == "explored:greywood_edge:route" for item in facts)
        assert not any(item["ref"] == "behavior:explore_greywood" for item in platform.decision_view("lu_xingzhou")["executable_behaviors"])
    finally:
        platform.close()


def test_exploration_unlocks_a_concrete_follow_up_and_changes_the_world(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "exploration-follow-up-world", tmp_path / "runtime"))
    try:
        before_trust = float((platform.store.get_meta("world_properties", {}) or {})["public_trust"])
        assert not any(item["ref"] == "behavior:recover_caravan_ledger" for item in platform.decision_view("lu_xingzhou")["executable_behaviors"])
        assert platform.submit_behavior(BehaviorIntent(
            "lu_xingzhou", "behavior:explore_greywood", {}, {}, "Survey the unknown edge.", 0.9, "exploration-follow-up"
        ))["status"] == "accepted"
        step_n(platform, 241)
        offered = {item["ref"] for item in platform.decision_view("lu_xingzhou")["executable_behaviors"]}
        assert "behavior:recover_caravan_ledger" in offered
        assert platform.submit_behavior(BehaviorIntent(
            "lu_xingzhou", "behavior:recover_caravan_ledger", {}, {}, "Recover the ledger found during the survey.", 0.9, "recover-ledger"
        ))["status"] == "accepted"
        step_n(platform, 180)
        ledger = platform.store.get_entity("caravan_ledger")
        assert ledger["initial_state"]["recovered"] is True
        after_trust = float((platform.store.get_meta("world_properties", {}) or {})["public_trust"])
        assert after_trust > before_trust
        facts = platform.store.list_knowledge_facts("lu_xingzhou", limit=30)
        assert any(item["fact_key"] == "recovered:caravan_ledger" for item in facts)
        assert not any(item["ref"] == "behavior:recover_caravan_ledger" for item in platform.decision_view("lu_xingzhou")["executable_behaviors"])
        assert any(item["event_type"] == "object.recovered" for item in platform.store.recent_events(limit=30))
    finally:
        platform.close()


def test_completed_research_records_fact_and_is_not_offered_again(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "research-novelty-world", tmp_path / "runtime"))
    try:
        for attempt in range(2):
            if attempt:
                agent = platform.store.get_agent("su_wu")
                agent["initial_state"]["attention"] = 0
                agent["initial_state"]["energy"] = 90
                with platform.store.transaction():
                    platform.store.update_agent("su_wu", agent)
            assert platform.submit_behavior(BehaviorIntent(
                "su_wu", "behavior:research_source", {}, {}, "Study the pact source.", 0.9, f"research-novelty-{attempt}"
            ))["status"] == "accepted"
            step_n(platform, 192)
        facts = platform.store.list_knowledge_facts("su_wu", limit=20)
        assert any(item["fact_key"] == "researched:old_pact_tablet:origin" for item in facts)
        assert not any(item["ref"] == "behavior:research_source" for item in platform.decision_view("su_wu")["executable_behaviors"])
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
        step_n(platform, 2)
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


def test_god_world_change_and_trigger_create_platform_state(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "evolution-trigger-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        change = platform.submit_world_change("operator", {"change_type": "world_property", "summary": "Adjust fog pressure.", "property": "fog_pressure", "value": 0.4, "expected_effects": ["Fog pressure is recorded as 0.4."]})
        assert change["status"] == "accepted"
        assert platform.store.get_meta("world_properties")["fog_pressure"] == 0.4
        world_change = platform.store.recent_world_change_events(limit=1)[0]
        assert world_change["expected_effects"] == ["Fog pressure is recorded as 0.4."]
        resource = platform.store.get_entity("village_food")
        resource["amount"] = 100
        with platform.store.transaction():
            platform.store.put_entity("village_food", resource["entity_type"], resource["name"], {key: value for key, value in resource.items() if key not in {"id", "entity_type", "name"}})
        platform.step()
        tasks = platform.ready_evolution_tasks()
        assert any(task.get("trigger_id") == "food_crisis" for task in tasks)
    finally:
        platform.close()


def test_property_and_shared_resource_projection_stay_in_sync(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "resource-projection-world", tmp_path / "runtime"))
    try:
        resource = platform.store.get_entity("village_food")
        resource["amount"] = 100
        with platform.store.transaction():
            platform.store.put_entity("village_food", resource["entity_type"], resource["name"], {key: value for key, value in resource.items() if key not in {"id", "entity_type", "name"}})
        platform.step()
        assert any(task.get("trigger_id") == "food_crisis" for task in platform.ready_evolution_tasks())
        change = platform.submit_world_change("operator", {
            "change_type": "world_property",
            "summary": "Record the approved emergency food reserve.",
            "property": "village food",
            "value": 180,
            "expected_effects": ["Village food is recorded as 180."],
        })
        assert change["status"] == "accepted"
        assert platform.store.get_meta("world_properties")["village_food"] == 180
        assert platform.store.get_entity("village_food")["amount"] == 180
        assert change["effect"]["resource_projection"] == {"entity_id": "village_food", "before": 100, "after": 180}
    finally:
        platform.close()


def test_god_cannot_directly_mutate_shared_resource(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "god-resource-policy-world", tmp_path / "runtime"))
    try:
        rejected = platform.submit_world_change("god", {
            "change_type": "world_property",
            "summary": "Give the village more food.",
            "property": "village food",
            "value": 420,
        })
        assert rejected["status"] == "rejected"
        assert rejected["failed_check"] == "god_direct_property_change"
        assert platform.store.get_entity("village_food")["amount"] == 320
    finally:
        platform.close()


def test_god_cannot_create_stocked_resource_without_behavior_result(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "god-stocked-resource-policy-world", tmp_path / "runtime"))
    try:
        rejected = platform.submit_entity_proposal("god", {
            "entity_type": "resource",
            "name": "Unfounded Grain",
            "location_id": "granary",
            "state": {"quantity": 60},
        })
        assert rejected["status"] == "rejected"
        assert rejected["failed_check"] == "god_resource_creation"
    finally:
        platform.close()


def test_god_reacts_after_event_effect_rejection(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "god-quality-world", tmp_path / "runtime")
    platform = Platform(db_path)
    provider = SequenceProvider([
        {"intent_type": "propose_world_change", "change_type": "event", "summary": "Mobilize a public response.", "effects": ["stabilize the clinic"]},
        {"intent_type": "propose_world_change", "change_type": "rule", "summary": "Record the clinic report rule.", "rule": {"id": "clinic_fever_response", "condition": "clinic fever is reported", "effect": "open the configured treatment response"}},
    ])
    try:
        view = platform.decision_view("god", role="god")
        turn = AgentEngine(platform, provider).run(role="god", actor_id="god", task_type="quality")
        quality = evaluate_turn(turn, view)
        assert turn.result["status"] == "accepted"
        assert len(provider.calls) == 2
        assert quality["passed"] is False
        assert quality["grade"] == "accepted_needs_world_followup"
        assert platform.store.get_meta("rules")["world_rules"][-1]["id"] == "clinic_fever_response"
    finally:
        platform.close()


def test_god_wait_is_a_valid_quality_outcome_when_no_proposal_is_supported(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "god-wait-quality-world", tmp_path / "runtime"))
    provider = SequenceProvider([{"intent": "wait", "reason": "No evidence-backed world change is needed."}])
    try:
        view = platform.decision_view("god", role="god")
        turn = AgentEngine(platform, provider).run(role="god", actor_id="god", task_type="quality")
        quality = evaluate_turn(turn, view)
        assert turn.result["status"] == "wait"
        assert quality["passed"] is True
        assert quality["grade"] == "grounded"
    finally:
        platform.close()


def test_god_trigger_rejects_narrative_only_change_before_platform_commit(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "god-trigger-quality-world", tmp_path / "runtime")
    platform = Platform(db_path)
    provider = SequenceProvider([
        {"tool_call": {"name": "propose_world_change", "arguments": {"change_type": "event", "summary": "A bell sounds.", "effects": ["People notice the bell."]}}},
        {"tool_call": {"name": "propose_world_change", "arguments": {"change_type": "rule", "summary": "Record the food crisis response rule.", "rule": {"id": "food_crisis_response", "condition": "food crisis is reported", "effect": "use the configured ration behavior"}, "expected_effects": ["The crisis response rule is recorded."]}}},
    ])
    try:
        turn = AgentEngine(platform, provider).run(role="god", actor_id="god", task_type="trigger:food_crisis")
        assert turn.result["status"] == "accepted"
        assert len(provider.calls) == 2
        assert not platform.store.connection.execute("SELECT 1 FROM event_log WHERE event_type='world.change.event'").fetchone()
        assert platform.store.get_meta("rules")["world_rules"][-1]["id"] == "food_crisis_response"
    finally:
        platform.close()


def test_world_rule_signal_wakes_god_once_and_rearms_after_recovery(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "world-rule-signal-world", tmp_path / "runtime"))
    try:
        resource = platform.store.get_entity("village_food")
        resource["amount"] = 100
        with platform.store.transaction():
            platform.store.put_entity("village_food", resource["entity_type"], resource["name"], {key: value for key, value in resource.items() if key not in {"id", "entity_type", "name"}})
        platform.step()
        signals = [item for item in platform.store.recent_events(limit=30) if item["event_type"] == "world.rule.public_ration"]
        assert len(signals) == 1
        assert any(item["role"] == "god" and item["task_type"] == "rule:public_ration" for item in platform.ready_evolution_tasks())
        started = platform.submit_behavior(BehaviorIntent("liang_he", "behavior:manage_ration", {}, {}, "Review the low food reserve.", 0.8, "rule-followup"))
        assert started["status"] == "accepted"
        step_n(platform, started["remaining_minutes"])
        followups = [item for item in platform.store.recent_events(limit=100) if item["event_type"] == "world.rule.followed"]
        assert any(item["rule"] == "public_ration" and item["behavior_id"] == "manage_ration" for item in followups)
        platform.step()
        assert len([item for item in platform.store.recent_events(limit=500) if item["event_type"] == "world.rule.public_ration"]) == 1

        resource = platform.store.get_entity("village_food")
        resource["amount"] = 160
        with platform.store.transaction():
            platform.store.put_entity("village_food", resource["entity_type"], resource["name"], {key: value for key, value in resource.items() if key not in {"id", "entity_type", "name"}})
        platform.step()
        resource = platform.store.get_entity("village_food")
        resource["amount"] = 100
        with platform.store.transaction():
            platform.store.put_entity("village_food", resource["entity_type"], resource["name"], {key: value for key, value in resource.items() if key not in {"id", "entity_type", "name"}})
        platform.step()
        assert len([item for item in platform.store.recent_events(limit=500) if item["event_type"] == "world.rule.public_ration"]) == 2
    finally:
        platform.close()


def test_invalid_world_rule_is_rejected_before_persistence(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "world-rule-validation-world", tmp_path / "runtime"))
    try:
        result = platform.submit_world_change("god", {
            "change_type": "rule",
            "summary": "Unclear policy.",
            "rule": {"id": "missing-effect", "condition": "world.water_quality < 0.5"},
        })
        assert result["status"] == "rejected"
        assert result["failed_check"] == "rule_schema"
        assert not any(item.get("id") == "missing-effect" for item in (platform.store.get_meta("rules", {}) or {}).get("world_rules", []))
        unsupported = platform.submit_world_change("god", {
            "change_type": "rule",
            "summary": "Unknown projection.",
            "rule": {"id": "unknown-projection", "condition": "world.clinic.fever_reported == true", "effect": "clinic.alert", "behavior_names": ["Inspect Object"]},
        })
        assert unsupported["status"] == "rejected"
        assert unsupported["failed_check"] == "rule_schema"
        compound = platform.submit_world_change("god", {
            "change_type": "rule",
            "summary": "Compound effect.",
            "rule": {"id": "compound-effect", "condition": "world.water_quality < 0.8", "effect": "watch.alert and deny_access", "behavior_names": ["Patrol Route"]},
        })
        assert compound["status"] == "rejected"
        assert compound["failed_check"] == "rule_schema"
        signal_claiming_gate = platform.submit_world_change("god", {
            "change_type": "rule",
            "summary": "Signal a review before the next ration decision.",
            "effects": ["The ration decision is prioritized before trade."],
            "expected_effects": ["The named behavior must wait until review."],
            "rule": {"id": "signal-claimed-as-gate", "condition": "world.water_quality < 0.8", "effect": "council.review_alert", "behavior_names": ["Manage Ration"]},
        })
        assert signal_claiming_gate["status"] == "rejected"
        assert signal_claiming_gate["failed_check"] == "rule_effect_semantics"
        temporal_signal = platform.submit_world_change("god", {
            "change_type": "rule",
            "summary": "Record the patrol follow-up before the next cycle.",
            "effects": ["The watch signal is recorded before the next patrol."],
            "expected_effects": ["The watch reviews the report before the next patrol."],
            "rule": {"id": "temporal-signal", "condition": "event.recent.governance.reviewed == true", "effect": "watch.review_alert"},
        })
        assert temporal_signal["status"] == "accepted"
        condition_mismatch = platform.submit_world_change("god", {
            "change_type": "rule",
            "summary": "Keep the explicit food condition in the policy.",
            "conditions": ["event.recent.governance.reviewed == true", "world[\"village food\"] < 200"],
            "rule": {"id": "condition-mismatch", "condition": "event.recent.governance.reviewed == true", "effect": "council.review_alert"},
        })
        assert condition_mismatch["status"] == "rejected"
        assert condition_mismatch["failed_check"] == "rule_condition_semantics"
        ambiguous_constraint = platform.submit_world_change("god", {
            "change_type": "rule",
            "summary": "Hold one behavior until review.",
            "rule": {
                "id": "ambiguous-constraint",
                "condition": "world.water_quality < 0.8",
                "effect": {"type": "behavior_constraint", "message": "Review required."},
                "behavior_names": ["Manage Ration", "Inspect Granary"],
            },
        })
        assert ambiguous_constraint["status"] == "rejected"
        assert ambiguous_constraint["failed_check"] == "rule_schema"
    finally:
        platform.close()


def test_active_world_rule_removes_denied_behavior_from_agent_view(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "world-rule-denial-world", tmp_path / "runtime"))
    try:
        agent = platform.store.get_agent("su_wu")
        agent["faction_ids"] = []
        with platform.store.transaction():
            platform.store.update_agent("su_wu", agent)
        view = platform.decision_view("su_wu")
        choices = {item["ref"] for item in view["executable_behaviors"]}
        assert "behavior:inspect_object" not in choices
        assert "old_well" not in render_context(view, "Choose one behavior.")
    finally:
        platform.close()


def test_behavior_constraint_rule_blocks_named_behavior_without_mutating_state(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "world-rule-constraint-world", tmp_path / "runtime"))
    try:
        before = platform.store.get_agent("liang_he")["capabilities"]["cooking"]
        result = platform.submit_world_change("god", {
            "change_type": "rule",
            "summary": "Review cooking during the reserve review.",
            "rule": {
                "id": "cooking_review_gate",
                "condition": "world.village_food < 400",
                "effect": {"type": "behavior_constraint", "message": "Cooking is paused until the reserve review."},
                "behavior_names": ["Craft Meal"],
            },
        })
        assert result["status"] == "accepted"
        view = platform.decision_view("liang_he")
        assert "behavior:craft_meal" not in {item["ref"] for item in view["executable_behaviors"]}
        # The constraint names only the cooking BT; it must not hide an
        # unrelated food Action/BT at the same time.
        assert "behavior:get_food" in {item["ref"] for item in view["executable_behaviors"]}
        assert platform.store.get_agent("liang_he")["capabilities"]["cooking"] == before
    finally:
        platform.close()


def test_recent_event_rule_uses_only_events_reached_by_world_time(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "event-rule-world", tmp_path / "runtime"))
    try:
        result = platform.submit_world_change("god", {
            "change_type": "rule",
            "summary": "Review the clinic event when it occurs.",
            "rule": {
                "id": "fever_event_signal",
                "label": "Clinic fever signal",
                "condition": "event.recent.clinic.fever_reported == true",
                "effect": "clinic.triage_alert",
                "behavior_names": ["Heal Target"],
                "wake": ["god"],
            },
        })
        assert result["status"] == "accepted"
        assert not any(item["event_type"] == "world.rule.fever_event_signal" for item in platform.store.recent_events(limit=30))
        step_n(platform, 11)
        assert not any(item["event_type"] == "world.rule.fever_event_signal" for item in platform.store.recent_events(limit=100))
        platform.step()
        assert any(item["event_type"] == "world.rule.fever_event_signal" for item in platform.store.recent_events(limit=100))
        step_n(platform, 3)
        assert len([item for item in platform.store.recent_events(limit=200) if item["event_type"] == "world.rule.fever_event_signal"]) == 1
    finally:
        platform.close()


def test_god_can_update_faction_strategy_through_platform(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "faction-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        result = platform.submit_world_change("god", {"change_type": "faction", "summary": "The watch changes patrol policy.", "faction": {"id": "village_watch", "strategy": "protect_boundary", "norms": ["protect_civilians", "verify_unknowns"]}})
        assert result["status"] == "accepted"
        faction = platform.store.get_entity("village_watch")
        assert faction["strategy"] == "protect_boundary"
    finally:
        platform.close()


def test_world_event_change_applies_structured_property_effect(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "event-change-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        result = platform.submit_world_change("operator", {"change_type": "event", "summary": "A boundary signal changes threat assessment.", "property": "north_gate_threat_level", "value": 0.3})
        assert result["status"] == "accepted"
        assert result["applied_effects"] is True
        assert platform.store.get_meta("world_properties")["north_gate_threat_level"] == 0.3
    finally:
        platform.close()


def test_god_view_compares_world_change_effects_on_next_window(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "god-outcome-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        result = platform.submit_world_change("operator", {"change_type": "world_property", "summary": "Lower fog pressure after a clear reading.", "property": "fog_pressure", "value": 0.2})
        assert result["status"] == "accepted"
        view = platform.decision_view("god", role="god")
        change = view["recent_world_changes"][0]
        assert change["effect"]["before"] is None
        assert change["effect"]["after"] == 0.2
        prompt = render_context(view, "Review the world result before the next scheduled intervention.")
        assert "<world_outcomes>" in prompt
        assert "Lower fog pressure" in prompt
        assert "before" in prompt and "after" in prompt
    finally:
        platform.close()


def test_experience_repetition_is_rejected_without_time_based_forgetting(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "memory-activation-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        first = platform.submit_experience("lu_xingzhou", "event:lesson", "The river route is safe only before the fog rises.", 0.9)
        assert first["status"] == "accepted"
        duplicate = platform.submit_experience("lu_xingzhou", "event:lesson-2", "The river route is safe only before the fog rises.", 0.9)
        assert duplicate["status"] == "rejected"
        assert duplicate["failed_check"] in {"duplicate_experience", "similar_experience"}
        platform.store.set_meta("world_minute", 100000)
        retained = platform.store.list_memories("lu_xingzhou", limit=20)
        assert any(item.get("summary", "").startswith("The river route") for item in retained)
        recalled = platform.memory.recall("lu_xingzhou", "river route", limit=1)
        assert recalled["memories"][0]["uses"] >= 1
    finally:
        platform.close()


def test_dialogue_trade_negotiation_and_observer_text_view(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "interaction-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        dialogue = platform.submit_dialogue("fei_lan", "余歌", "route", "The western road is open before dusk.")
        assert dialogue["status"] == "accepted"
        testimony = platform.store.list_memories("yu_ge", limit=10)
        assert any(item.get("type") == "testimony" and item.get("message", "").startswith("The western road") for item in testimony)
        testimony_row = next(item for item in testimony if item.get("type") == "testimony")
        assert testimony_row.get("tags", {}).get("participants") == "费兰"
        offer = platform.create_trade_offer("fei_lan", "yu_ge", {"trade_goods": 1}, {"letter_bundle": 1})
        assert offer["status"] == "accepted"
        counter = platform.counter_trade_offer("yu_ge", offer["offer_id"], {"letter_bundle": 1}, {"trade_goods": 1})
        assert counter["status"] == "accepted"
        completed = platform.accept_trade_offer("fei_lan", counter["offer_id"])
        assert completed["trade_status"] == "completed"
        assert platform.store.get_agent("fei_lan")["inventory"]["letter_bundle"] == 1
        observer = platform.observer_view(event_limit=20)
        assert any(event["event_type"] == "trade.completed" for event in observer["recent_events"])
        assert observer["private_data"] == "excluded"
        session = TextWorldSession(platform, observer=True)
        world_text = session.execute("world")
        assert "World minute" in world_text and "Characters:" in world_text
        assert "message" not in world_text
        assert session.execute("timeline 5")
        assert "paused" in session.execute("pause")
        assert "running" in session.execute("resume")
        try:
            session.execute("wait")
        except ValueError as exc:
            assert "read-only" in str(exc)
        else:
            raise AssertionError("observer mode must not advance world time")
        assert platform.status()["world_minute"] == 0
    finally:
        platform.close()


def test_player_text_session_can_enter_and_move_without_direct_state_write(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "text-player-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        entered = platform.enter_player("player_text", "courier")
        assert entered["status"] == "accepted"
        session = TextWorldSession(platform, player_id="player_text")
        assert "Commands:" in session.execute("help")
        assert session.execute("say yu_ge route hello") .startswith("status=rejected")
        # A text interface command still submits a Platform behavior intent;
        # it does not mutate the player's location directly.
        travel = platform.submit_behavior(BehaviorIntent("player_text", "behavior:travel", {}, {"location_id": "village_square"}, "Enter the village.", 0.8, "player-travel"))
        assert travel["status"] == "accepted"
        step_n(platform, 2)
        assert platform.store.get_agent("player_text")["initial_state"]["location_id"] == "village_square"
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
        assert service.health()["status"] == "ok"
        assert service.health()["database"] == "ok"
        service.pause("test")
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
        step_n(platform, 3)
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
        with urlopen(base + "/health") as response:
            health = json.loads(response.read())
        assert health["status"] == "ok"
        request = Request(base + "/pause", data=b'{"reason":"test"}', headers={"content-type": "application/json"}, method="POST")
        with urlopen(request) as response:
            paused = json.loads(response.read())
        assert paused["status"] == "paused"
    finally:
        server.close()
        thread.join(timeout=2)


def test_observer_timeline_is_chronological_and_public(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "timeline-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        platform.inject_event("observer.signal", {"summary": "The fog reaches the north gate."})
        timeline = platform.timeline(limit=20)
        assert timeline["status"] == "ok"
        assert timeline["events"]
        assert [item["event_id"] for item in timeline["events"]] == sorted(item["event_id"] for item in timeline["events"])
        signal = next(item for item in timeline["events"] if item["event_type"] == "observer.signal")
        assert signal["narrative"] == "The world: The fog reaches the north gate."
        assert "actor_id" not in signal["public"]
        next_page = platform.timeline(limit=20, after_event_id=signal["event_id"])
        assert all(item["event_id"] > signal["event_id"] for item in next_page["events"])
    finally:
        platform.close()


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
        step_n(first, 3)
        step_n(second, 3)
        select = "SELECT event_type,actor_id,world_minute,data_json FROM event_log ORDER BY event_id"
        first_events = [tuple(row) for row in first.store.connection.execute(select).fetchall()]
        second_events = [tuple(row) for row in second.store.connection.execute(select).fetchall()]
        assert first_events == second_events
    finally:
        first.close()
        second.close()


def test_starter_village_end_to_end_scenario(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "full-scenario", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        assert platform.enter_player("player_courier", "courier")["status"] == "accepted"
        decision_provider = SequenceProvider([{"behavior_ref": "behavior:explore_greywood", "reason": "Map the unknown area.", "confidence": 0.9}])
        decision = AgentEngine(platform, decision_provider).run(role="agent_decision", actor_id="lu_xingzhou")
        assert decision.result["status"] == "accepted"
        step_n(platform, 241)
        assert any(item["fact_key"] == "explored:greywood_edge" for item in platform.store.list_knowledge_facts("lu_xingzhou"))
        reflection_provider = SequenceProvider([{"intent_type": "record_experience", "event_ref": "exploration.completed", "summary": "The survey revealed a route boundary.", "confidence": 0.9}])
        reflection = AgentEngine(platform, reflection_provider).run(role="agent_reflection", actor_id="lu_xingzhou", task_type="daily_reflection")
        assert reflection.result["status"] == "accepted"
        god_provider = SequenceProvider([{"tool_call": {"name": "propose_entity", "arguments": {"entity_type": "landmark", "name": "Survey Marker", "location_id": "greywood_edge"}}}])
        god = AgentEngine(platform, god_provider).run(role="god", actor_id="god")
        assert god.result["status"] == "accepted"
        assert platform.metrics()["events"] > 0
    finally:
        platform.close()
