from __future__ import annotations

from pathlib import Path

import yaml

from sim2.api.service import ControlService
from sim2.bootstrap.service import create_instance, describe_config, validate_config
from sim2.intents.models import BehaviorIntent
from worlds import world_path

WORLD = Path(__file__).parents[1] / "worlds" / "windward_skyfall"


def _start(platform, ref: str, intent_id: str, parameters: dict | None = None) -> dict:
    return platform.submit_behavior(BehaviorIntent(
        actor_id="hero",
        behavior_ref=ref,
        target={},
        parameters=dict(parameters or {}),
        reason="Exercise the windward vertical slice.",
        confidence=0.9,
        intent_id=intent_id,
    ))


def _finish(platform) -> dict:
    agent = platform.store.repositories.entities.get_agent("hero")
    minutes = int((agent.get("initial_state") or {}).get("remaining_minutes", 0) or 0)
    for _ in range(max(1, minutes)):
        platform.step()
    return platform.store.repositories.entities.get_agent("hero")


def _do(platform, ref: str, intent_id: str, parameters: dict | None = None) -> dict:
    started = _start(platform, ref, intent_id, parameters)
    assert started["status"] == "accepted", started
    _finish(platform)
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
    assert "dynamic_salvage" in contract["required_content"]["behaviors_using_action"]


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
            platform.store.repositories.metadata.set("world_minute", 1300)
        for _ in range(60):
            platform.step()
        integrity = platform.store.repositories.entities.get_agent("hero")["initial_state"]["integrity"]
        assert integrity < 100
    finally:
        service.close()
