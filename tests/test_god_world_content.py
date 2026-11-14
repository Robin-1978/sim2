from __future__ import annotations

from pathlib import Path

from sim2.agents.tools import tool_schemas_for_role
from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform
from sim2.prompting.renderer import render_context


def _town(tmp_path: Path, instance: str) -> Platform:
    return Platform(create_instance(Path(__file__).parents[1] / "worlds" / "three_lanterns_town", instance, tmp_path / "runtime"))


def test_god_contract_exposes_all_world_content_paths():
    tools = {item["function"]["name"]: item["function"] for item in tool_schemas_for_role("god")}

    assert {"propose_entity", "propose_definition", "propose_world_change", "propose_institution"} <= set(tools)
    assert tools["propose_definition"]["parameters"]["properties"]["definition_type"]["enum"] == [
        "attribute", "world_property", "action", "behavior", "recipe", "capability",
    ]
    assert "world_property" not in tools["propose_world_change"]["parameters"]["properties"]["change_type"]["enum"]
    assert "agent" in tools["propose_entity"]["parameters"]["properties"]["entity_type"]["description"]
    definition = tools["propose_definition"]["parameters"]["properties"]["definition"]
    definition_schema = tools["propose_definition"]["parameters"]["properties"]["definition"]
    assert "Travel" in definition_schema["properties"]["nodes"]["description"]
    conditions = definition_schema["properties"]["entry_conditions"]["description"]
    assert "bare world property" in conditions


def test_god_can_create_an_executable_character_from_configured_defaults(tmp_path: Path):
    platform = _town(tmp_path, "god-created-character")
    try:
        result = platform.submit_entity_proposal("god", {
            "entity_type": "agent",
            "name": "岑舟",
            "description": "A record keeper introduced after the verified reserve dispute.",
            "state": {
                "role": "record_keeper",
                "ultimate_goal": "Keep the cistern ledger accurate and publicly accountable.",
                "traits": {"careful": 0.8},
                "capabilities": {"stewardship": 0.55},
                "needs": {"purpose": 64},
                "initial_bt_ids": ["Inspect Cistern"],
                "initial_state": {"location_id": "三灯广场", "status": "idle"},
                "inventory": {"rations": 1},
            },
        })

        assert result["status"] == "accepted", result
        character = platform.store.repositories.entities.get_agent(result["entity_id"])
        assert character["name"] == "岑舟"
        assert character["initial_bt_ids"] == ["inspect_cistern"]
        assert character["initial_state"]["location_id"] == "lantern_square"
        assert character["needs"]["belonging"] == 55
        assert character["initial_state"]["integrity"] == 100
        assert character["initial_state"]["vigor"] == 90
        assert character["capabilities"]["exploration"] == 0
        view = platform.decision_view(result["entity_id"])
        assert any(item["choice"] == "Inspect Cistern" for item in view["executable_behaviors"])
    finally:
        platform.close()


def test_god_prompt_describes_world_content_creation_beyond_institutions(tmp_path: Path):
    platform = _town(tmp_path, "god-world-content-prompt")
    try:
        prompt = render_context(platform.decision_view("god", role="god"), "Create the missing world content.")
        assert "Create any missing world content" in prompt
        assert "agent, location, faction, object, empty resource node" in prompt
        assert "institution, relationship, rule" in prompt
    finally:
        platform.close()


def test_god_prompt_reports_exact_contexts_for_dynamic_behavior_proposals(tmp_path: Path):
    platform = _town(tmp_path, "god-definition-guidance")
    try:
        prompt = render_context(platform.decision_view("god", role="god"), "Review verified research.")
        assert "Exact visible entities" in prompt
        assert "Machine condition examples" in prompt
        assert "actor.can_<capability>" in prompt
        assert "actor.state.location_id == <visible location name>" in prompt
        assert "read-budget warning" in prompt
        assert "Configured regeneration" in prompt
        assert "蓄水" in prompt
        assert "civilization.milestone" in prompt
        assert "entity.created" in prompt
        assert "definition.registered" in prompt
    finally:
        platform.close()


def test_god_agent_creation_rejects_an_unexecutable_character(tmp_path: Path):
    platform = _town(tmp_path, "god-unexecutable-character")
    try:
        result = platform.submit_entity_proposal("god", {
            "entity_type": "agent",
            "name": "无名",
            "state": {
                "role": "resident",
                "ultimate_goal": "Remain available for a later story.",
                "initial_state": {"location_id": "lantern_square", "status": "idle"},
            },
        })

        assert result["status"] == "rejected"
        assert result["failed_check"] == "agent_schema"
        assert any("initial_bt_ids" in error for error in result["errors"])
    finally:
        platform.close()


def test_god_cannot_bypass_behavior_with_a_direct_world_property_change(tmp_path: Path):
    platform = _town(tmp_path, "god-direct-property-policy")
    try:
        rejected = platform.submit_world_change("god", {
            "change_type": "world_property",
            "summary": "Raise town security without a behavior result.",
            "property": "town_security",
            "value": 0.9,
        })
        assert rejected["status"] == "rejected"
        assert rejected["failed_check"] == "god_direct_property_change"
    finally:
        platform.close()


def test_god_cannot_create_duplicate_visible_entities(tmp_path: Path):
    platform = _town(tmp_path, "god-duplicate-entity")
    try:
        result = platform.submit_entity_proposal("god", {
            "entity_type": "object",
            "name": "褪色界碑",
            "description": "A duplicate copy should not create an ambiguous target.",
        })
        assert result["status"] == "rejected"
        assert result["failed_check"] == "entity_name_duplicate"
    finally:
        platform.close()
