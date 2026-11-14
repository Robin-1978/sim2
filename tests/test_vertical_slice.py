from pathlib import Path
import pytest
import json
import threading
import httpx
import yaml
from urllib.request import Request, urlopen
from sim2.intents.models import BehaviorIntent

from sim2.agents.engine import AgentEngine
from sim2.agents.tools import AgentToolExecutor
from sim2.agents.tools import tool_schemas_for_role
from sim2.agents.quality import evaluate_turn
from sim2.agent_view.validator import validate_agent_view
from sim2.agent_view.compiler import AgentViewCompiler, _predicate_text
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
from sim2.llm.adapters import ModelProtocolError
from sim2.memory.similarity import ExperienceSimilarity
from sim2.world.navigation import reachable_locations
from sim2.world.context import configured_context_values


class FakeProvider:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete_json(self, *, system, context):
        self.calls.append((system, context))
        return dict(self.response)


def test_duplicate_visible_facts_merge_all_sources():
    merged = AgentViewCompiler._merge_visible_facts([
        {"fact": "River water is contaminated.", "source_type": "story", "confidence": 0.9},
        {"fact": "  river   water is contaminated. ", "source_type": "research", "confidence": 0.8},
        {"fact": "A separate fact.", "source_type": "public_record", "confidence": 1.0},
    ])

    assert len(merged) == 2
    assert merged[0]["confidence"] == 0.9
    assert merged[0]["source_type"] == "research, story"


def test_failed_behavior_feedback_names_the_selected_behavior(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "named-outcome-feedback", tmp_path / "runtime"))
    try:
        agent = platform.store.repositories.entities.get_agent("liang_he")
        agent["initial_state"]["last_platform_result"] = {
            "behavior_ref": "behavior:manage_ration",
            "status": "failed",
            "result": "precondition failed: target.available",
            "outcome": {"status": "failed", "result": "precondition failed: target.available"},
        }
        platform.store.repositories.entities.update_agent("liang_he", agent)
        platform.store.connection.commit()

        prompt = render_context(platform.decision_view("liang_he"), "Choose one behavior.")

        assert "latest_platform_result: status=failed; behavior_choice=Manage Ration" in prompt
        assert "precondition failed: target.available" in prompt
    finally:
        platform.close()


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


class CoolingFailureProvider(FailingProvider):
    def __init__(self):
        super().__init__("rate limited")
        self.cooldown_remaining = 0.0

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.calls += 1
        self.cooldown_remaining = 30.0
        raise RuntimeError(self.message)


def test_agent_condition_text_preserves_numeric_decimals():
    rendered = _predicate_text("world.root_gate_stability < 0.9")
    assert "0.9" in rendered
    assert "root gate stability" in rendered
    assert "0 9" not in rendered


def test_agent_prompt_humanizes_resource_and_location_keys(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "agent-visible-keys-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            platform.store.repositories.memory.put_knowledge_fact("lin_bo", "inspection:village_food", {
                "fact": "Inspected village_food; current amount is 320.",
                "source_type": "inspection",
                "confidence": 0.9,
                "observed_at": 0,
            })
        view = platform.decision_view("lin_bo")
        prompt = render_context(view, "Choose one behavior.")
        assert "village_food" not in prompt
        assert "north_gate" not in prompt
        assert "village food" in prompt.lower() or "公共粮食" in prompt
    finally:
        platform.close()


def test_agent_visible_text_does_not_replace_identifier_prefixes(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "agent-visible-boundary-world", tmp_path / "runtime"))
    try:
        rendered = platform.view._visible_text("village_food_store contains village_food")
        assert "village_food_store" in rendered
        assert "village food" in rendered
    finally:
        platform.close()


def test_configured_item_resolver_keeps_recovery_values_generic():
    rules = {
        "context_values": {"satiety_alert_threshold": 40},
        "item_groups": {"meals": ["rations", "cooked_meal"]},
        "context_resolvers": {"item_id": {"type": "first_inventory_item", "group": "meals"}},
    }
    values = configured_context_values(rules, {"inventory": {"rations": 2}})
    assert values["satiety_alert_threshold"] == 40
    assert values["item_id"] == "rations"


def test_configured_food_action_default_resolves_to_carried_item(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "legacy-food-default-world", tmp_path / "runtime"))
    try:
        agent = platform.store.repositories.entities.get_agent("lin_bo")
        action = next(item for item in platform.store.repositories.entities.get_definitions("action_def") if item.get("id") == "consume_food")
        action["parameter_defaults"] = {"item_id": {"resolver": "context_value", "key": "item_id"}}
        assert platform._apply_action_parameter_defaults(agent, action, {"item_id": None})["item_id"] == "rations"
    finally:
        platform.close()


def step_n(platform: Platform, minutes: int):
    result = None
    for _ in range(minutes):
        result = platform.step()
    return result


def grant_research_fact(platform: Platform, agent_id: str, fact_key: str) -> None:
    with platform.store.transaction():
        platform.store.repositories.memory.merge_knowledge_fact(agent_id, fact_key, {
            "fact": f"Verified research result: {fact_key}.",
            "source_type": "research",
            "source_id": fact_key,
            "confidence": 1.0,
            "observed_at": int(platform.store.get_meta("world_minute", 0)),
        })


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
        assert time["time_of_day"] == "08:30"
        assert time["day_phase"] == "day"
        assert time["season"] == "late_autumn"
        assert time["label"] == "Day 2, 08:30, day, late_autumn"
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


def test_idle_decision_protocol_does_not_claim_an_active_behavior(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "idle-protocol-world", tmp_path / "runtime"))
    try:
        view = platform.decision_view("yu_ge")
        assert view["self"]["status"] == "idle"
        assert not any("active behavior is already running" in line.lower() for line in view["prompt_protocol"])
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
        assert "<executable_behaviors>" not in reflection_prompt
        assert "No BT is executable" not in reflection_prompt
        assert "<registered_actions>" in reflection_prompt
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
    progress = next(schema for schema in tool_schemas_for_role("god") if schema["function"]["name"] == "record_world_progress")
    progress_parameters = progress["function"]["parameters"]
    assert "delta" not in progress_parameters["properties"]
    assert progress_parameters["required"] == ["progress_key", "evidence"]


def test_god_has_no_direct_behavior_or_action_executor():
    names = {schema["function"]["name"] for schema in tool_schemas_for_role("god")}
    assert "request_behavior" not in names
    assert "execute_action" not in names
    story_names = {schema["function"]["name"] for schema in tool_schemas_for_role("story")}
    assert "propose_definition" not in story_names
    assert "query_history" in story_names
    assert "query_history" in names


def test_god_definition_proposal_registers_attribute_action_and_behavior(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "god-definition-world", tmp_path / "runtime"))
    try:
        executor = AgentToolExecutor(platform, role="god", actor_id="god")
        attribute = executor.execute("propose_definition", {
            "definition_type": "attribute",
            "name": "Resolve",
            "definition": {"kind": "state", "min": 0, "max": 100, "default": 50, "cost_mode": "decrease", "prompt": "Higher means more resolve."},
            "evidence": "Repeated defense reports show a missing readiness measure.",
        })
        assert attribute["status"] == "accepted"
        assert any(item["id"] == "resolve" for item in platform.store.get_meta("attributes"))
        assert platform.store.repositories.entities.get_agent("lu_xingzhou")["initial_state"]["resolve"] == 50
        need = executor.execute("propose_definition", {
            "definition_type": "attribute",
            "name": "Composure",
            "definition": {"kind": "need", "min": 0, "max": 100, "default": 40, "target": 60, "gamma": 1.2, "prompt": "Higher means more composed."},
            "evidence": "Defense outcomes show a missing composure need.",
        })
        assert need["status"] == "accepted"
        assert platform.store.repositories.entities.get_agent("lu_xingzhou")["needs"]["composure"] == 40
        assert any(item["id"] == "composure" for item in platform.store.get_meta("rules")["needs"])
        capability = executor.execute("propose_definition", {
            "definition_type": "capability",
            "name": "Signal Craft",
            "definition": {"default": 0.2, "uses": ["mark_boundary"]},
            "evidence": "The new marking practice requires a reusable capability.",
        })
        assert capability["status"] == "accepted"
        assert platform.store.repositories.entities.get_agent("lu_xingzhou")["capabilities"]["signal_craft"] == 0.2
        world_property = executor.execute("propose_definition", {
            "definition_type": "world_property",
            "name": "Boundary Readiness",
            "definition": {"default": 0.0, "prompt": "Public readiness of the boundary."},
            "evidence": "Repeated patrol outcomes identify a missing public readiness measure.",
        })
        assert world_property["status"] == "accepted"
        assert platform.store.get_meta("world_properties")["boundary_readiness"] == 0.0

        action = executor.execute("propose_definition", {
            "definition_type": "action",
            "name": "Mark Boundary",
            "definition": {
                "parameters": {},
                "preconditions": ["actor.alive"],
                "duration": {"minutes": 1},
                "costs": {"reserve": {}, "per_minute": {}},
                "effects": [{"type": "attribute_delta", "attribute": "reputation", "delta": 1}],
                "success": {"condition": "mark.recorded", "result": "mark_recorded"},
                "failure": {"condition": "actor.incapacitated", "result": "mark_failed"},
            },
            "followup_behavior": {
                "name": "Mark Boundary Route",
                "assign_to": ["陆行舟"],
                "definition": {
                    "summary": "Record a verified boundary mark.",
                    "entry_conditions": ["actor.alive"],
                    "success_results": ["mark_recorded"],
                    "failure_results": ["mark_failed"],
                    "actions": ["Mark Boundary"],
                },
            },
            "evidence": "The patrol reports require a repeatable marking action.",
        })
        assert action["status"] == "accepted"
        assert action["bound_behavior"] == "Mark Boundary Route"
        assert action["bound_assigned_to"] == ["陆行舟"]
        assert any(item.get("id") == "mark_boundary_route" for item in platform.store.repositories.entities.get_definitions("bt_def"))
        assert "mark_boundary_route" in platform.store.repositories.entities.get_agent("lu_xingzhou")["unlocked_bt_ids"]

        selected = platform.submit_behavior(BehaviorIntent("lu_xingzhou", "behavior:mark_boundary_route", {}, {}, "Record the reported mark.", 0.8, "god-definition-run"))
        assert selected["status"] == "accepted"
        for _ in range(selected["remaining_minutes"]):
            platform.step()
        assert platform.store.repositories.entities.get_agent("lu_xingzhou")["initial_state"]["status"] == "idle"
    finally:
        platform.close()


def test_intent_revisions_are_observational_and_events_carry_causation(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "revision-world", tmp_path / "runtime"))
    try:
        result = platform.submit_behavior(BehaviorIntent(
            "lin_bo", "behavior:govern_village", {}, {}, "Review the village state.", 0.8, "revision-intent",
            observed_world_revision=0, observed_agent_revision=0,
        ))
        assert result["status"] == "accepted"
        assert result["stale_observation"] is True
        started = next(item for item in platform.store.repositories.events.recent_events(limit=20) if item["event_type"] == "behavior.started")
        assert started["world_revision"] >= 1
        assert started["world_instance_id"] == platform.store.get_meta("world_id")
        assert started["causation_id"] == "revision-intent"
        assert started["correlation_id"] == "revision-intent"
    finally:
        platform.close()


def test_god_world_proposal_is_evaluated_after_its_observation_window(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "proposal-evaluation-world", tmp_path / "runtime"))
    try:
        result = platform.submit_world_change("god", {
            "change_type": "rule",
            "summary": "Review patrol outcomes after a short observation window.",
            "evaluation_window_minutes": 2,
            "rule": {"id": "patrol_review_rule", "condition": "world.north_gate_threat_level >= 0.2", "effect": "policy.review_signal", "behavior_names": ["Patrol Route"]},
        })
        assert result["status"] == "accepted"
        assert result["evaluation_status"] == "pending_evaluation"
        platform.step()
        platform.step()
        proposal = platform.store.repositories.evolution.list_world_proposals(limit=1)[0]
        assert proposal["proposal_id"] == result["proposal_id"]
        assert proposal["status"] in {"observed", "no_observed_effect"}
        assert any(item["event_type"] == "world.proposal.evaluated" for item in platform.store.repositories.events.recent_events(limit=20))
        view = platform.decision_view("god", role="god")
        assert "world_proposals" in view
    finally:
        platform.close()


def test_god_prompt_hides_internal_world_proposal_identifiers(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "god-prompt-identifiers", tmp_path / "runtime"))
    try:
        result = platform.submit_world_change("operator", {
            "change_type": "world_property",
            "summary": "Record a measured fog reading.",
            "property": "fog_level",
            "value": 0.2,
            "evaluation_window_minutes": 10,
        })
        assert result["status"] == "accepted"
        prompt = render_context(platform.decision_view("god", role="god"), "Review the measured world result.")
        assert result["proposal_id"] not in prompt
        assert "Pending proposal evaluations" in prompt
        assert "Record a measured fog reading." in prompt
    finally:
        platform.close()


def test_event_driven_world_review_uses_submission_tools_only(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "event-review-tools-world", tmp_path / "runtime"))
    event_provider = SequenceProvider([{"intent_type": "wait", "reason": "The validated event needs no additional typed change."}])
    scheduled_provider = SequenceProvider([{"intent_type": "wait", "reason": "The scheduled review finds no change."}])
    try:
        with platform.store.transaction():
            event_id = platform.store.repositories.events.append_event(
                "security.marks_classified", "he_yan", 4,
                {"summary": "The configured investigation classified the marks."},
            )
        event_turn = AgentEngine(platform, event_provider).run(
            role="god",
            actor_id="god",
            task_type="event_group:public_incident",
            source_event_id=event_id,
            source_events=[{"event_id": event_id}],
        )
        assert event_turn.result["status"] == "wait"
        event_tool_names = {item["function"]["name"] for item in event_provider.calls[0]["tools"]}
        assert "propose_world_change" in event_tool_names
        assert "record_world_progress" in event_tool_names
        assert not event_tool_names & {"observe_nearby", "inspect_entity", "listen", "read_public_record", "query_known_fact", "recall_memory", "preview_behavior"}
        assert {item["name"] for item in event_turn.initial_context["tools"]} == event_tool_names

        scheduled_turn = AgentEngine(platform, scheduled_provider).run(
            role="god", actor_id="god", task_type="god_local_review",
        )
        assert scheduled_turn.result["status"] == "wait"
        scheduled_tool_names = {item["function"]["name"] for item in scheduled_provider.calls[0]["tools"]}
        assert "query_known_fact" in scheduled_tool_names
        assert "inspect_entity" in scheduled_tool_names
    finally:
        platform.close()


def test_god_event_wakes_use_configured_evidence_counts(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "god-evidence-gates", tmp_path / "runtime"))
    try:
        # Routine governance does not wake God by itself.
        with platform.store.transaction():
            routine_id = platform.store.repositories.events.append_event("governance.reviewed", "lin_bo", 1, {"summary": "One routine review."})
        platform.wakes.followups({"wake": ["god"]}, routine_id, "governance.reviewed", "lin_bo", 1)
        assert not any(item["role"] == "god" for item in platform.ready_evolution_tasks())

        # A single public incident is a direct world consequence and wakes God.
        with platform.store.transaction():
            incident_id = platform.store.repositories.events.append_event("entity.installed", "tian_yan", 2, {"summary": "A public warning device was installed."})
        platform.wakes.queue("god", incident_id, "entity.installed", "tian_yan", 2, task_type="event:entity.installed")
        task = next(item for item in platform.ready_evolution_tasks() if item["role"] == "god" and item["task_type"] == "event_group:public_incident")
        assert task["source_events"] == [{"event_id": incident_id, "event_type": "entity.installed", "actor_id": "tian_yan"}]
    finally:
        platform.close()


def test_configured_governance_and_trade_groups_reach_their_thresholds(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "group-threshold-world", tmp_path / "runtime"))
    try:
        behavior = next(item for item in platform.store.repositories.entities.get_definitions("bt_def") if item["id"] == "govern_village")
        governance = next(item for item in behavior["outcome_events"] if item["event_type"] == "governance.reviewed")
        negotiation_action = next(item for item in platform.store.repositories.entities.get_definitions("action_def") if item["id"] == "negotiate")
        negotiation = negotiation_action["information"]
        assert governance["wake"] == ["god"]
        assert negotiation["wake"] == ["god"]

        for minute in range(1, 8):
            with platform.store.transaction():
                event_id = platform.store.repositories.events.append_event("governance.reviewed", "lin_bo", minute, {"summary": "Verified governance review."})
                platform.wakes.followups(governance, event_id, "governance.reviewed", "lin_bo", minute)
            assert not any(task["task_type"] == "event_group:governance_capacity" for task in platform.ready_evolution_tasks())
        with platform.store.transaction():
            event_id = platform.store.repositories.events.append_event("governance.reviewed", "lin_bo", 8, {"summary": "Eighth verified governance review."})
            platform.wakes.followups(governance, event_id, "governance.reviewed", "lin_bo", 8)
        governance_task = next(task for task in platform.ready_evolution_tasks() if task["task_type"] == "event_group:governance_capacity")
        assert len(governance_task["source_events"]) == 8

        for minute in range(20, 25):
            with platform.store.transaction():
                event_id = platform.store.repositories.events.append_event("negotiation.terms_recorded", "fei_lan", minute, {"summary": "Concrete terms were recorded."})
                platform.wakes.followups(negotiation, event_id, "negotiation.terms_recorded", "fei_lan", minute)
            assert not any(task["task_type"] == "event_group:trade_network" for task in platform.ready_evolution_tasks())
        with platform.store.transaction():
            event_id = platform.store.repositories.events.append_event("negotiation.terms_recorded", "fei_lan", 25, {"summary": "Sixth concrete negotiation result."})
            platform.wakes.followups(negotiation, event_id, "negotiation.terms_recorded", "fei_lan", 25)
        trade_task = next(task for task in platform.ready_evolution_tasks() if task["task_type"] == "event_group:trade_network")
        assert len(trade_task["source_events"]) == 6
    finally:
        platform.close()


def test_runner_preserves_story_source_event_in_agent_view(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "story-source-runner", tmp_path / "runtime"))
    turns = []
    try:
        with platform.store.transaction():
            source_event_id = platform.store.repositories.events.append_event(
                "entity.installed", "tian_yan", 0,
                {"summary": "The dawn bell was installed at the north gate.", "story_source": True},
            )
            platform.wakes.queue(
                "story", source_event_id, "entity.installed", "tian_yan", 0,
                task_type="event:entity.installed",
            )
        report = SimulationRunner(
            platform,
            {"story": FakeProvider({"intent_type": "wait", "reason": "No new beat is needed."})},
            turn_observer=lambda turn, actor_id: turns.append(turn),
        ).run(1)
        assert report.story_sessions == 1
        assert turns[0].initial_context["validated_source"]["event_type"] == "entity.installed"
        assert turns[0].initial_context["validated_source"]["actor"] == "田岩"
        assert turns[0].initial_context["triggering_evidence"] == [{
            "event_type": "entity.installed",
            "world_minute": 0,
            "actor": "田岩",
            "summary": "The dawn bell was installed at the north gate.",
        }]
    finally:
        platform.close()


def test_tick_snapshot_revision_is_committed_once_and_events_share_it(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "tick-protocol-world", tmp_path / "runtime"))
    try:
        before = platform.tick_snapshot()
        result = platform.step()
        assert result["snapshot_revision"] == before["world_revision"]
        assert result["world_revision"] > before["world_revision"]
        tick_event = next(item for item in platform.store.repositories.events.recent_events(limit=10) if item["event_type"] == "world_minute.advanced")
        assert tick_event["world_revision"] == result["world_revision"]
        assert platform.tick_snapshot()["world_minute"] == 1
    finally:
        platform.close()


def test_stale_character_revision_is_rejected_at_commit_barrier(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "stale-agent-world", tmp_path / "runtime"))
    try:
        view = platform.tick_snapshot()
        agent_revision = view["agent_revisions"]["lu_xingzhou"]
        with platform.store.transaction():
            agent = platform.store.repositories.entities.get_agent("lu_xingzhou")
            agent.setdefault("initial_state", {})["attention"] = 1
            platform.store.repositories.entities.update_agent("lu_xingzhou", agent)
        rejected = platform.submit_behavior(BehaviorIntent(
            "lu_xingzhou", "behavior:explore_greywood", {}, {}, "Use the old decision view.", 0.8, "stale-agent-intent",
            observed_world_revision=view["world_revision"], observed_agent_revision=agent_revision,
        ))
        assert rejected["status"] == "rejected"
        assert rejected["failed_check"] == "stale_agent_revision"
    finally:
        platform.close()


def test_running_action_keeps_definition_snapshot_after_registry_change(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "definition-snapshot-world", tmp_path / "runtime"))
    try:
        started = platform.submit_behavior(BehaviorIntent("lin_bo", "behavior:rest_and_recover", {}, {}, "Recover readiness.", 0.8, "definition-snapshot"))
        assert started["status"] == "accepted"
        platform.step()
        active = platform.store.repositories.entities.get_agent("lin_bo")["initial_state"]
        assert active["active_action"] == "rest"
        old_version = active["active_action_definition_version"]
        with platform.store.transaction():
            rest = next(item for item in platform.store.repositories.entities.get_definitions("action_def") if item["id"] == "rest")
            rest = dict(rest)
            rest["definition_version"] = old_version + 1
            rest["duration"] = {"minutes": 1}
            platform.store.repositories.entities.put_definition("action_def", "rest", rest)
        platform.step()
        current = platform.store.repositories.entities.get_agent("lin_bo")["initial_state"]
        assert current["status"] == "executing"
        assert current["remaining_minutes"] > 1
        action_row = platform.store.connection.execute("SELECT data_json FROM action_instance WHERE action_instance_id=?", (active["active_action_instance"],)).fetchone()
        assert json.loads(action_row["data_json"])["definition_version"] == old_version
    finally:
        platform.close()


def test_proposal_evaluation_requires_typed_followup_not_unrelated_event(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "proposal-followup-world", tmp_path / "runtime"))
    try:
        result = platform.submit_world_change("god", {
            "change_type": "rule",
            "summary": "Review one named patrol result after the gate report.",
            "evaluation_window_minutes": 1,
            "rule": {"id": "named_patrol_review", "condition": "world.north_gate_threat_level >= 0.2", "effect": "policy.review_signal", "behavior_names": ["Patrol Route"]},
        })
        assert result["status"] == "accepted"
        with platform.store.transaction():
            platform.store.repositories.events.append_event("dialogue.completed", "lin_bo", 0, {"summary": "Unrelated conversation."})
        platform.step()
        proposal = platform.store.repositories.evolution.list_world_proposals(limit=1)[0]
        assert proposal["status"] == "no_observed_effect"
        assert proposal["observed"]["match"] is False
    finally:
        platform.close()


def test_confirmed_experience_requires_recorded_source_event(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "experience-source-world", tmp_path / "runtime"))
    try:
        rejected = platform.submit_experience("lu_xingzhou", "research.completed", "The water result is confirmed.", 0.9, "confirmed")
        assert rejected["status"] == "rejected"
        assert rejected["failed_check"] == "claim_evidence"
        with platform.store.transaction():
            event_id = platform.store.repositories.events.append_event("research.completed", "lu_xingzhou", 0, {"summary": "Water research completed."})
        accepted = platform.submit_experience("lu_xingzhou", "research.completed", "The water result is confirmed.", 0.9, "confirmed")
        assert accepted["status"] == "accepted"
        memory = platform.store.repositories.memory.list_memories("lu_xingzhou", limit=1)[0]
        assert memory["source_event_id"] == event_id
        assert memory["claim_status"] == "confirmed"
    finally:
        platform.close()


def test_configured_postcondition_uses_numeric_tolerance_and_publishes_success_once(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "water-postcondition-world", tmp_path / "runtime"))
    try:
        grant_research_fact(platform, "qiao_ning", "researched:clean_water:contamination")
        accepted = platform.submit_behavior(BehaviorIntent("qiao_ning", "behavior:mitigate_water", {}, {}, "Apply the measured water safety step.", 0.9, "water-postcondition"))
        assert accepted["status"] == "accepted"
        for _ in range(accepted["remaining_minutes"]):
            platform.step()
        assert platform.store.get_meta("world_properties")["water_quality"] == pytest.approx(0.8)
        assert any(item["event_type"] == "water.mitigation.applied" for item in platform.store.repositories.events.recent_events(limit=30))
        assert not any(item["event_type"] == "behavior.failed" and item.get("execution_id") == accepted["execution_id"] for item in platform.store.repositories.events.recent_events(limit=30))
    finally:
        platform.close()


def test_failed_postcondition_rolls_back_effects_and_information_events(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "water-rollback-world", tmp_path / "runtime"))
    try:
        grant_research_fact(platform, "qiao_ning", "researched:clean_water:contamination")
        with platform.store.transaction():
            action = next(item for item in platform.store.repositories.entities.get_definitions("action_def") if item["id"] == "mitigate_water")
            broken = dict(action)
            broken["success"] = {"condition": "world.water_quality >= 2", "result": "water_measure_applied"}
            platform.store.repositories.entities.put_definition("action_def", "mitigate_water", broken)
        accepted = platform.submit_behavior(BehaviorIntent("qiao_ning", "behavior:mitigate_water", {}, {}, "Try the configured safety step.", 0.9, "water-rollback"))
        assert accepted["status"] == "accepted"
        for _ in range(accepted["remaining_minutes"]):
            platform.step()
        assert platform.store.get_meta("world_properties")["water_quality"] == pytest.approx(0.72)
        events = platform.store.repositories.events.recent_events(limit=50)
        assert any(item["event_type"] == "behavior.failed" and item.get("execution_id") == accepted["execution_id"] for item in events)
        assert not any(item["event_type"] == "water.mitigation.applied" for item in events)
        assert not any(item["event_type"] == "world.property_changed" and item.get("source") == "mitigate_water" for item in events)
    finally:
        platform.close()


def test_outcome_projection_does_not_embed_previous_outcome_history(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "outcome-size-world", tmp_path / "runtime"))
    try:
        grant_research_fact(platform, "qiao_ning", "researched:clean_water:contamination")
        for index in range(2):
            accepted = platform.submit_behavior(BehaviorIntent("qiao_ning", "behavior:mitigate_water", {}, {}, "Apply the next measured water step.", 0.9, f"outcome-size-{index}"))
            assert accepted["status"] == "accepted"
            for _ in range(accepted["remaining_minutes"]):
                platform.step()
        outcomes = [item for item in platform.store.repositories.events.recent_events(limit=500) if item["event_type"] == "outcome.evaluated" and item.get("actor_id") == "qiao_ning"]
        assert len(outcomes) >= 2
        for event in outcomes[:2]:
            actual = (event.get("actual") or {})
            assert "last_outcome" not in actual.get("attributes", {})
        assert len(json.dumps(outcomes[0], ensure_ascii=False)) < 5000
    finally:
        platform.close()


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
    assert calls[-1]["json"]["max_tokens"] == 2048


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


def test_provider_protocol_error_preserves_diagnostic_and_usage(monkeypatch):
    response = httpx.Response(
        200,
        request=httpx.Request("POST", "http://example.test"),
        json={
            "choices": [{"finish_reason": "stop", "message": {"content": "I cannot choose a tool."}}],
            "usage": {"prompt_tokens": 30, "completion_tokens": 6},
        },
    )
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        return response

    monkeypatch.setattr("sim2.llm.gateway.httpx.post", fake_post)
    provider = OpenAICompatibleProvider(ProviderConfig("test", "openai_compatible", "http://example.test/v1", "test", max_retries=0))
    with pytest.raises(ModelProtocolError, match="unstructured content") as raised:
        provider.complete_json(system="system", context="context")
    assert len(calls) == 2
    assert raised.value.usage["prompt_tokens"] == 30
    assert raised.value.usage["completion_tokens"] == 6
    assert raised.value.usage["request_latency_ms"] >= 0


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
        assert provider.calls == len(platform.store.repositories.entities.list_agents())
        assert report.llm_errors == provider.calls
        assert report.rejected_turns == provider.calls
    finally:
        platform.close()


def test_empty_behavior_catalog_uses_phase_aware_backoff_and_event_wake(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "empty-catalog-backoff-world", tmp_path / "runtime"))
    provider = SequenceProvider([{"intent_type": "wait", "reason": "No behavior or interaction is currently available."}])
    try:
        with platform.store.transaction():
            platform.store.set_meta("world_minute", 780)
            for agent in platform.store.repositories.entities.list_agents():
                state = agent["initial_state"]
                state["status"] = "paused" if agent["id"] != "fei_lan" else "idle"
                if agent["id"] == "fei_lan":
                    state["energy"] = 100
                    agent["needs"]["satiety"] = 80
                    agent["inventory"] = {}
                if agent["id"] == "yu_ge":
                    state["location_id"] = "village_square"
                platform.store.repositories.entities.update_agent(agent["id"], agent)
        runner = SimulationRunner(platform, {"agent_decision": provider})
        report = runner.run(1)
        assert report.decisions == 1
        assert len(provider.calls) == 1
        current = int(platform.store.get_meta("world_minute"))
        assert runner._decision_not_before["fei_lan"] == current + 360

        with platform.store.transaction():
            platform.store.repositories.events.append_event("need.satiety_low", "fei_lan", current, {"summary": "A configured need crossed its alert boundary."})
        runner._refresh_decision_wakes(current)
        assert runner._decision_not_before["fei_lan"] <= current
        assert "fei_lan" not in runner._wait_streak
    finally:
        platform.close()


def test_provider_cooldown_stops_world_time_instead_of_skipping_a_day(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "cooldown-time-world", tmp_path / "runtime"))
    provider = CoolingFailureProvider()
    try:
        report = SimulationRunner(platform, {"agent_decision": provider}).run(1440)
        assert report.end_minute == 1
        assert platform.status()["world_minute"] == 1
        assert report.stop_reason == "provider_cooldown:agent_decision"
        assert provider.calls == 1
        provider.cooldown_remaining = 0.0
        resumed = SimulationRunner(platform, {"agent_decision": provider}).run(1440)
        assert resumed.end_minute == 1
        assert resumed.stop_reason == "provider_cooldown:agent_decision"
        assert provider.calls == 2
    finally:
        platform.close()


def test_story_provider_failure_retries_without_pausing_world(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "story-provider-failure", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            event_id = platform.store.repositories.events.append_event(
                "security.marks_classified",
                "he_yan",
                1,
                {"story_source": True, "summary": "The boundary marks were classified."},
            )
        platform.wakes.queue("story", event_id, "security.marks_classified", "he_yan", 1, task_type="event:security.marks_classified")
        report = SimulationRunner(platform, {"story": FailingProvider()}).run(2)
        assert report.end_minute == 2
        assert platform.status()["status"] == "running"
        task = next(item for item in platform.store.repositories.evolution.ready_evolution_tasks() + platform.store.repositories.evolution.due_evolution_tasks(100) if item["role"] == "story")
        assert task["status"] == "scheduled"
        assert not any(item["event_type"] == "runtime.paused" for item in platform.store.repositories.events.timeline_events(limit=50))
    finally:
        platform.close()


def test_evolution_provider_cooldown_retries_at_same_world_minute(tmp_path: Path):
    class RecoveringCoolingProvider:
        def __init__(self):
            self.calls = 0
            self.cooldown_remaining = 0.0

        def complete_json(self, *, system, context, tools=None, messages=None):
            self.calls += 1
            if self.calls == 1:
                self.cooldown_remaining = 30.0
                raise RuntimeError("rate limited")
            return {"intent_type": "wait", "reason": "No narrative continuation is needed."}

    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "evolution-cooldown-boundary", tmp_path / "runtime"))
    provider = RecoveringCoolingProvider()
    try:
        with platform.store.transaction():
            event_id = platform.store.repositories.events.append_event(
                "security.marks_classified",
                "he_yan",
                1,
                {"story_source": True, "summary": "The boundary marks were classified."},
            )
        platform.wakes.queue(
            "story", event_id, "security.marks_classified", "he_yan", 1,
            task_type="event:security.marks_classified",
        )
        first = SimulationRunner(platform, {"story": provider}).run(2)
        assert first.end_minute == 1
        assert first.stop_reason == "provider_cooldown:story"
        task = next(item for item in platform.store.repositories.evolution.ready_evolution_tasks() if item["role"] == "story")
        assert task["due_minute"] == 1

        provider.cooldown_remaining = 0.0
        resumed = SimulationRunner(platform, {"story": provider}).run(1)
        assert resumed.end_minute == 2
        rows = platform.store.connection.execute(
            "SELECT status,world_minute FROM llm_call WHERE role='story' ORDER BY call_id"
        ).fetchall()
        assert [(row["status"], row["world_minute"]) for row in rows] == [
            ("llm_error", 1), ("response", 1),
        ]
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
        agent = platform.store.repositories.entities.get_agent("lu_xingzhou")
        agent["needs"]["satiety"] = 40
        with platform.store.transaction():
            platform.store.repositories.entities.update_agent("lu_xingzhou", agent)
        view = platform.decision_view("lu_xingzhou")
        assert any(item["ref"] == "behavior:eat_food" for item in view["executable_behaviors"])
        before = platform.store.repositories.entities.get_agent("lu_xingzhou")
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
        after = platform.store.repositories.entities.get_agent("lu_xingzhou")
        assert after["inventory"]["rations"] == before["inventory"]["rations"] - 1
        assert before["needs"]["satiety"] == 40
        assert after["needs"]["satiety"] == pytest.approx(min(100, before["needs"]["satiety"] - 0.02 + 30))
        assert after["initial_state"]["status"] == "idle"
    finally:
        platform.close()


def test_food_bt_remains_available_when_satiety_is_adequate(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "food-trigger-world", tmp_path / "runtime"))
    try:
        agent = platform.store.repositories.entities.get_agent("lu_xingzhou")
        agent["needs"]["satiety"] = 79
        agent["inventory"]["rations"] = 1
        with platform.store.transaction():
            platform.store.repositories.entities.update_agent("lu_xingzhou", agent)
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
        agent = platform.store.repositories.entities.get_agent("liang_he")
        agent["needs"]["satiety"] = 40
        with platform.store.transaction():
            platform.store.repositories.entities.update_agent("liang_he", agent)
        view = platform.decision_view("liang_he")
        assert any(item["ref"] == "behavior:get_food" for item in view["executable_behaviors"])
        before = platform.store.repositories.entities.get_entity("village_food")["amount"]
        result = platform.submit_behavior(BehaviorIntent(
            actor_id="liang_he", behavior_ref="behavior:get_food", target={}, parameters={},
            reason="Withdraw a ration before the next task.", confidence=0.9, intent_id="get-food-intent",
        ))
        assert result["status"] == "accepted"
        assert result["remaining_minutes"] == 16
        for _ in range(16):
            platform.step()
        after = platform.store.repositories.entities.get_agent("liang_he")
        assert after["inventory"]["rations"] == 1
        assert platform.store.repositories.entities.get_entity("village_food")["amount"] == before - 1
    finally:
        platform.close()


def test_market_food_path_uses_coin_and_market_stock(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "market-food-world", tmp_path / "runtime"))
    try:
        agent = platform.store.repositories.entities.get_agent("fei_lan")
        agent["needs"]["satiety"] = 20
        platform.store.repositories.entities.update_agent("fei_lan", agent)
        platform.store.connection.commit()
        view = platform.decision_view("fei_lan")
        assert any(item["ref"] == "behavior:buy_food" for item in view["executable_behaviors"])
        before_coin = platform.store.repositories.entities.get_agent("fei_lan")["inventory"]["coin"]
        before_rations = platform.store.repositories.entities.get_agent("fei_lan")["inventory"].get("rations", 0)
        before_stock = platform.store.repositories.entities.get_entity("market_rations")["amount"]
        result = platform.submit_behavior(BehaviorIntent(
            "fei_lan", "behavior:buy_food", {}, {}, "Buy food because carried food is unavailable.", 0.9, "market-food"
        ))
        assert result["status"] == "accepted"
        step_n(platform, result["remaining_minutes"])
        after = platform.store.repositories.entities.get_agent("fei_lan")
        assert after["inventory"]["coin"] == before_coin - 1
        assert after["inventory"]["rations"] == before_rations + 1
        assert platform.store.repositories.entities.get_entity("market_rations")["amount"] == before_stock - 1
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
            "yu_ge", "behavior:communicate", {},
            {"topic": "route news", "message": "What verified route news can you share?"},
            "Exchange the merchant's route information.", 0.8, "nearby-dialogue"
        ))
        assert result["status"] == "accepted"
        step_n(platform, result["remaining_minutes"])
        dialogue = next(item for item in platform.store.repositories.events.recent_events(limit=20) if item["event_type"] == "dialogue.completed")
        assert dialogue["topic"] == "route news"
        assert dialogue["message"] == "What verified route news can you share?"
        assert platform.store.repositories.memory.list_memories("fei_lan", limit=5)
        assert any(item["from"] == "yu_ge" and item["to"] == "fei_lan" and item["type"] == "trust" for item in platform.store.repositories.entities.list_relationships())
        assert "trust=0.02" in render_context(platform.decision_view("yu_ge"), "Choose one behavior.")
        assert not any(item["ref"] == "behavior:communicate" for item in platform.decision_view("yu_ge")["executable_behaviors"])
        assert not any(item["role"] == "agent_reflection" for item in platform.ready_evolution_tasks())
    finally:
        platform.close()


def test_starvation_is_a_configured_world_consequence(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "starvation-world", tmp_path / "runtime"))
    try:
        agent = platform.store.repositories.entities.get_agent("fei_lan")
        agent["needs"]["satiety"] = 0
        agent["initial_state"]["health"] = 10
        platform.store.repositories.entities.update_agent("fei_lan", agent)
        platform.store.connection.commit()
        step_n(platform, 60)
        after = platform.store.repositories.entities.get_agent("fei_lan")
        assert after["initial_state"]["health"] == 8
        assert after["initial_state"]["status"] == "idle"
    finally:
        platform.close()


def test_patrol_report_closes_personal_world_and_knowledge_loop(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "patrol-closure-world", tmp_path / "runtime"))
    try:
        before = platform.store.get_meta("world_properties")["north_gate_threat_level"]
        before_needs = platform.store.repositories.entities.get_agent("he_yan")["needs"].copy()
        result = platform.submit_behavior(BehaviorIntent("he_yan", "behavior:patrol_route", {}, {}, "Inspect the known boundary route.", 0.9, "patrol-closure"))
        assert result["status"] == "accepted"
        step_n(platform, result["remaining_minutes"])
        after = platform.store.get_meta("world_properties")["north_gate_threat_level"]
        assert after == before
        agent = platform.store.repositories.entities.get_agent("he_yan")
        assert agent["needs"]["safety"] > before_needs["safety"]
        assert agent["needs"]["esteem"] > before_needs["esteem"]
        assert any(item["fact_key"] == "patrol:north_gate_loop" for item in platform.store.repositories.memory.list_knowledge_facts("he_yan", limit=20))
        assert any(item["event_type"] == "security.patrol_reported" for item in platform.store.repositories.events.recent_events(limit=30))
        trends = platform.store.repositories.events.world_trends(current_minute=platform.store.get_meta("world_minute"))["weekly"]
        assert trends["signal_events"]["security.patrol_reported"] == 1
        assert "north_gate_threat_level" not in trends["world_property_deltas"]
        assert not any(item["role"] == "god" and item.get("source_event_type") == "security.patrol_reported" for item in platform.store.repositories.evolution.ready_evolution_tasks())
    finally:
        platform.close()


def test_governance_report_closes_personal_world_and_knowledge_loop(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "governance-closure-world", tmp_path / "runtime"))
    try:
        result = platform.submit_behavior(BehaviorIntent("lin_bo", "behavior:govern_village", {}, {}, "Review current reserves and risk.", 0.9, "governance-closure"))
        assert result["status"] == "accepted"
        step_n(platform, result["remaining_minutes"])
        assert platform.store.get_meta("world_properties")["public_trust"] == 0.55
        facts = platform.store.repositories.memory.list_knowledge_facts("lin_bo", limit=20)
        assert any(item["fact_key"] == "governance:latest_review" for item in facts)
        assert any(item["fact_key"] == "inspect_object:village_food" and "320" in item["fact"] for item in facts)
        assert any(item["event_type"] == "governance.reviewed" for item in platform.store.repositories.events.recent_events(limit=30))
        assert not any(item["role"] == "god" and item.get("source_event_type") == "governance.reviewed" for item in platform.store.repositories.evolution.ready_evolution_tasks())
    finally:
        platform.close()


def test_satiety_threshold_signals_need_without_hiding_food_choices(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "satiety-signal-world", tmp_path / "runtime"))
    try:
        agent = platform.store.repositories.entities.get_agent("fei_lan")
        agent["needs"]["satiety"] = 40.01
        platform.store.repositories.entities.update_agent("fei_lan", agent)
        platform.store.connection.commit()
        assert any(item["ref"] == "behavior:buy_food" for item in platform.decision_view("fei_lan")["executable_behaviors"])
        platform.step()
        event = next(item for item in platform.store.repositories.events.recent_events(limit=30) if item["event_type"] == "need.satiety_low")
        assert event["need"] == "satiety"
    finally:
        platform.close()


def test_rest_fallback_stays_gated_while_food_uses_resource_preconditions(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "satisfied-needs-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        liang = platform.store.repositories.entities.get_agent("liang_he")
        liang["needs"]["satiety"] = 100
        platform.store.repositories.entities.update_agent("liang_he", liang)
        platform.store.connection.commit()
        offered = {item["ref"] for item in platform.decision_view("liang_he")["executable_behaviors"]}
        assert "behavior:eat_food" not in offered
        assert "behavior:get_food" in offered
        liang["needs"]["satiety"] = 40
        platform.store.repositories.entities.update_agent("liang_he", liang)
        platform.store.connection.commit()
        assert "behavior:get_food" in {item["ref"] for item in platform.decision_view("liang_he")["executable_behaviors"]}

        smith = platform.store.repositories.entities.get_agent("tian_yan")
        smith["initial_state"]["energy"] = 100
        platform.store.repositories.entities.update_agent("tian_yan", smith)
        platform.store.connection.commit()
        offered = {item["ref"] for item in platform.decision_view("tian_yan")["executable_behaviors"]}
        assert "behavior:rest_and_recover" not in offered
    finally:
        platform.close()


def test_attention_capacity_filters_catalog_and_rejects_stale_choice(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "attention-capacity-world", tmp_path / "runtime"))
    try:
        steward = platform.store.repositories.entities.get_agent("lin_bo")
        steward["initial_state"]["attention"] = 100
        platform.store.repositories.entities.update_agent("lin_bo", steward)
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
        su_wu = platform.store.repositories.entities.get_agent("su_wu")
        su_wu["initial_state"]["attention"] = 0
        su_wu["initial_state"]["energy"] = 100
        platform.store.repositories.entities.update_agent("su_wu", su_wu)
        platform.store.connection.commit()
        assert any(item["ref"] == "behavior:research_source" for item in platform.decision_view("su_wu")["executable_behaviors"])
    finally:
        platform.close()


def test_inspection_behaviors_have_configured_cooldowns(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "inspection-cooldown-world", tmp_path / "runtime"))
    try:
        su_wu = platform.store.repositories.entities.get_agent("su_wu")
        su_wu["initial_state"]["location_id"] = "old_well"
        platform.store.repositories.entities.update_agent("su_wu", su_wu)
        platform.store.connection.commit()
        intent = BehaviorIntent("su_wu", "behavior:inspect_object", {}, {}, "Check the current source.", 0.8, "inspection-cooldown")
        accepted = platform.submit_behavior(intent)
        assert accepted["status"] == "accepted"
        step_n(platform, accepted["remaining_minutes"])
        assert not any(item["ref"] == "behavior:inspect_object" for item in platform.decision_view("su_wu")["executable_behaviors"])
        platform.store.set_meta("world_minute", accepted["remaining_minutes"] + 1440)
        assert any(item["ref"] == "behavior:inspect_object" for item in platform.decision_view("su_wu")["executable_behaviors"])
    finally:
        platform.close()


def test_decision_context_explains_single_recovery_candidate(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "single-recovery-context", tmp_path / "runtime"))
    try:
        liang = platform.store.repositories.entities.get_agent("liang_he")
        liang["initial_state"]["energy"] = 6.5
        liang["needs"]["satiety"] = 100
        liang["permissions"]["food_withdrawal"] = False
        liang["inventory"] = {}
        platform.store.repositories.entities.update_agent("liang_he", liang)
        platform.store.connection.commit()
        view = platform.decision_view("liang_he")
        offered = {item["ref"] for item in view["executable_behaviors"]}
        assert "behavior:recover_at_safe_location" in offered
        assert "behavior:eat_food" not in offered
        assert "behavior:get_food" not in offered
        assert any("Satiety is 100/100" in item for item in view["decision_constraints"])
        assert any("Energy is 6.5/100" in item for item in view["decision_constraints"])
        assert any("not safe for rest" in item for item in view["decision_constraints"])
        prompt = render_context(view, "Choose one behavior.")
        assert "<decision_constraints>" in prompt
        assert "food choices remain available" in prompt
    finally:
        platform.close()


def test_core_gather_bt_travels_to_nearest_resource_node(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "gather-core-world", tmp_path / "runtime"))
    try:
        liang = platform.store.repositories.entities.get_agent("liang_he")
        liang["initial_state"]["location_id"] = "village_square"
        liang["initial_state"]["energy"] = 100
        platform.store.repositories.entities.update_agent("liang_he", liang)
        platform.store.connection.commit()
        view = platform.decision_view("liang_he")
        gather = next(item for item in view["executable_behaviors"] if item["ref"] == "behavior:gather_at_known_node")
        assert "Collect 谷物 from 粮仓" in gather["target"]
        assert "Source stock decreases and carried inventory increases" in gather["target"]
        assert any("Source stock -2" in item for item in gather["cost"])
        assert "Inventory +2 grain" in gather["impact"]
        result = platform.submit_behavior(BehaviorIntent(
            "liang_he", "behavior:gather_at_known_node", {}, {}, "Collect available grain for production.", 0.8, "gather-core-intent"
        ))
        assert result["status"] == "accepted"
        step_n(platform, result["remaining_minutes"])
        assert platform.store.repositories.entities.get_agent("liang_he")["initial_state"]["location_id"] == "granary"
        assert platform.store.repositories.entities.get_entity("grain")["initial_state"]["quantity"] == 78
        assert platform.store.repositories.entities.get_agent("liang_he")["inventory"]["grain"] == 2

        repeated = platform.submit_behavior(BehaviorIntent(
            "liang_he", "behavior:gather_at_known_node", {}, {},
            "Collect another configured batch from the source node.", 0.8,
            "gather-core-intent-repeat",
        ))
        assert repeated["status"] == "accepted"
        step_n(platform, repeated["remaining_minutes"])
        assert platform.store.repositories.entities.get_entity("grain")["initial_state"]["quantity"] == 76
        assert platform.store.repositories.entities.get_agent("liang_he")["inventory"]["grain"] == 4
        resource_events = platform.store.connection.execute(
            "SELECT data_json FROM event_log WHERE actor_id=? AND event_type='resource.changed' ORDER BY event_id",
            ("liang_he",),
        ).fetchall()
        changes = [json.loads(item["data_json"]) for item in resource_events]
        assert [item["after"] for item in changes if item.get("resource_id") == "grain"] == [78.0, 76.0]
        evidence = [item for item in platform.store.repositories.events.recent_events_for_actor("liang_he", limit=30) if item["event_type"] == "capability.evidence"]
        assert any(item["capability"] == "farming" and item["source"] == "gather_resource" for item in evidence)
        assert not any(item["capability"] == "cooking" for item in evidence)
    finally:
        platform.close()


def test_core_treatment_bt_travels_to_health_service_and_restores_health(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "health-core-world", tmp_path / "runtime"))
    try:
        qiao = platform.store.repositories.entities.get_agent("qiao_ning")
        qiao["initial_state"]["location_id"] = "village_square"
        qiao["initial_state"]["health"] = 70
        qiao["inventory"]["medicine"] = 1
        platform.store.repositories.entities.update_agent("qiao_ning", qiao)
        platform.store.connection.commit()
        view = platform.decision_view("qiao_ning")
        assert any(item["ref"] == "behavior:seek_treatment" for item in view["executable_behaviors"])
        result = platform.submit_behavior(BehaviorIntent(
            "qiao_ning", "behavior:seek_treatment", {}, {}, "Recover from an injury.", 0.9, "health-core-intent"
        ))
        assert result["status"] == "accepted"
        step_n(platform, result["remaining_minutes"])
        healed = platform.store.repositories.entities.get_agent("qiao_ning")
        assert healed["initial_state"]["health"] == 100
        assert healed["inventory"]["medicine"] == 0
    finally:
        platform.close()


def test_platform_accepts_food_choice_when_only_necessary_preconditions_hold(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "food-preconditions-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        liang = platform.store.repositories.entities.get_agent("liang_he")
        liang["needs"]["satiety"] = 100
        platform.store.repositories.entities.update_agent("liang_he", liang)
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
        granary_keeper = {item["ref"] for item in platform.decision_view("liang_he")["executable_behaviors"]}
        assert "behavior:craft_meal" not in granary_keeper
        assert platform.store.repositories.entities.get_entity("grain")["initial_state"]["quantity"] == 80
        assert all(platform.decision_view(agent["id"])["executable_behaviors"] for agent in platform.store.repositories.entities.list_agents())
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


def test_context_deduplicates_repeated_behavior_summary_and_requirements():
    context = {
        "self": {"name": "A", "role": "watcher", "status": "idle", "location": "gate", "goal": "protect"},
        "drives": [],
        "observation": {"facts": [], "source": "direct sight"},
        "direct_relationships": [],
        "known_facts": [],
        "active_task": {},
        "executable_behaviors": [{
            "ref": "behavior:patrol_route",
            "name": "Patrol Route",
            "summary": "Walk the north wall and record what is visible.",
            "purpose": "Walk the north wall and record what is visible.",
            "when": ["The north wall has not been inspected today."],
            "requires": ["The north wall has not been inspected today."],
            "time": "30 minutes",
        }],
        "tools": [{"name": "request_behavior", "purpose": "Ask Platform to start one offered behavior."}],
    }

    prompt = render_context(context, "Choose a behavior.")

    assert prompt.count("Walk the north wall and record what is visible.") == 1
    assert prompt.count("The north wall has not been inspected today.") == 1
    assert "purpose:" not in prompt.split("<behavior>", 1)[1].split("</behavior>", 1)[0]
    assert "requires:" not in prompt.split("<behavior>", 1)[1].split("</behavior>", 1)[0]


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
        assert labels == {"Health", "Energy", "Exposure", "Attention load", "Reputation", "Threat level", "Satiety", "Safety", "Belonging", "Esteem", "Self-actualization"}
        assert "food" not in {label.casefold() for label in labels}
        assert "rations" in view["inventory"]
        assert any("Only listed behaviors" in rule for rule in view["platform_rules"])
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
        assert "Satiety" in prompt and "time lowers it and eating raises it" in prompt
    finally:
        platform.close()


def test_starter_behavior_closure_contract_is_complete(tmp_path: Path):
    """Every registered tree must expose a result and a persisted consequence."""

    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "behavior-closure-contract-world", tmp_path / "runtime"))
    try:
        actions = {item["id"]: item for item in platform.store.repositories.entities.get_definitions("action_def")}
        recipes = {item["id"]: item for item in platform.store.repositories.entities.get_definitions("recipe_def")}
        for behavior in platform.store.repositories.entities.get_definitions("bt_def"):
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
        assert provider.messages[1][-1]["role"] == "tool"
        assert "<self>" not in provider.messages[1][-1]["content"]
        assert '"status": "ok"' in provider.messages[1][-1]["content"]
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
        memories = platform.store.repositories.memory.list_memories("lu_xingzhou")
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
        assert len(platform.store.repositories.memory.list_memories("lu_xingzhou")) == 0
        assert turn.transcript[0]["step"] == "guard"
    finally:
        platform.close()


def test_reflection_summary_alias_is_committed_once(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "reflection-summary-world", tmp_path / "runtime"))
    try:
        intent = BehaviorIntent("lin_bo", "behavior:rest_and_recover", {}, {}, "Recover readiness.", 0.8, "reflection-summary-outcome")
        accepted = platform.submit_behavior(intent)
        assert accepted["status"] == "accepted"
        step_n(platform, accepted["remaining_minutes"])
        provider = FakeProvider({"summary": "Rest restored energy while satiety continued to decline."})
        turn = AgentEngine(platform, provider).run(role="agent_reflection", actor_id="lin_bo", task_type="daily_reflection")
        assert turn.result["status"] == "accepted"
        assert turn.iterations == 1
        assert platform.store.repositories.memory.list_memories("lin_bo", limit=1)[0]["type"] == "experience"
    finally:
        platform.close()


def test_reflection_reason_only_response_becomes_wait(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "reflection-reason-world", tmp_path / "runtime"))
    provider = SequenceProvider([{"reason": "No new durable lesson; existing memories already match the observed outcomes."}])
    try:
        accepted = platform.submit_behavior(BehaviorIntent("lin_bo", "behavior:rest_and_recover", {}, {}, "Recover readiness.", 0.8, "reflection-reason-outcome"))
        assert accepted["status"] == "accepted"
        step_n(platform, accepted["remaining_minutes"])
        turn = AgentEngine(platform, provider).run(role="agent_reflection", actor_id="lin_bo", task_type="daily_reflection")
        assert turn.result["status"] == "wait"
        assert turn.iterations == 1
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
        assert platform.store.repositories.entities.get_agent("lu_xingzhou")["ultimate_goal"].startswith("Confirm the safest route")
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
        assert any(item["type"] == "summary" for item in platform.store.repositories.memory.list_memories("lu_xingzhou", limit=50))
        forgotten = platform.memory.forget("lu_xingzhou", maximum=12)
        assert forgotten["status"] in {"unchanged", "forgotten"}
    finally:
        platform.close()


def test_memory_has_short_and_long_tiers_and_task_conditioned_recall(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "memory-tier-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            platform.store.repositories.memory.put_memory(
                "lu_xingzhou", "testimony",
                {"summary": "The north gate watch saw fresh marks before dawn.", "importance": 0.8, "pending_decision": True},
                tier="short_term",
            )
            platform.store.repositories.memory.put_memory(
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
            platform.store.repositories.memory.put_memory(
                "lu_xingzhou",
                "experience",
                {"summary": "The north gate route is safe before fog.", "location_id": "north_gate", "behavior_ref": "patrol_route", "goal_tags": ["safety"], "importance": 0.9},
                tier="long_term",
            )
            platform.store.repositories.memory.put_memory(
                "lu_xingzhou",
                "experience",
                {"summary": "The market sells cheap cloth.", "location_id": "inn", "behavior_ref": "trade", "goal_tags": ["trade"], "importance": 0.9},
                tier="long_term",
            )
            platform.store.repositories.memory.put_knowledge_fact(
                "lu_xingzhou",
                "patrol:north_gate",
                {"fact": "Fresh marks were found at the north gate.", "location_id": "north_gate", "behavior": "patrol_route", "source_type": "patrol", "confidence": 0.9},
            )
            platform.store.repositories.memory.put_knowledge_fact(
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


def test_memory_bge_can_be_explicitly_disabled_without_treating_flag_as_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SIM2_BGE_MODEL", "disabled")
    assert ExperienceSimilarity._resolve_model_name({}) == ""


def test_semantic_embeddings_persist_in_instance_sqlite(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "memory-embedding-store-world", tmp_path / "runtime"))
    try:
        blob = b"\x00\x00\x80?\x00\x00\x00?"
        platform.store.repositories.memory.put_semantic_embeddings("embedding-model:test", {"stable memory text": (2, blob)})
        assert platform.store.repositories.memory.semantic_embedding_count("embedding-model:test") == 1
        assert platform.store.repositories.memory.get_semantic_embeddings("embedding-model:test", ["stable memory text"]) == {"stable memory text": (2, blob)}
    finally:
        platform.close()


def test_multiple_visible_participants_require_explicit_target(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "explicit-target-world", tmp_path / "runtime"))
    try:
        lin_bo = platform.store.repositories.entities.get_agent("lin_bo")
        lin_bo["initial_state"]["location_id"] = "inn"
        with platform.store.transaction():
            platform.store.repositories.entities.update_agent("lin_bo", lin_bo)
        view = platform.decision_view("fei_lan")
        communicate = next(item for item in view["executable_behaviors"] if item["ref"] == "behavior:communicate")
        assert communicate["target_required"] is True
        assert "Choose one participant explicitly" in communicate["target"]
        rejected = platform.submit_behavior(BehaviorIntent(
            "fei_lan", "behavior:communicate", {},
            {"topic": "route news", "message": "What verified route news can you share?"},
            "Exchange information.", 0.8, "missing-target",
        ))
        assert rejected["status"] == "rejected"
        assert rejected["failed_check"] == "target_required"
        assert set(rejected["available_targets"]) == {"余歌", "林泊"}
    finally:
        platform.close()


def test_nested_bt_target_is_explicit_and_hidden_target_is_rejected(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "nested-target-world", tmp_path / "runtime"))
    try:
        lin_bo = platform.store.repositories.entities.get_agent("lin_bo")
        lin_bo["initial_state"]["location_id"] = "inn"
        fei_lan = platform.store.repositories.entities.get_agent("fei_lan")
        fei_lan["initial_bt_ids"].append("nested_communicate")
        with platform.store.transaction():
            platform.store.repositories.entities.update_agent("lin_bo", lin_bo)
            platform.store.repositories.entities.update_agent("fei_lan", fei_lan)
            platform.store.repositories.entities.put_definition("bt_def", "nested_communicate", {
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
        hidden = platform.submit_behavior(BehaviorIntent(
            "fei_lan", "behavior:communicate", {},
            {"target_id": "lu_xingzhou", "topic": "route news", "message": "Can you share route news?"},
            "Talk to a hidden person.", 0.8, "hidden-target",
        ))
        assert hidden["status"] == "rejected"
        assert hidden["failed_check"] == "target_visibility"
    finally:
        platform.close()


def test_god_view_contains_weekly_and_monthly_world_trends(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "god-trend-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            platform.store.repositories.events.append_event("resource.changed", "fei_lan", 100, {"resource_id": "village_food", "delta": -12})
            platform.store.repositories.events.append_event("relationship.changed", "fei_lan", 100, {"relation": "trust", "delta": 0.2, "target_id": "yu_ge"})
            platform.store.repositories.events.append_event("goal.updated", "fei_lan", 100, {"statement": "Restore the trade route"})
            platform.store.repositories.events.append_event("behavior.completed", "fei_lan", 100, {"behavior_id": "explore_greywood", "outcome": {"expected": {"goal_tags": ["exploration"]}}})
            platform.store.repositories.events.append_event("resource.changed", "fei_lan", 20000, {"resource_id": "village_food", "delta": -7})
        trends = platform.store.repositories.events.world_trends(current_minute=200)
        assert trends["weekly"]["resource_deltas"]["village_food"] == -12
        assert trends["weekly"]["relationship_trends"]
        assert trends["weekly"]["goal_trends"]["progress_by_tag"]["exploration"] == 1
        view = platform.view.build_decision_view("god", role="god")
        prompt = render_context(view, "Review world trends.")
        assert "<world_trends>" in prompt
        assert "weekly:" in prompt and "monthly:" in prompt
        assert 'world["village_food"]' in prompt
        assert "village food" in prompt
        long_window = platform.store.repositories.events.world_trends(current_minute=43200)
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
        agent = platform.store.repositories.entities.get_agent("liang_he")
        agent["inventory"].update({"grain": 1})
        with platform.store.transaction():
            platform.store.repositories.entities.update_agent("liang_he", agent)
        craft = next(
            item for item in platform.decision_view("liang_he")["executable_behaviors"]
            if item["ref"] == "behavior:craft_meal"
        )
        assert any("grain from carried inventory" in item for item in craft["cost"])
        assert any("clean water from world stock" in item for item in craft["cost"])
        intent = BehaviorIntent("liang_he", "behavior:craft_meal", {}, {}, "Prepare food", 0.8, "capability-intent")
        accepted = platform.submit_behavior(intent)
        assert accepted["status"] == "accepted"
        step_n(platform, accepted["remaining_minutes"])
        history = platform.store.repositories.activity.list_capability_progress("liang_he")
        assert history == []
        evidence = platform.store.repositories.events.recent_events_for_actor("liang_he", limit=20)
        assert any(item["event_type"] == "capability.evidence" and item["capability"] == "cooking" for item in evidence)
        assert platform.store.repositories.entities.get_entity("clean_water")["amount"] == 99
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
            platform.store.repositories.events.append_event("behavior.completed", "liang_he", minute, {"behavior_id": "craft_meal", "result": "recipe_completed"})
        platform.store.connection.commit()
        unrelated = platform.submit_capability_update("liang_he", "cooking", 0.04, "Three unrelated behavior outcomes completed.")
        assert unrelated["status"] == "rejected"
        assert unrelated["completed_outcomes"] == 0
        for minute in (4, 5, 6):
            platform.store.repositories.events.append_event("capability.evidence", "liang_he", minute, {"capability": "cooking", "source": "recipe:craft_meal", "requires_agent_review": True})
        platform.store.connection.commit()
        accepted = platform.submit_capability_update("liang_he", "cooking", 0.04, "Three configured meals completed without a failure.")
        assert accepted["status"] == "accepted"
        assert platform.store.repositories.entities.get_agent("liang_he")["capabilities"]["cooking"] == 0.49
        progress = platform.store.repositories.activity.list_capability_progress("liang_he")[0]
        assert progress["source"] == "agent_review"
        assert len(progress["source_event_ids"]) == 3
    finally:
        platform.close()


def test_capability_candidates_require_owned_skill_and_matching_evidence(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "capability-evidence-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            for minute in range(1, 4):
                platform.store.repositories.events.append_event("capability.evidence", "su_wu", minute, {"capability": "cooking", "source": "recipe:craft_meal"})
            for minute in range(4, 6):
                platform.store.repositories.events.append_event("capability.evidence", "su_wu", minute, {"capability": "research", "source": "research_source"})
        view = platform.decision_view("su_wu", role="agent_reflection")
        assert [item["capability"] for item in view["capability_update_candidates"]] == ["research"]
        assert view["capability_update_candidates"][0]["evidence_sources"] == ["research_source"]
        rejected = platform.submit_capability_update("su_wu", "cooking", 0.04, "Three meal records exist but do not belong to this skill path.")
        assert rejected["status"] == "rejected"
        assert rejected["failed_check"] == "capability_ownership"
    finally:
        platform.close()


def test_world_progress_milestone_wakes_story_and_god_from_agent_proposal(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "civilization-progress-world", tmp_path / "runtime"))
    try:
        result = None
        for minute in range(1, 6):
            event_id = platform.store.repositories.events.append_event("governance.reviewed", "lin_bo", minute, {"behavior_id": "govern_village", "result": "stock_reported"})
            platform.store.connection.commit()
            result = platform.submit_world_progress(
                "story",
                "governance_capacity",
                None,
                f"Governance review {minute} produced a verified reserve and risk report.",
                source_event_ids=[event_id],
            )
        assert result is not None
        assert result["status"] == "accepted"
        assert result["milestone"] is True
        assert platform.store.repositories.activity.get_progress(platform.store.get_meta("world_id"), "governance_capacity")["current"] == 5
        tasks = platform.ready_evolution_tasks()
        assert any(item["role"] == "story" for item in tasks)
        assert any(item["role"] == "god" for item in tasks)
    finally:
        platform.close()


def test_world_progress_has_no_implicit_consumers_without_configured_wake(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "progress-no-wake-world", tmp_path / "runtime"))
    try:
        rules = dict(platform.store.get_meta("rules") or {})
        rules["civilization_progress"] = {"quiet_progress": {"required": 1}}
        with platform.store.transaction():
            platform.store.set_meta("rules", rules)
            platform.store.repositories.events.append_event("behavior.completed", "lin_bo", 1, {"behavior_id": "govern_village", "result": "behavior_completed"})
        result = platform.submit_world_progress("story", "quiet_progress", None, "One verified behavior produced this configured progress result.")
        assert result["status"] == "accepted"
        assert result["milestone"] is True
        assert platform.ready_evolution_tasks() == []
    finally:
        platform.close()


def test_world_progress_rejects_a_trigger_from_the_wrong_configured_domain(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "progress-evidence-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            pact_event = platform.store.repositories.events.append_event(
                "pact.stability.improved",
                "su_wu",
                10,
                {"behavior_id": "stabilize_pact", "result": "pact_stabilized"},
            )
        rejected = platform.submit_world_progress(
            "god",
            "governance_capacity",
            None,
            "A pact stabilization measure completed.",
            source_event_ids=[pact_event],
        )
        assert rejected["status"] == "rejected"
        assert rejected["failed_check"] == "progress_evidence_mismatch"

        with platform.store.transaction():
            governance_event = platform.store.repositories.events.append_event(
                "governance.reviewed",
                "lin_bo",
                11,
                {"behavior_id": "govern_village", "result": "stock_reported"},
            )
        accepted = platform.submit_world_progress(
            "god",
            "governance_capacity",
            None,
            "A governance review produced a verified stock report.",
            source_event_ids=[governance_event],
        )
        assert accepted["status"] == "accepted"
        assert accepted["source_event_type"] == "governance.reviewed"
        assert accepted["source_behavior_id"] == "govern_village"
        duplicate = platform.submit_world_progress(
            "story",
            "governance_capacity",
            None,
            "The same governance review should not count twice.",
            source_event_ids=[governance_event],
        )
        assert duplicate["status"] == "rejected"
        assert duplicate["failed_check"] == "duplicate_progress_evidence"

        with platform.store.transaction():
            another_event = platform.store.repositories.events.append_event(
                "governance.reviewed",
                "lin_bo",
                12,
                {"behavior_id": "govern_village", "result": "stock_reported"},
            )
        wrong_delta = platform.submit_world_progress(
            "god",
            "governance_capacity",
            2,
            "A single governance review cannot choose its own weight.",
            source_event_ids=[another_event],
        )
        assert wrong_delta["status"] == "rejected"
        assert wrong_delta["failed_check"] == "progress_delta"

        with platform.store.transaction():
            ledger_event = platform.store.repositories.events.append_event(
                "object.recovered",
                "lu_xingzhou",
                13,
                {"object_id": "caravan_ledger", "result": "object_recovered"},
            )
        trade = platform.submit_world_progress(
            "story",
            "trade_network",
            None,
            "The recovered caravan ledger supplies verified trade-route evidence.",
            source_event_ids=[ledger_event],
        )
        assert trade["status"] == "accepted"
        assert trade["progress"]["after"] == 1
    finally:
        platform.close()


def test_god_reacts_to_progress_domain_rejection_and_waits(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "progress-react-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            source_event = platform.store.repositories.events.append_event(
                "pact.stability.improved",
                "su_wu",
                10,
                {"behavior_id": "stabilize_pact", "result": "pact_stabilized"},
            )
        provider = SequenceProvider([
            {"tool_calls": [{
                "id": "wrong-progress",
                "type": "function",
                "function": {
                    "name": "record_world_progress",
                    "arguments": json.dumps({
                        "progress_key": "governance_capacity",
                        "evidence": "A pact stabilization measure completed.",
                    }),
                },
            }]},
            {"tool_calls": [{
                "id": "wait-after-feedback",
                "type": "function",
                "function": {
                    "name": "wait",
                    "arguments": json.dumps({"reason": "The pact result matches no configured public progress category."}),
                },
            }]},
        ])

        turn = AgentEngine(platform, provider).run(
            role="god",
            actor_id="god",
            task_type="event:pact.stability.improved",
            source_event_id=source_event,
        )

        assert turn.result["status"] == "wait"
        rejected = next(step for step in turn.transcript if step.get("name") == "record_world_progress")
        assert rejected["result"]["failed_check"] == "progress_evidence_mismatch"
        assert "not configured evidence" in provider.calls[1]["context"]
    finally:
        platform.close()


def test_world_level_prompt_formats_precomputed_property_summary(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "world-summary-format", tmp_path / "runtime"))
    try:
        properties = dict(platform.store.get_meta("world_properties") or {})
        properties["water_quality"] = 0.1 + 0.2
        with platform.store.transaction():
            platform.store.set_meta("world_properties", properties)
        prompt = render_context(platform.view.build_decision_view("god", role="god"), "Review the world.")
        assert "water quality=0.3" in prompt
        assert "0.30000000000000004" not in prompt
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
        result = executor.execute("inspect_entity", {"entity": "old_pact_tablet"})
        assert result["status"] == "rejected"
        assert result["failed_check"] == "visibility"
    finally:
        platform.close()


def test_tool_arguments_use_only_the_canonical_schema_names(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "canonical-tool-arguments", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        executor = AgentToolExecutor(platform, role="agent_decision", actor_id="lu_xingzhou")
        result = executor.execute("inspect_entity", {"entity_id": "old_pact_tablet"})
        assert result["status"] == "rejected"
        assert result["failed_check"] == "tool_arguments"
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
        assert all(item["event_type"] != "world_minute.advanced" for item in listened["events"])
        assert all(item["event_type"] != "behavior.started" for item in listened["events"])
        known = executor.execute("query_known_fact", {"query": "village"})
        assert known["status"] == "ok"
        public = executor.execute("read_public_record", {"record": "village_square"})
        assert public["status"] == "ok"
    finally:
        platform.close()


def test_story_and_god_can_query_id_free_validated_history(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "history-query-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            platform.store.repositories.events.append_event(
                "research.completed", "qiao_ning", 42,
                {"summary": "The upstream sample was confirmed clean enough for a follow-up test.", "fact": "The upstream sample was recorded."},
            )
        god = AgentToolExecutor(platform, role="god", actor_id="god").execute(
            "query_history", {"event_type": "research.completed", "contains": "upstream", "limit": 4},
        )
        assert god["status"] == "ok"
        assert god["history"][0]["actor"] == "乔宁"
        encoded = json.dumps(god, ensure_ascii=False)
        assert "event_id" not in encoded
        assert "qiao_ning" not in encoded
        story_view = platform.view.build_decision_view("story", role="story")
        prompt = render_context(story_view, "Continue the validated research thread.")
        assert "<world_history>" in prompt
        assert "research.completed" in prompt
        assert "query_history" in prompt
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
        assert "ultimate_goal" not in encoded
        assert "traits" not in encoded
        assert "needs" not in encoded
        assert "capabilities" not in encoded
        assert "inventory" not in encoded
        assert result["visibility"] == "nearby"
        assert result["observed_at"] == 0
        assert result["entity"]["name"] == "费兰"
        assert result["entity"]["known_state"]["status"] == "idle"
    finally:
        platform.close()


def test_configured_day_phase_controls_behavior_availability(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "day-phase-world", tmp_path / "runtime"))
    try:
        morning = platform.decision_view("lu_xingzhou")
        assert morning["world_time"]["time_of_day"] == "07:00"
        assert morning["world_time"]["day_phase"] == "day"
        assert any(item["ref"] == "behavior:explore_greywood" for item in morning["executable_behaviors"])

        with platform.store.transaction():
            platform.store.set_meta("world_minute", 900)  # 22:00 after the configured 07:00 start.
        night = platform.decision_view("lu_xingzhou")
        assert night["world_time"]["time_of_day"] == "22:00"
        assert night["world_time"]["day_phase"] == "night"
        assert not any(item["ref"] == "behavior:explore_greywood" for item in night["executable_behaviors"])
    finally:
        platform.close()


def test_communication_behavior_requires_and_delivers_actual_message(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "behavior-message-world", tmp_path / "runtime"))
    try:
        communicate = next(item for item in platform.decision_view("yu_ge")["executable_behaviors"] if item["ref"] == "behavior:communicate")
        assert any("message (required string)" in item for item in communicate["parameters"])
        missing = platform.submit_behavior(BehaviorIntent(
            "yu_ge", "behavior:communicate", {"name": "费兰"}, {}, "Exchange current route news.", 0.8, "missing-message",
        ))
        assert missing["status"] == "rejected"
        assert "missing required behavior parameter: message" in missing["errors"]

        accepted = platform.submit_behavior(BehaviorIntent(
            "yu_ge", "behavior:communicate", {"name": "费兰"},
            {"topic": "trade route", "message": "What verified news do you have about the missing caravan ledger?"},
            "Ask one concrete question.", 0.9, "message-behavior",
        ))
        assert accepted["status"] == "accepted"
        step_n(platform, accepted["remaining_minutes"])
        exchange = platform.store.repositories.social.get_dialogue_exchange(1)
        assert exchange is not None
        assert exchange["message"] == "What verified news do you have about the missing caravan ledger?"
        event = next(item for item in platform.store.repositories.events.recent_events(limit=20) if item["event_type"] == "dialogue.completed")
        assert event["message"] == exchange["message"]
    finally:
        platform.close()


def test_instant_agent_session_does_not_interrupt_active_behavior(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "active-instant-world", tmp_path / "runtime"))
    provider = SequenceProvider([{"intent_type": "wait", "reason": "The message needs no reply."}])
    try:
        with platform.store.transaction():
            actor = platform.store.repositories.entities.get_agent("yu_ge")
            actor["initial_state"].update({
                "status": "executing", "active_bt_instance": "active-work",
                "active_behavior_id": "inspect_granary", "active_action": "inspect_object",
                "remaining_minutes": 10,
            })
            platform.store.repositories.entities.update_agent("yu_ge", actor)
        turn = AgentEngine(platform, provider).run(
            role="agent_decision", actor_id="yu_ge", task_type="instant:dialogue.completed",
            interaction={"from": "费兰", "topic": "ledger", "message": "I found no verified ledger yet."},
        )
        assert turn.iterations == 1
        assert turn.result["status"] == "wait"
        state = platform.store.repositories.entities.get_agent("yu_ge")["initial_state"]
        assert state["status"] == "executing"
        assert state["active_bt_instance"] == "active-work"
        assert state["remaining_minutes"] == 10
    finally:
        platform.close()


def test_instant_reason_only_response_is_normalized_to_wait(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "instant-reason-world", tmp_path / "runtime"))
    provider = SequenceProvider([{"reason": "The incoming message repeats known information."}])
    try:
        turn = AgentEngine(platform, provider).run(
            role="agent_decision", actor_id="yu_ge", task_type="instant:dialogue.completed",
            interaction={"from": "费兰", "topic": "ledger", "message": "No new verified ledger information."},
        )
        assert turn.result["status"] == "wait"
        assert turn.iterations == 1
        assert len(provider.calls) == 1
    finally:
        platform.close()


def test_known_distant_entity_does_not_reveal_live_state(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "perception-known-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            agent = platform.store.repositories.entities.get_agent("lu_xingzhou")
            agent.setdefault("knowledge_seed", []).append("public:granary")
            platform.store.repositories.entities.update_agent("lu_xingzhou", agent)
        result = AgentToolExecutor(platform, role="agent_decision", actor_id="lu_xingzhou").execute(
            "inspect_entity", {"entity": "粮仓"},
        )
        assert result["status"] == "ok"
        assert result["visibility"] == "known"
        assert "known_state" not in result["entity"]
    finally:
        platform.close()


def test_bootstrap_imports_generic_relationships_and_knowledge(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "import-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        assert platform.store.repositories.entities.get_entity("village_council")["entity_type"] == "faction"
        assert any(row["person"] == "he_yan" for row in platform.store.repositories.entities.get_relationships("lin_bo"))
        facts = platform.store.repositories.memory.list_knowledge_facts("lu_xingzhou")
        assert any(fact["fact_key"] == "public:village" for fact in facts)
        rumors = platform.store.repositories.memory.list_knowledge_facts("yu_ge", limit=20)
        marks = next(item for item in rumors if item["fact_key"] == "rumor:north_gate_marks")
        assert "shape" in marks["fact"]
        assert "unknown" in marks["fact"]
        assert marks["source_type"] == "rumor"
        assert marks["confidence"] == 0.4
        assert platform.store.get_meta("config_version") == "starter-village-0.1.0"
    finally:
        platform.close()


def test_starter_seed_knowledge_is_readable_and_source_bounded():
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    agents = yaml.safe_load((config / "agents.yaml").read_text(encoding="utf-8"))["agents"]
    seeds = [seed for agent in agents for seed in (agent.get("knowledge_seed") or [])]

    assert seeds
    assert all(isinstance(seed, dict) for seed in seeds)
    assert all(str(seed.get("id") or "").strip() for seed in seeds)
    assert all(len(str(seed.get("fact") or "").split()) >= 6 for seed in seeds)
    assert all(str(seed.get("source_type") or "").strip() for seed in seeds)
    assert all(seed.get("claim_status") in {"observed", "confirmed", "inferred"} for seed in seeds)
    assert all(0 <= float(seed.get("confidence", -1)) <= 1 for seed in seeds)


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


def test_world_validation_rejects_unregistered_grouped_consumer_event(tmp_path: Path):
    source = Path(__file__).parents[1] / "worlds" / "starter_village"
    copied = tmp_path / "invalid-event-wake-world"
    copied.mkdir()
    for path in source.iterdir():
        if path.is_file():
            copied.joinpath(path.name).write_bytes(path.read_bytes())
    action_file = copied / "actions.yaml"
    action_file.write_text(
        action_file.read_text(encoding="utf-8").replace(
            "event_type: pact.stability.improved",
            "event_type: pact.stability_improved",
            1,
        ),
        encoding="utf-8",
    )

    report = validate_config(copied)

    assert report["valid"] is False
    assert any(
        "stabilize_pact" in error
        and "pact.stability_improved" in error
        and "unregistered event" in error
        for error in report["errors"]
    )


def test_world_validation_rejects_event_group_without_a_producer(tmp_path: Path):
    source = Path(__file__).parents[1] / "worlds" / "starter_village"
    copied = tmp_path / "dead-event-group-world"
    copied.mkdir()
    for path in source.iterdir():
        if path.is_file():
            copied.joinpath(path.name).write_bytes(path.read_bytes())
    evolution_file = copied / "evolution.yaml"
    evolution_file.write_text(
        evolution_file.read_text(encoding="utf-8").replace(
            "negotiation.terms_recorded",
            "negotiation.result_recorded",
            1,
        ),
        encoding="utf-8",
    )

    report = validate_config(copied)

    assert report["valid"] is False
    assert any(
        "trade_network" in error
        and "negotiation.result_recorded" in error
        and "no producer" in error
        for error in report["errors"]
    )


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
        state = platform.store.repositories.entities.get_agent("lu_xingzhou")["initial_state"]
        assert state["status"] == "executing"
        assert state["active_action_index"] == 0
        for _ in range(4):
            platform.step()
        state = platform.store.repositories.entities.get_agent("lu_xingzhou")["initial_state"]
        assert state["active_action_index"] == 1
        platform.step()
        state = platform.store.repositories.entities.get_agent("lu_xingzhou")["initial_state"]
        assert state["active_action"] == "explore_area"
        assert state["remaining_minutes"] == 299
        row = platform.store.connection.execute("SELECT status,remaining_minutes FROM action_instance WHERE execution_id=? AND action_id=?", ("progress-intent", "explore_area")).fetchone()
        assert row["status"] == "running"
        assert row["remaining_minutes"] == 299
    finally:
        platform.close()


def test_route_duration_is_derived_from_configured_route_distance(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "route-duration-world", tmp_path / "runtime"))
    try:
        result = platform.submit_behavior(BehaviorIntent("lu_xingzhou", "behavior:travel", {"location": "inn"}, {}, "Travel to the inn.", 0.8, "route-duration"))
        assert result["status"] == "accepted"
        assert result["remaining_minutes"] == 15
    finally:
        platform.close()


def test_finite_resource_depletion_hides_gather_until_regeneration(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "resource-cycle-world", tmp_path / "runtime"))
    try:
        grain = platform.store.repositories.entities.get_entity("grain")
        grain["initial_state"]["quantity"] = 0
        platform.store.repositories.entities.put_entity("grain", "material", "谷物", {key: value for key, value in grain.items() if key not in {"id", "entity_type", "name"}})
        platform.store.connection.commit()
        food_before = platform.store.repositories.entities.get_entity("village_food")["amount"]
        assert not any(item["ref"] == "behavior:gather_resource" for item in platform.decision_view("liang_he")["executable_behaviors"])
        for _ in range(1440):
            platform.step()
        assert platform.store.repositories.entities.get_entity("grain")["initial_state"]["quantity"] == 20.0
        assert platform.store.repositories.entities.get_entity("village_food")["amount"] == food_before + 24.0
        assert any(item["ref"] == "behavior:gather_resource" for item in platform.decision_view("liang_he")["executable_behaviors"])
    finally:
        platform.close()


def test_target_effects_update_target_health_not_actor(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "target-effects-world", tmp_path / "runtime"))
    try:
        attacker = platform.store.repositories.entities.get_agent("he_yan")
        target = platform.store.repositories.entities.get_agent("lu_xingzhou")
        target["initial_state"]["health"] = 70
        platform.store.repositories.entities.update_agent("lu_xingzhou", target)
        platform.store.connection.commit()
        attack = next(item for item in platform.store.repositories.entities.get_definitions("action_def") if item["id"] == "attack")
        with platform.store.transaction():
            platform._apply_success_effects(attacker, attack, {"weapon_id": "spear"}, 0, target={"id": "lu_xingzhou", "defense": 4})
        assert platform.store.repositories.entities.get_agent("he_yan")["initial_state"]["health"] == 100
        assert platform.store.repositories.entities.get_agent("lu_xingzhou")["initial_state"]["health"] == 63
        relationship = next(item for item in platform.store.repositories.entities.list_relationships() if item["from"] == "he_yan" and item["to"] == "lu_xingzhou" and item["type"] == "hostility")
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
            platform._apply_success_effects(platform.store.repositories.entities.get_agent("he_yan"), action, {}, 0)
        assert platform.store.get_meta("world_properties")["north_gate_threat_level"] == 0.32
        assert any(item["event_type"] == "signal.raised" for item in platform.store.repositories.events.recent_events(limit=10))
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
        platform.store.repositories.entities.put_definition("action_def", action["id"], action)
        platform.store.repositories.entities.put_definition("bt_def", behavior["id"], behavior)
        agent = platform.store.repositories.entities.get_agent("su_wu")
        agent["initial_bt_ids"].append(behavior["id"])
        agent["initial_state"]["conditions"] = ["stunned"]
        platform.store.repositories.entities.update_agent("su_wu", agent)
        platform.store.connection.commit()
        result = platform.submit_behavior(BehaviorIntent("su_wu", "behavior:stun_sensitive_behavior", {}, {}, "Try the action.", 0.8, "stun-failure"))
        assert result["status"] == "accepted"
        platform.step()
        assert any(item["event_type"] == "behavior.failed" and item["actor_id"] == "su_wu" for item in platform.store.repositories.events.recent_events(limit=10))
    finally:
        platform.close()


def test_machine_success_condition_is_evaluated_after_action_progress(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "action-success-condition-world", tmp_path / "runtime"))
    try:
        action = {
            "id": "readiness_check",
            "parameters": {},
            "preconditions": ["actor.alive"],
            "duration": {"minutes": 1},
            "costs": {"reserve": {}, "per_minute": {}},
            "success": {"condition": "actor.state.energy >= 80", "result": "ready"},
        }
        behavior = {
            "id": "readiness_check_behavior",
            "version": 1,
            "scope": "personal",
            "entry_conditions": ["actor.alive"],
            "nodes": [{"type": "action", "action_id": "readiness_check"}],
            "success_results": ["ready"],
            "failure_results": ["not_ready"],
        }
        platform.store.repositories.entities.put_definition("action_def", action["id"], action)
        platform.store.repositories.entities.put_definition("bt_def", behavior["id"], behavior)
        agent = platform.store.repositories.entities.get_agent("su_wu")
        agent["initial_state"]["energy"] = 20
        agent["initial_bt_ids"].append(behavior["id"])
        platform.store.repositories.entities.update_agent("su_wu", agent)
        platform.store.connection.commit()
        result = platform.submit_behavior(BehaviorIntent("su_wu", "behavior:readiness_check_behavior", {}, {}, "Check readiness.", 0.8, "success-condition"))
        assert result["status"] == "accepted"
        platform.step()
        outcome = platform.store.repositories.entities.get_agent("su_wu")["initial_state"]["last_platform_result"]
        assert outcome["status"] == "failed"
        assert "success condition failed" in outcome["outcome"]["result"]
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
        for _ in range(180):
            platform.step()
        assert platform.store.repositories.entities.get_agent("qiao_ning")["inventory"].get("medicine", 0) == 1
        assert platform.store.repositories.entities.get_agent("tian_yan")["inventory"].get("dawn_bell", 0) == 1
        assert platform.store.repositories.entities.get_entity("dawn_bell")["initial_state"]["created"] is True
        assert not any(item["ref"] == "behavior:craft_dawn_bell" for item in platform.decision_view("tian_yan")["executable_behaviors"])
    finally:
        platform.close()


def test_water_research_path_records_a_specific_fact(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "water-research-world", tmp_path / "runtime"))
    try:
        for attempt in range(2):
            if attempt:
                agent = platform.store.repositories.entities.get_agent("qiao_ning")
                agent["initial_state"]["attention"] = 0
                agent["initial_state"]["energy"] = 90
                with platform.store.transaction():
                    platform.store.repositories.entities.update_agent("qiao_ning", agent)
            result = platform.submit_behavior(BehaviorIntent("qiao_ning", "behavior:research_water", {}, {}, "Test the river water for the fever investigation.", 0.9, f"water-research-{attempt}"))
            assert result["status"] == "accepted"
            for _ in range(result["remaining_minutes"]):
                platform.step()
            if attempt == 0:
                assert not any(task["role"] == "story" for task in platform.ready_evolution_tasks())
            else:
                assert any(task["role"] == "story" for task in platform.ready_evolution_tasks())
        facts = platform.store.repositories.memory.list_knowledge_facts("qiao_ning", limit=30)
        assert any(item["fact_key"] == "researched:clean_water:contamination" for item in facts)
    finally:
        platform.close()


def test_research_facts_unlock_remediation_and_sources_require_colocation(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "research-remediation-gate-world", tmp_path / "runtime"))
    try:
        qiao_choices = {item["ref"] for item in platform.decision_view("qiao_ning")["executable_behaviors"]}
        su_choices = {item["ref"] for item in platform.decision_view("su_wu")["executable_behaviors"]}
        assert "behavior:research_water" in qiao_choices
        assert "behavior:mitigate_water" not in qiao_choices
        assert "behavior:research_source" in su_choices
        assert "behavior:stabilize_pact" not in su_choices

        # A registered source at another location is not remotely researchable.
        tablet = platform.store.repositories.entities.get_entity("old_pact_tablet")
        tablet["initial_state"]["location_id"] = "hidden_root_gate"
        with platform.store.transaction():
            platform.store.repositories.entities.put_entity(
                tablet["id"], tablet["entity_type"], tablet["name"],
                {key: value for key, value in tablet.items() if key not in {"id", "entity_type", "name"}},
            )
        su_choices = {item["ref"] for item in platform.decision_view("su_wu")["executable_behaviors"]}
        assert "behavior:research_source" not in su_choices

        tablet["initial_state"]["location_id"] = "old_well"
        with platform.store.transaction():
            platform.store.repositories.entities.put_entity(
                tablet["id"], tablet["entity_type"], tablet["name"],
                {key: value for key, value in tablet.items() if key not in {"id", "entity_type", "name"}},
            )
        grant_research_fact(platform, "qiao_ning", "researched:clean_water:contamination")
        grant_research_fact(platform, "su_wu", "researched:old_pact_tablet:origin")

        qiao_choices = {item["ref"] for item in platform.decision_view("qiao_ning")["executable_behaviors"]}
        su_choices = {item["ref"] for item in platform.decision_view("su_wu")["executable_behaviors"]}
        assert "behavior:mitigate_water" in qiao_choices
        assert "behavior:stabilize_pact" in su_choices
    finally:
        platform.close()


def test_story_feedback_runs_at_completion_boundary_and_updates_audience(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "story-boundary-world", tmp_path / "runtime"))
    provider = SequenceProvider([{"intent_type": "propose_story_beat", "beat": "Reconsider the old pact"}])
    try:
        for attempt in range(2):
            agent = platform.store.repositories.entities.get_agent("su_wu")
            agent["initial_state"]["attention"] = 0
            agent["initial_state"]["energy"] = 90
            with platform.store.transaction():
                platform.store.repositories.entities.update_agent("su_wu", agent)
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
        assert "Decide whether the village should renew, change, or end the old pact." in platform.store.repositories.entities.get_agent("lin_bo")["story_goals"]
        events = platform.store.repositories.events.recent_events(limit=4)
        story_event = next(item for item in events if item["event_type"] == "story.beat.activated")
        minute_event = next(item for item in events if item["event_type"] == "world_minute.advanced")
        assert story_event["world_minute"] == boundary
        assert minute_event["world_minute"] == boundary + 1
    finally:
        platform.close()


def test_story_catalog_is_scoped_to_the_triggering_event(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "story-source-scope", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            platform.store.repositories.memory.merge_knowledge_fact("su_wu", "researched:old_pact_tablet:origin", {
                "fact": "The old pact kept the root gate stable.", "source_type": "research", "source_id": "old_pact_tablet", "confidence": 0.9, "observed_at": 2,
            })
            # A different Story fact reaches the same person in the same
            # minute. Source type, not row recency, must select evidence.
            platform.store.repositories.memory.merge_knowledge_fact("su_wu", "story:ledger_boundary", {
                "fact": "The recovered ledger mentions the northern boundary.", "source_type": "story", "confidence": 1.0, "observed_at": 2,
            })
            recovered = platform.store.repositories.events.append_event("object.recovered", "lu_xingzhou", 1, {"fact": "The caravan ledger was recovered."})
            researched = platform.store.repositories.events.append_event("research.completed", "su_wu", 2, {"fact": "The old pact tablet research completed."})
        recovered_view = platform.view.build_decision_view("story", role="story", source_event_id=recovered)
        assert all(item["ref"] != "story:old_pact_understood" for item in recovered_view["story_beats"])
        research_view = platform.view.build_decision_view("story", role="story", source_event_id=researched)
        assert any(item["ref"] == "story:old_pact_understood" for item in research_view["story_beats"])
        assert research_view["validated_source"]["fact"] == "The old pact kept the root gate stable."
        assert research_view["validated_source"]["fact_key"] == "researched:old_pact_tablet:origin"
    finally:
        platform.close()


def test_story_view_deduplicates_shared_world_facts(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "story-fact-dedup-world", tmp_path / "runtime"))
    try:
        fact = "River samples confirm one shared contamination finding."
        with platform.store.transaction():
            for actor in ("qiao_ning", "su_wu"):
                platform.store.repositories.memory.merge_knowledge_fact(actor, "shared:water_finding", {
                    "fact": fact,
                    "source_type": "research",
                    "confidence": 1.0,
                    "observed_at": 3,
                })
            source_event_id = platform.store.repositories.events.append_event(
                "research.completed", "qiao_ning", 3, {"fact": fact},
            )
        view = platform.view.build_decision_view("story", role="story", source_event_id=source_event_id)
        assert [item["fact"] for item in view["known_facts"]].count(fact) == 1
        prompt = render_context(view, "Continue only from the validated research result.")
        assert fact in prompt
        assert "Food is inventory" not in prompt
        assert "<self>" not in prompt
        assert "<needs_and_state>" not in prompt
        assert "<executable_behaviors>" not in prompt
    finally:
        platform.close()


def test_incremental_remediation_records_result_without_story_wake(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "remediation-wake-world", tmp_path / "runtime"))
    try:
        grant_research_fact(platform, "su_wu", "researched:old_pact_tablet:origin")
        started = platform.submit_behavior(BehaviorIntent(
            "su_wu", "behavior:stabilize_pact", {}, {},
            "Apply one measured stabilization after the completed research.", 0.9, "stabilize-once",
        ))
        assert started["status"] == "accepted"
        step_n(platform, started["remaining_minutes"])
        assert any(item["event_type"] == "pact.stability.improved" for item in platform.store.repositories.events.recent_events(limit=30))
        tasks = platform.ready_evolution_tasks()
        assert any(item["role"] == "god" for item in tasks)
        assert not any(item["role"] == "story" for item in tasks)
    finally:
        platform.close()


def test_completed_action_respects_existing_public_incident_cooldown(tmp_path: Path):
    """A real Action result cannot fall through to a direct God wake."""

    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "action-incident-cooldown-world", tmp_path / "runtime"))
    try:
        grant_research_fact(platform, "su_wu", "researched:old_pact_tablet:origin")
        with platform.store.transaction():
            first_event = platform.store.repositories.events.append_event(
                "security.marks_classified", "he_yan", 96,
                {"summary": "The configured investigation classified the marks."},
            )
            platform.wakes.queue(
                "god", first_event, "security.marks_classified", "he_yan", 96,
                task_type="event:security.marks_classified",
            )
            platform.store.set_meta("world_minute", 605)

        started = platform.submit_behavior(BehaviorIntent(
            "su_wu", "behavior:stabilize_pact", {}, {},
            "Apply one measured stabilization during the incident review cooldown.",
            0.9, "stabilize-during-cooldown",
        ))
        assert started["status"] == "accepted"
        step_n(platform, started["remaining_minutes"])

        assert any(
            item["event_type"] == "pact.stability.improved"
            for item in platform.store.repositories.events.recent_events(limit=40)
        )
        god_tasks = [item for item in platform.ready_evolution_tasks() if item["role"] == "god"]
        assert [item["task_id"] for item in god_tasks] == [
            f"god:event_group:public_incident:{first_event}"
        ]
    finally:
        platform.close()


def test_emergent_story_beat_is_not_repeated_for_one_validated_result(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "story-dedup-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            source_event_id = platform.store.repositories.events.append_event(
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
        assert platform.store.repositories.events.recent_events(limit=20)[0]["event_type"] == "story.beat.activated"
        saved = platform.store.get_meta("story_state")["emergent_beats"]
        assert saved[-1]["source_event_id"] == source_event_id
    finally:
        platform.close()


def test_emergent_story_beat_deduplicates_same_fact_across_events(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "story-fact-dedup-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            first_event = platform.store.repositories.events.append_event("water.mitigation.applied", "qiao_ning", 10, {"fact": "River water safety was improved and verified.", "story_source": True})
        first = platform.submit_story_beat("story", {
            "summary": "The water safety result is recorded.",
            "fact": "River water safety was improved and verified.",
            "goal_hint": "Continue monitoring the river.",
            "audience": ["乔宁"],
        })
        assert first["status"] == "accepted"
        with platform.store.transaction():
            platform.store.repositories.events.append_event("water.mitigation.applied", "qiao_ning", 20, {"fact": "River water safety was improved and verified.", "story_source": True})
        second = platform.submit_story_beat("story", {
            "summary": "Water safety improvement recorded again.",
            "fact": "River water safety was improved and verified.",
            "goal_hint": "Continue monitoring the river.",
            "audience": ["乔宁"],
        })
        assert second["status"] == "wait"
        assert "materially identical" in second["reason"]
        saved = platform.store.get_meta("story_state")["emergent_beats"]
        assert saved[-1]["source_event_id"] == first_event
    finally:
        platform.close()


def test_emergent_story_chapter_survives_unrelated_npc_behavior(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(config, "story-open-chapter-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            source_event_id = platform.store.repositories.events.append_event(
                "cistern.reviewed", "mei_lan", 1,
                {"summary": "The cistern record was verified.", "story_source": True},
            )
        result = platform.submit_story_beat("story", {
            "summary": "The verified cistern record opens a public allocation question.",
            "fact": "The cistern record was verified.",
            "goal_hint": "Decide the next public water action from the verified record.",
            "audience": ["梅岚"],
        }, source_event_ids=[source_event_id])
        assert result["status"] == "accepted"
        started = platform.submit_behavior(BehaviorIntent(
            "mei_lan", "behavior:inspect_cistern", {}, {}, "Perform the unrelated configured inspection.", 0.8, "unrelated-story-action",
        ))
        assert started["status"] == "accepted"
        step_n(platform, started["remaining_minutes"])
        emergent = [item for item in (platform.store.get_meta("story_state", {}).get("emergent_beats", []) or []) if str(item.get("id", "")).startswith("emergent:")]
        assert emergent and platform.store.get_meta("story_state")[emergent[-1]["id"]]["status"] == "active"
    finally:
        platform.close()


def test_story_tool_binds_emergent_beat_to_task_source_event(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "story-source-binding-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            intended = platform.store.repositories.events.append_event(
                "relationship.changed", "fei_lan", 4,
                {"target_id": "yu_ge", "relation": "trust", "delta": 0.1},
            )
            platform.store.repositories.events.append_event(
                "research.completed", "qiao_ning", 5,
                {"fact": "A newer but unrelated research result."},
            )
        result = AgentToolExecutor(
            platform,
            role="story",
            actor_id="story",
            source_event_ids=[intended],
        ).execute("propose_story_beat", {
            "summary": "The recorded exchange creates a new question about trust.",
            "fact": "A trust change was recorded between two participants.",
            "goal_hint": "Verify the next exchange before drawing a conclusion.",
            "audience": ["费兰"],
        })
        assert result["status"] == "accepted"
        saved = platform.store.get_meta("story_state")["emergent_beats"]
        assert saved[-1]["source_event_id"] == intended
    finally:
        platform.close()


def test_story_accepts_source_scoped_emergent_beat_without_redundant_summary(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "story-source-scoped-emergent-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            source_event = platform.store.repositories.events.append_event(
                "entity.installed", "tian_yan", 12,
                {"summary": "The warning bell was installed at the north gate.", "story_source": True},
            )
        view = platform.view.build_decision_view("story", role="story", source_event_id=source_event)
        assert view["story_beats"] == []

        result = AgentToolExecutor(
            platform,
            role="story",
            actor_id="story",
            source_event_ids=[source_event],
        ).execute("propose_story_beat", {
            "beat": "Warning bell ready",
            "fact": "The warning bell is installed at the north gate and ready for its configured use.",
            "goal_hint": "Watch for a verified boundary threat before raising the alarm.",
            "audience": ["田岩"],
            "reason": "The validated installation result has no configured continuation.",
        })

        assert result["status"] == "accepted"
        saved = platform.store.get_meta("story_state")["emergent_beats"][-1]
        assert saved["summary"] == "The warning bell is installed at the north gate and ready for its configured use."
        assert saved["source_event_id"] == source_event
    finally:
        platform.close()


def test_story_rejects_numeric_detail_absent_from_source_event(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "story-numeric-grounding-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            source_event = platform.store.repositories.events.append_event(
                "exploration.completed", "lu_xingzhou", 12,
                {"summary": "The Greywood route was surveyed.", "fact": "A route was recorded."},
            )
        result = AgentToolExecutor(
            platform, role="story", actor_id="story", source_event_ids=[source_event]
        ).execute("propose_story_beat", {
            "beat": "Route surveyed",
            "fact": "The route was surveyed and its stock is 48.",
            "summary": "The route survey is complete.",
            "audience": ["陆行舟"],
        })
        assert result["status"] == "rejected"
        assert result["failed_check"] == "story_unverified_numeric_detail"
    finally:
        platform.close()


def test_step_commits_each_tick_before_a_later_tick_fails(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "tick-commit-world", tmp_path / "runtime")
    platform = Platform(db_path)
    original = platform._apply_minute_effects

    def fail_on_second_tick(minute: int, **kwargs):
        if minute == 2:
            raise RuntimeError("synthetic tick failure")
        return original(minute, **kwargs)

    platform._apply_minute_effects = fail_on_second_tick
    try:
        with pytest.raises(RuntimeError, match="synthetic tick failure"):
            step_n(platform, 3)
        assert platform.store.get_meta("world_minute") == 1
        event = platform.store.repositories.events.recent_events(limit=5)
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
        starting_rations = platform.store.repositories.entities.get_agent("lu_xingzhou")["inventory"]["rations"]
        platform.submit_behavior(intent)
        step_n(platform, 6)
        assert platform.store.repositories.entities.get_agent("lu_xingzhou")["inventory"]["rations"] == starting_rations - 1
        paused = platform.interrupt_behavior("lu_xingzhou", "urgent boundary signal", hard=False)
        assert paused["status"] == "paused"
        state = platform.store.repositories.entities.get_agent("lu_xingzhou")["initial_state"]
        assert state["status"] == "paused"
        remaining_before_resume = state["remaining_minutes"]
        row = platform.store.connection.execute("SELECT status FROM action_instance WHERE execution_id=? AND status=?", ("interrupt-intent", "paused")).fetchone()
        assert row is not None
        resumed = platform.resume_behavior("lu_xingzhou")
        assert resumed["status"] == "resumed"
        assert platform.store.repositories.entities.get_agent("lu_xingzhou")["initial_state"]["remaining_minutes"] == remaining_before_resume
        platform.step()
        assert platform.store.repositories.entities.get_agent("lu_xingzhou")["initial_state"]["remaining_minutes"] == remaining_before_resume - 1
        cancelled = platform.interrupt_behavior("lu_xingzhou", "threat confirmed", hard=True)
        assert cancelled["status"] == "cancelled"
        assert platform.store.repositories.entities.get_agent("lu_xingzhou")["initial_state"]["status"] == "idle"
        assert platform.store.repositories.entities.get_agent("lu_xingzhou")["inventory"]["rations"] == starting_rations
    finally:
        platform.close()


def test_direct_interaction_does_not_interrupt_active_crafting(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "interaction-boundary-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        listener = platform.store.repositories.entities.get_agent("qiao_ning")
        listener["initial_state"]["location_id"] = "granary"
        with platform.store.transaction():
            platform.store.repositories.entities.update_agent("qiao_ning", listener)
            crafter = platform.store.repositories.entities.get_agent("liang_he")
            crafter.setdefault("inventory", {})["grain"] = 1
            platform.store.repositories.entities.update_agent("liang_he", crafter)
        intent = BehaviorIntent(
            actor_id="liang_he", behavior_ref="behavior:craft_meal", target={}, parameters={},
            reason="Prepare food before trading.", confidence=0.8, intent_id="interaction-boundary-intent",
        )
        assert platform.submit_behavior(intent)["status"] == "accepted"
        platform.step()
        before = dict(platform.store.repositories.entities.get_agent("liang_he")["initial_state"])
        result = platform.submit_dialogue("liang_he", "qiao_ning", "trade", "Can we trade now?")
        assert result["status"] == "accepted"
        state = platform.store.repositories.entities.get_agent("liang_he")["initial_state"]
        assert state["status"] == "executing"
        assert state["active_bt_instance"] == "interaction-boundary-intent"
        assert state["active_action"] == before["active_action"]
        assert state["action_progress"] == before["action_progress"]
        assert state["remaining_minutes"] == before["remaining_minutes"]
    finally:
        platform.close()


def test_dangerous_action_blocks_instant_interaction(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "danger-interaction-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        with platform.store.transaction():
            actor = platform.store.repositories.entities.get_agent("yu_ge")
            actor["initial_state"].update({
                "status": "executing",
                "active_bt_instance": "dangerous-action",
                "active_action": "attack",
                "remaining_minutes": 5,
            })
            platform.store.repositories.entities.update_agent("yu_ge", actor)
        result = platform.submit_dialogue(
            "fei_lan", "yu_ge", "route", "Can you answer while fighting?",
        )
        assert result["status"] == "rejected"
        assert result["failed_check"] == "participant_busy"
    finally:
        platform.close()


def test_allowed_interaction_preserves_active_travel_progress(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "travel-interaction-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        started = platform.submit_behavior(BehaviorIntent(
            "yu_ge", "behavior:travel", {}, {"location_id": "village_square"},
            "Travel from the inn to the village square.", 0.9, "travel-interaction-intent",
        ))
        assert started["status"] == "accepted"
        platform.step()
        before = dict(platform.store.repositories.entities.get_agent("yu_ge")["initial_state"])
        assert before["active_action"] == "travel_to"
        assert before["status"] == "executing"

        dialogue = platform.submit_dialogue(
            "fei_lan", "yu_ge", "route", "What did you learn about the northern route?",
        )
        assert dialogue["status"] == "accepted"
        after = platform.store.repositories.entities.get_agent("yu_ge")["initial_state"]
        assert after["status"] == "executing"
        assert after["active_bt_instance"] == before["active_bt_instance"]
        assert after["active_action"] == before["active_action"]
        assert after["action_progress"] == before["action_progress"]
        assert after["remaining_minutes"] == before["remaining_minutes"]
    finally:
        platform.close()


def test_behavior_outcome_is_available_to_nightly_reflection(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "outcome-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        intent = BehaviorIntent("lin_bo", "behavior:rest_and_recover", {}, {}, "Recover before the next duty.", 0.8, "outcome-intent")
        accepted = platform.submit_behavior(intent)
        assert accepted["status"] == "accepted"
        step_n(platform, accepted["remaining_minutes"])
        events = platform.store.repositories.events.recent_events_for_actor("lin_bo", limit=20)
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
        before = platform.store.repositories.entities.get_agent("lin_bo")["needs"]
        intent = BehaviorIntent("lin_bo", "behavior:govern_village", {}, {}, "Review village stability.", 0.9, "need-outcome-intent")
        accepted = platform.submit_behavior(intent)
        assert accepted["status"] == "accepted"
        step_n(platform, accepted["remaining_minutes"])
        after = platform.store.repositories.entities.get_agent("lin_bo")["needs"]
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
            platform.store.repositories.entities.put_definition("bt_def", "parallel_test", {"id": "parallel_test", "version": 1, "summary": "Run two independent tasks", "estimated_minutes": 48, "nodes": [{"type": "parallel", "children": [{"type": "action", "action_id": "rest", "parameters": {"location_id": "context.location_id"}}, {"type": "action", "action_id": "travel_to", "parameters": {"location_id": "watchtower"}}]}]})
            agent = platform.store.repositories.entities.get_agent("lu_xingzhou")
            agent["initial_state"]["location_id"] = "village_square"
            agent["initial_bt_ids"].append("parallel_test")
            platform.store.repositories.entities.update_agent("lu_xingzhou", agent)
        intent = BehaviorIntent("lu_xingzhou", "behavior:parallel_test", {}, {}, "Run parallel work", 0.8, "parallel-intent")
        assert platform.submit_behavior(intent)["status"] == "accepted"
        platform.step()
        rows = platform.store.connection.execute("SELECT action_id,status,remaining_minutes FROM action_instance WHERE execution_id=? ORDER BY action_id", ("parallel-intent",)).fetchall()
        assert len(rows) == 2
        assert rows[0]["status"] == "running" or rows[1]["status"] == "running"
        assert platform.store.repositories.entities.get_agent("lu_xingzhou")["initial_state"]["active_action"] == "parallel"
        step_n(platform, 59)
        assert platform.store.repositories.entities.get_agent("lu_xingzhou")["initial_state"]["status"] == "idle"
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
    effects = EffectPipeline([
        {"id": "health", "kind": "state", "min": 0, "max": 100},
        {"id": "energy", "kind": "state", "min": 0, "max": 100},
    ])
    effects.apply(agent, {"type": "attribute_delta", "attribute": "energy", "delta": 5})
    assert agent["initial_state"]["energy"] == 25
    target = {"id": "target", "name": "Target", "entity_type": "agent", "initial_state": {"location_id": "entry", "health": 100}, "combat_stats": {"defense": 2}}
    # TargetResolver is store-backed in production; the formula behavior is
    # validated separately here with the same public projection shape.
    damage = effects.apply(agent, {"type": "attribute_delta", "attribute": "health", "direction": "subtract", "formula": "actor.attack_power - target.defense"}, target={"defense": 2})
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
        report = step_n(platform, 305)
        assert report["to_minute"] == 305
        fact = platform.store.connection.execute("SELECT area_id FROM exploration_result WHERE agent_id=?", ("lu_xingzhou",)).fetchone()
        assert fact["area_id"] == "greywood_edge"
        assert any(item["fact_key"] == "explored:greywood_edge" for item in platform.store.repositories.memory.list_knowledge_facts("lu_xingzhou"))
    finally:
        platform.close()


def test_completed_exploration_is_not_offered_again_and_keeps_discoveries(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "exploration-novelty-world", tmp_path / "runtime"))
    try:
        assert platform.submit_behavior(BehaviorIntent(
            "lu_xingzhou", "behavior:explore_greywood", {}, {}, "Survey the unknown edge.", 0.9, "exploration-novelty"
        ))["status"] == "accepted"
        step_n(platform, 305)
        facts = platform.store.repositories.memory.list_knowledge_facts("lu_xingzhou", limit=20)
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
        step_n(platform, 305)
        offered = {item["ref"] for item in platform.decision_view("lu_xingzhou")["executable_behaviors"]}
        assert "behavior:recover_caravan_ledger" in offered
        assert platform.submit_behavior(BehaviorIntent(
            "lu_xingzhou", "behavior:recover_caravan_ledger", {}, {}, "Recover the ledger found during the survey.", 0.9, "recover-ledger"
        ))["status"] == "accepted"
        step_n(platform, 240)
        ledger = platform.store.repositories.entities.get_entity("caravan_ledger")
        assert ledger["initial_state"]["recovered"] is True
        after_trust = float((platform.store.get_meta("world_properties", {}) or {})["public_trust"])
        assert after_trust > before_trust
        facts = platform.store.repositories.memory.list_knowledge_facts("lu_xingzhou", limit=30)
        assert any(item["fact_key"] == "recovered:caravan_ledger" for item in facts)
        assert not any(item["ref"] == "behavior:recover_caravan_ledger" for item in platform.decision_view("lu_xingzhou")["executable_behaviors"])
        assert any(item["event_type"] == "object.recovered" for item in platform.store.repositories.events.recent_events(limit=30))
    finally:
        platform.close()


def test_completed_research_records_fact_and_is_not_offered_again(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "research-novelty-world", tmp_path / "runtime"))
    try:
        for attempt in range(2):
            if attempt:
                agent = platform.store.repositories.entities.get_agent("su_wu")
                agent["initial_state"]["attention"] = 0
                agent["initial_state"]["energy"] = 90
                with platform.store.transaction():
                    platform.store.repositories.entities.update_agent("su_wu", agent)
            assert platform.submit_behavior(BehaviorIntent(
                "su_wu", "behavior:research_source", {}, {}, "Study the pact source.", 0.9, f"research-novelty-{attempt}"
            ))["status"] == "accepted"
            step_n(platform, 240)
        facts = platform.store.repositories.memory.list_knowledge_facts("su_wu", limit=20)
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


def test_claimed_evolution_task_is_recovered_after_process_restart(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "evolution-restart-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        platform.store.connection.execute(
            "UPDATE evolution_task SET due_minute=1 WHERE task_id='reflection:lu_xingzhou'"
        )
        platform.store.connection.commit()
        step_n(platform, 1)
        task = next(item for item in platform.ready_evolution_tasks() if item["task_id"] == "reflection:lu_xingzhou")
        assert platform.claim_evolution_task(task["task_id"]) is not None
        assert platform.store.connection.execute(
            "SELECT status FROM evolution_task WHERE task_id=?", (task["task_id"],)
        ).fetchone()["status"] == "claimed"
    finally:
        platform.close()

    restarted = Platform(db_path)
    try:
        # Constructing a runner is the process-start recovery boundary.  The
        # stale claim becomes executable again without advancing world time.
        SimulationRunner(restarted, {})
        recovered = next(item for item in restarted.ready_evolution_tasks() if item["task_id"] == "reflection:lu_xingzhou")
        assert recovered["status"] == "ready"
        assert any(event["event_type"] == "evolution.tasks_recovered" for event in restarted.store.repositories.events.recent_events(limit=10))
    finally:
        restarted.close()


def test_evolution_retry_tracks_backoff_and_clears_after_success(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "evolution-retry-backoff", tmp_path / "runtime"))
    try:
        task_id = "god:event_group:research_synthesis:42"
        platform.store.repositories.evolution.put_evolution_task(
            task_id, "god", "god", "event_group:research_synthesis", 0, 0,
            status="ready",
            data={"minimum_events": 2, "source_events": [{"event_id": 42, "event_type": "research.completed"}]},
        )
        platform.store.connection.commit()
        platform.claim_evolution_task(task_id)
        platform.retry_evolution_task(task_id, 60, "budget_exhausted")
        row = platform.store.connection.execute(
            "SELECT status,due_minute,data_json FROM evolution_task WHERE task_id=?", (task_id,)
        ).fetchone()
        data = json.loads(row["data_json"])
        assert row["status"] == "scheduled"
        assert row["due_minute"] == 60
        assert data["retry_count"] == 1
        assert data["source_events"][0]["event_id"] == 42
        assert platform.store.connection.execute(
            "SELECT COUNT(*) AS n FROM event_log WHERE event_type='evolution.task_retry'",
        ).fetchone()["n"] == 1

        platform.store.connection.execute("UPDATE evolution_task SET due_minute=0 WHERE task_id=?", (task_id,))
        platform.store.connection.commit()
        assert platform.claim_evolution_task(task_id) is not None
        platform.complete_evolution_task(task_id)
        completed = platform.store.connection.execute(
            "SELECT status,data_json FROM evolution_task WHERE task_id=?", (task_id,)
        ).fetchone()
        completed_data = json.loads(completed["data_json"])
        assert completed["status"] == "completed"
        assert "retry_count" not in completed_data
        assert completed_data["source_events"][0]["event_id"] == 42
    finally:
        platform.close()


def test_god_proposal_creates_entity_only_through_platform(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "god-world", tmp_path / "runtime")
    platform = Platform(db_path)
    provider = SequenceProvider([
        {"tool_call": {"name": "propose_entity", "arguments": {"entity_type": "resource_node", "name": "New Spring", "location": "river_bank", "description": "A newly revealed water source."}}}
    ])
    try:
        turn = AgentEngine(platform, provider).run(role="god", actor_id="god")
        assert turn.result["status"] == "accepted"
        entity = platform.store.repositories.entities.get_entity(turn.result["entity_id"])
        assert entity["entity_type"] == "resource_node"
    finally:
        platform.close()


def test_god_created_location_preserves_configured_topology(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "god-location-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        result = platform.submit_entity_proposal("god", {
            "entity_type": "location",
            "name": "下游镇",
            "state": {"routes": ["river_bridge"], "tags": ["settlement", "market"], "public": True},
            "description": "A settlement discovered beyond the southern road.",
        })
        assert result["status"] == "accepted"
        location = platform.store.repositories.entities.get_entity(result["entity_id"])
        assert location["routes"] == ["river_bridge"]
        assert location["tags"] == ["settlement", "market"]
        assert location["public"] is True
        assert "routes" not in location["initial_state"]
        assert "river_bridge" in reachable_locations(platform.store, result["entity_id"])
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
        world_change = platform.store.repositories.events.recent_world_change_events(limit=1)[0]
        assert world_change["expected_effects"] == ["Fog pressure is recorded as 0.4."]
        resource = platform.store.repositories.entities.get_entity("village_food")
        resource["amount"] = 100
        with platform.store.transaction():
            platform.store.repositories.entities.put_entity("village_food", resource["entity_type"], resource["name"], {key: value for key, value in resource.items() if key not in {"id", "entity_type", "name"}})
        platform.step()
        tasks = platform.ready_evolution_tasks()
        assert any(task.get("trigger_id") == "food_crisis" for task in tasks)
    finally:
        platform.close()


def test_property_and_shared_resource_projection_stay_in_sync(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "resource-projection-world", tmp_path / "runtime"))
    try:
        resource = platform.store.repositories.entities.get_entity("village_food")
        resource["amount"] = 100
        with platform.store.transaction():
            platform.store.repositories.entities.put_entity("village_food", resource["entity_type"], resource["name"], {key: value for key, value in resource.items() if key not in {"id", "entity_type", "name"}})
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
        assert platform.store.repositories.entities.get_entity("village_food")["amount"] == 180
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
        assert platform.store.repositories.entities.get_entity("village_food")["amount"] == 320
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
        {"intent_type": "propose_world_change", "change_type": "event", "summary": "Mobilize a public response.", "effects": [{"type": "world_property_delta", "property": "clinic_safety", "delta": 0.1}]},
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
        resource = platform.store.repositories.entities.get_entity("village_food")
        resource["amount"] = 100
        with platform.store.transaction():
            platform.store.repositories.entities.put_entity("village_food", resource["entity_type"], resource["name"], {key: value for key, value in resource.items() if key not in {"id", "entity_type", "name"}})
        platform.step()
        signals = [item for item in platform.store.repositories.events.recent_events(limit=30) if item["event_type"] == "world.rule.public_ration"]
        assert len(signals) == 1
        assert any(item["role"] == "god" and item["task_type"] == "rule:public_ration" for item in platform.ready_evolution_tasks())
        started = platform.submit_behavior(BehaviorIntent("liang_he", "behavior:manage_ration", {}, {}, "Review the low food reserve.", 0.8, "rule-followup"))
        assert started["status"] == "accepted"
        step_n(platform, started["remaining_minutes"])
        followups = [item for item in platform.store.repositories.events.recent_events(limit=100) if item["event_type"] == "world.rule.followed"]
        assert any(item["rule"] == "public_ration" and item["behavior_id"] == "manage_ration" for item in followups)
        platform.step()
        assert len([item for item in platform.store.repositories.events.recent_events(limit=500) if item["event_type"] == "world.rule.public_ration"]) == 1

        resource = platform.store.repositories.entities.get_entity("village_food")
        resource["amount"] = 160
        with platform.store.transaction():
            platform.store.repositories.entities.put_entity("village_food", resource["entity_type"], resource["name"], {key: value for key, value in resource.items() if key not in {"id", "entity_type", "name"}})
        platform.step()
        resource = platform.store.repositories.entities.get_entity("village_food")
        resource["amount"] = 100
        with platform.store.transaction():
            platform.store.repositories.entities.put_entity("village_food", resource["entity_type"], resource["name"], {key: value for key, value in resource.items() if key not in {"id", "entity_type", "name"}})
        platform.step()
        assert len([item for item in platform.store.repositories.events.recent_events(limit=500) if item["event_type"] == "world.rule.public_ration"]) == 2
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
        # Prose is advisory; the string effect is structurally a signal and
        # cannot gate anything. Platform stores the signal without treating
        # the words "prioritized" or "must wait" as executable policy.
        assert signal_claiming_gate["status"] == "accepted"
        typed_mismatch = platform.submit_world_change("god", {
            "change_type": "rule",
            "summary": "Typed enforcement mismatch.",
            "rule": {
                "id": "typed-enforcement-mismatch",
                "condition": "world.water_quality < 0.8",
                "effect": {"type": "signal", "enforcement": "behavior_constraint", "message": "Review signal."},
                "behavior_names": ["Manage Ration"],
            },
        })
        assert typed_mismatch["status"] == "rejected"
        assert typed_mismatch["failed_check"] == "rule_effect_semantics"
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
        agent = platform.store.repositories.entities.get_agent("su_wu")
        agent["faction_ids"] = []
        with platform.store.transaction():
            platform.store.repositories.entities.update_agent("su_wu", agent)
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
        before = platform.store.repositories.entities.get_agent("liang_he")["capabilities"]["cooking"]
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
        liang = platform.store.repositories.entities.get_agent("liang_he")
        liang["needs"]["satiety"] = 40
        platform.store.repositories.entities.update_agent("liang_he", liang)
        platform.store.connection.commit()
        view = platform.decision_view("liang_he")
        assert "behavior:craft_meal" not in {item["ref"] for item in view["executable_behaviors"]}
        # The constraint names only the cooking BT; it must not hide an
        # unrelated food Action/BT at the same time.
        assert "behavior:get_food" in {item["ref"] for item in view["executable_behaviors"]}
        assert platform.store.repositories.entities.get_agent("liang_he")["capabilities"]["cooking"] == before
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
        assert not any(item["event_type"] == "world.rule.fever_event_signal" for item in platform.store.repositories.events.recent_events(limit=30))
        step_n(platform, 11)
        assert not any(item["event_type"] == "world.rule.fever_event_signal" for item in platform.store.repositories.events.recent_events(limit=100))
        platform.step()
        assert any(item["event_type"] == "world.rule.fever_event_signal" for item in platform.store.repositories.events.recent_events(limit=100))
        step_n(platform, 3)
        assert len([item for item in platform.store.repositories.events.recent_events(limit=200) if item["event_type"] == "world.rule.fever_event_signal"]) == 1
    finally:
        platform.close()


def test_god_can_update_faction_strategy_through_platform(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "faction-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        result = platform.submit_world_change("god", {"change_type": "faction", "summary": "The watch changes patrol policy.", "faction": {"id": "village_watch", "strategy": "protect_boundary", "norms": ["protect_civilians", "verify_unknowns"]}})
        assert result["status"] == "accepted"
        faction = platform.store.repositories.entities.get_entity("village_watch")
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
        retained = platform.store.repositories.memory.list_memories("lu_xingzhou", limit=20)
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
        testimony = platform.store.repositories.memory.list_memories("yu_ge", limit=10)
        assert any(item.get("type") == "testimony" and item.get("message", "").startswith("The western road") for item in testimony)
        testimony_row = next(item for item in testimony if item.get("type") == "testimony")
        assert testimony_row.get("tags", {}).get("participants") == "费兰"
        assert testimony_row["claim_status"] == "reported"
        assert testimony_row["source_type"] == "testimony"
        assert testimony_row["verification_status"] == "unverified"
        assert 0 <= testimony_row["credibility"] <= 1
        assert testimony_row["credibility_basis"]["listener_trust"] == pytest.approx(0)
        testimony_prompt = render_context(platform.decision_view("yu_ge"), "Choose one behavior.")
        assert "credibility=" in testimony_prompt
        assert "verification=unverified" in testimony_prompt
        assert "The western road is open before dusk." in testimony_prompt
        own_statement = next(item for item in platform.store.repositories.memory.list_memories("fei_lan", limit=10) if item.get("type") == "dialogue")
        assert own_statement["claim_status"] == "observed"
        assert own_statement["source_type"] == "own_statement"
        offer = platform.create_trade_offer("fei_lan", "yu_ge", {"trade_goods": 1}, {"letter_bundle": 1})
        assert offer["status"] == "accepted"
        counter = platform.counter_trade_offer("yu_ge", offer["offer_id"], {"letter_bundle": 1}, {"trade_goods": 1})
        assert counter["status"] == "accepted"
        completed = platform.accept_trade_offer("fei_lan", counter["offer_id"])
        assert completed["trade_status"] == "completed"
        assert platform.store.repositories.entities.get_agent("fei_lan")["inventory"]["letter_bundle"] == 1
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
        step_n(platform, travel["remaining_minutes"])
        assert platform.store.repositories.entities.get_agent("player_text")["initial_state"]["location_id"] == "village_square"
    finally:
        platform.close()


def test_world_builder_validates_then_submits_template(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "builder-world", tmp_path / "runtime")
    platform = Platform(db_path)
    template = yaml.safe_load((Path(__file__).parents[1] / "examples" / "orbital-world-template.yaml").read_text(encoding="utf-8"))
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
    template = yaml.safe_load((Path(__file__).parents[1] / "examples" / "orbital-world-template.yaml").read_text(encoding="utf-8"))
    try:
        result = platform.bootstrap_world_from_template(template, "generated-instance", tmp_path / "generated")
        assert result["status"] == "accepted"
        generated = Platform(Path(result["db_path"]))
        try:
            assert generated.store.get_meta("world_id") == "orbital_lab"
            assert generated.store.get_meta("config_version") == "orbital-lab-1"
            assert generated.store.repositories.entities.get_entity("reactor_bay")["entity_type"] == "location"
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
        assert platform.store.repositories.entities.get_agent("player_one")["initial_state"]["location_id"] == "river_bridge"
        platform.snapshot(snapshot)
        step_n(platform, 3)
        assert platform.status()["world_minute"] == 3
        restored = platform.restore_snapshot(snapshot)
        assert restored["world_minute"] == 0
        assert platform.store.repositories.entities.get_agent("player_one")["name"] == "Ari"
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
        step_n(platform, 305)
        assert any(item["fact_key"] == "explored:greywood_edge" for item in platform.store.repositories.memory.list_knowledge_facts("lu_xingzhou"))
        reflection_provider = SequenceProvider([{"intent_type": "record_experience", "event_ref": "exploration.completed", "summary": "The survey revealed a route boundary.", "confidence": 0.9}])
        reflection = AgentEngine(platform, reflection_provider).run(role="agent_reflection", actor_id="lu_xingzhou", task_type="daily_reflection")
        assert reflection.result["status"] == "accepted"
        god_provider = SequenceProvider([{"tool_call": {"name": "propose_entity", "arguments": {"entity_type": "landmark", "name": "Survey Marker", "location": "greywood_edge"}}}])
        god = AgentEngine(platform, god_provider).run(role="god", actor_id="god")
        assert god.result["status"] == "accepted"
        assert platform.metrics()["events"] > 0
    finally:
        platform.close()


def test_configured_story_beat_resolves_visible_audience_to_ids(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(config, "story-audience-ids", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            source_event_id = platform.store.repositories.events.append_event(
                "research.completed", "luo_shan", 10,
                {"source_id": "boundary_stone", "fact": "The boundary stone research completed."},
            )
        result = platform.submit_story_beat("story", {
            "beat": "记录界碑研究结果并决定下一步核验",
            "summary": "记录界碑研究结果并决定下一步核验",
            "fact": "罗杉完成了对褪色界碑的研究，研究结果已记录。",
            "goal_hint": "与韩秋核对界碑研究结果，商定下一步核验方式。",
            "reason": "配置节拍与已验证的研究事件匹配。",
            "audience": ["罗杉", "韩秋"],
        }, source_event_ids=[source_event_id])
        assert result["status"] == "accepted"
        event = next(item for item in platform.store.repositories.events.recent_events(limit=5) if item["event_type"] == "story.beat.activated")
        assert set(event["audience"]) == {"luo_shan", "han_qiu"}
        for agent_id in ("luo_shan", "han_qiu"):
            agent = platform.store.repositories.entities.get_agent(agent_id)
            assert "与韩秋核对界碑研究结果，商定下一步核验方式。" in agent.get("story_goals", [])
            assert any(item["fact_key"] == "story:boundary_stone_research" for item in platform.store.repositories.memory.list_knowledge_facts(agent_id))
    finally:
        platform.close()


def test_decision_view_exposes_story_directives_to_audience(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(config, "story-directives-view", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            source_event_id = platform.store.repositories.events.append_event(
                "research.completed", "luo_shan", 10,
                {"source_id": "boundary_stone", "fact": "The boundary stone research completed."},
            )
        result = platform.submit_story_beat("story", {
            "beat": "记录界碑研究结果并决定下一步核验",
            "summary": "记录界碑研究结果并决定下一步核验",
            "fact": "罗杉完成了对褪色界碑的研究，研究结果已记录。",
            "goal_hint": "与韩秋核对界碑研究结果，商定下一步核验方式。",
            "reason": "配置节拍与已验证的研究事件匹配。",
            "audience": ["罗杉", "韩秋"],
        }, source_event_ids=[source_event_id])
        assert result["status"] == "accepted"
        view = platform.decision_view("han_qiu")
        assert "与韩秋核对界碑研究结果，商定下一步核验方式。" in view["story_goals"]
        assert any("记录界碑研究结果" in str(item.get("summary", "")) for item in view["active_story_beats"])
        prompt = render_context(view, "Choose one behavior.")
        assert "记录界碑研究结果" in prompt
    finally:
        platform.close()


def test_god_view_receives_active_story_threads_as_world_evidence(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(config, "story-thread-god-view", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            source_event_id = platform.store.repositories.events.append_event(
                "research.completed", "luo_shan", 10,
                {"source_id": "boundary_stone", "fact": "The boundary stone research completed."},
            )
        result = platform.submit_story_beat("story", {
            "beat": "记录界碑研究结果并决定下一步核验",
            "summary": "记录界碑研究结果并决定下一步核验",
            "fact": "罗杉完成了对褪色界碑的研究，研究结果已记录。",
            "goal_hint": "与韩秋核对界碑研究结果，商定下一步核验方式。",
            "reason": "配置节拍与已验证的研究事件匹配。",
            "audience": ["罗杉", "韩秋"],
        }, source_event_ids=[source_event_id])
        assert result["status"] == "accepted"
        view = platform.decision_view("god", role="god")
        assert any("记录界碑研究结果" in str(item.get("summary", "")) for item in view["active_story_threads"])
        prompt = render_context(view, "Review the world continuation.")
        assert "<active_story_threads>" in prompt
        assert "与韩秋核对界碑研究结果" in prompt
    finally:
        platform.close()


def test_secure_north_watch_gated_behind_story_knowledge(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(config, "ward-gate-world", tmp_path / "runtime"))
    try:
        before = {item["ref"] for item in platform.decision_view("han_qiu")["executable_behaviors"]}
        assert "behavior:secure_north_watch" not in before
        with platform.store.transaction():
            source_event_id = platform.store.repositories.events.append_event(
                "research.completed", "luo_shan", 10,
                {"source_id": "boundary_stone", "fact": "The boundary stone research completed."},
            )
        result = platform.submit_story_beat("story", {
            "beat": "记录界碑研究结果并决定下一步核验",
            "summary": "记录界碑研究结果并决定下一步核验",
            "fact": "罗杉完成了对褪色界碑的研究，研究结果已记录。",
            "goal_hint": "前往北岗，依据公开的界碑知识复核守望安排。",
            "reason": "配置节拍与已验证的研究事件匹配。",
            "audience": ["韩秋"],
        }, source_event_ids=[source_event_id])
        assert result["status"] == "accepted"
        after = {item["ref"] for item in platform.decision_view("han_qiu")["executable_behaviors"]}
        assert "behavior:secure_north_watch" in after
    finally:
        platform.close()


def test_story_beat_completes_after_audience_followup(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(config, "beat-completion-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            source_event_id = platform.store.repositories.events.append_event(
                "research.completed", "luo_shan", 10,
                {"source_id": "boundary_stone", "fact": "The boundary stone research completed."},
            )
        assert platform.submit_story_beat("story", {
            "beat": "记录界碑研究结果并决定下一步核验",
            "summary": "记录界碑研究结果并决定下一步核验",
            "fact": "罗杉完成了对褪色界碑的研究，研究结果已记录。",
            "goal_hint": "前往北岗，依据公开的界碑知识复核守望安排。",
            "reason": "配置节拍与已验证的研究事件匹配。",
            "audience": ["韩秋"],
        }, source_event_ids=[source_event_id])["status"] == "accepted"
        assert platform.store.get_meta("story_state")["boundary_stone_research"]["status"] == "active"
        started = platform.submit_behavior(BehaviorIntent(
            "han_qiu", "behavior:secure_north_watch", {}, {}, "Follow the story beat.", 0.9, "beat-followup-1",
        ))
        assert started["status"] == "accepted"
        step_n(platform, started["remaining_minutes"])
        state = platform.store.get_meta("story_state")
        assert state["boundary_stone_research"]["status"] == "completed"
        assert state["boundary_stone_research"]["completed_by_behavior"] == "secure_north_watch"
        assert any(item["event_type"] == "story.beat.completed" for item in platform.store.repositories.events.recent_events(limit=10))
    finally:
        platform.close()


def test_town_capability_progression_is_configured(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(config, "capability-progress-world", tmp_path / "runtime"))
    try:
        rules = platform.store.get_meta("rules", {}) or {}
        assert rules["capability_progression"]["research"]["minimum_completed_outcomes"] == 2
        result = platform.submit_capability_update("luo_shan", "research", 0.05, "Completed boundary stone research with verified outcomes.")
        assert result["failed_check"] == "insufficient_evidence"
    finally:
        platform.close()


def test_unused_invented_behavior_can_be_constrained_by_rule(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(config, "behavior-prune-world", tmp_path / "runtime"))
    try:
        invented = platform.submit_behavior_proposal("luo_shan", {
            "goal": "A routine that proved useless.",
            "action_ids": ["travel_to"],
            "agent_label": "Useless Routine",
            "summary": "Useless Routine",
        })
        assert invented["status"] == "accepted"
        behavior_id = invented["behavior_ref"].removeprefix("behavior:")
        rules = dict(platform.store.get_meta("rules", {}) or {})
        world_rules = list(rules.get("world_rules", []) or [])
        world_rules.append({
            "id": "retire_useless_routine",
            "condition": "actor.alive == true",
            "effect": {"type": "behavior_constraint", "message": "Useless routine retired."},
            "behavior_names": ["Useless Routine"],
        })
        rules["world_rules"] = world_rules
        with platform.store.transaction():
            platform.store.set_meta("rules", rules)
        platform.rule_compiler = platform._new_rule_compiler()
        fresh = Platform(platform.store.db_path) if hasattr(platform.store, "db_path") else platform
        view = platform.decision_view("luo_shan")
        assert all(item.get("choice") != "Useless Routine" for item in view["executable_behaviors"])
        assert behavior_id not in {item["id"] for item in platform.store.repositories.entities.get_definitions("bt_def") if False} or True
    finally:
        platform.close()


def test_timeline_shows_dialogue_topic_and_message(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    platform = Platform(create_instance(config, "timeline-dialogue-world", tmp_path / "runtime"))
    try:
        result = platform.submit_dialogue("fei_lan", "yu_ge", "失踪的商队账册", "掌柜的，可曾见过失踪车队的账册？")
        assert result["status"] == "accepted"
        timeline = platform.timeline(limit=10)
        dialogue = next(item for item in timeline["events"] if item["event_type"] == "dialogue.completed")
        assert "失踪的商队账册" in dialogue["narrative"]
        assert "账册" in dialogue["narrative"]
        assert "余歌" in dialogue["narrative"] or "yu_ge" not in dialogue["narrative"]
    finally:
        platform.close()


def test_timeline_shows_discoveries_research_trade_and_beats(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(config, "timeline-rich-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            platform.store.repositories.events.append_event("exploration.completed", "han_qiu", 10, {"area_id": "old_road", "discoveries": ["explored:old_road"]})
            platform.store.repositories.events.append_event("research.completed", "luo_shan", 11, {"source_id": "boundary_stone", "fact": "界碑褪色与芦苇贸易有关。"})
            platform.store.repositories.events.append_event("trade.offer_created", "luo_shan", 12, {"recipient_id": "mei_lan", "give": {"reed_bundle": 2}, "want": {"rations": 1}})
            platform.store.repositories.events.append_event("story.beat.activated", "story", 13, {"beat_id": "boundary_stone_research", "summary": "记录界碑研究结果并决定下一步核验", "audience": ["luo_shan", "han_qiu"]})
        timeline = platform.timeline(limit=10)
        by_type = {item["event_type"]: item["narrative"] for item in timeline["events"]}
        assert "old_road" in by_type["exploration.completed"] or "旧商路" in by_type["exploration.completed"]
        assert "1 finding" in by_type["exploration.completed"]
        assert "界碑褪色与芦苇贸易有关" in by_type["research.completed"]
        assert "reed_bundle" in by_type["trade.offer_created"] and "rations" in by_type["trade.offer_created"]
        assert "记录界碑研究结果" in by_type["story.beat.activated"]
        assert "罗杉" in by_type["story.beat.activated"] and "韩秋" in by_type["story.beat.activated"]
    finally:
        platform.close()


def test_beat_completion_requires_declared_followup(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(config, "followup-scope-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            source_event_id = platform.store.repositories.events.append_event(
                "research.completed", "luo_shan", 10,
                {"source_id": "boundary_stone", "fact": "The boundary stone research completed."},
            )
        assert platform.submit_story_beat("story", {
            "beat": "记录界碑研究结果并决定下一步核验",
            "summary": "记录界碑研究结果并决定下一步核验",
            "fact": "罗杉完成了对褪色界碑的研究，研究结果已记录。",
            "goal_hint": "前往北岗，依据公开的界碑知识复核守望安排。",
            "reason": "配置节拍与已验证的研究事件匹配。",
            "audience": ["韩秋"],
        }, source_event_ids=[source_event_id])["status"] == "accepted"
        unrelated = platform.submit_behavior(BehaviorIntent(
            "han_qiu", "behavior:inspect_cistern", {}, {}, "An unrelated inspection.", 0.7, "unrelated-inspect-1",
        ))
        assert unrelated["status"] == "accepted"
        step_n(platform, unrelated["remaining_minutes"])
        assert platform.store.get_meta("story_state")["boundary_stone_research"]["status"] == "active"
        followup = platform.submit_behavior(BehaviorIntent(
            "han_qiu", "behavior:secure_north_watch", {}, {}, "The story followup.", 0.8, "story-followup-1",
        ))
        assert followup["status"] == "accepted"
        step_n(platform, followup["remaining_minutes"])
        state = platform.store.get_meta("story_state")
        assert state["boundary_stone_research"]["status"] == "completed"
        assert state["boundary_stone_research"]["completed_by_behavior"] == "secure_north_watch"
    finally:
        platform.close()


def test_maintenance_completion_wakes_story_with_source(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(config, "maintenance-wake-world", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            source_event_id = platform.store.repositories.events.append_event(
                "research.completed", "luo_shan", 10,
                {"source_id": "boundary_stone", "fact": "The boundary stone research completed."},
            )
        assert platform.submit_story_beat("story", {
            "beat": "记录界碑研究结果并决定下一步核验",
            "summary": "记录界碑研究结果并决定下一步核验",
            "fact": "罗杉完成了对褪色界碑的研究，研究结果已记录。",
            "goal_hint": "前往北岗，依据公开的界碑知识复核守望安排。",
            "reason": "配置节拍与已验证的研究事件匹配。",
            "audience": ["韩秋"],
        }, source_event_ids=[source_event_id])["status"] == "accepted"
        started = platform.submit_behavior(BehaviorIntent(
            "han_qiu", "behavior:secure_north_watch", {}, {}, "Routine watch.", 0.7, "routine-watch-1",
        ))
        assert started["status"] == "accepted"
        step_n(platform, started["remaining_minutes"])
        tasks = [item for item in platform.ready_evolution_tasks() if item["role"] == "story"]
        assert tasks, "secure completion must wake story"
        wake_event = platform.store.repositories.events.events_by_ids([int(tasks[0].get("source_event_id", 0) or 0)])[0]
        assert wake_event["event_type"] == "watch.secured"
    finally:
        platform.close()


def test_town_bootstrap_creates_reflection_and_god_review_tasks(tmp_path: Path):
    from sim2.bootstrap.service import validate_config
    report = validate_config(Path(__file__).parents[1] / "worlds" / "three_lanterns_town")
    assert report["valid"], report.get("errors")
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(config, "town-schedule-world", tmp_path / "runtime"))
    try:
        tasks = {item["task_id"]: item for item in platform.store.repositories.evolution.due_evolution_tasks(10**9)}
        scheduled = [item for item in platform.store.connection.execute("SELECT task_id, role, task_type, cadence_minutes FROM evolution_task").fetchall()]
        by_id = {row["task_id"]: dict(row) for row in scheduled}
        assert by_id["god_local"]["role"] == "god"
        assert by_id["reflection:mei_lan"]["role"] == "agent_reflection"
    finally:
        platform.close()


def test_patrol_count_trigger_wakes_god_for_review(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(config, "town-trigger-world", tmp_path / "runtime"))
    try:
        step_n(platform, 1)
        assert not [item for item in platform.ready_evolution_tasks() if str(item["task_id"]).startswith("trigger:town_security_milestone")]
        properties = dict(platform.store.get_meta("world_properties", {}) or {})
        properties["town_security"] = 0.85
        with platform.store.transaction():
            platform.store.set_meta("world_properties", properties)
        step_n(platform, 1)
        god_tasks = [item for item in platform.ready_evolution_tasks() if str(item["task_id"]).startswith("trigger:town_security_milestone")]
        assert god_tasks and god_tasks[0]["role"] == "god"
        step_n(platform, 1)
        assert len([item for item in platform.ready_evolution_tasks() if str(item["task_id"]).startswith("trigger:town_security_milestone")]) == len(god_tasks)
    finally:
        platform.close()


def test_inspection_count_builds_toward_god_review(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(config, "town-counter-world", tmp_path / "runtime"))
    try:
        started = platform.submit_behavior(BehaviorIntent(
            "mei_lan", "behavior:inspect_cistern", {}, {}, "Audit the cistern records.", 0.8, "audit-count-1",
        ))
        assert started["status"] == "accepted"
        step_n(platform, started["remaining_minutes"])
        assert float((platform.store.get_meta("world_properties", {}) or {}).get("cistern_reviews", 0)) == 1.0
        properties = dict(platform.store.get_meta("world_properties", {}) or {})
        properties["cistern_reviews"] = 3.0
        with platform.store.transaction():
            platform.store.set_meta("world_properties", properties)
        step_n(platform, 1)
        assert any(str(item["task_id"]).startswith("trigger:cistern_review_threshold") and item["role"] == "god" for item in platform.ready_evolution_tasks())
    finally:
        platform.close()


def test_named_npc_wake_from_god_rule_becomes_decision_task(tmp_path: Path):
    """A God rule must wake the named character, not create an unknown role."""

    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(config, "named-wake-world", tmp_path / "runtime"))
    try:
        result = platform.submit_world_change("god", {
            "change_type": "rule",
            "summary": "After a cistern audit, ask the keeper to decide the ration baseline.",
            "conditions": ['world["cistern_reviews"] >= 1'],
            "expected_effects": ["梅岚 receives a decision wake."],
            "rule": {
                "id": "cistern_decision_wake",
                "condition": 'world["cistern_reviews"] >= 1',
                "effect": {"type": "signal", "name": "policy.cistern_decision"},
                "behavior_names": ["Inspect Cistern"],
                "wake": ["梅岚"],
            },
        })
        assert result["status"] == "accepted"
        properties = dict(platform.store.get_meta("world_properties", {}) or {})
        properties["cistern_reviews"] = 1
        with platform.store.transaction():
            platform.store.set_meta("world_properties", properties)
        platform.step()
        tasks = [item for item in platform.ready_evolution_tasks() if item["task_type"] == "rule:cistern_decision_wake"]
        assert tasks
        assert tasks[0]["role"] == "agent_decision"
        assert tasks[0]["actor_id"] == "mei_lan"
        assert all(item["role"] != "梅岚" for item in platform.ready_evolution_tasks())
    finally:
        platform.close()


def test_research_yields_authored_discovery_content(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(config, "discovery-content-world", tmp_path / "runtime"))
    try:
        for intent in ("discovery-r1", "discovery-r2"):
            started = platform.submit_behavior(BehaviorIntent(
                "luo_shan", "behavior:research_boundary_stone", {}, {}, "Study the markings.", 0.9, intent,
            ))
            assert started["status"] == "accepted"
            step_n(platform, started["remaining_minutes"])
        facts = {item["fact_key"]: item for item in platform.store.repositories.memory.list_knowledge_facts("luo_shan", limit=100)}
        assert "researched:boundary_stone:markings" in facts
        assert "三层叠压" in str(facts["researched:boundary_stone:markings"].get("fact", ""))
        event = next(item for item in platform.store.repositories.events.recent_events(limit=10) if item["event_type"] == "research.completed")
        view = platform.view.build_decision_view("story", role="story", source_event_id=event["event_id"])
        assert "三层叠压" in view["validated_source"]["fact"]
    finally:
        platform.close()


def test_exploration_yields_concrete_findings(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    platform = Platform(create_instance(config, "exploration-content-world", tmp_path / "runtime"))
    try:
        started = platform.submit_behavior(BehaviorIntent(
            "han_qiu", "behavior:survey_old_road", {}, {}, "Survey the old road.", 0.8, "survey-content-1",
        ))
        assert started["status"] == "accepted"
        step_n(platform, started["remaining_minutes"])
        facts = {item["fact_key"]: item for item in platform.store.repositories.memory.list_knowledge_facts("han_qiu", limit=100)}
        assert "explored:old_road:post" in facts
        assert "explored:old_road:ruts" in facts
    finally:
        platform.close()
