from __future__ import annotations

from pathlib import Path

from sim2.api.service import ControlService
from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent

WORLD = Path(__file__).parents[1] / "worlds" / "night_pawnshop"


def _start(platform, actor: str, behavior: str, intent: str) -> dict:
    return platform.submit_behavior(BehaviorIntent(
        actor_id=actor,
        behavior_ref=f"behavior:{behavior}",
        target={},
        parameters={},
        reason="Exercise a parameterized living-world definition.",
        confidence=0.9,
        intent_id=intent,
    ))


def _finish(platform, actor: str) -> None:
    agent = platform.store.repositories.entities.get_agent(actor)
    for _ in range(max(1, int((agent.get("initial_state") or {}).get("remaining_minutes", 0) or 0))):
        platform.step()


def _behavior(platform, behavior_id: str, label: str, action_id: str, parameters: dict, conditions: list[str], *, nodes: list[dict] | None = None, deferred: bool = False) -> dict:
    return platform.submit_definition_proposal("god", {
        "definition_type": "behavior",
        "name": label,
        "assign_to": ["player"],
        "evidence": "A parameterized case slot is the current decision.",
        "definition": {
            "id": behavior_id,
            "agent_label": label,
            "summary": label,
            "description": "A generic living-world behavior.",
            "entry_conditions": conditions,
            "estimated_minutes": 15,
            "cooldown_minutes": 0,
            "nodes": nodes or [{"type": "action", "action_id": action_id, "parameters": parameters}],
            **({"deferred": True} if deferred else {}),
            "success_results": ["completed"],
            "failure_results": ["action_failed", "interrupted"],
            "goal_tags": ["trade"],
            "drive_tags": ["purpose"],
        },
    })


def test_dynamic_bracket_preconditions_gate_a_generic_case(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "dsl-dynamic-case", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "old_hand", name="林晚")["status"] == "accepted"
        item = platform.submit_entity_proposal("god", {
            "entity_type": "item",
            "entity_id": "test_case_item",
            "name": "测试案物",
            "description": "A generated item used to exercise a parameterized case.",
        })
        assert item["status"] == "accepted", item

        pawn = _behavior(
            platform, "dyn_pawn_dsl", "收下测试案物", "dynamic_pawn",
            {"item_id": "test_case_item", "case_slot": "dyn_case_1", "faction": "chapel_rep"},
            ["actor.alive", "location.tag.pawnshop", "world.night_actions >= 1"],
        )
        assert pawn["status"] == "accepted", pawn
        sell = _behavior(
            platform, "dyn_sell_dsl", "卖出测试案物", "dynamic_sell",
            {"item_id": "test_case_item", "case_slot": "dyn_case_1", "price": "33", "faction": "chapel_rep"},
            ["actor.alive", "world.dyn_case_1 == 1", "actor.inventory.test_case_item >= 1", "world.night_actions >= 1"],
            nodes=[
                {"type": "action", "action_id": "travel_to", "parameters": {"location_id": "grey_chapel"}},
                {"type": "action", "action_id": "dynamic_sell", "parameters": {"item_id": "test_case_item", "case_slot": "dyn_case_1", "price": "33", "faction": "chapel_rep"}},
            ],
            deferred=True,
        )
        assert sell["status"] == "accepted", sell

        rejected = _start(platform, "hero", "dyn_sell_dsl", "early-sell")
        assert rejected["status"] == "rejected", rejected
        assert "world.dyn_case_1 == 1" in str(rejected.get("condition") or rejected.get("message"))

        accepted = _start(platform, "hero", "dyn_pawn_dsl", "pawn-first")
        assert accepted["status"] == "accepted", accepted
        _finish(platform, "hero")
        sold = _start(platform, "hero", "dyn_sell_dsl", "sell-after-pawn")
        assert sold["status"] == "accepted", sold
        _finish(platform, "hero")
        agent = platform.store.repositories.entities.get_agent("hero")
        properties = dict(platform.store.repositories.metadata.get("world_properties") or {})
        assert agent["inventory"]["test_case_item"] == 0
        assert properties["dyn_case_1"] == 3
    finally:
        service.close()


def test_world_property_set_accepts_a_parameter_value(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "dsl-property-set", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "old_hand", name="林晚")["status"] == "accepted"
        action = platform.submit_definition_proposal("god", {
            "definition_type": "action",
            "name": "Set Dynamic Slot",
            "definition": {
                "id": "set_dynamic_slot",
                "verb": "trade",
                "description": "Set one generated case slot to a supplied value.",
                "parameters": {"slot": "string", "value": "integer"},
                "preconditions": ["actor.alive"],
                "duration": {"minutes": 1},
                "costs": {"reserve": {}, "per_minute": {}, "on_success": {}, "on_failure": {}, "refund": {}},
                "effects": [{"type": "world_property_set", "property": "context.slot", "value": "context.value"}],
                "success": {"condition": "actor.alive", "result": "slot_set"},
                "failure": {"condition": "actor.incapacitated", "result": "failed"},
            },
            "followup_behavior": {
                "name": "设置案子槽位",
                "assign_to": ["player"],
                "definition": {
                    "id": "set_dynamic_slot_behavior",
                    "agent_label": "设置案子槽位",
                    "summary": "设置案子槽位",
                    "description": "Set a generated case slot.",
                    "entry_conditions": ["actor.alive"],
                    "estimated_minutes": 1,
                    "cooldown_minutes": 0,
                    "nodes": [{"type": "action", "action_id": "set_dynamic_slot", "parameters": {"slot": "dyn_case_1", "value": 5}}],
                    "success_results": ["slot_set"],
                    "failure_results": ["failed"],
                    "goal_tags": ["trade"],
                    "drive_tags": ["purpose"],
                },
            },
        })
        assert action["status"] == "accepted", action
        started = _start(platform, "hero", "set_dynamic_slot_behavior", "set-slot")
        assert started["status"] == "accepted", started
        _finish(platform, "hero")
        properties = dict(platform.store.repositories.metadata.get("world_properties") or {})
        assert properties["dyn_case_1"] == 5
    finally:
        service.close()


def test_visible_item_name_is_canonicalized_in_a_deferred_behavior(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "dsl-item-alias", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "old_hand", name="林晚")["status"] == "accepted"
        item = platform.submit_entity_proposal("god", {
            "entity_type": "item",
            "entity_id": "sealed_copper_tag",
            "name": "港务局封蜡铜牌",
            "description": "A visible item name that a model may shorten when writing a condition.",
        })
        assert item["status"] == "accepted", item
        sell = _behavior(
            platform, "dyn_sell_alias", "把封蜡铜牌卖给港务局", "dynamic_sell",
            {"item_id": "封蜡铜牌", "case_slot": "dyn_case_1", "price": "30", "faction": "watch_rep"},
            ["actor.inventory.封蜡铜牌 >= 1"],
            deferred=True,
        )
        assert sell["status"] == "accepted", sell
        stored = next(
            item for item in platform.store.repositories.entities.get_definitions("bt_def")
            if item.get("id") == "dyn_sell_alias"
        )
        assert stored["entry_conditions"] == ["actor.inventory.sealed_copper_tag >= 1"]
        assert stored["nodes"][0]["parameters"]["item_id"] == "sealed_copper_tag"

        with platform.store.transaction():
            agent = platform.store.repositories.entities.get_agent("hero")
            agent["inventory"]["sealed_copper_tag"] = 1
            platform.store.repositories.entities.put_agent("hero", agent, "林晚")
            properties = dict(platform.store.repositories.metadata.get("world_properties") or {})
            properties["dyn_case_1"] = 1
            platform.store.repositories.metadata.set("world_properties", properties)
        view = service.player_view("hero")
        assert "behavior:dyn_sell_alias" in {action["ref"] for action in view["actions"]}
    finally:
        service.close()
