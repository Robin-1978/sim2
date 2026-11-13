from __future__ import annotations

from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform


WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"


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
        assert not any(item.get("name") == "Unbound Action" for item in platform.store.get_definitions("action_def"))
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
        assert any(item.get("name") == "Craft Test Token" for item in platform.store.get_definitions("action_def"))
        assert any(item.get("name") == "Test Token Recipe" for item in platform.store.get_definitions("recipe_def"))
        assert any(item.get("agent_label") == "Craft Test Token Behavior" for item in platform.store.get_definitions("bt_def"))
        assert any(item.get("choice") == "Craft Test Token Behavior" for item in platform.view.build_decision_view("mei_lan", role="agent_decision")["executable_behaviors"])
    finally:
        platform.close()


def test_recipe_action_normalizes_model_shorthand_and_visible_resource(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "action-recipe-model-shorthand", tmp_path / "runtime"))
    try:
        luo = platform.store.get_agent("luo_shan")
        luo["inventory"] = {**(luo.get("inventory") or {}), "reed_bundle": 2}
        platform.store.update_agent("luo_shan", luo)
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
        action = next(item for item in platform.store.get_definitions("action_def") if item.get("name") == "编织测试芦苇席")
        assert action["parameters"]["recipe_id"] == "string"
        assert action["costs"]["reserve"]["recipe_inputs"] == 1
        recipe = next(item for item in platform.store.get_definitions("recipe_def") if item.get("name") == "测试芦苇席配方")
        assert recipe["inputs"][0]["item"] == "reed_bundle"
        assert any(item.get("choice") == "编织测试芦苇席行为" for item in platform.view.build_decision_view("luo_shan", role="agent_decision")["executable_behaviors"])
        behavior_id = next(item["id"] for item in platform.store.get_definitions("bt_def") if item.get("agent_label") == "编织测试芦苇席行为")
        started = platform.submit_behavior(BehaviorIntent(
            "luo_shan", f"behavior:{behavior_id}", {}, {},
            "Use the collected reeds to craft the configured mat.", 0.9, "run-bound-recipe-action",
        ))
        assert started["status"] == "accepted", started
        for _ in range(int(started.get("remaining_minutes", 1)) + 4):
            platform.step()
        state = platform.store.get_agent("luo_shan")["initial_state"]
        assert state["status"] == "idle", state
        assert platform.store.get_agent("luo_shan")["inventory"].get("测试芦苇席", 0) == 1
        assert platform.store.get_agent("luo_shan")["inventory"].get("reed_bundle", 0) == 0
    finally:
        platform.close()

