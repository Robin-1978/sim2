from __future__ import annotations

from pathlib import Path

from sim2.agents.tools import tool_schemas_for_role
from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
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
    nodes = definition_schema["properties"]["nodes"]
    assert {"action", "behavior", "condition", "wait"} <= set(nodes["items"]["properties"]["type"]["enum"])
    conditions = definition_schema["properties"]["entry_conditions"]
    assert conditions["type"] == "array" and conditions["items"]["type"] == "string"
    assert "predicates" in conditions["description"]


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


def test_god_created_location_requires_and_builds_a_reciprocal_route(tmp_path: Path):
    platform = _town(tmp_path, "god-linked-location")
    try:
        result = platform.submit_entity_proposal("god", {
            "entity_type": "location",
            "name": "North Watch",
            "description": "A verified viewpoint beyond the square.",
            "state": {
                "routes": ["lantern_square"],
                "route_distances": {"lantern_square": 3},
                "tags": ["public", "watch"],
                "public": True,
                "safe": False,
            },
        })
        assert result["status"] == "accepted", result
        square = platform.store.repositories.entities.get_entity("lantern_square")
        watch = platform.store.repositories.entities.get_entity(result["entity_id"])
        assert result["entity_id"] in square["routes"]
        assert "lantern_square" in watch["routes"]
        assert square["route_distances"][result["entity_id"]] == 3
        assert watch["route_distances"]["lantern_square"] == 3
    finally:
        platform.close()


def test_god_cannot_create_a_disconnected_public_location(tmp_path: Path):
    platform = _town(tmp_path, "god-disconnected-location")
    try:
        result = platform.submit_entity_proposal("god", {
            "entity_type": "location",
            "name": "Isolated Platform",
            "description": "A location with no executable way to reach it.",
            "state": {"tags": ["public"], "public": True},
        })
        assert result["status"] == "rejected"
        assert result["failed_check"] == "entity_schema"
        assert any("requires at least one existing route neighbor" in item for item in result["errors"])
    finally:
        platform.close()


def test_god_can_create_a_behavior_that_changes_a_world_resource(tmp_path: Path):
    platform = _town(tmp_path, "god-world-resource-delta")
    try:
        stock = platform.submit_entity_proposal("god", {
            "entity_type": "resource",
            "name": "Test Local Stock",
            "description": "An empty local stock that only a behavior may fill.",
            "location": "三灯广场",
            "state": {"quantity": 0},
        })
        assert stock["status"] == "accepted", stock
        ledger_before = platform.view.build_decision_view("god", role="god")["world_growth_ledger"]
        assert any("Test Local Stock" in item for item in ledger_before["dead_content"])

        action = platform.submit_definition_proposal("god", {
            "definition_type": "action",
            "name": "Add Local Stock",
            "definition": {
                "description": "Add one unit to a visible local stock.",
                "parameters": {"resource_id": "string"},
                "duration": {"minutes": 5},
                "costs": {"reserve": {}, "per_minute": {}, "on_success": {}, "on_failure": {}, "refund": {}},
                "preconditions": ["actor.alive"],
                "effects": [{"type": "world_resource_delta", "resource_id": "context.resource_id", "delta": 1, "maximum": 5}],
                "success": {"condition": "actor.alive", "result": "local stock added"},
                "failure": {"result": "local stock failed"},
                "success_results": ["local stock added"],
                "failure_results": ["local stock failed"],
            },
            "followup_behavior": {
                "name": "Add Local Stock",
                "assign_to": ["梅岚"],
                "definition": {
                    "summary": "Walk to the square and add one local stock unit.",
                    "entry_conditions": ["actor.alive"],
                    "nodes": [
                        {"type": "action", "action_id": "travel_to", "parameters": {"location_id": "三灯广场"}},
                        {"type": "action", "action_id": "Add Local Stock", "parameters": {"resource_id": stock["entity_id"]}},
                    ],
                    "success_results": ["local stock added"],
                    "failure_results": ["local stock failed"],
                },
            },
        })
        assert action["status"] == "accepted", action

        behavior = next(
            item for item in platform.store.repositories.entities.get_definitions("bt_def")
            if item.get("agent_label") == "Add Local Stock"
        )
        started = platform.submit_behavior(BehaviorIntent(
            "mei_lan", f"behavior:{behavior['id']}", {}, {},
            "Verify the new world-resource production action.", 0.9, "world-resource-1",
        ))
        assert started["status"] == "accepted", started
        for _ in range(int(started.get("remaining_minutes", 1) or 1) + 3):
            platform.step()
        after = platform.store.repositories.entities.get_entity(stock["entity_id"])
        assert after["initial_state"]["quantity"] == 1
        ledger_after = platform.view.build_decision_view("god", role="god")["world_growth_ledger"]
        assert not any("Test Local Stock" in item for item in ledger_after["dead_content"])
    finally:
        platform.close()


def test_god_growth_ledger_counts_structure_and_flags_a_dead_place(tmp_path: Path):
    platform = _town(tmp_path, "god-growth-ledger")
    try:
        place = platform.submit_entity_proposal("god", {
            "entity_type": "location",
            "name": "Ledger Test Place",
            "description": "A place created without an activity yet.",
            "state": {"routes": ["lantern_square"], "route_distances": {"lantern_square": 2}},
        })
        assert place["status"] == "accepted", place
        ledger = platform.view.build_decision_view("god", role="god")["world_growth_ledger"]
        assert ledger["cells"]["place"]["count"] >= 1
        assert ledger["cells"]["verb"]["count"] >= 1
        assert any("Ledger Test Place" in item for item in ledger["dead_content"])
        assert ledger["last_structural_change"] is not None
    finally:
        platform.close()


def test_god_prompt_shows_the_growth_ledger_and_empty_cells(tmp_path: Path):
    platform = _town(tmp_path, "god-growth-ledger-prompt")
    try:
        prompt = render_context(platform.decision_view("god", role="god"), "Review structural growth.")
        assert "<world_growth_ledger>" in prompt
        assert "empty cells:" in prompt
        assert "A new research source does not fill a structural cell" in prompt
        assert "dead content" in prompt
    finally:
        platform.close()


def test_positive_world_resource_delta_needs_a_cap_and_respects_it(tmp_path: Path):
    platform = _town(tmp_path, "god-resource-cap")
    try:
        stock = platform.submit_entity_proposal("god", {
            "entity_type": "resource",
            "name": "Test Capped Stock",
            "description": "A stock used to verify production caps.",
            "location": "三灯广场",
            "state": {"quantity": 0},
        })
        assert stock["status"] == "accepted", stock

        def action(maximum: int | None):
            effect = {"type": "world_resource_delta", "resource_id": "context.resource_id", "delta": 10}
            if maximum is not None:
                effect["maximum"] = maximum
            return platform.submit_definition_proposal("god", {
                "definition_type": "action",
                "name": "Add Capped Stock",
                "definition": {
                    "description": "Add to a visible capped stock.",
                    "parameters": {"resource_id": "string"},
                    "duration": {"minutes": 5},
                    "costs": {"reserve": {}, "per_minute": {}, "on_success": {}, "on_failure": {}, "refund": {}},
                    "preconditions": ["actor.alive"],
                    "effects": [effect],
                    "success": {"condition": "actor.alive", "result": "capped stock added"},
                    "failure": {"result": "capped stock failed"},
                    "success_results": ["capped stock added"],
                    "failure_results": ["capped stock failed"],
                },
                "followup_behavior": {
                    "name": "Add Capped Stock",
                    "assign_to": ["梅岚"],
                    "definition": {
                        "summary": "Walk to the square and add capped stock.",
                        "entry_conditions": ["actor.alive"],
                        "nodes": [
                            {"type": "action", "action_id": "travel_to", "parameters": {"location_id": "三灯广场"}},
                            {"type": "action", "action_id": "Add Capped Stock", "parameters": {"resource_id": stock["entity_id"]}},
                        ],
                        "success_results": ["capped stock added"],
                        "failure_results": ["capped stock failed"],
                    },
                },
            })

        uncapped = action(None)
        assert uncapped["status"] == "rejected", uncapped
        assert any("maximum" in item for item in uncapped["errors"])

        with platform.store.transaction():
            rules = dict(platform.store.repositories.metadata.get("rules", {}) or {})
            rules["resource_caps"] = {"Test Capped Stock": {"max": 1, "min": 0}}
            platform.store.repositories.metadata.set("rules", rules)

        accepted = action(10)
        assert accepted["status"] == "accepted", accepted
        behavior = next(
            item for item in platform.store.repositories.entities.get_definitions("bt_def")
            if item.get("agent_label") == "Add Capped Stock"
        )
        started = platform.submit_behavior(BehaviorIntent(
            "mei_lan", f"behavior:{behavior['id']}", {}, {},
            "Verify the configured resource cap.", 0.9, "resource-cap-1",
        ))
        assert started["status"] == "accepted", started
        for _ in range(int(started.get("remaining_minutes", 1) or 1) + 3):
            platform.step()
        after = platform.store.repositories.entities.get_entity(stock["entity_id"])
        assert after["initial_state"]["quantity"] == 1
    finally:
        platform.close()
