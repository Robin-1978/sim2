from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from sim2.api.service import ControlService
from sim2.api.text import TextWorldSession
from sim2.agents.engine import AgentEngine
from sim2.bootstrap.service import create_instance, describe_config, upgrade_instance, validate_config
from sim2.evolution.config import normalize_evolution_config
from sim2.intents.models import BehaviorIntent
from sim2.simulation.runner import SimulationRunner
from worlds import world_path

WORLD = Path(__file__).parents[1] / "worlds" / "windward_skyfall"


def _start(platform, ref: str, intent_id: str, parameters: dict | None = None, actor: str = "hero") -> dict:
    return platform.submit_behavior(BehaviorIntent(
        actor_id=actor,
        behavior_ref=ref,
        target={},
        parameters=dict(parameters or {}),
        reason="Exercise the windward vertical slice.",
        confidence=0.9,
        intent_id=intent_id,
    ))


def _finish(platform, actor: str = "hero") -> dict:
    agent = platform.store.repositories.entities.get_agent(actor)
    minutes = int((agent.get("initial_state") or {}).get("remaining_minutes", 0) or 0)
    for _ in range(max(1, minutes)):
        platform.step()
    return platform.store.repositories.entities.get_agent(actor)


def _do(platform, ref: str, intent_id: str, parameters: dict | None = None, actor: str = "hero") -> dict:
    started = _start(platform, ref, intent_id, parameters, actor)
    assert started["status"] == "accepted", started
    _finish(platform, actor)
    return started


def _properties(platform) -> dict:
    return dict(platform.store.repositories.metadata.get("world_properties") or {})


def _set_properties(platform, **values) -> None:
    with platform.store.transaction():
        properties = _properties(platform)
        properties.update(values)
        platform.store.repositories.metadata.set("world_properties", properties)


def _set_inventory(platform, **items) -> None:
    with platform.store.transaction():
        agent = platform.store.repositories.entities.get_agent("hero")
        agent["inventory"].update(items)
        platform.store.repositories.entities.put_agent("hero", agent, "林晚")


def _set_location(platform, location_id: str) -> None:
    with platform.store.transaction():
        agent = platform.store.repositories.entities.get_agent("hero")
        agent["initial_state"]["location_id"] = location_id
        platform.store.repositories.entities.put_agent("hero", agent, "林晚")


def test_windward_package_is_complete() -> None:
    report = validate_config(WORLD)
    assert report["valid"], report["errors"]
    assert (world_path("windward_skyfall") / "manifest.yaml").is_file()
    description = describe_config(WORLD)
    assert description["world_id"] == "windward_skyfall"
    counts = description["counts"]
    assert counts["locations"] >= 10
    assert counts["agents"] >= 8
    assert counts["actions"] >= 50
    assert counts["behavior_trees"] >= 75
    assert counts["resources"] >= 19
    assert counts["recipes"] >= 6
    evolution = yaml.safe_load((WORLD / "evolution.yaml").read_text(encoding="utf-8"))
    contract = evolution["contracts"]["god_structural"]
    assert contract["required_definition_type"] == "behavior"
    assert contract["max_proposals"] == 1
    assert contract["required_content"]["growth_cell"] is True
    assert contract["behavior_constraints"]["action_id"] == evolution["director"]["behavior_action"] == "dynamic_salvage"


def test_first_expedition_builds_the_tool_ladder(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-first-expedition", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        view = service.player_view("hero")
        assert "behavior:travel" not in {action["ref"] for action in view["actions"]}
        assert "behavior:travel_windworn" in {action["ref"] for action in view["actions"]}

        _do(platform, "behavior:build_workshop_1", "build-workshop")
        assert _properties(platform)["workshop_level"] == 1
        _do(platform, "behavior:craft_stone_pick", "craft-pick")
        assert platform.store.repositories.entities.get_agent("hero")["inventory"]["stone_pick"] == 1

        _do(platform, "behavior:travel_saltbreak", "sail-saltbreak")
        _do(platform, "behavior:explore_saltbreak", "survey-saltbreak")
        properties = _properties(platform)
        assert properties["explored_islands"] == 1
        assert properties["act"] == 1
        _do(platform, "behavior:travel_copper_tide", "sail-copper")
        _do(platform, "behavior:mine_copper", "mine-copper")

        agent = platform.store.repositories.entities.get_agent("hero")
        assert agent["inventory"]["copper_ore"] == 2
        properties = _properties(platform)
        assert properties["storm_pressure"] == 3
        assert properties["explored_islands"] == 1
    finally:
        service.close()


@pytest.mark.parametrize(
    ("choice", "result", "trust", "copper", "plates", "can_anchor"),
    [
        ("rescue_kite_scout", 1, 0.35, 0, 0, True),
        ("salvage_dawn_copper", 2, 0.10, 2, 1, False),
    ],
)
def test_dawn_choices_change_later_options(
    tmp_path: Path, choice: str, result: int, trust: float, copper: int, plates: int, can_anchor: bool,
) -> None:
    service = ControlService(create_instance(WORLD, "windward-dawn", tmp_path / "runtime"))
    platform = service.platform
    rescue = "behavior:rescue_kite_scout"
    salvage = "behavior:salvage_dawn_copper"
    try:
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        view = service.player_view("hero")
        assert view["goals"][0]["id"] == "dawn_salvage"
        assert view["goals"][0]["ready_behavior_refs"] == [rescue, salvage]
        assert {rescue, salvage} <= {action["ref"] for action in view["actions"]}

        _do(platform, f"behavior:{choice}", "choose-what-to-save")
        properties = _properties(platform)
        assert properties["dawn_salvage"] == result
        assert properties["kite_rep"] == pytest.approx(trust)
        assert properties["supply_spare_plates"] == plates
        assert platform.store.repositories.entities.get_agent("hero")["inventory"].get("copper_ore", 0) == copper
        assert platform.store.repositories.events.event_type_counts()["skyfall.dawn_salvage"] == 1
        other = salvage if choice == "rescue_kite_scout" else rescue
        assert _start(platform, other, "try-other-choice")["status"] != "accepted"
        assert "dawn_salvage" not in {goal["id"] for goal in service.player_view("hero")["goals"]}

        _set_properties(platform, hull_integrity=0.85)
        platform.step()
        assert _properties(platform)["hull_integrity"] == pytest.approx(0.85 + 0.12 * plates)
        assert _properties(platform)["supply_spare_plates"] == 0
        _set_properties(platform, beacon_frame=1, beacon_cores=3, garden_level=2)
        with platform.store.transaction():
            platform.store.repositories.metadata.set("world_minute", 719)
        platform.step()
        goals = {goal["id"]: goal for goal in service.player_view("hero")["goals"]}
        assert goals["emergency_copper_dispatch"]["ready_behavior_refs"] == [
            "behavior:allocate_emergency_stock_defense", "behavior:allocate_emergency_stock_relief",
        ]
        _do(platform, "behavior:allocate_emergency_stock_relief", "honor-the-claim")
        refs = {action["ref"] for action in service.player_view("hero")["actions"]}
        assert ("behavior:anchor_islands" in refs) == can_anchor
    finally:
        service.close()


def test_dawn_window_expires_during_a_late_action(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-dawn-late", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "cook", name="林晚")["status"] == "accepted"
        with platform.store.transaction():
            platform.store.repositories.metadata.set("world_minute", 58)
        _do(platform, "behavior:salvage_dawn_copper", "too-late")
        view = service.player_view("hero")
        assert _properties(platform)["dawn_salvage"] == 3
        assert _properties(platform)["supply_spare_plates"] == 0
        assert not view["inventory"].get("copper_ore")
        assert "dawn_salvage" not in {goal["id"] for goal in view["goals"]}
        assert platform.store.repositories.events.event_type_counts()["skyfall.dawn_salvage"] == 1
        assert view["recent_outcomes"][0]["result"] == "dawn_salvage_closed"
    finally:
        service.close()


@pytest.mark.parametrize("dawn_choice", ["rescue", "copper"])
def test_first_expedition_construction_changes_route_and_commits_a_payoff(tmp_path: Path, dawn_choice: str) -> None:
    service = ControlService(create_instance(WORLD, f"windward-expedition-{dawn_choice}", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        if dawn_choice == "rescue":
            opening = "rescue_kite_scout"
            fitting = "fit_crosswind_beacon"
            alternate_fitting = "fit_copper_tow_rig"
            waystop, route = "travel_windworn", "travel_rainshade"
            objective, destination = "rainshade_expedition", "rainshade_cliff"
            payoff, item, amount = "supply_rainshade_lookout", "herb", 2
            material = "wood"
        else:
            opening = "salvage_dawn_copper"
            fitting = "fit_copper_tow_rig"
            alternate_fitting = "fit_crosswind_beacon"
            waystop, route = "travel_saltbreak", "travel_copper_tide"
            objective, destination = "copper_tide_expedition", "copper_tide"
            payoff, item, amount = "salvage_copper_tide_haul", "copper_ore", 3
            material = "copper_ore"

        _do(platform, f"behavior:{opening}", "first-rescue-or-salvage")
        view = service.player_view("hero")
        preparation = next(goal for goal in view["goals"] if goal["id"] == "prepare_expedition")
        assert preparation["ready_behavior_refs"] == [f"behavior:{fitting}"]
        assert f"behavior:{alternate_fitting}" not in {action["ref"] for action in view["actions"]}
        assert f"behavior:{waystop}" in {action["ref"] for action in view["actions"]}
        assert _start(platform, f"behavior:{route}", "try-unprepared-route")["status"] != "accepted"

        before = platform.store.repositories.entities.get_agent("hero")["inventory"][material]
        _do(platform, f"behavior:{fitting}", "install-equipment")
        fitted = platform.store.repositories.entities.get_agent("hero")["inventory"].get(material, 0)
        assert fitted == before - 2
        assert _start(platform, f"behavior:{route}", "try-shortcut-from-camp")["status"] != "accepted"
        _do(platform, f"behavior:{waystop}", "fly-to-waystop")
        view = service.player_view("hero")
        highlighted = next(goal for goal in view["goals"] if goal["id"] == objective)
        assert f"behavior:{route}" in highlighted["ready_behavior_refs"]
        assert not view["inventory"].get("rope" if dawn_choice == "rescue" else "stone_pick")
        _do(platform, f"behavior:{route}", "use-built-route")
        assert service.player_view("hero")["location"]["id"] == destination

        before_inventory = platform.store.repositories.entities.get_agent("hero")["inventory"]
        before_properties = _properties(platform)
        _do(platform, f"behavior:{payoff}", "complete-expedition")
        after = platform.store.repositories.entities.get_agent("hero")["inventory"]
        assert after[item] == before_inventory.get(item, 0) + amount
        assert _start(platform, f"behavior:{payoff}", "repeat-the-haul")["status"] != "accepted"
        assert objective not in {goal["id"] for goal in service.player_view("hero")["goals"]}
        assert platform.store.repositories.events.event_type_counts()["skyfall.expedition_completed"] == 1
        assert any(task["task_type"] == "event_group:story_after_expedition" for task in platform.ready_evolution_tasks())

        properties = _properties(platform)
        if dawn_choice == "rescue":
            assert after["ration"] == before_inventory["ration"] - 1
            assert properties["kite_rep"] == pytest.approx(0.43)
            assert properties["camp_morale"] == pytest.approx(before_properties["camp_morale"] + 0.04)
        else:
            assert after["coal"] == before_inventory.get("coal", 0) + 1
            assert properties["storm_pressure"] == before_properties["storm_pressure"] + 8
    finally:
        service.close()


def test_copper_start_can_build_the_crosswind_beacon_after_relief(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-two-routes", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        _do(platform, "behavior:salvage_dawn_copper", "save-the-copper")
        _do(platform, "behavior:fit_copper_tow_rig", "install-the-rig")
        assert _start(platform, "behavior:fit_crosswind_beacon", "try-without-trust")["status"] != "accepted"
        for index, arrival in enumerate((720, 1440)):
            with platform.store.transaction():
                platform.store.repositories.metadata.set("world_minute", arrival - 1)
            platform.step()
            _do(platform, "behavior:allocate_emergency_stock_relief", f"relief-{index}")
        _do(platform, "behavior:fit_crosswind_beacon", "fit-after-earning-trust")
        properties = _properties(platform)
        assert properties["dawn_salvage"] == 2
        assert (properties["crosswind_beacon"], properties["copper_tow_rig"]) == (1, 1)
    finally:
        service.close()


def test_copper_dispatch_shows_the_carryover_obligation(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-copper-debt", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        with platform.store.transaction():
            platform.store.repositories.metadata.set("world_minute", 719)
        platform.step()
        _do(platform, "behavior:allocate_emergency_stock_defense", "keep-first-batch")
        with platform.store.transaction():
            platform.store.repositories.metadata.set("world_minute", 1439)
        platform.step()
        goals = {goal["id"]: goal for goal in service.player_view("hero")["goals"]}
        assert "emergency_copper_dispatch" not in goals
        assert goals["emergency_copper_repayment"]["ready_behavior_refs"] == [
            "behavior:allocate_emergency_stock_relief",
        ]
        _do(platform, "behavior:allocate_emergency_stock_relief", "repay-second-batch")
        assert _properties(platform)["supply_relief_due"] == 0
        assert "emergency_copper_repayment" not in {goal["id"] for goal in service.player_view("hero")["goals"]}
    finally:
        service.close()


def test_exploration_unlocks_the_next_route(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-explore-unlock", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        _do(platform, "behavior:travel_windworn", "sail-woods")
        _do(platform, "behavior:explore_windworn", "survey-woods")
        view = service.player_view("hero")
        refs = {action["ref"] for action in view["actions"]}
        assert "behavior:travel_lighthouse" in refs
        facts = [item.get("fact_key") for item in platform.store.repositories.memory.list_knowledge_facts("hero", limit=100)]
        assert any("explored:windworn_woods" in str(fact) for fact in facts)
        assert platform.store.repositories.events.event_type_counts()["exploration.completed"] == 1
        assert any(task["task_type"] == "event_group:story_after_exploration" for task in platform.ready_evolution_tasks())
    finally:
        service.close()


def test_upgrading_the_workshop_opens_better_tools(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-workshop-two", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        _set_properties(platform, workshop_level=1)
        _set_inventory(platform, wood=10, copper_ore=6, crystal=2, fiber=4, bone=2, oil=2)
        _do(platform, "behavior:build_workshop_2", "workshop-two")
        assert _properties(platform)["workshop_level"] == 2
        view = service.player_view("hero")
        refs = {action["ref"] for action in view["actions"]}
        unavailable = {action["ref"] for action in view.get("unavailable_actions", [])}
        assert "behavior:craft_crystal_saw" in refs or "behavior:craft_crystal_saw" in unavailable
        assert "behavior:craft_glider" in refs or "behavior:craft_glider" in unavailable
    finally:
        service.close()


def test_storm_thresholds_damage_the_hull_and_crash_ends_the_run(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-storm", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        _set_properties(platform, storm_pressure=65, hull_integrity=1.0)
        for _ in range(3):
            platform.step()
        properties = _properties(platform)
        assert properties["storm_stage"] == 2
        assert properties["hull_integrity"] < 1.0
        event_types = {
            row["event_type"]
            for row in platform.store.connection.execute(
                "SELECT event_type FROM event_log WHERE event_type LIKE 'storm.%'"
            ).fetchall()
        }
        assert "storm.warning" in event_types
        assert "storm.hit" in event_types

        _set_properties(platform, hull_integrity=0)
        for _ in range(2):
            platform.step()
        properties = _properties(platform)
        assert properties["ending_trigger"] == 4
        assert service.player_view("hero")["chapter"]["id"] == "crash"
    finally:
        service.close()


def test_beacon_launch_produces_the_escape_ending(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-ending", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        _set_properties(platform, workshop_level=2, beacon_frame=1, beacon_cores=3, hull_integrity=0.8)
        _do(platform, "behavior:launch_escape", "launch")
        assert _properties(platform)["ending_trigger"] == 1
        chapter = service.player_view("hero")["chapter"]
        assert chapter["id"] == "escape"
        assert chapter["ended"] is True
    finally:
        service.close()


def test_camp_projects_crew_and_facility_state(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-camp-view", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        view = service.player_view("hero")
        names = {person["name"] for person in view.get("nearby_participants", [])}
        assert {"阿岚", "砚秋", "老陶", "白雀", "小满"} <= names
        _do(platform, "behavior:build_workshop_1", "build-workshop-view")
        workshop = next(item for item in service.player_view("hero")["facilities"] if item["id"] == "camp_workshop")
        assert workshop["status"] == "working"
        assert workshop["properties"][0]["value"] == 1
    finally:
        service.close()


def test_a_god_created_salvage_site_adds_a_material_and_pressure(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-dynamic-salvage", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        marker = platform.submit_entity_proposal("god", {
            "entity_type": "resource",
            "name": "雨背崖沉绳",
            "description": "A windfall of old rope caught in the cliff.",
            "location_id": "rainshade_cliff",
            "state": {"quantity": 0},
        })
        assert marker["status"] == "accepted", marker
        behavior = platform.submit_definition_proposal("god", {
            "definition_type": "behavior",
            "name": "打捞雨背崖沉绳",
            "assign_to": ["player"],
            "evidence": "The Director asked for a new salvage site on the cliff.",
            "definition": {
                "id": "dyn_salvage_test",
                "agent_label": "打捞雨背崖沉绳",
                "summary": "打捞雨背崖沉绳",
                "description": "Cut the oil-soaked rope out of the cliff and carry it back.",
                "deferred": True,
                "cooldown_minutes": 1440,
                "entry_conditions": ["actor.alive", "location.tag.cliff"],
                "estimated_minutes": 60,
                "nodes": [{
                    "type": "action",
                    "action_id": "dynamic_salvage",
                    "parameters": {"item": "fiber", "amount": 2, "location_id": "rainshade_cliff"},
                }],
                "success_results": ["resource_gathered"],
                "failure_results": ["gathering_failed"],
                "goal_tags": ["gathering"],
                "drive_tags": ["purpose"],
            },
        })
        assert behavior["status"] == "accepted", behavior
        _set_location(platform, "windworn_woods")
        assert "behavior:dyn_salvage_test" not in {a["ref"] for a in service.player_view("hero")["actions"]}
        assert _start(platform, "behavior:dyn_salvage_test", "wrong-island")["status"] == "rejected"
        _set_location(platform, "rainshade_cliff")
        before = platform.store.repositories.entities.get_agent("hero")["inventory"].get("fiber", 0)
        _do(platform, "behavior:dyn_salvage_test", "salvage-site")
        after = platform.store.repositories.entities.get_agent("hero")["inventory"].get("fiber", 0)
        assert after == before + 2
        assert _properties(platform)["storm_pressure"] == 4
    finally:
        service.close()


def test_hull_reinforcement_reduces_the_first_storm_hit(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-hull-reduction", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        _set_properties(platform, hull_level=2, storm_pressure=65, hull_integrity=1.0)
        for _ in range(3):
            platform.step()
        properties = _properties(platform)
        assert properties["storm_stage"] == 2
        assert 0.94 <= properties["hull_integrity"] < 0.96
    finally:
        service.close()


def test_night_exposure_hurts_an_unsheltered_player(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-night", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        _set_location(platform, "windworn_woods")
        with platform.store.transaction():
            platform.store.repositories.metadata.set("world_minute", 1200)
        for _ in range(60):
            platform.step()
        integrity = platform.store.repositories.entities.get_agent("hero")["initial_state"]["integrity"]
        assert integrity < 100
    finally:
        service.close()


def test_crew_can_take_food_and_water_from_the_camp_larder(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-crew-food", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        with platform.store.transaction():
            alan = platform.store.repositories.entities.get_agent("alan")
            alan["inventory"] = {}
            alan["needs"]["satiety"] = 20
            alan["needs"]["hydration"] = 20
            platform.store.repositories.entities.put_agent("alan", alan, "阿岚")
        _do(platform, "behavior:take_ration", "alan-take", actor="alan")
        _do(platform, "behavior:fill_water", "alan-water", actor="alan")
        _do(platform, "behavior:eat_ration", "alan-eat", actor="alan")
        _do(platform, "behavior:drink_water", "alan-drink", actor="alan")
        alan = platform.store.repositories.entities.get_agent("alan")
        assert alan["inventory"].get("ration", 0) >= 1
        assert alan["inventory"].get("water", 0) >= 1
        assert alan["needs"]["satiety"] > 20
        assert alan["needs"]["hydration"] > 20
    finally:
        service.close()


def test_faction_agent_can_forage_at_its_own_island(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-faction-food", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        _do(platform, "behavior:forage_supplies", "ke-forage", actor="ke")
        _do(platform, "behavior:eat_ration", "ke-eat", actor="ke")
        _do(platform, "behavior:drink_water", "ke-drink", actor="ke")
        ke = platform.store.repositories.entities.get_agent("ke")
        assert ke["inventory"].get("ration", 0) >= 1
        assert ke["inventory"].get("water", 0) >= 1
    finally:
        service.close()


def test_night_hides_gathering_and_offers_wait_for_dawn(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-night-ux", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        _set_location(platform, "windworn_woods")
        with platform.store.transaction():
            platform.store.repositories.metadata.set("world_minute", 1300)
        view = service.player_view("hero")
        refs = {action["ref"] for action in view["actions"]}
        assert "behavior:wait_for_dawn" in refs
        assert "behavior:gather_wood" not in refs
        assert "behavior:chop_timber" not in refs
        _set_inventory(platform, lantern=1)
        lit_refs = {action["ref"] for action in service.player_view("hero")["actions"]}
        assert "behavior:gather_wood" in lit_refs
    finally:
        service.close()


def test_upgrade_teaches_existing_crew_and_player_the_new_survival_behaviors(tmp_path: Path) -> None:
    db_path = create_instance(WORLD, "windward-upgrade", tmp_path / "runtime")
    service = ControlService(db_path)
    platform = service.platform
    try:
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        with platform.store.transaction():
            alan = platform.store.repositories.entities.get_agent("alan")
            alan["controller"] = "bt"
            alan.pop("controller_fallback")
            alan["initial_bt_ids"] = [item for item in alan["initial_bt_ids"] if item not in {"take_ration", "fill_water"}]
            platform.store.repositories.entities.put_agent("alan", alan, "阿岚")
            player = platform.store.repositories.entities.get_agent("hero")
            new_expedition_behaviors = {
                "fit_crosswind_beacon", "fit_copper_tow_rig", "supply_rainshade_lookout", "salvage_copper_tide_haul",
            }
            player["initial_bt_ids"] = [
                item for item in player["initial_bt_ids"]
                if item not in {"wait_for_dawn", "rescue_kite_scout", "salvage_dawn_copper"} | new_expedition_behaviors
            ]
            player["inventory"]["copper_ore"] = 2
            platform.store.repositories.entities.put_agent("hero", player, "林晚")
            metadata = platform.store.repositories.metadata
            metadata.set("world_minute", 124)
            properties = metadata.get("world_properties")
            for key in ("dawn_salvage", "emergency_stock", "supply_consultation", "supply_relief_due", "supply_minutes", "supply_spare_plates"):
                properties.pop(key)
            for key in ("crosswind_beacon", "copper_tow_rig", "rainshade_lookout_supplied", "copper_tide_haul_taken"):
                properties.pop(key)
            properties["hull_integrity"] = 0.72
            metadata.set("world_properties", properties)
            platform.store.connection.execute("DELETE FROM entity WHERE id=?", ("emergency_copper_cache",))
            metadata.set("evolution", normalize_evolution_config({"cadence": {"god": "7d"}}))
            metadata.set("season_plan", {"plan_id": "old-verb-plan", "target_cell": "verb", "status": "active", "baseline": 12, "expires_minute": 10081})
            platform.store.repositories.evolution.put_evolution_task("god_local", "god", None, "god_local", 124, 10080, status="ready")
    finally:
        service.close()

    result = upgrade_instance(WORLD, db_path)
    assert result["agents_updated"] >= 1
    assert result["players_updated"] >= 1

    service = ControlService(db_path)
    platform = service.platform
    try:
        alan = platform.store.repositories.entities.get_agent("alan")
        player = platform.store.repositories.entities.get_agent("hero")
        assert {"take_ration", "fill_water"} <= set(alan["initial_bt_ids"])
        assert "wait_for_dawn" in player["initial_bt_ids"]
        assert {"rescue_kite_scout", "salvage_dawn_copper"} <= set(player["initial_bt_ids"])
        assert new_expedition_behaviors <= set(player["initial_bt_ids"])
        view = service.player_view("hero")
        assert "dawn_salvage" not in {goal["id"] for goal in view["goals"]}
        assert not {"behavior:rescue_kite_scout", "behavior:salvage_dawn_copper"} & {action["ref"] for action in view["actions"]}
        assert any(
            goal["id"] == "prepare_expedition" and "behavior:fit_copper_tow_rig" in goal["ready_behavior_refs"]
            for goal in view["goals"]
        )
        assert (alan["controller"], alan["controller_fallback"]) == ("llm", "bt")
        metadata = platform.store.repositories.metadata
        assert metadata.get("world_minute") == 124
        properties = metadata.get("world_properties")
        assert properties["hull_integrity"] == 0.72
        assert properties["crosswind_beacon"] == properties["copper_tow_rig"] == 0
        assert properties["emergency_stock"] == properties["supply_consultation"] == 0
        stock = platform.store.repositories.entities.get_entity("emergency_copper_cache")
        assert stock["initial_state"]["quantity"] == 0
        assert metadata.get("evolution")["director"]["behavior_action"] == "dynamic_salvage"
        assert all(task["task_id"] != "god_local" for task in platform.ready_evolution_tasks())
        platform.step()
        assert metadata.get("season_plan")["baseline"] == 0
        assert metadata.get("season_plan_history")["old-verb-plan"]["status"] == "expired"
    finally:
        service.close()


def _at_minute(platform, minute: int) -> None:
    with platform.store.transaction():
        platform.store.repositories.metadata.set("world_minute", minute)


def test_authored_routes_are_shared_by_player_clients_and_platform(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-travel-policy", tmp_path / "runtime"))
    platform = service.platform
    try:
        platform.enter_player("hero", "mechanic", name="林晚")
        view = service.player_view("hero")
        routes = {route["id"]: route["behavior_ref"] for route in view["routes"]}
        assert routes["saltbreak_flats"] == "behavior:travel_saltbreak"
        assert any(action.get("destination_id") == "saltbreak_flats" for action in view["actions"])
        session = TextWorldSession(platform, player_id="hero")
        session.execute("go 碎盐滩")
        assert _finish(platform)["initial_state"]["location_id"] == "saltbreak_flats"
        assert "copper_tide" not in {route["id"] for route in service.player_view("hero")["routes"]}

        # Old saves can already contain the synthesized generic definition.
        with platform.store.transaction():
            platform.store.repositories.entities.put_definition("bt_def", "travel", {"id": "travel"})
        result = service.player_act({
            "player_id": "hero", "behavior_ref": "behavior:travel", "parameters": {"location_id": "copper_tide"},
        })
        assert result["failed_check"] == "travel_policy"
        assert platform.store.repositories.entities.get_agent("hero")["initial_state"]["location_id"] == "saltbreak_flats"
    finally:
        service.close()


def test_storm_eases_reopens_routes_and_wakes_story(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-storm-eases", tmp_path / "runtime"))
    platform = service.platform
    try:
        platform.enter_player("hero", "mechanic", name="林晚")
        _set_location(platform, "windworn_woods")
        _do(platform, "behavior:explore_windworn", "discover-lighthouse")
        _at_minute(platform, 325)
        _set_properties(platform, storm_pressure=65)
        platform.step()
        assert _properties(platform)["storm_stage"] == 2
        assert "old_lighthouse" not in {route["id"] for route in service.player_view("hero")["routes"]}
        storm_tasks = [task for task in platform.ready_evolution_tasks() if task["task_type"] == "event_group:story_after_storm"]
        assert len(storm_tasks) == 1
        assert storm_tasks[0]["source_events"][0]["event_type"] in {"storm.warning", "storm.hit"}

        _do(platform, "behavior:travel_lantern_cape", "return-to-camp")
        _set_inventory(platform, relic=1)
        _do(platform, "behavior:jettison_relic", "throw-cargo-overboard")
        platform.step()
        assert _properties(platform)["storm_pressure"] == 45
        assert _properties(platform)["storm_stage"] == 1
        hull = _properties(platform)["hull_integrity"]
        _at_minute(platform, 719)
        platform.step()
        assert _properties(platform)["hull_integrity"] == hull

        _set_location(platform, "windworn_woods")
        assert "old_lighthouse" in {route["id"] for route in service.player_view("hero")["routes"]}
        _set_properties(platform, storm_pressure=65)
        platform.step()
        assert _properties(platform)["storm_stage"] == 2
        assert _properties(platform)["hull_integrity"] == hull
        assert platform.store.repositories.events.event_type_counts()["storm.hit"] == 1
    finally:
        service.close()


def test_ending_commits_once_when_deadline_crosses_an_active_launch(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-ending-race", tmp_path / "runtime"))
    platform = service.platform
    try:
        platform.enter_player("hero", "mechanic", name="林晚")
        deadline = 29 * 1440 - 420
        _at_minute(platform, deadline - 5)
        _set_properties(platform, day=29, beacon_frame=1, beacon_cores=3, hull_integrity=0.8)
        assert _start(platform, "behavior:launch_escape", "timed-launch")["status"] == "accepted"
        for _ in range(5):
            platform.step()
        assert service.player_view("hero")["chapter"]["id"] == "long_drift"
        agent = _finish(platform)
        assert agent["initial_state"]["last_platform_result"]["status"] == "failed"
        assert _properties(platform)["ending_trigger"] == 5
        counts = platform.store.repositories.events.event_type_counts()
        assert counts["skyfall.long_drift"] == 1
        assert "skyfall.launched" not in counts
        assert any(task["task_type"] == "event_group:story_after_ending" for task in platform.ready_evolution_tasks())
    finally:
        service.close()


def test_start_of_day_and_wait_for_dawn_use_the_same_calendar(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-calendar", tmp_path / "runtime"))
    platform = service.platform
    try:
        platform.enter_player("hero", "mechanic", name="林晚")
        _at_minute(platform, 1019)
        platform.step()
        time = service.player_view("hero")["world_time"]
        assert time["time_of_day"] == "00:00"
        assert time["day"] == _properties(platform)["day"] == 2

        _at_minute(platform, 1319)
        action = next(a for a in service.player_view("hero")["actions"] if a["ref"] == "behavior:wait_for_dawn")
        assert action["time"] == "1 minutes"
        result = _start(platform, action["ref"], "wait-one-minute")
        assert result["status"] == "accepted"
        assert result["remaining_minutes"] == 1
        _finish(platform)
        assert service.player_view("hero")["world_time"]["time_of_day"] == "05:00"
    finally:
        service.close()


class _ScenarioProvider:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = iter(responses)
        self.calls = 0

    def complete_json(self, **_kwargs):
        self.calls += 1
        return next(self.responses)


def _salvage_proposal(suffix: str, item: str, location: str = "broken_span", amount: int = 2) -> dict:
    return {
        "definition_type": "behavior",
        "name": f"审计打捞 {suffix}",
        "assign_to": ["ke"],
        "definition": {
            "id": f"dyn_salvage_{suffix}",
            "agent_label": f"打捞内容 {suffix}",
            "summary": f"回收物资 {suffix}",
            "description": "从外岛回收可用材料。",
            "deferred": True,
            "cooldown_minutes": 1440,
            "entry_conditions": ["actor.alive", "location.tag.salvage"],
            "estimated_minutes": 60,
            "nodes": [{"type": "action", "action_id": "dynamic_salvage", "parameters": {
                "item": item, "amount": amount, "location_id": location,
            }}],
            "success_results": ["resource_gathered"],
            "failure_results": ["gathering_failed"],
        },
    }


def test_god_contract_rejects_direct_and_tool_mutations_and_caps_a_native_batch(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-evolution-policy", tmp_path / "runtime"))
    platform = service.platform
    try:
        platform.enter_player("hero", "mechanic", name="林晚")
        platform.step()
        def tool_call(call_id: str, proposal: dict) -> dict:
            return {"id": call_id, "type": "function", "function": {
                "name": "propose_definition", "arguments": json.dumps(proposal, ensure_ascii=False),
            }}
        provider = _ScenarioProvider([
            {"entity_type": "resource", "name": "超出契约", "location": "windworn_woods"},
            {"tool": "propose_definition", "arguments": _salvage_proposal("oversized", "wood", amount=100)},
            {"tool_calls": [
                tool_call("valid", _salvage_proposal("valid", "wood")),
                tool_call("over_budget", _salvage_proposal("over_budget", "stone")),
            ]},
        ])
        turn = AgentEngine(platform, provider, max_iterations=5).run(role="god", actor_id="god", task_type="god_structural")
        assert turn.result["status"] == "accepted", turn.result
        checks = {step.get("result", {}).get("failed_check") for step in turn.transcript}
        assert {"task_tool", "evolution_contract", "proposal_budget"} <= checks
        definitions = {row["id"] for row in platform.store.repositories.entities.get_definitions("bt_def")}
        assert {"dyn_salvage_valid"} == {name for name in definitions if name.startswith("dyn_salvage_")}
        assert not any(row["name"] == "超出契约" for row in platform.store.repositories.entities.entities_of_type("resource"))
    finally:
        service.close()


def test_director_reenters_a_second_salvage_plan_through_the_runner(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-growth-cycles", tmp_path / "runtime"))
    platform = service.platform
    try:
        provider = _ScenarioProvider([
            {"tool": "propose_definition", "arguments": _salvage_proposal("first", "wood")},
            {"tool": "propose_definition", "arguments": _salvage_proposal("duplicate", "wood")},
            {"intent_type": "propose_definition", **_salvage_proposal("second", "stone")},
        ])
        runner = SimulationRunner(platform, {"god": provider})
        runner.run(61)
        assert provider.calls == 1
        assert not any(row.get("role") == "player" for row in platform.store.repositories.entities.list_agents())
        assert platform.store.repositories.entities.get_agent("ke")["inventory"]["wood"] == 2
        metadata = platform.store.repositories.metadata

        _at_minute(platform, 2879)
        runner.run(1)
        assert provider.calls == 3
        assert metadata.get("last_completed_season_plan")["baseline"] == 0
        assert metadata.get("last_completed_season_plan")["outcome_event_id"]
        assert metadata.get("season_plan")["baseline"] == 1
        SimulationRunner(platform, {}).run(300)
        assert platform.store.repositories.entities.get_agent("ke")["inventory"]["stone"] == 2
        _at_minute(platform, 5759)
        platform.step()
        assert metadata.get("last_completed_season_plan")["baseline"] == 1
        assert metadata.get("season_plan")["baseline"] == 2
    finally:
        service.close()


def test_featured_crew_use_model_decisions_with_offline_bt_fallback(tmp_path: Path) -> None:
    service = ControlService(create_instance(WORLD, "windward-featured-crew", tmp_path / "runtime"))
    platform = service.platform
    try:
        provider = _ScenarioProvider([{"intent": "wait", "reason": "No urgent tasks."}] * 2)
        roles: list[str] = []
        report = SimulationRunner(platform, {"agent_decision": provider}, turn_observer=lambda _turn, actor: roles.append(actor)).run(1)
        assert set(roles) == {"alan", "yanqiu"}
        assert report.bt_decisions == 6
        _at_minute(platform, 61)
        offline = SimulationRunner(platform, {}).run(1)
        assert offline.bt_decisions >= 2
        for actor in roles:
            assert platform.store.repositories.entities.get_agent(actor)["initial_state"].get("active_behavior_id")
    finally:
        service.close()
