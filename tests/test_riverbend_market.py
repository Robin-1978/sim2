from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from sim2.api.service import ControlService
from sim2.bootstrap.service import create_instance, describe_config, validate_config
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform
from sim2.simulation.runner import SimulationReport, SimulationRunner

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
    assert description["counts"]["actions"] == 19
    assert description["counts"]["behavior_trees"] == 23
    assert description["counts"]["recipes"] == 9
    assert description["counts"]["resources"] == 11

    rules = yaml.safe_load((WORLD / "rules.yaml").read_text(encoding="utf-8"))
    assert rules["item_labels"]["coin"] == "金币"
    assert rules["world_property_labels"]["days_to_fair"]["unit"] == "amount"
    assert rules["world_property_labels"]["days_to_fair"]["alert"] is True
    orders = {goal["id"]: goal for goal in rules["progression"]}
    for order_id in ("smithy_order", "tavern_order", "clinic_order", "inn_order", "market_order"):
        assert orders[order_id]["complete_when"]["count"] == 5
    # Five sales at +0.08 reputation reach the 0.4 stocking line exactly.
    assert round(5 * 0.08, 2) == 0.4

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
        with platform.store.transaction():
            agent = platform.store.repositories.entities.get_agent("hero")
            agent["initial_state"]["location_id"] = "north_mine"
            platform.store.repositories.entities.put_agent("hero", agent, "阿岚")
        _start_and_finish(platform, "hero", "gather", "ledger-gather-1", {"entry_id": "gather_iron_ore"})
        _start_and_finish(platform, "hero", "gather", "ledger-gather-2", {"entry_id": "gather_iron_ore"})
        agent = platform.store.repositories.entities.get_agent("hero")
        assert agent["inventory"]["iron_ore"] == 6
        assert platform.store.repositories.entities.get_entity("iron_vein")["initial_state"]["quantity"] == 36

        with platform.store.transaction():
            agent["initial_state"]["location_id"] = "smithy"
            platform.store.repositories.entities.put_agent("hero", agent, "阿岚")
        for index in range(5):
            _start_and_finish(platform, "hero", "trade_smithy", f"ledger-sell-{index}", {"entry_id": "sell_ore_to_smith"})

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

        _start_and_finish(platform, "hero", "trade_smithy", "ledger-tool", {"entry_id": "buy_pickaxe"})
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
        _start_and_finish(platform, "hero", "craft", "ledger-craft", {"recipe_id": "iron_ingot"})
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
        _start_and_finish(platform, "hero", "trade_market", "no-mint-ale", {"entry_id": "sell_ale_to_market"})
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
        assert any(item["item"] == "coin" for item in entry["origins"][0]["starting_items"])
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


def test_active_task_reports_persisted_progress_for_long_actions(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "riverbend-task-progress", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "miner", name="阿岚")["status"] == "accepted"
        started = _start(platform, "hero", "consume", "task-progress", {"entry_id": "eat_meal"})
        assert started["status"] == "accepted"
        for _ in range(2):
            platform.step()
        task = service.player_view("hero")["active_task"]
        assert task["bt"] == "使用随身物品"
        assert task["total_minutes"] == 10
        assert 0 < task["progress"] < 1
        assert task["remaining_minutes"] < 10
    finally:
        service.close()


def test_low_hydration_keeps_drink_water_in_the_visible_actions(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "riverbend-water-visible", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "peddler", name="阿岚")["status"] == "accepted"
        with platform.store.transaction():
            agent = platform.store.repositories.entities.get_agent("hero")
            agent["initial_state"]["location_id"] = "inn"
            agent["needs"]["hydration"] = 33
            platform.store.repositories.entities.put_agent("hero", agent, "阿岚")
        view = service.player_view("hero")
        refs = [action["ref"] for action in view["actions"]]
        assert "behavior:drink_water" in refs
        assert refs.index("behavior:drink_water") < 6
    finally:
        service.close()


def test_craft_actions_are_visible_when_inputs_are_available(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "riverbend-craft-visible", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "miner", name="阿岚")["status"] == "accepted"
        with platform.store.transaction():
            agent = platform.store.repositories.entities.get_agent("hero")
            agent["inventory"].update({"iron_ore": 2, "coal": 1, "herb": 2})
            agent["initial_state"]["location_id"] = "smithy"
            platform.store.repositories.entities.put_agent("hero", agent, "阿岚")
        view = service.player_view("hero")
        craft = next(action for action in view["actions"] if action["ref"] == "behavior:craft")
        assert "iron_ingot" in {option["value"] for option in craft["parameter_options"]["recipe_id"]}

        with platform.store.transaction():
            agent = platform.store.repositories.entities.get_agent("hero")
            agent["initial_state"]["location_id"] = "clinic"
            platform.store.repositories.entities.put_agent("hero", agent, "阿岚")
        view = service.player_view("hero")
        craft = next(action for action in view["actions"] if action["ref"] == "behavior:craft")
        assert "tonic" in {option["value"] for option in craft["parameter_options"]["recipe_id"]}
    finally:
        service.close()


def test_director_kickoff_creates_the_first_plan_immediately(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "riverbend-director-kickoff", tmp_path / "runtime"))
    try:
        assert platform.store.repositories.metadata.get("season_plan") is None
        platform.step()
        plan = platform.store.repositories.metadata.get("season_plan") or {}
        assert plan.get("status") == "active"
        assert plan.get("target_cell") in {"verb", "social", "rule", "place"}
        assert "fair_act" in str(plan.get("reason") or "")
        assert any(task.get("task_type") == "god_structural" for task in platform.store.repositories.evolution.ready_evolution_tasks())
    finally:
        platform.close()


def test_global_story_beat_expires_so_the_same_beat_can_reappear(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "riverbend-story-ttl", tmp_path / "runtime"))
    try:
        assert platform.enter_player("hero", "peddler", name="阿岚")["status"] == "accepted"
        _set_world_properties(platform, smithy_rep=0.4)
        platform.step()
        platform.step()
        ready = next(
            event for event in platform.store.repositories.events.recent_events(limit=40)
            if event["event_type"] == "shop.ready"
        )
        accepted = platform.submit_story_beat(
            "story", {"beat": "一家店备好了"}, source_event_ids=[ready["event_id"]],
        )
        assert accepted["status"] == "accepted", accepted
        state = platform.store.repositories.metadata.get("story_state") or {}
        started = int(state["shop_ready"]["started_minute"])
        assert state["shop_ready"]["status"] == "active"

        with platform.store.transaction():
            platform.store.repositories.metadata.set("world_minute", started + 721)
        replay = platform.submit_story_beat(
            "story", {"beat": "一家店备好了"}, source_event_ids=[ready["event_id"]],
        )
        assert replay["status"] == "accepted", replay
        state = platform.store.repositories.metadata.get("story_state") or {}
        assert state["shop_ready"]["status"] == "active"
        assert int(state["shop_ready"]["started_minute"]) > started
    finally:
        platform.close()


def test_validate_config_rejects_a_gather_entry_bound_to_another_location(tmp_path: Path) -> None:
    package = tmp_path / "bad-gather-location"
    shutil.copytree(WORLD, package)
    rules_path = package / "rules.yaml"
    rules = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    entry = next(item for item in rules["action_catalogs"]["gather"] if item["id"] == "gather_iron_ore")
    entry["location"] = "riverbank"
    rules_path.write_text(yaml.safe_dump(rules, allow_unicode=True, sort_keys=False), encoding="utf-8")

    report = validate_config(package)

    assert report["valid"] is False
    assert any(
        "gather_iron_ore" in error
        and "riverbank" in error
        and "north_mine" in error
        for error in report["errors"]
    )


def test_validate_config_rejects_a_gather_entry_without_a_bound_resource_or_known_tool(tmp_path: Path) -> None:
    package = tmp_path / "bad-gather-binding"
    shutil.copytree(WORLD, package)
    rules_path = package / "rules.yaml"
    rules = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    entry = next(item for item in rules["action_catalogs"]["gather"] if item["id"] == "gather_iron_ore")
    entry.pop("resource_id")
    entry["requires"].append("actor.inventory.pickaxe_typo >= 1")
    entry["requires"].append("actor.permissions.mine_access == true")
    rules_path.write_text(yaml.safe_dump(rules, allow_unicode=True, sort_keys=False), encoding="utf-8")

    report = validate_config(package)

    assert report["valid"] is False
    assert any("gather_iron_ore requires resource_id" in error for error in report["errors"])
    assert any("pickaxe_typo" in error and "undeclared inventory item" in error for error in report["errors"])
    assert any("actor.permissions.mine_access" in error and "unsupported condition path" in error for error in report["errors"])


def test_depleted_resource_is_not_offered_as_a_gather_choice(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "riverbend-depleted-node", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "miner", name="阿岚")["status"] == "accepted"
        with platform.store.transaction():
            agent = platform.store.repositories.entities.get_agent("hero")
            agent["initial_state"]["location_id"] = "north_mine"
            platform.store.repositories.entities.put_agent("hero", agent, "阿岚")
        _set_resource_quantity(platform, "iron_vein", 0)

        view = service.player_view("hero")
        gather = next(action for action in view["actions"] if action["ref"] == "behavior:gather")
        choices = {option["value"] for option in gather["parameter_options"]["entry_id"]}

        assert "gather_iron_ore" not in choices
        assert "gather_coal" in choices
    finally:
        service.close()


def test_onboarding_checklist_tracks_done_and_current_steps(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "riverbend-checklist", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "miner", name="阿岚")["status"] == "accepted"
        view = service.player_view("hero")
        checklist = view["onboarding_checklist"]
        assert len(checklist) == 3
        assert checklist[0]["status"] == "current"
        assert [item["status"] for item in checklist].count("current") == 1
        assert view["onboarding_progress"]["total"] == 5

        with platform.store.transaction():
            platform.store.repositories.events.append_event("trade.sold", "hero", 1, {"item": "iron_ore", "price": 3})
        view = service.player_view("hero")
        checklist = view["onboarding_checklist"]
        assert len(checklist) == 3
        assert sum(item["status"] == "current" for item in checklist) == 1
        assert any(item["id"] == "stock_four_shops" and item["status"] == "current" for item in checklist)
        assert view["onboarding_progress"]["done"] >= 1
    finally:
        service.close()


def test_player_view_exposes_dialogue_relationship_and_recipe_ledger(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "riverbend-relationship", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "miner", name="阿岚")["status"] == "accepted"
        with platform.store.transaction():
            agent = platform.store.repositories.entities.get_agent("hero")
            agent["initial_state"]["location_id"] = "smithy"
            platform.store.repositories.entities.put_agent("hero", agent, "阿岚")
        assert platform.submit_dialogue("hero", "zhou", "general", "桥市前还缺什么？")["status"] == "accepted"

        view = service.player_view("hero")
        relationship = next(item for item in view["relationships"] if item["person"] == "zhou")
        assert relationship["relation"] == "trust"
        assert relationship["strength"] > 0
        assert relationship["description"]
        assert len(view["recipes"]) == 9
        assert any(recipe["id"] == "iron_ingot" for recipe in view["recipes"])
        assert view["recap"]["latest_event_id"] >= 1
        assert any("香炖菜" in use for use in view["item_uses"]["spice"])
        assert any("桥市礼箱" in use for use in view["item_uses"]["spice"])
    finally:
        service.close()


def test_travel_options_exclude_the_current_location(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "riverbend-travel-options", tmp_path / "runtime"))
    platform = service.platform
    try:
        view = platform.decision_view("ashi")
        travel = next(item for item in view["executable_behaviors"] if item["ref"] == "behavior:travel")
        options = travel["parameter_options"]["location_id"]
        assert {"name", "value"} <= set(options[0])
        assert "north_mine" not in {option["value"] for option in options}
    finally:
        service.close()


def test_background_miner_chooses_configured_work_over_travel(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "riverbend-bt-priority", tmp_path / "runtime"))
    platform = service.platform
    try:
        agent = platform.store.repositories.entities.get_agent("ashi")
        assert agent["bt_priority"][0] == "gather"
        assert SimulationRunner(platform, {})._run_bt_only_decision(agent, SimulationReport(0, 0), 0)

        updated = platform.store.repositories.entities.get_agent("ashi")
        state = updated["initial_state"]
        assert state["active_behavior_id"] in {"gather", "deep_gather"}
        assert state["active_parameters"]["entry_id"].startswith("gather_")
    finally:
        service.close()


def test_background_miner_without_priority_does_not_default_to_travel(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "riverbend-bt-fallback", tmp_path / "runtime"))
    platform = service.platform
    try:
        with platform.store.transaction():
            agent = platform.store.repositories.entities.get_agent("ashi")
            agent.pop("bt_priority")
            platform.store.repositories.entities.put_agent("ashi", agent, agent.get("name") or "阿石")

        agent = platform.store.repositories.entities.get_agent("ashi")
        assert SimulationRunner(platform, {})._run_bt_only_decision(agent, SimulationReport(0, 0), 0)

        updated = platform.store.repositories.entities.get_agent("ashi")
        state = updated["initial_state"]
        assert state["active_behavior_id"] != "travel"
        assert state["active_parameters"]["entry_id"].startswith("gather_")
    finally:
        service.close()


def test_every_riverbend_item_has_a_source_and_a_sink() -> None:
    rules = yaml.safe_load((WORLD / "rules.yaml").read_text(encoding="utf-8"))
    recipes = yaml.safe_load((WORLD / "recipes.yaml").read_text(encoding="utf-8"))["recipes"]
    actions = yaml.safe_load((WORLD / "actions.yaml").read_text(encoding="utf-8"))["actions"]
    agents = yaml.safe_load((WORLD / "agents.yaml").read_text(encoding="utf-8"))["agents"]
    labels = set(rules["item_labels"])
    catalogs = rules["action_catalogs"]
    producers: set[str] = set()
    consumers: set[str] = set()

    for recipe in recipes:
        for entry in recipe.get("inputs") or []:
            consumers.add(str(entry["item"]))
        for entry in recipe.get("outputs") or []:
            producers.add(str(entry["item"]))
    for entry in catalogs["gather"]:
        for effect in entry.get("effects") or []:
            item = effect.get("output_item") or effect.get("item")
            if item:
                producers.add(str(item))
        for condition in entry.get("requires") or []:
            for item in labels:
                if f"actor.inventory.{item}" in str(condition):
                    consumers.add(item)
    for entry in catalogs["trade_offers"]:
        for item in (entry.get("costs", {}).get("reserve") or {}):
            consumers.add(str(item))
        for effect in entry.get("effects") or []:
            if effect.get("type") == "inventory_add" and effect.get("item"):
                producers.add(str(effect["item"]))
    for entry in catalogs["consume"]:
        for item in (entry.get("costs", {}).get("reserve") or {}):
            consumers.add(str(item))
    for action in actions:
        for item in (action.get("costs", {}).get("reserve") or {}):
            consumers.add(str(item))
        for effect in action.get("effects") or []:
            if effect.get("type") == "inventory_add" and effect.get("item"):
                producers.add(str(effect["item"]))
    for agent in agents:
        for item in (agent.get("inventory") or {}):
            producers.add(str(item))

    assert producers >= labels - {"coin"}
    assert consumers >= labels - {"coin"}
    spice_recipe = next(recipe for recipe in recipes if recipe["id"] == "spiced_stew")
    assert any(entry["item"] == "spice" for entry in spice_recipe["inputs"])


def test_spice_has_a_repeatable_cooking_and_sale_loop(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "riverbend-spice-loop", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "miner", name="阿岚")["status"] == "accepted"
        with platform.store.transaction():
            agent = platform.store.repositories.entities.get_agent("hero")
            agent["initial_state"]["location_id"] = "tavern"
            agent["inventory"].update({"stew": 1, "spice": 1})
            platform.store.repositories.entities.put_agent("hero", agent, "阿岚")

        _start_and_finish(platform, "hero", "craft", "spice-craft", {"recipe_id": "spiced_stew"})
        agent = platform.store.repositories.entities.get_agent("hero")
        assert agent["inventory"]["spice"] == 0
        assert agent["inventory"]["stew"] == 0
        assert agent["inventory"]["spiced_stew"] == 1

        coin_before = float(agent["inventory"].get("coin", 0) or 0)
        _start_and_finish(platform, "hero", "trade_tavern", "spice-sale", {"entry_id": "sell_spiced_stew_to_tavern"})
        agent = platform.store.repositories.entities.get_agent("hero")
        assert agent["inventory"]["spiced_stew"] == 0
        assert float(agent["inventory"]["coin"]) == coin_before + 9
    finally:
        service.close()


def test_tool_offers_refuse_a_duplicate_when_one_is_already_carried() -> None:
    rules = yaml.safe_load((WORLD / "rules.yaml").read_text(encoding="utf-8"))
    offers = {entry["id"]: entry for entry in rules["action_catalogs"]["trade_offers"]}
    for offer_id, item in (("buy_pickaxe", "pickaxe"), ("buy_herb_sickle", "herb_sickle"), ("buy_fishing_rod", "fishing_rod")):
        assert f"actor.inventory.{item} < 1" in offers[offer_id]["requires"]


def test_upgrade_instance_adds_new_config_without_resetting_world_state(tmp_path: Path) -> None:
    from sim2.bootstrap.service import upgrade_instance

    db_path = create_instance(WORLD, "riverbend-upgrade", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        assert platform.enter_player("hero", "miner", name="阿岚")["status"] == "accepted"
        with platform.store.transaction():
            properties = dict(platform.store.repositories.metadata.get("world_properties") or {})
            properties["days_to_fair"] = 3
            platform.store.repositories.metadata.set("world_properties", properties)
            platform.store.connection.execute("DELETE FROM recipe_def WHERE id='spiced_stew'")
            rules = dict(platform.store.repositories.metadata.get("rules") or {})
            rules.pop("item_uses", None)
            rules["world_rules"] = [{"id": "emergent_rule", "description": "runtime rule"}]
            platform.store.repositories.metadata.set("rules", rules)
            agent = platform.store.repositories.entities.get_agent("hero")
            agent["inventory"]["spice"] = 2
            platform.store.repositories.entities.put_agent("hero", agent, "阿岚")
        minute_before = int(platform.store.repositories.metadata.get("world_minute", 0))
    finally:
        platform.close()

    result = upgrade_instance(WORLD, db_path)
    assert result["status"] == "ok"
    platform = Platform(db_path)
    try:
        recipes = {item["id"] for item in platform.store.repositories.entities.get_definitions("recipe_def")}
        assert "spiced_stew" in recipes
        rules = platform.store.repositories.metadata.get("rules") or {}
        assert "item_uses" in rules
        assert rules["world_rules"][0]["id"] == "emergent_rule"
        properties = platform.store.repositories.metadata.get("world_properties") or {}
        assert properties["days_to_fair"] == 3
        assert int(platform.store.repositories.metadata.get("world_minute", 0)) == minute_before
        agent = platform.store.repositories.entities.get_agent("hero")
        assert agent["inventory"]["spice"] == 2
    finally:
        platform.close()
