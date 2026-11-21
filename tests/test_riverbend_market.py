from __future__ import annotations

from pathlib import Path

import yaml

from sim2.api.service import ControlService
from sim2.bootstrap.service import create_instance, describe_config, validate_config
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform

WORLD = Path(__file__).parents[1] / "worlds" / "riverbend_market"


def _start(platform: Platform, actor_id: str, behavior_id: str, intent_id: str, parameters: dict | None = None) -> dict:
    return platform.submit_behavior(BehaviorIntent(
        actor_id=actor_id,
        behavior_ref=f"behavior:{behavior_id}",
        target={},
        parameters=parameters or {},
        reason="The configured shop behavior matches the current economic goal.",
        confidence=0.9,
        intent_id=intent_id,
    ))


def _run_active(platform: Platform, actor_id: str) -> int:
    agent = platform.store.repositories.entities.get_agent(actor_id)
    minutes = int((agent.get("initial_state") or {}).get("remaining_minutes", 0) or 0)
    for _ in range(minutes):
        platform.step()
    return minutes


def _start_and_finish(platform: Platform, actor_id: str, behavior_id: str, intent_id: str, parameters: dict | None = None) -> dict:
    started = _start(platform, actor_id, behavior_id, intent_id, parameters)
    assert started["status"] == "accepted", started
    _run_active(platform, actor_id)
    return started


def _set_world_properties(platform: Platform, **values) -> None:
    with platform.store.transaction():
        properties = dict(platform.store.repositories.metadata.get("world_properties") or {})
        properties.update(values)
        platform.store.repositories.metadata.set("world_properties", properties)


def _set_resource_quantity(platform: Platform, resource_id: str, quantity: float) -> None:
    with platform.store.transaction():
        entity = platform.store.repositories.entities.get_entity(resource_id)
        state = dict(entity.get("initial_state") or {})
        state["quantity"] = quantity
        entity["initial_state"] = state
        platform.store.repositories.entities.put_entity(resource_id, entity["entity_type"], entity["name"], {key: value for key, value in entity.items() if key not in {"id", "entity_type", "name"}})
        properties = dict(platform.store.repositories.metadata.get("world_properties") or {})
        properties[resource_id] = quantity
        platform.store.repositories.metadata.set("world_properties", properties)


def _properties(platform: Platform) -> dict:
    return dict(platform.store.repositories.metadata.get("world_properties") or {})


def test_riverbend_market_is_a_complete_five_shop_package(tmp_path: Path) -> None:
    report = validate_config(WORLD)
    assert report["valid"], report["errors"]
    description = describe_config(WORLD)
    assert description["world_id"] == "riverbend_market"
    assert description["counts"]["locations"] == 10
    assert description["counts"]["agents"] == 8
    assert description["counts"]["actions"] == 46
    assert description["counts"]["behavior_trees"] == 53
    assert description["counts"]["recipes"] == 8
    assert description["counts"]["resources"] == 11

    rules = yaml.safe_load((WORLD / "rules.yaml").read_text(encoding="utf-8"))
    assert rules["item_labels"]["coin"] == "金币"
    assert rules["world_property_labels"]["days_to_fair"]["unit"] == "amount"
    assert rules["world_property_labels"]["days_to_fair"]["alert"] is True

    platform = Platform(create_instance(WORLD, "riverbend-package", tmp_path / "runtime"))
    try:
        assert _properties(platform)["days_to_fair"] == 7
        assert _properties(platform)["market_till"] == 36
        assert all(platform.decision_view(agent["id"])["executable_behaviors"] for agent in platform.store.repositories.entities.list_agents())
    finally:
        platform.close()


def test_gather_sell_tool_and_craft_keep_the_coin_ledger(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "riverbend-ledger", tmp_path / "runtime"))
    try:
        assert platform.enter_player("hero", "miner", name="阿岚")["status"] == "accepted"
        _start_and_finish(platform, "hero", "gather_iron_ore", "ledger-gather-1")
        _start_and_finish(platform, "hero", "gather_iron_ore", "ledger-gather-2")
        agent = platform.store.repositories.entities.get_agent("hero")
        assert agent["inventory"]["iron_ore"] == 6
        assert platform.store.repositories.entities.get_entity("iron_vein")["initial_state"]["quantity"] == 36

        for index in range(5):
            _start_and_finish(platform, "hero", "sell_ore_to_smith", f"ledger-sell-{index}")

        agent = platform.store.repositories.entities.get_agent("hero")
        properties = _properties(platform)
        assert agent["inventory"]["coin"] == 21
        assert agent["inventory"]["iron_ore"] == 1
        assert properties["smithy_till"] == 15
        assert properties["smithy_stock"] == 11
        assert round(properties["smithy_rep"], 2) == 0.40
        assert round(properties["town_prosperity"], 2) == 0.35

        platform.step()
        platform.step()
        properties = _properties(platform)
        assert properties["smithy_ready"] == 1
        assert properties["fair_ready"] == 1
        assert any(
            event["event_type"] == "shop.ready"
            for event in platform.store.repositories.events.recent_events(limit=30)
        )

        _start_and_finish(platform, "hero", "buy_pickaxe", "ledger-tool")
        agent = platform.store.repositories.entities.get_agent("hero")
        properties = _properties(platform)
        assert agent["inventory"]["pickaxe"] == 1
        assert agent["inventory"]["coin"] == 11
        assert properties["smithy_till"] == 25
        assert properties["smithy_stock"] == 9

        with platform.store.transaction():
            agent = platform.store.repositories.entities.get_agent("hero")
            agent["inventory"].update({"iron_ore": 2, "coal": 1})
            platform.store.repositories.entities.put_agent("hero", agent, "阿岚")
        _start_and_finish(platform, "hero", "craft_iron_ingot", "ledger-craft")
        agent = platform.store.repositories.entities.get_agent("hero")
        assert agent["inventory"].get("iron_ore", 0) == 0
        assert agent["inventory"].get("coal", 0) == 0
        assert agent["inventory"]["iron_ingot"] == 1
    finally:
        platform.close()


def test_market_ale_trade_does_not_mint_coin(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "riverbend-no-mint", tmp_path / "runtime"))
    try:
        assert platform.enter_player("hero", "peddler", name="阿岚")["status"] == "accepted"
        with platform.store.transaction():
            agent = platform.store.repositories.entities.get_agent("hero")
            agent["inventory"]["ale"] = 1
            agent["inventory"]["coin"] = 0
            agent["initial_state"]["location_id"] = "market_square"
            platform.store.repositories.entities.put_agent("hero", agent, "阿岚")
        before_till = _properties(platform)["market_till"]
        _start_and_finish(platform, "hero", "sell_ale_to_market", "no-mint-ale")
        agent = platform.store.repositories.entities.get_agent("hero")
        assert agent["inventory"]["coin"] == 2
        assert _properties(platform)["market_till"] == before_till - 2
    finally:
        platform.close()


def test_fair_crate_and_fair_success_need_all_five_tracks(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "riverbend-fair-success", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "peddler", name="阿岚")["status"] == "accepted"
        _set_world_properties(
            platform,
            days_to_fair=1,
            fair_act=4,
            fair_stage=0,
            fair_ready=4,
            fair_crate=0,
            inn_ready=1,
            market_rep=0.5,
            town_prosperity=0.65,
        )
        with platform.store.transaction():
            agent = platform.store.repositories.entities.get_agent("hero")
            agent["inventory"].update({"iron_ingot": 1, "tonic": 1, "stew": 1, "ale": 1, "spice": 1, "coin": 20})
            agent["initial_state"]["location_id"] = "market_square"
            platform.store.repositories.entities.put_agent("hero", agent, "阿岚")

        _start_and_finish(platform, "hero", "assemble_fair_crate", "fair-crate")
        agent = platform.store.repositories.entities.get_agent("hero")
        properties = _properties(platform)
        assert properties["fair_crate"] == 1
        assert properties["market_till"] == 46
        assert agent["inventory"].get("spice", 0) == 0
        assert agent["inventory"]["coin"] == 10

        _set_world_properties(platform, days_to_fair=0)
        platform.step()
        properties = _properties(platform)
        assert properties["fair_stage"] == 2
        assert properties["town_prosperity"] >= 0.75
        chapter = service.player_view("hero")["chapter"]
        assert chapter["id"] == "bridge_fair_reopened"
        assert chapter["kind"] == "ending"
    finally:
        service.close()


def test_fair_failure_is_soft_and_the_player_can_still_work(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "riverbend-fair-failure", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "peddler", name="阿岚")["status"] == "accepted"
        _set_world_properties(
            platform,
            days_to_fair=0,
            fair_stage=0,
            fair_ready=0,
            fair_crate=0,
            town_prosperity=0.3,
        )
        platform.step()
        properties = _properties(platform)
        assert properties["fair_stage"] == 4
        assert properties["town_prosperity"] == 0.15
        assert service.player_view("hero")["chapter"]["id"] == "bridge_fair_failed"

        _set_world_properties(platform, relief_fund=4, market_till=4)
        with platform.store.transaction():
            agent = platform.store.repositories.entities.get_agent("hero")
            agent["inventory"]["coin"] = 0
            agent["initial_state"]["location_id"] = "market_square"
            platform.store.repositories.entities.put_agent("hero", agent, "阿岚")
        started = _start(platform, "hero", "work_market", "failure-work")
        assert started["status"] == "accepted", started
        _run_active(platform, "hero")
        assert platform.store.repositories.entities.get_agent("hero")["inventory"]["coin"] == 2
    finally:
        service.close()


def test_story_beats_are_evidence_scoped_and_assign_executable_work(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "riverbend-story", tmp_path / "runtime"))
    try:
        assert platform.enter_player("hero", "peddler", name="阿岚")["status"] == "accepted"
        _set_world_properties(platform, days_to_fair=3, fair_act=3, fair_stage=0, clinic_stock=1)
        _set_resource_quantity(platform, "clinic_stock", 1)
        platform.step()
        clinic_low = next(
            event for event in platform.store.repositories.events.recent_events(limit=40)
            if event["event_type"] == "clinic.stock.low"
        )
        accepted = platform.submit_story_beat(
            "story", {"beat": "医馆的药材不够了"}, source_event_ids=[clinic_low["event_id"]],
        )
        assert accepted["status"] == "accepted", accepted
        state = platform.store.repositories.metadata.get("story_state") or {}
        assert state["clinic_shortage"]["status"] == "active"
        assert state["clinic_shortage"]["audience"] == ["baizhi"]
        assert platform.store.repositories.entities.get_agent("baizhi")["story_goals"]

        unrelated = platform.submit_story_beat(
            "story", {"beat": "第一枚金币"}, source_event_ids=[clinic_low["event_id"]],
        )
        assert unrelated["status"] == "rejected"
        assert unrelated["failed_check"] == "story_beat_choice"
    finally:
        platform.close()


def test_director_plan_and_god_task_use_the_shared_growth_ledger(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "riverbend-director", tmp_path / "runtime"))
    try:
        assert platform.enter_player("hero", "peddler", name="阿岚")["status"] == "accepted"
        with platform.store.transaction():
            platform.store.repositories.metadata.set("world_minute", 2879)
        platform.step()
        plan = platform.store.repositories.metadata.get("season_plan") or {}
        assert plan.get("status") == "active"
        assert plan.get("target_cell") in {"verb", "social", "rule", "place"}
        tasks = platform.store.repositories.evolution.ready_evolution_tasks()
        assert any(task.get("task_type") == "god_structural" for task in tasks)
    finally:
        platform.close()


def test_player_view_exposes_fair_metrics_shops_and_goals(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "riverbend-view", tmp_path / "runtime"))
    try:
        entry = service.player_entry()
        assert [origin["id"] for origin in entry["origins"]] == ["peddler", "miner", "herbalist", "cook"]
        assert len({origin["first_task"] for origin in entry["origins"]}) == 4
        assert service.enter_player("hero", "peddler", name="阿岚")["status"] == "accepted"
        view = service.player_view("hero")
        assert [metric["property"] for metric in view["ui"]["crisis_metrics"]] == [
            "days_to_fair",
            "town_prosperity",
            "fair_ready",
            "market_till",
        ]
        assert {facility["name"] for facility in view["facilities"]} >= {"铁匠铺", "食铺", "医馆", "旅店", "桥市告示板"}
        goals = {goal["id"]: goal for goal in view["goals"]}
        assert {"fair_preparation", "smithy_order", "tavern_order", "clinic_order", "inn_order", "market_order"}.issubset(goals)
        assert view["onboarding"]["id"] == "first_coin"
        assert view["world_properties"]["days_to_fair"] == 7
    finally:
        service.close()


def test_fair_quiet_is_a_partial_result(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "riverbend-fair-quiet", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "peddler", name="阿岚")["status"] == "accepted"
        _set_world_properties(
            platform,
            days_to_fair=0,
            fair_stage=0,
            fair_ready=2,
            fair_crate=1,
            town_prosperity=0.45,
        )
        platform.step()
        properties = _properties(platform)
        assert properties["fair_stage"] == 3
        assert properties["town_prosperity"] == 0.48
        assert service.player_view("hero")["chapter"]["id"] == "bridge_fair_quiet"
    finally:
        service.close()
