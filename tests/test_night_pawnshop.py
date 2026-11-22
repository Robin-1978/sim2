from __future__ import annotations

from pathlib import Path

import yaml

from sim2.api.service import ControlService
from sim2.bootstrap.service import create_instance, describe_config, validate_config
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform

WORLD = Path(__file__).parents[1] / "worlds" / "night_pawnshop"


def _properties(platform: Platform) -> dict:
    return dict(platform.store.repositories.metadata.get("world_properties") or {})


def _start(platform: Platform, actor_id: str, behavior_id: str, intent_id: str, parameters: dict | None = None) -> dict:
    return platform.submit_behavior(BehaviorIntent(
        actor_id=actor_id,
        behavior_ref=f"behavior:{behavior_id}",
        target={},
        parameters=parameters or {},
        reason="The living case at the counter matches the current decision.",
        confidence=0.9,
        intent_id=intent_id,
    ))


def _finish(platform: Platform, actor_id: str) -> None:
    agent = platform.store.repositories.entities.get_agent(actor_id)
    minutes = int((agent.get("initial_state") or {}).get("remaining_minutes", 0) or 0)
    for _ in range(max(1, minutes)):
        platform.step()


def _start_and_finish(platform: Platform, actor_id: str, behavior_id: str, intent_id: str, parameters: dict | None = None) -> dict:
    started = _start(platform, actor_id, behavior_id, intent_id, parameters)
    assert started["status"] == "accepted", started
    _finish(platform, actor_id)
    return started


def _move(platform: Platform, actor_id: str, location_id: str) -> None:
    with platform.store.transaction():
        agent = platform.store.repositories.entities.get_agent(actor_id)
        agent["initial_state"]["location_id"] = location_id
        platform.store.repositories.entities.put_agent(actor_id, agent, str(agent.get("name") or actor_id))


def _god_behavior(platform: Platform, behavior_id: str, label: str, action_id: str, parameters: dict, conditions: list[str], *, nodes: list[dict] | None = None, minutes: int = 20) -> dict:
    return platform.submit_definition_proposal("god", {
        "definition_type": "behavior",
        "name": label,
        "assign_to": ["player"],
        "evidence": "The player just resolved a case, so the living counter grows one executable next step.",
        "definition": {
            "id": behavior_id,
            "agent_label": label,
            "summary": label,
            "description": "A case grown from the player's verified decision.",
            "entry_conditions": conditions,
            "estimated_minutes": minutes,
            "cooldown_minutes": 0,
            "nodes": nodes or [{"type": "action", "action_id": action_id, "parameters": parameters}],
            "success_results": ["case_step_completed"],
            "failure_results": ["action_failed", "interrupted"],
            "goal_tags": ["trade"],
            "drive_tags": ["purpose"],
        },
    })


def test_night_pawnshop_is_a_complete_seed_package(tmp_path: Path) -> None:
    report = validate_config(WORLD)
    assert report["valid"], report["errors"]
    description = describe_config(WORLD)
    assert description["world_id"] == "night_pawnshop"
    assert description["counts"]["locations"] == 7
    assert description["counts"]["agents"] == 7
    assert description["counts"]["actions"] == 31
    assert description["counts"]["behavior_trees"] == 29

    rules = yaml.safe_load((WORLD / "rules.yaml").read_text(encoding="utf-8"))
    assert rules["item_labels"]["brass_key"] == "铜钥匙"
    assert "打开旧灯塔海门" in rules["item_uses"]["brass_key"]
    offers = rules["action_catalogs"]["trade_offers"]
    assert len(offers) == 126
    assert any(offer["id"] == "pawn_1" for offer in offers)
    assert any(offer["id"] == "sell_high_21" for offer in offers)

    platform = Platform(create_instance(WORLD, "night-pawnshop-package", tmp_path / "runtime"))
    try:
        assert _properties(platform)["night"] == 1
        assert _properties(platform)["debt_owed"] == 290
        assert all(platform.decision_view(agent["id"])["executable_behaviors"] for agent in platform.store.repositories.entities.list_agents())
    finally:
        platform.close()


def test_seed_case_appraises_pawns_and_sells_without_ending_the_game(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "night-pawnshop-seed", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "old_hand", name="林晚")["status"] == "accepted"
        view = service.player_view("hero")
        assert view["chapter"]["kind"] == "situation"
        assert not view["chapter"].get("ended")
        trade = next(action for action in view["actions"] if action["ref"] == "behavior:trade_pawnshop")
        assert len(trade["parameter_options"]["entry_id"]) == 2

        _move(platform, "hero", "pawnshop_backroom")
        _start_and_finish(platform, "hero", "appraise_item", "seed-appraise", {"entry_id": "appraise_1"})
        properties = _properties(platform)
        assert properties["appraised_1"] == 1
        assert properties["clues"] == 1

        _move(platform, "hero", "pawnshop")
        _start_and_finish(platform, "hero", "trade_pawnshop", "seed-pawn", {"entry_id": "pawn_1"})
        _move(platform, "hero", "grey_chapel")
        _start_and_finish(platform, "hero", "trade_chapel", "seed-sell", {"entry_id": "sell_high_1"})

        agent = platform.store.repositories.entities.get_agent("hero")
        properties = _properties(platform)
        assert agent["inventory"]["silver_thimble"] == 0
        assert agent["inventory"]["coin"] == 75
        assert properties["case_1"] == 3
        assert round(properties["chapel_rep"], 2) == 0.26
        assert properties["night_actions"] == 0
        assert properties["campaign_over"] == 0
    finally:
        service.close()


def test_god_grows_a_case_from_an_item_entity_and_player_behaviors(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "night-pawnshop-living", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "old_hand", name="林晚")["status"] == "accepted"
        item = platform.submit_entity_proposal("god", {
            "entity_type": "item",
            "entity_id": "fog_lantern_shard",
            "name": "雾灯铜片",
            "description": "一片从旧灯塔拆下来的铜片，边缘有修会的灰蜡。",
            "state": {"uses": ["卖给灰袍修会", "交给港务局"]},
        })
        assert item["status"] == "accepted", item
        client = platform.submit_entity_proposal("god", {
            "entity_type": "agent",
            "entity_id": "lantern_keeper",
            "name": "守灯人阿烬",
            "description": "从旧灯塔下来的守灯人，怀里抱着一片铜片。",
            "location_id": "pawnshop",
            "state": {
                "role": "守灯人",
                "controller": "bt",
                "ultimate_goal": "把旧灯塔的最后一片铜片换成能点亮下一夜的钱。",
                "traits": {"沉默": 0.8, "认路": 0.7},
                "beliefs": [{"id": "lantern_keeper_belief", "statement": "灯灭了可以再点，人走了就回不来。", "confidence": 0.8}],
                "needs": {"hydration": 80, "satiety": 80, "belonging": 65, "purpose": 75},
                "capabilities": {"trade": 0.4, "conversation": 0.5, "appraisal": 0.5},
                "initial_bt_ids": ["linger_at_counter"],
                "faction_ids": ["grey_order"],
            },
        })
        assert client["status"] == "accepted", client

        pawn = _god_behavior(
            platform, "dyn_pawn_fog", "收下守灯人阿烬的雾灯铜片", "dynamic_pawn",
            {"item_id": "fog_lantern_shard", "case_slot": "dyn_case_1", "faction": "chapel_rep"},
            ["actor.alive", "location.tag.pawnshop", "world.dyn_case_1 == 0", "world.night_actions >= 1"],
            minutes=30,
        )
        assert pawn["status"] == "accepted", pawn
        returned = _god_behavior(
            platform, "dyn_return_fog", "把雾灯铜片还给守灯人阿烬", "dynamic_return",
            {"case_slot": "dyn_case_1", "faction": "chapel_rep"},
            ["actor.alive", "location.tag.pawnshop", "world.dyn_case_1 == 0", "world.night_actions >= 1"],
        )
        assert returned["status"] == "accepted", returned

        view = service.player_view("hero")
        assert view["item_labels"]["fog_lantern_shard"] == "雾灯铜片"
        assert view["item_uses"]["fog_lantern_shard"] == ["卖给灰袍修会", "交给港务局"]
        labels = {action["ref"]: action["name"] for action in view["actions"]}
        assert labels["behavior:dyn_pawn_fog"] == "收下守灯人阿烬的雾灯铜片"

        _start_and_finish(platform, "hero", "dyn_pawn_fog", "living-pawn")
        agent = platform.store.repositories.entities.get_agent("hero")
        properties = _properties(platform)
        assert agent["inventory"]["fog_lantern_shard"] == 1
        assert properties["dyn_case_1"] == 1
        assert any(
            item.get("role") == "god" and "god_after_dynamic_pawn" in str(item.get("task_type") or "")
            for item in platform.ready_evolution_tasks()
        )
        event = platform.store.connection.execute(
            "SELECT data_json FROM event_log WHERE event_type='case.pawned_dynamic' ORDER BY event_id DESC LIMIT 1"
        ).fetchone()
        assert '"item":"fog_lantern_shard"' in event["data_json"]
        assert '"slot":"dyn_case_1"' in event["data_json"]
        assert '"faction":"chapel_rep"' in event["data_json"]

        sell = _god_behavior(
            platform, "dyn_sell_fog", "把雾灯铜片卖给灰袍修会", "dynamic_sell",
            {"item_id": "fog_lantern_shard", "case_slot": "dyn_case_1", "price": "36", "faction": "chapel_rep"},
            ["actor.alive", "world.dyn_case_1 == 1", "actor.inventory.fog_lantern_shard >= 1", "world.night_actions >= 1"],
            nodes=[
                {"type": "action", "action_id": "travel_to", "parameters": {"location_id": "grey_chapel"}},
                {"type": "action", "action_id": "dynamic_sell", "parameters": {"item_id": "fog_lantern_shard", "case_slot": "dyn_case_1", "price": "36", "faction": "chapel_rep"}},
            ],
            minutes=15,
        )
        assert sell["status"] == "accepted", sell
        _start_and_finish(platform, "hero", "dyn_sell_fog", "living-sell")

        agent = platform.store.repositories.entities.get_agent("hero")
        properties = _properties(platform)
        assert agent["inventory"]["coin"] == 71
        assert agent["inventory"]["fog_lantern_shard"] == 0
        assert properties["dyn_case_1"] == 3
        assert round(properties["chapel_rep"], 2) == 0.25
    finally:
        service.close()


def test_next_season_keeps_the_world_and_replaces_pressure(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "night-pawnshop-season", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "watchmaker", name="林晚")["status"] == "accepted"
        with platform.store.transaction():
            agent = platform.store.repositories.entities.get_agent("hero")
            agent["inventory"]["brass_key"] = 1
            platform.store.repositories.entities.put_agent("hero", agent, "林晚")
            properties = _properties(platform)
            properties.update({
                "season": 1,
                "night": 21,
                "campaign_over": 1,
                "debt_stage": 3,
                "debt_owed": 0,
                "ledger_level": 1,
                "dyn_case_3": 4,
                "clues": 8,
            })
            platform.store.repositories.metadata.set("world_properties", properties)

        started = _start(platform, "hero", "begin_next_season", "next-season")
        assert started["status"] == "accepted", started
        _finish(platform, "hero")
        properties = _properties(platform)
        agent = platform.store.repositories.entities.get_agent("hero")
        assert properties["season"] == 2
        assert properties["night"] == 1
        assert properties["campaign_over"] == 0
        assert properties["debt_stage"] == 0
        assert properties["debt_owed"] == 320
        assert properties["ledger_level"] == 1
        assert properties["dyn_case_3"] == 4
        assert properties["clues"] == 8
        assert agent["inventory"]["brass_key"] == 1
    finally:
        service.close()


def test_director_and_god_drive_living_world_growth() -> None:
    evolution = yaml.safe_load((WORLD / "evolution.yaml").read_text(encoding="utf-8"))
    assert evolution["director"]["policy"] == "llm"
    assert "social" in evolution["director"]["targets"]
    contract = evolution["contracts"]["god_structural"]
    assert contract["max_proposals"] >= 7
    assert "dyn_case_" in contract["result"]
    wakes = {item["id"]: item for item in evolution["wakes"]}
    assert "god_after_dynamic_pawn" in wakes
    assert "case.pawned_dynamic" in wakes["god_after_dynamic_pawn"]["on"]
    assert "god_on_stagnation" in wakes

    rules = yaml.safe_load((WORLD / "rules.yaml").read_text(encoding="utf-8"))
    labels = rules["world_property_labels"]
    assert labels["season"]["label"] == "第几季"
    assert all(f"dyn_case_{slot}" in rules["world_property_labels"] for slot in range(1, 6))
