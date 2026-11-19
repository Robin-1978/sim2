from __future__ import annotations

from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform


WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
SOCIAL_WORLD = Path(__file__).parents[1] / "worlds" / "social_authority_probe"


def test_action_without_followup_behavior_is_rejected(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "action-needs-binding", tmp_path / "runtime"))
    try:
        result = platform.submit_definition_proposal(
            "god",
            {
                "definition_type": "action",
                "name": "Unbound Action",
                "definition": {
                    "duration": {"minutes": 1},
                    "preconditions": ["actor.alive"],
                    "effects": [],
                    "success": {"condition": "done", "result": "done"},
                    "failure": {"condition": "failed", "result": "failed"},
                },
            },
        )
        assert result["status"] == "rejected"
        assert any("followup_behavior" in error for error in result["errors"])
        assert not any(item.get("name") == "Unbound Action" for item in platform.store.repositories.entities.get_definitions("action_def"))
    finally:
        platform.close()


def test_malformed_action_parameter_shape_is_rejected_without_runtime_error(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "malformed-action-shape", tmp_path / "runtime"))
    try:
        result = platform.submit_definition_proposal(
            "god",
            {
                "definition_type": "action",
                "name": "Malformed Parameters Action",
                "definition": {
                    "parameters": ["item_id"],
                    "duration": {"minutes": 1},
                    "effects": [{"type": "public_event"}],
                    "success": {"result": "completed"},
                    "failure": {"result": "failed"},
                },
                "followup_behavior": {
                    "name": "Malformed Parameters Behavior",
                    "assign_to": ["梅岚"],
                    "definition": {
                        "nodes": [{"type": "action", "action_id": "Malformed Parameters Action", "parameters": {}}],
                        "success_results": ["completed"],
                        "failure_results": ["failed"],
                    },
                },
            },
        )
        assert result["status"] == "rejected"
        assert result["failed_check"] == "definition_schema"
        assert any("parameters" in error for error in result["errors"])
    finally:
        platform.close()


def test_unobservable_dynamic_action_is_rejected(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "action-needs-observable-result", tmp_path / "runtime"))
    try:
        result = platform.submit_definition_proposal(
            "god",
            {
                "definition_type": "action",
                "name": "Refill Test Cistern",
                "definition": {
                    "duration": {"minutes": 1},
                    "parameters": {"location_id": "string"},
                    "success": {"result": "cistern refilled"},
                    "failure": {"result": "refill failed"},
                },
                "followup_behavior": {
                    "name": "Refill Test Cistern Behavior",
                    "assign_to": ["梅岚"],
                    "definition": {
                        "nodes": [{"type": "action", "action_id": "Refill Test Cistern", "parameters": {"location_id": "蓄水屋"}}],
                        "success_results": ["cistern refilled"],
                        "failure_results": ["refill failed"],
                    },
                },
            },
        )
        assert result["status"] == "rejected"
        assert any("observable" in error for error in result["errors"])
    finally:
        platform.close()


def test_resource_transfer_without_carried_reservation_is_rejected(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "action-resource-transfer-contract", tmp_path / "runtime"))
    try:
        result = platform.submit_definition_proposal(
            "god",
            {
                "definition_type": "action",
                "name": "Unreserved Water Transfer",
                "definition": {
                    "parameters": {"resource_id": "string", "item_id": "string"},
                    "duration": {"minutes": 1},
                    "effects": [{"type": "resource_transfer", "resource_id": "context.resource_id", "quantity": 1}],
                    "success": {"result": "water transferred"},
                    "failure": {"result": "water transfer failed"},
                },
                "followup_behavior": {
                    "name": "Unreserved Water Transfer Behavior",
                    "assign_to": ["梅岚"],
                    "definition": {
                        "nodes": [{"type": "action", "action_id": "Unreserved Water Transfer", "parameters": {"resource_id": "cistern_water", "item_id": "蓄水"}}],
                        "success_results": ["water transferred"],
                        "failure_results": ["water transfer failed"],
                    },
                },
            },
        )
        assert result["status"] == "rejected", result
        assert any("costs.reserve.item_parameter" in error for error in result["errors"])
    finally:
        platform.close()


def test_dynamic_item_parameter_cannot_be_used_as_inventory_condition(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "action-resource-transfer-condition", tmp_path / "runtime"))
    try:
        result = platform.submit_definition_proposal(
            "god",
            {
                "definition_type": "action",
                "name": "Parameter Condition Transfer",
                "definition": {
                    "parameters": {"resource_id": "string", "item_id": "string"},
                    "duration": {"minutes": 1},
                    "costs": {"reserve": {"item_parameter": "item_id"}},
                    "effects": [{"type": "resource_transfer", "resource_id": "context.resource_id", "quantity": 1}],
                    "success": {"condition": "actor.inventory.item_id > 0", "result": "water transferred"},
                    "failure": {"result": "water transfer failed"},
                },
                "followup_behavior": {
                    "name": "Parameter Condition Transfer Behavior",
                    "assign_to": ["梅岚"],
                    "definition": {
                        "nodes": [{"type": "action", "action_id": "Parameter Condition Transfer", "parameters": {"resource_id": "cistern_water", "item_id": "蓄水"}}],
                        "success_results": ["water transferred"],
                        "failure_results": ["water transfer failed"],
                    },
                },
            },
        )
        assert result["status"] == "rejected", result
        assert any("dynamic parameter" in error for error in result["errors"])
    finally:
        platform.close()


def test_resource_transfer_success_cannot_depend_on_reserved_inventory(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "action-resource-transfer-postcondition", tmp_path / "runtime"))
    try:
        result = platform.submit_definition_proposal(
            "god",
            {
                "definition_type": "action",
                "name": "Post Reservation Inventory Check",
                "definition": {
                    "parameters": {"resource_id": "string", "item_id": "string"},
                    "duration": {"minutes": 1},
                    "costs": {"reserve": {"item_parameter": "item_id"}},
                    "effects": [{"type": "resource_transfer", "resource_id": "context.resource_id", "quantity": 1}],
                    "success": {"condition": "actor.inventory.energy_cell > 0", "result": "water transferred"},
                    "failure": {"result": "water transfer failed"},
                },
                "followup_behavior": {
                    "name": "Post Reservation Inventory Check Behavior",
                    "assign_to": ["梅岚"],
                    "definition": {
                        "nodes": [{"type": "action", "action_id": "Post Reservation Inventory Check", "parameters": {"resource_id": "cistern_water", "item_id": "蓄水"}}],
                        "success_results": ["water transferred"],
                        "failure_results": ["water transfer failed"],
                    },
                },
            },
        )
        assert result["status"] == "rejected", result
        assert any("after reservation" in error for error in result["errors"])
    finally:
        platform.close()


def test_behavior_outcome_event_requires_a_real_event_type(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "action-outcome-event-contract", tmp_path / "runtime"))
    try:
        result = platform.submit_definition_proposal(
            "god",
            {
                "definition_type": "action",
                "name": "Publish Probe Result",
                "definition": {
                    "duration": {"minutes": 1},
                    "effects": [{"type": "public_event", "event": "probe.result"}],
                    "success": {"result": "probe published"},
                    "failure": {"result": "probe failed"},
                },
                "followup_behavior": {
                    "name": "Publish Probe Result Behavior",
                    "assign_to": ["梅岚"],
                    "definition": {
                        "nodes": [{"type": "action", "action_id": "Publish Probe Result", "parameters": {}}],
                        "success_results": ["probe published"],
                        "failure_results": ["probe failed"],
                        "outcome_events": [{"type": "probe.completed", "summary": "The probe result was published."}],
                    },
                },
            },
        )
        assert result["status"] == "rejected", result
        assert any("outcome_events[0]" in error for error in result["errors"])
    finally:
        platform.close()


def test_recipe_action_recipe_and_behavior_bind_atomically(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "action-recipe-binding", tmp_path / "runtime"))
    try:
        result = platform.submit_definition_proposal(
            "god",
            {
                "definition_type": "action",
                "name": "Craft Test Token",
                "definition": {
                    "parameters": {"recipe_id": "string"},
                    "duration": {"mode": "recipe_duration"},
                    "costs": {"reserve": {"recipe_inputs": 1}},
                    "success": {"condition": "recipe.crafted", "result": "token_crafted"},
                    "failure": {"condition": "recipe.failed", "result": "craft_failed"},
                },
                "followup_recipe": {
                    "name": "Test Token Recipe",
                    "definition": {
                        "duration_minutes": 1,
                        "inputs": [{"item": "rations", "quantity": 1}],
                        "outputs": [{"item": "test_token", "quantity": 1}],
                    },
                },
                "followup_behavior": {
                    "name": "Craft Test Token Behavior",
                    "assign_to": ["梅岚"],
                    "definition": {
                        "summary": "Craft one test token from the configured recipe.",
                        "entry_conditions": ["actor.alive"],
                        "nodes": [{"type": "action", "action_id": "Craft Test Token"}],
                        "success_results": ["token_crafted"],
                        "failure_results": ["craft_failed"],
                    },
                },
            },
        )
        assert result["status"] == "accepted", result
        assert result["bound_behavior"] == "Craft Test Token Behavior"
        assert result["bound_recipe"] == "Test Token Recipe"
        assert any(item.get("name") == "Craft Test Token" for item in platform.store.repositories.entities.get_definitions("action_def"))
        assert any(item.get("name") == "Test Token Recipe" for item in platform.store.repositories.entities.get_definitions("recipe_def"))
        assert any(item.get("agent_label") == "Craft Test Token Behavior" for item in platform.store.repositories.entities.get_definitions("bt_def"))
        assert any(item.get("choice") == "Craft Test Token Behavior" for item in platform.view.build_decision_view("mei_lan", role="agent_decision")["executable_behaviors"])
    finally:
        platform.close()


def test_recipe_action_normalizes_model_shorthand_and_visible_resource(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "action-recipe-model-shorthand", tmp_path / "runtime"))
    try:
        luo = platform.store.repositories.entities.get_agent("luo_shan")
        luo["inventory"] = {**(luo.get("inventory") or {}), "reed_bundle": 2}
        platform.store.repositories.entities.update_agent("luo_shan", luo)
        result = platform.submit_definition_proposal(
            "god",
            {
                "definition_type": "action",
                "name": "编织测试芦苇席",
                # This is the compact shape the real model produced.  The
                # platform normalizes the generic shorthand to costs.reserve
                # and adds the recipe parameter when the bound recipe exists.
                "definition": {
                    "duration": {"mode": "recipe_duration"},
                    "reserve": {"recipe_inputs": True},
                    "success": {"result": "mat_crafted"},
                    "failure": {"result": "mat_failed"},
                },
                "followup_recipe": {
                    "name": "测试芦苇席配方",
                    "definition": {
                        "duration_minutes": 1,
                        "inputs": [{"item": "灯芯芦苇", "quantity": 2}],
                        "outputs": [{"item": "测试芦苇席", "quantity": 1}],
                    },
                },
                "followup_behavior": {
                    "name": "编织测试芦苇席行为",
                    "assign_to": ["罗杉"],
                    "definition": {
                        "summary": "把灯芯芦苇编成测试芦苇席。",
                        "nodes": [
                            {"type": "action", "action_id": "Travel To", "parameters": {"location_id": "旧商路"}},
                            {"type": "action", "action_id": "编织测试芦苇席", "parameters": {}},
                        ],
                        "success_results": ["mat_crafted"],
                        "failure_results": ["mat_failed"],
                    },
                },
            },
        )
        assert result["status"] == "accepted", result
        action = next(item for item in platform.store.repositories.entities.get_definitions("action_def") if item.get("name") == "编织测试芦苇席")
        assert action["parameters"]["recipe_id"] == "string"
        assert action["costs"]["reserve"]["recipe_inputs"] == 1
        recipe = next(item for item in platform.store.repositories.entities.get_definitions("recipe_def") if item.get("name") == "测试芦苇席配方")
        assert recipe["inputs"][0]["item"] == "reed_bundle"
        assert any(item.get("choice") == "编织测试芦苇席行为" for item in platform.view.build_decision_view("luo_shan", role="agent_decision")["executable_behaviors"])
        behavior_id = next(item["id"] for item in platform.store.repositories.entities.get_definitions("bt_def") if item.get("agent_label") == "编织测试芦苇席行为")
        started = platform.submit_behavior(BehaviorIntent(
            "luo_shan", f"behavior:{behavior_id}", {}, {},
            "Use the collected reeds to craft the configured mat.", 0.9, "run-bound-recipe-action",
        ))
        assert started["status"] == "accepted", started
        for _ in range(int(started.get("remaining_minutes", 1)) + 4):
            platform.step()
        state = platform.store.repositories.entities.get_agent("luo_shan")["initial_state"]
        assert state["status"] == "idle", state
        assert platform.store.repositories.entities.get_agent("luo_shan")["inventory"].get("测试芦苇席", 0) == 1
        assert platform.store.repositories.entities.get_agent("luo_shan")["inventory"].get("reed_bundle", 0) == 0
    finally:
        platform.close()


def test_dynamic_action_can_transfer_carried_resource_into_world_stock(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "action-resource-transfer", tmp_path / "runtime"))
    try:
        agent = platform.store.repositories.entities.get_agent("mei_lan")
        agent["inventory"] = {**(agent.get("inventory") or {}), "蓄水": 2}
        platform.store.repositories.entities.update_agent("mei_lan", agent)
        pool = platform.store.repositories.entities.get_entity("cistern_water")
        pool["initial_state"]["quantity"] = 0
        platform.store.repositories.entities.put_entity(pool["id"], pool["entity_type"], pool["name"], {key: value for key, value in pool.items() if key not in {"id", "entity_type", "name"}})
        result = platform.submit_definition_proposal(
            "god",
            {
                "definition_type": "action",
                "name": "Transfer Carried Water",
                "definition": {
                    "parameters": {"resource_id": "string", "item_id": "string"},
                    "duration": {"minutes": 1},
                    "costs": {"reserve": {"item_parameter": "item_id"}},
                    "effects": [{"type": "resource_transfer", "resource_id": "context.resource_id", "quantity": 1}],
                    "success": {"result": "water transferred"},
                    "failure": {"result": "water transfer failed"},
                },
                "followup_behavior": {
                    "name": "Transfer Carried Water Behavior",
                    "assign_to": ["梅岚"],
                    "definition": {
                        "nodes": [
                            {"type": "action", "action_id": "Travel To", "parameters": {"location_id": "蓄水屋"}},
                            {"type": "action", "action_id": "Transfer Carried Water", "parameters": {"resource_id": "cistern_water", "item_id": "蓄水"}},
                        ],
                        "success_results": ["water transferred"],
                        "failure_results": ["water transfer failed"],
                    },
                },
            },
        )
        assert result["status"] == "accepted", result
        behavior_id = next(item["id"] for item in platform.store.repositories.entities.get_definitions("bt_def") if item.get("agent_label") == "Transfer Carried Water Behavior")
        started = platform.submit_behavior(BehaviorIntent("mei_lan", f"behavior:{behavior_id}", {}, {}, "Transfer carried water to the public pool.", 0.9, "transfer-carried-water"))
        assert started["status"] == "accepted", started
        for _ in range(int(started["remaining_minutes"]) + 4):
            platform.step()
        assert platform.store.repositories.entities.get_entity("cistern_water")["initial_state"]["quantity"] == 1
        assert platform.store.repositories.entities.get_agent("mei_lan")["inventory"]["蓄水"] == 1
    finally:
        platform.close()


def test_dynamic_resource_transfer_uses_configured_quantity(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "action-resource-transfer-quantity", tmp_path / "runtime"))
    try:
        agent = platform.store.repositories.entities.get_agent("mei_lan")
        agent["inventory"] = {**(agent.get("inventory") or {}), "蓄水": 2}
        platform.store.repositories.entities.update_agent("mei_lan", agent)
        pool = platform.store.repositories.entities.get_entity("cistern_water")
        pool["initial_state"]["quantity"] = 0
        platform.store.repositories.entities.put_entity(pool["id"], pool["entity_type"], pool["name"], {key: value for key, value in pool.items() if key not in {"id", "entity_type", "name"}})
        result = platform.submit_definition_proposal(
            "god",
            {
                "definition_type": "action",
                "name": "Transfer Configured Water",
                "definition": {
                    "parameters": {"resource_id": "string", "item_id": "string", "quantity": "number"},
                    "duration": {"minutes": 1},
                    "costs": {"reserve": {"item_parameter": "item_id", "item_quantity_parameter": "quantity"}},
                    "effects": [{"type": "resource_transfer", "resource_id": "context.resource_id", "source_item_parameter": "item_id", "quantity_parameter": "quantity"}],
                    "success": {"result": "water transferred"},
                    "failure": {"result": "water transfer failed"},
                },
                "followup_behavior": {
                    "name": "Transfer Configured Water Behavior",
                    "assign_to": ["梅岚"],
                    "definition": {
                        "nodes": [
                            {"type": "action", "action_id": "Travel To", "parameters": {"location_id": "蓄水屋"}},
                            {"type": "action", "action_id": "Transfer Configured Water", "parameters": {"resource_id": "cistern_water", "item_id": "蓄水", "quantity": 2}},
                        ],
                        "success_results": ["water transferred"],
                        "failure_results": ["water transfer failed"],
                    },
                },
            },
        )
        assert result["status"] == "accepted", result
        behavior_id = next(item["id"] for item in platform.store.repositories.entities.get_definitions("bt_def") if item.get("agent_label") == "Transfer Configured Water Behavior")
        started = platform.submit_behavior(BehaviorIntent("mei_lan", f"behavior:{behavior_id}", {}, {}, "Transfer two carried water units.", 0.9, "configured-transfer-quantity"))
        assert started["status"] == "accepted", started
        for _ in range(int(started["remaining_minutes"]) + 4):
            platform.step()
        assert platform.store.repositories.entities.get_entity("cistern_water")["initial_state"]["quantity"] == 2
        assert platform.store.repositories.entities.get_agent("mei_lan")["inventory"]["蓄水"] == 0
    finally:
        platform.close()


def test_dynamic_resource_transfer_cannot_mint_more_than_reserved(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "action-resource-transfer-conservation", tmp_path / "runtime"))
    try:
        agent = platform.store.repositories.entities.get_agent("mei_lan")
        agent["inventory"] = {**(agent.get("inventory") or {}), "蓄水": 1}
        platform.store.repositories.entities.update_agent("mei_lan", agent)
        pool = platform.store.repositories.entities.get_entity("cistern_water")
        pool["initial_state"]["quantity"] = 0
        platform.store.repositories.entities.put_entity(
            pool["id"],
            pool["entity_type"],
            pool["name"],
            {key: value for key, value in pool.items() if key not in {"id", "entity_type", "name"}},
        )
        result = platform.submit_definition_proposal(
            "god",
            {
                "definition_type": "action",
                "name": "Overfill Carried Water",
                "definition": {
                    "parameters": {"resource_id": "string", "item_id": "string"},
                    "duration": {"minutes": 1},
                    "costs": {"reserve": {"item_parameter": "item_id"}},
                    "effects": [{"type": "resource_transfer", "resource_id": "context.resource_id", "quantity": 2}],
                    "success": {"result": "water transferred"},
                    "failure": {"result": "water transfer failed"},
                },
                "followup_behavior": {
                    "name": "Overfill Carried Water Behavior",
                    "assign_to": ["梅岚"],
                    "definition": {
                        "nodes": [
                            {"type": "action", "action_id": "Travel To", "parameters": {"location_id": "蓄水屋"}},
                            {"type": "action", "action_id": "Overfill Carried Water", "parameters": {"resource_id": "cistern_water", "item_id": "蓄水"}},
                        ],
                        "success_results": ["water transferred"],
                        "failure_results": ["water transfer failed"],
                    },
                },
            },
        )
        assert result["status"] == "rejected", result
        assert any("item_quantity_parameter" in error for error in result["errors"])
        assert platform.store.repositories.entities.get_entity("cistern_water")["initial_state"]["quantity"] == 0
        assert platform.store.repositories.entities.get_agent("mei_lan")["inventory"]["蓄水"] == 1
    finally:
        platform.close()



def test_resource_transfer_requires_the_actor_at_the_stock_location(tmp_path: Path) -> None:
    """A carried item cannot be deposited into a stock in another location."""

    platform = Platform(create_instance(WORLD, "action-transfer-location", tmp_path / "runtime"))
    try:
        agent = platform.store.repositories.entities.get_agent("mei_lan")
        agent["inventory"] = {**(agent.get("inventory") or {}), "蓄水": 1}
        platform.store.repositories.entities.update_agent("mei_lan", agent)
        with platform.store.transaction():
            pool = platform.store.repositories.entities.get_entity("cistern_water")
            pool["initial_state"]["quantity"] = 0
            platform.store.repositories.entities.put_entity(
                pool["id"],
                pool["entity_type"],
                pool["name"],
                {key: value for key, value in pool.items() if key not in {"id", "entity_type", "name"}},
            )
        result = platform.submit_definition_proposal(
            "god",
            {
                "definition_type": "action",
                "name": "Transfer Without Travel",
                "definition": {
                    "parameters": {"resource_id": "string", "item_id": "string"},
                    "duration": {"minutes": 1},
                    "costs": {"reserve": {"item_parameter": "item_id"}},
                    "effects": [{"type": "resource_transfer", "resource_id": "context.resource_id", "quantity": 1}],
                    "success": {"result": "water transferred"},
                    "failure": {"result": "water transfer failed"},
                },
                "followup_behavior": {
                    "name": "Transfer Without Travel Behavior",
                    "assign_to": ["梅岚"],
                    "definition": {
                        "nodes": [{
                            "type": "action",
                            "action_id": "Transfer Without Travel",
                            "parameters": {"resource_id": "cistern_water", "item_id": "蓄水"},
                        }],
                        "success_results": ["water transferred"],
                        "failure_results": ["water transfer failed"],
                    },
                },
            },
        )
        assert result["status"] == "rejected", result
        assert any(
            "not at the resource location" in str(item) or "travel node" in str(item)
            for item in result.get("errors") or []
        ), result
        assert not any(
            item.get("agent_label") == "Transfer Without Travel Behavior"
            for item in platform.store.repositories.entities.get_definitions("bt_def")
        )
    finally:
        platform.close()


def test_action_rejects_behavior_only_fields(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "action-field-scope", tmp_path / "runtime"))
    try:
        result = platform.submit_definition_proposal(
            "god",
            {
                "definition_type": "action",
                "name": "Scoped Field Action",
                "definition": {
                    "parameters": {"resource_id": "string"},
                    "duration": {"minutes": 1},
                    "effects": [{
                        "type": "resource_withdrawal",
                        "resource_id": "context.resource_id",
                        "amount": 1,
                        "scope": "resource_withdrawal",
                    }],
                    "success": {"result": "done"},
                    "failure": {"result": "failed"},
                    "entry_conditions": ["actor.alive"],
                    "success_results": ["done"],
                    "failure_results": ["failed"],
                    "outcome_events": [{"event_type": "scoped.done", "summary": "Done."}],
                    "parameter_bindings": {"resource_location_id": "resource_location"},
                    "authorization_operation": "review_violation",
                },
                "followup_behavior": {
                    "name": "Scoped Field Action Behavior",
                    "assign_to": ["梅岚"],
                    "definition": {
                        "nodes": [{
                            "type": "action",
                            "action_id": "Scoped Field Action",
                            "parameters": {"resource_id": "cistern_water"},
                        }],
                    },
                },
            },
        )
        assert result["status"] == "rejected", result
        errors = " ".join(str(item) for item in result.get("errors") or [])
        for field in (
            "entry_conditions", "success_results", "failure_results", "outcome_events",
            "parameter_bindings", "authorization_operation",
        ):
            assert f"behavior field {field}" in errors, errors
    finally:
        platform.close()


def _move_agent(platform: Platform, agent_id: str, location_id: str) -> None:
    with platform.store.transaction():
        agent = platform.store.repositories.entities.get_agent(agent_id)
        state = dict(agent.get("initial_state") or {})
        state["location_id"] = location_id
        agent["initial_state"] = state
        platform.store.repositories.entities.update_agent(agent_id, agent)


def _withdraw_action(name: str, action_id: str) -> dict:
    return {
        "definition_type": "action",
        "name": name,
        "definition": {
            "verb": "transfer_resource",
            "parameters": {"resource_id": "string", "scope": "string"},
            "duration": {"minutes": 1},
            "effects": [{
                "type": "resource_withdrawal",
                "resource_id": "context.resource_id",
                "amount": 1,
                "scope": "context.scope",
            }],
            "success": {"condition": "actor.alive", "result": "withdrawn"},
            "failure": {"condition": "actor.incapacitated", "result": "withdrawal failed"},
        },
        "followup_behavior": {
            "name": f"{name} Behavior",
            "assign_to": ["梅岚"],
            "definition": {},
        },
    }


def test_parameterized_withdrawal_derives_the_stock_location_and_travels(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "withdraw-location-binding", tmp_path / "runtime"))
    try:
        proposal = _withdraw_action("Withdraw Bound Stock", "withdraw_bound_stock")
        proposal["followup_behavior"]["definition"] = {
            "id": "withdraw_bound_stock",
            "agent_label": "Withdraw Bound Stock",
            "agent_parameters": {"resource_id": {"type": "string", "required": True}},
            "parameter_bindings": {"resource_location_id": "resource_location"},
            "nodes": [
                {"type": "action", "action_id": "travel_to", "parameters": {"location_id": "context.resource_location_id"}},
                {"type": "action", "action_id": "Withdraw Bound Stock", "parameters": {"resource_id": "context.resource_id", "scope": "resource_withdrawal"}},
            ],
        }
        result = platform.submit_definition_proposal("god", proposal)
        assert result["status"] == "accepted", result

        before = platform.store.repositories.entities.get_entity("cistern_water")["initial_state"]["quantity"]
        admitted = platform.submit_behavior(
            BehaviorIntent(
                "mei_lan",
                "behavior:withdraw_bound_stock",
                {},
                {"resource_id": "cistern_water"},
                "Withdraw from the cistern.",
                0.9,
                "withdraw-location-binding-1",
            )
        )
        assert admitted["status"] == "accepted", admitted
        stored = platform.store.repositories.evolution.get_intent("withdraw-location-binding-1")
        assert stored["data"]["parameters"]["resource_location_id"] == "cistern_house"

        for _ in range(10):
            platform.step()
            agent = platform.store.repositories.entities.get_agent("mei_lan")
            if agent["initial_state"].get("status") != "executing":
                break
        agent = platform.store.repositories.entities.get_agent("mei_lan")
        assert agent["initial_state"]["location_id"] == "cistern_house"
        assert platform.store.repositories.entities.get_entity("cistern_water")["initial_state"]["quantity"] == before - 1
    finally:
        platform.close()


def test_remote_withdrawal_without_travel_is_rejected_at_admission(tmp_path: Path) -> None:
    platform = Platform(create_instance(SOCIAL_WORLD, "withdraw-remote-gate", tmp_path / "runtime"))
    try:
        _move_agent(platform, "mira", "relay_yard")
        view = platform.view.build_decision_view("mira", role="agent_decision", include_diagnostics=True)
        diagnostic = next(
            item for item in view["unavailable_behavior_diagnostics"] if item.get("behavior_id") == "withdraw_resource"
        )
        assert any("not at the resource location" in str(item) for item in diagnostic["failed"])

        before = platform.store.repositories.entities.get_entity("energy_reserve")["initial_state"]["quantity"]
        rejected = platform.submit_behavior(
            BehaviorIntent(
                "mira",
                "behavior:withdraw_resource",
                {},
                {},
                "Try to withdraw from another location.",
                0.9,
                "withdraw-remote-gate-1",
            )
        )
        assert rejected["status"] == "rejected", rejected
        assert rejected["failed_check"] == "resource_location"
        assert platform.store.repositories.entities.get_entity("energy_reserve")["initial_state"]["quantity"] == before
    finally:
        platform.close()

def test_remote_take_without_travel_is_rejected_at_admission(tmp_path: Path) -> None:
    platform = Platform(create_instance(SOCIAL_WORLD, "take-remote-gate", tmp_path / "runtime"))
    try:
        _move_agent(platform, "tomas", "relay_yard")
        rejected = platform.submit_behavior(
            BehaviorIntent(
                "tomas",
                "behavior:take_resource",
                {},
                {},
                "Try to take from another location.",
                0.9,
                "take-remote-gate-1",
            )
        )
        assert rejected["status"] == "rejected", rejected
        assert rejected["failed_check"] == "resource_location"
        assert platform.store.repositories.entities.get_entity("energy_reserve")["initial_state"]["quantity"] == 10
    finally:
        platform.close()

def test_withdrawal_completion_rechecks_the_stock_location(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "withdraw-completion-gate", tmp_path / "runtime"))
    try:
        _move_agent(platform, "mei_lan", "cistern_house")
        proposal = _withdraw_action("Slow Withdraw Cistern Water", "slow_withdraw_cistern_water")
        proposal["definition"]["duration"] = {"minutes": 3}
        proposal["followup_behavior"]["definition"] = {
            "id": "slow_withdraw_cistern_water",
            "agent_label": "Slow Withdraw Cistern Water",
            "nodes": [
                {"type": "action", "action_id": "travel_to", "parameters": {"location_id": "cistern_house"}},
                {
                    "type": "action",
                    "action_id": "Slow Withdraw Cistern Water",
                    "parameters": {"resource_id": "cistern_water", "scope": "resource_withdrawal"},
                },
            ],
        }
        result = platform.submit_definition_proposal("god", proposal)
        assert result["status"] == "accepted", result

        started = platform.submit_behavior(
            BehaviorIntent(
                "mei_lan",
                "behavior:slow_withdraw_cistern_water",
                {},
                {},
                "Start the withdrawal at the cistern.",
                0.9,
                "withdraw-completion-gate-1",
            )
        )
        assert started["status"] == "accepted", started
        before = platform.store.repositories.entities.get_entity("cistern_water")["initial_state"]["quantity"]
        for _ in range(6):
            platform.step()
            if platform.store.repositories.entities.get_agent("mei_lan")["initial_state"].get("active_action") == "slow_withdraw_cistern_water":
                break
        else:
            raise AssertionError("the withdrawal Action did not start")
        _move_agent(platform, "mei_lan", "lantern_square")

        for _ in range(8):
            platform.step()
        agent = platform.store.repositories.entities.get_agent("mei_lan")
        assert agent["initial_state"].get("status") != "executing"
        assert platform.store.repositories.entities.get_entity("cistern_water")["initial_state"]["quantity"] == before
        assert not [
            item for item in platform.store.repositories.events.timeline_events(after_event_id=0, limit=500, include_ticks=True)
            if item.get("event_type") == "resource.withdrawal.completed"
        ]
    finally:
        platform.close()


def test_remote_withdrawal_behavior_without_travel_is_rejected_at_registration(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "withdraw-registration-gate", tmp_path / "runtime"))
    try:
        proposal = _withdraw_action("Bound Cistern Withdrawal", "bound_cistern_withdrawal")
        proposal["followup_behavior"]["definition"] = {
            "id": "bound_cistern_withdrawal",
            "agent_label": "Bound Cistern Withdrawal",
            "nodes": [{
                "type": "action",
                "action_id": "Bound Cistern Withdrawal",
                "parameters": {"resource_id": "cistern_water", "scope": "resource_withdrawal"},
            }],
        }
        result = platform.submit_definition_proposal("god", proposal)
        assert result["status"] == "rejected", result
        assert any("must include Travel before the stock operation" in str(item) for item in result.get("errors") or []), result
        assert not any(
            item.get("id") == "bound_cistern_withdrawal"
            for item in platform.store.repositories.entities.get_definitions("bt_def")
        )
    finally:
        platform.close()


def test_stock_location_travel_binding_must_be_declared_on_the_behavior(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "withdraw-missing-binding", tmp_path / "runtime"))
    try:
        proposal = _withdraw_action("Unbound Travel Withdrawal", "unbound_travel_withdrawal")
        proposal["followup_behavior"]["definition"] = {
            "id": "unbound_travel_withdrawal",
            "agent_label": "Unbound Travel Withdrawal",
            "agent_parameters": {"resource_id": {"type": "string", "required": True}},
            "nodes": [
                {"type": "action", "action_id": "travel_to", "parameters": {"location_id": "context.resource_location_id"}},
                {"type": "action", "action_id": "Unbound Travel Withdrawal", "parameters": {"resource_id": "context.resource_id", "scope": "resource_withdrawal"}},
            ],
        }
        result = platform.submit_definition_proposal("god", proposal)
        assert result["status"] == "rejected", result
        assert any(
            "parameter_bindings.resource_location_id: resource_location" in str(item)
            for item in result.get("errors") or []
        ), result
        assert not any(
            item.get("id") == "unbound_travel_withdrawal"
            for item in platform.store.repositories.entities.get_definitions("bt_def")
        )
    finally:
        platform.close()
