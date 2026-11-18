from __future__ import annotations

from pathlib import Path

import yaml

from sim2.bootstrap.service import create_instance, describe_config, validate_config
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform

WORLD = Path(__file__).parents[1] / "worlds" / "tidebound_commons"


def _run_active(platform: Platform, actor_id: str) -> int:
    agent = platform.store.repositories.entities.get_agent(actor_id)
    minutes = int((agent.get("initial_state") or {}).get("remaining_minutes", 0) or 0)
    for _ in range(minutes):
        platform.step()
    return minutes


def _start(platform: Platform, actor_id: str, behavior_id: str, intent_id: str) -> dict:
    return platform.submit_behavior(BehaviorIntent(
        actor_id=actor_id,
        behavior_ref=f"behavior:{behavior_id}",
        target={},
        parameters={},
        reason="The configured behavior matches the current resource and goal.",
        confidence=0.9,
        intent_id=intent_id,
    ))


def test_tidebound_commons_is_a_balanced_non_village_package(tmp_path: Path) -> None:
    report = validate_config(WORLD)
    assert report["valid"], report["errors"]
    description = describe_config(WORLD)
    assert description["world_id"] == "tidebound_commons"
    assert description["counts"]["agents"] == 6
    assert description["counts"]["locations"] == 6
    assert description["counts"]["recipes"] == 3

    rules = yaml.safe_load((WORLD / "rules.yaml").read_text(encoding="utf-8"))
    storm_label = rules["world_property_labels"]["storm_eta_hours"]
    assert storm_label["unit"] == "hours" and storm_label["alert"] is True

    platform = Platform(create_instance(WORLD, "tidebound-balance", tmp_path / "runtime"))
    try:
        assert platform.store.repositories.metadata.get("world_properties")["tide_gate_integrity"] == 0.28
        assert "hydration" in platform.store.repositories.metadata.get("attributes")[2]["id"]
        assert all(platform.decision_view(agent["id"])["executable_behaviors"] for agent in platform.store.repositories.entities.list_agents())
        for resource_id, cap in (("fresh_water", 30), ("shellfish", 36), ("driftwood", 28), ("marsh_reed", 30)):
            entity = platform.store.repositories.entities.get_entity(resource_id)
            assert float(entity["initial_state"]["quantity"]) <= cap
        # The first choice is meaningful: harvesting shellfish creates a real
        # carried input, then preservation turns it into three rations at a
        # time-and-distance cost. Nothing is minted by the world state.
        accepted = _start(platform, "ivo", "gather_shellfish", "tide-gather-1")
        assert accepted["status"] == "accepted", accepted
        _run_active(platform, "ivo")
        ivo = platform.store.repositories.entities.get_agent("ivo")
        assert ivo["inventory"].get("shellfish", 0) == 3
        shellfish_before = platform.store.repositories.entities.get_entity("shellfish")["initial_state"]["quantity"]
        assert shellfish_before == 26

        accepted = _start(platform, "ivo", "craft_rations", "tide-craft-1")
        assert accepted["status"] == "accepted", accepted
        spent = _run_active(platform, "ivo")
        ivo = platform.store.repositories.entities.get_agent("ivo")
        assert spent >= 100  # weighted return to the workshop is part of the choice
        assert ivo["inventory"].get("food_ration", 0) == 5
        assert ivo["inventory"].get("shellfish", 0) == 1
        assert ivo["inventory"].get("fresh_water", 0) == 1

        # Regeneration is bounded and does not erase the prior cost.
        for _ in range(720):
            platform.step()
        shellfish = platform.store.repositories.entities.get_entity("shellfish")
        assert 26 < shellfish["initial_state"]["quantity"] <= 36
        assert platform.store.repositories.metadata.get("world_properties")["tide_gate_integrity"] == 0.28
    finally:
        platform.close()


def test_storm_clock_teaches_the_difference_between_a_patch_and_a_real_repair(tmp_path: Path) -> None:
    """A due storm must hurt an unprepared gate and reward real preparation.

    The clock is a declarative world-scope minute effect.  Its condition reads
    world properties, its event is published once for the minute, and the
    follow-up effects reset the clock instead of repeating damage every tick.
    """

    def properties(platform: Platform) -> dict:
        return dict(platform.store.repositories.metadata.get("world_properties") or {})

    def set_properties(platform: Platform, **values) -> None:
        with platform.store.transaction():
            for key, value in values.items():
                platform.store.repositories.metadata.set(
                    "world_properties",
                    {**properties(platform), key: value},
                )

    unprepared = Platform(create_instance(WORLD, "tidebound-storm-unprepared", tmp_path / "runtime"))
    try:
        set_properties(
            unprepared,
            storm_eta_hours=0,
            storm_pressure=1.0,
            tide_gate_integrity=0.5,
            public_confidence=0.5,
        )
        unprepared.step()
        after = properties(unprepared)
        assert after["storm_eta_hours"] > 47.9
        assert round(after["storm_pressure"], 3) == 0.3
        assert round(after["tide_gate_integrity"], 3) == 0.34
        assert any(
            event["event_type"] == "storm.breached"
            for event in unprepared.store.repositories.events.recent_events(limit=40)
        )
        breach_count = sum(
            1 for event in unprepared.store.repositories.events.recent_events(limit=80)
            if event["event_type"] == "storm.breached"
        )
        unprepared.step()
        assert sum(
            1 for event in unprepared.store.repositories.events.recent_events(limit=80)
            if event["event_type"] == "storm.breached"
        ) == breach_count
    finally:
        unprepared.close()

    prepared = Platform(create_instance(WORLD, "tidebound-storm-prepared", tmp_path / "runtime"))
    try:
        set_properties(
            prepared,
            storm_eta_hours=0,
            storm_pressure=1.0,
            tide_gate_integrity=0.85,
            public_confidence=0.5,
        )
        prepared.step()
        after = properties(prepared)
        assert after["storm_eta_hours"] > 47.9
        assert round(after["storm_pressure"], 3) == 0.3
        assert after["tide_gate_integrity"] == 0.85
        assert round(after["public_confidence"], 3) == 0.55
        events = [event["event_type"] for event in prepared.store.repositories.events.recent_events(limit=40)]
        assert "storm.held" in events
        assert "storm.breached" not in events
    finally:
        prepared.close()


def test_tidebound_research_can_expand_the_map_with_atomic_route_links(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "tidebound-expansion", tmp_path / "runtime"))
    try:
        created = platform.submit_entity_proposal("god", {
            "entity_type": "location",
            "name": "Outer Shelf",
            "description": "A newly exposed shelf beyond the drowned observatory.",
            "state": {
                "routes": ["drowned_observatory"],
                "route_distances": {"drowned_observatory": 4},
                "tags": ["exploration", "frontier"],
                "public": True,
                "safe": False,
                "map_position": {"x": 0, "y": 3},
            },
        })
        assert created["status"] == "accepted", created
        observatory = platform.store.repositories.entities.get_entity("drowned_observatory")
        shelf = platform.store.repositories.entities.get_entity(created["entity_id"])
        assert created["entity_id"] in observatory["routes"]
        assert "drowned_observatory" in shelf["routes"]
        assert float(observatory["route_distances"][created["entity_id"]]) == 4.0
        assert float(shelf["route_distances"]["drowned_observatory"]) == 4.0
    finally:
        platform.close()


def test_survival_guard_hides_optional_work_but_keeps_recovery_choices(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "tidebound-survival-guard", tmp_path / "runtime"))
    try:
        agent = platform.store.repositories.entities.get_agent("mara")
        agent["needs"]["hydration"] = 20
        agent["needs"]["satiety"] = 20
        platform.store.repositories.entities.update_agent("mara", agent)
        choices = {item["ref"] for item in platform.decision_view("mara")["executable_behaviors"]}
        assert "behavior:inspect_gate" not in choices
        assert "behavior:craft_repair_kit" not in choices
        assert "behavior:drink_water" in choices
        assert "behavior:eat_ration" in choices
        assert "behavior:gather_water" in choices

        agent = platform.store.repositories.entities.get_agent("mara")
        agent["needs"]["hydration"] = 60
        agent["needs"]["satiety"] = 20
        platform.store.repositories.entities.update_agent("mara", agent)
        choices = {item["ref"] for item in platform.decision_view("mara")["executable_behaviors"]}
        # Food gathering is part of the recovery chain. A hungry resident with
        # no ration must still be able to collect ingredients before cooking.
        assert "behavior:gather_shellfish" in choices
        assert "behavior:gather_water" in choices
        assert "behavior:eat_ration" in choices
    finally:
        platform.close()


def test_catalog_distinguishes_food_preparation_from_eating(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "tidebound-food-semantics", tmp_path / "runtime"))
    try:
        agent = platform.store.repositories.entities.get_agent("mara")
        agent["needs"]["hydration"] = 60
        agent["needs"]["satiety"] = 20
        agent["inventory"].update({"shellfish": 2, "fresh_water": 1, "food_ration": 1})
        platform.store.repositories.entities.update_agent("mara", agent)
        view = platform.decision_view("mara")
        behaviors = {item["ref"]: item for item in view["executable_behaviors"]}

        assert "behavior:eat_ration" in behaviors
        assert "behavior:craft_rations" in behaviors
        assert behaviors["behavior:eat_ration"]["drive_tags"] == ["satiety"]
        assert "Inventory -1 food ration" in behaviors["behavior:eat_ration"]["impact"]
        assert behaviors["behavior:craft_rations"]["drive_tags"] == ["purpose", "belonging"]
        assert behaviors["behavior:craft_rations"]["supports_drive_tags"] == ["satiety"]
        assert "吃口粮" in behaviors["behavior:craft_rations"]["purpose"]
        assert any("only collects or produces an item is preparation" in rule for rule in view["platform_rules"])

        agent["needs"]["satiety"] = 60
        platform.store.repositories.entities.update_agent("mara", agent)
        behaviors = {item["ref"]: item for item in platform.decision_view("mara")["executable_behaviors"]}
        assert behaviors["behavior:gather_shellfish"]["drive_tags"] == ["purpose"]
        assert behaviors["behavior:gather_shellfish"]["supports_drive_tags"] == ["satiety"]
        assert "不会恢复饱食" in behaviors["behavior:gather_shellfish"]["purpose"]
    finally:
        platform.close()


def test_preserved_rations_are_limited_by_inputs_and_time_not_other_meals(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "tidebound-ration-conservation", tmp_path / "runtime"))
    try:
        # Meals held by other residents must not block a hungry resident from
        # turning conserved inputs into another meal.
        others = [actor for actor in platform.store.repositories.entities.list_agents() if actor["id"] != "mara"][:3]
        for actor in others:
            actor["inventory"]["food_ration"] = 10
            platform.store.repositories.entities.update_agent(actor["id"], actor)
        mara = platform.store.repositories.entities.get_agent("mara")
        mara["needs"]["hydration"] = 60
        mara["needs"]["satiety"] = 20
        mara["inventory"].update({"shellfish": 2, "fresh_water": 1, "food_ration": 0})
        platform.store.repositories.entities.update_agent("mara", mara)

        accepted = _start(platform, "mara", "craft_rations", "tide-ration-cap")
        assert accepted["status"] == "accepted", accepted
        _run_active(platform, "mara")
        mara = platform.store.repositories.entities.get_agent("mara")
        assert mara["inventory"]["food_ration"] == 3
        assert mara["inventory"].get("shellfish", 0) == 0
        assert mara["inventory"].get("fresh_water", 0) == 0
    finally:
        platform.close()


def test_tidebound_progression_projection_exposes_ready_and_supporting_steps(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "tidebound-progression", tmp_path / "runtime"))
    try:
        mara = platform.decision_view("mara")
        goals = {item["id"]: item for item in mara["progression_goals"]}
        assert goals["inspect_tide_gate"]["status"] == "ready"
        assert "测量潮门" in goals["inspect_tide_gate"]["ready_behaviors"]
        assert "behavior:inspect_gate" in goals["inspect_tide_gate"]["ready_behavior_refs"]
        assert goals["repair_tide_gate"]["status"] == "needs_support"
        assert "打捞浮木" in goals["repair_tide_gate"]["support_behaviors"]
        assert any("repair kit" in item.lower() for item in goals["repair_tide_gate"]["missing_requirements"])
        selene = platform.decision_view("selene")
        chart = next(item for item in selene["progression_goals"] if item["id"] == "complete_drowned_chart")
        assert chart["status"] == "ready"
        assert "研究沉没海图" in chart["ready_behaviors"]
        taro_goals = {item["id"]: item for item in platform.decision_view("taro")["progression_goals"]}
        assert not any(
            item["id"] == "complete_drowned_chart"
            for item in platform.decision_view("taro")["progression_goals"]
        )
        assert taro_goals["reopen_outer_route"]["status"] == "blocked"

        with platform.store.transaction():
            platform.store.repositories.events.append_event(
                "research.completed", "selene", 1,
                {"source_id": "drowned_chart", "stage_id": "shoreline"},
            )
            platform.store.repositories.events.append_event(
                "research.completed", "selene", 2,
                {"source_id": "drowned_chart", "stage_id": "shoreline"},
            )
        chart = next(item for item in platform.decision_view("selene")["progression_goals"] if item["id"] == "complete_drowned_chart")
        assert chart["progress"] == {"current": 1, "required": 2}
        with platform.store.transaction():
            platform.store.repositories.events.append_event(
                "research.completed", "selene", 3,
                {"source_id": "drowned_chart", "stage_id": "open_water"},
            )
        assert not any(
            item["id"] == "complete_drowned_chart"
            for item in platform.decision_view("selene")["progression_goals"]
        )
    finally:
        platform.close()


def test_tidebound_evolution_is_automatic_and_keeps_generating(tmp_path: Path) -> None:
    """Story and God must keep evolving from facts, not replay a fixed plot.

    The package ships no pre-authored story beats.  A verified inspection has
    to create a ready Story task on its own, a periodic God cadence must exist,
    and stagnation must be able to wake God when the commons stops gaining new
    executable options.
    """

    import yaml

    from sim2.evolution.config import normalize_evolution_config

    story = yaml.safe_load((WORLD / "story.yaml").read_text(encoding="utf-8"))
    assert story["beats"] == []
    assert "gate.inspected" in story["emergent_source_event_types"]

    compiled = normalize_evolution_config(yaml.safe_load((WORLD / "evolution.yaml").read_text(encoding="utf-8")))
    assert compiled["schedule"]["god_local_minutes"] > 0
    assert compiled["schedule"]["god_structural_minutes"] > 0
    assert compiled["stagnation"]["window_minutes"] == 1440
    consumers = compiled["event_wake_policy"]["consumers"]
    assert consumers["story"]["manual_event_types"] == []
    assert consumers["god"]["manual_event_types"] == []
    synthesis = next(
        group for group in consumers["god"]["event_groups"]
        if group.get("id") == "research_synthesis"
    )
    assert synthesis["event_types"] == ["research.progressed", "research.completed"]
    assert synthesis["minimum_events"] == 2
    assert synthesis["distinct_by"] == ["source_id", "stage_id"]
    assert {"propose_entity", "propose_world_change", "wait"} <= set(synthesis["agent_contract"]["allowed_tools"])
    assert any(
        "world.stagnation" in group["event_types"]
        for group in consumers["god"]["event_groups"]
    )

    platform = Platform(create_instance(WORLD, "tidebound-auto-evolution", tmp_path / "runtime"))
    try:
        accepted = _start(platform, "mara", "inspect_gate", "tide-auto-inspect")
        assert accepted["status"] == "accepted", accepted
        _run_active(platform, "mara")
        ready = platform.store.repositories.evolution.ready_evolution_tasks()
        assert any(task["role"] == "story" for task in ready), ready
        scheduled = platform.store.repositories.evolution.due_evolution_tasks(10**9)
        assert any(task["task_id"] == "god_local" for task in scheduled), scheduled
        assert any(task["task_id"] == "god_structural" for task in scheduled), scheduled

        # A two-stage source emits progress events before its single terminal
        # completion event.  Both stages must count toward the structural wake.
        for minute, stage_id in ((20, "shoreline"), (40, "open_water")):
            event_id = platform.store.repositories.events.append_event(
                "research.progressed", "selene", minute,
                {"source_id": "drowned_chart", "stage_id": stage_id},
            )
            platform.wakes.configured(event_id, "research.progressed", "selene", minute)
        synthesis_tasks = [
            task for task in platform.ready_evolution_tasks()
            if task["role"] == "god" and task["task_type"].startswith("event_group:research_synthesis")
        ]
        assert len(synthesis_tasks) == 1, synthesis_tasks
    finally:
        platform.close()


def test_god_can_extend_tidebound_map_and_factions_through_generic_proposals(tmp_path: Path) -> None:
    """New gameplay structures are reachable through the declarative God API."""

    platform = Platform(create_instance(WORLD, "tidebound-structure-growth", tmp_path / "runtime"))
    try:
        location = platform.submit_entity_proposal(
            "god",
            {
                "entity_type": "location",
                "name": "外海信标礁",
                "description": "A newly charted reef beacon beyond the drowned observatory.",
                "state": {
                    "routes": ["drowned_observatory"],
                    "route_distances": {"drowned_observatory": 4},
                    "tags": ["exploration", "signal"],
                    "public": True,
                    "safe": False,
                    "map_position": {"x": 2, "y": 3},
                },
            },
        )
        assert location["status"] == "accepted", location
        new_location = platform.store.repositories.entities.get_entity(location["entity_id"])
        observatory = platform.store.repositories.entities.get_entity("drowned_observatory")
        assert new_location["entity_type"] == "location"
        assert observatory["routes"].count(location["entity_id"]) == 1
        assert new_location["id"] in observatory["routes"]

        faction = platform.submit_entity_proposal(
            "god",
            {
                "entity_type": "faction",
                "name": "外海守望者",
                "description": "A coastal group that keeps the new beacon route open.",
                "state": {
                    "goals": ["keep the beacon route open"],
                    "norms": ["share verified tide reports"],
                    "strategy": "cooperate with the commons",
                },
            },
        )
        assert faction["status"] == "accepted", faction
        assert platform.store.repositories.entities.get_entity(faction["entity_id"])["entity_type"] == "faction"
    finally:
        platform.close()


def _set_world_properties(platform: Platform, **values) -> None:
    with platform.store.transaction():
        properties = dict(platform.store.repositories.metadata.get("world_properties") or {})
        properties.update(values)
        platform.store.repositories.metadata.set("world_properties", properties)


def _start_and_finish(platform: Platform, actor_id: str, behavior_id: str, intent_id: str) -> dict:
    started = _start(platform, actor_id, behavior_id, intent_id)
    assert started["status"] == "accepted", started
    _run_active(platform, actor_id)
    return started


def test_origins_ship_distinct_openings_first_tasks_and_goals(tmp_path: Path) -> None:
    """Identity must be visible in the first minute, not only in starting stats."""

    from sim2.api.service import ControlService

    service = ControlService(create_instance(WORLD, "tidebound-origin-openings", tmp_path / "runtime"))
    try:
        entry = service.player_entry()
        first_tasks = {origin["id"]: origin["first_task"] for origin in entry["origins"]}
        assert set(first_tasks) == {"steward", "forager", "chart_keeper", "ferry_runner"}
        assert all(first_tasks.values())
        assert len(set(first_tasks.values())) == 4
        assert service.enter_player("hero", "chart_keeper", name="塞琳的学徒")["status"] == "accepted"
        agent = service.platform.store.repositories.entities.get_agent("hero")
        assert agent["origin_id"] == "chart_keeper"
        assert agent["origin_id"] in first_tasks
        assert "一条海图路线必须能被别人重复走通" in " ".join(agent["knowledge_seed"])
        opening = [
            event for event in service.platform.store.repositories.events.recent_events(limit=12)
            if event["event_type"] == "origin.chart_keeper"
        ]
        assert len(opening) == 1
        assert opening[0]["summary"] == first_tasks["chart_keeper"]
        view = service.player_view("hero")
        goal_ids = {goal["id"] for goal in view["goals"]}
        assert "chart_keeper_first_stage" in goal_ids
        assert "forager_first_haul" not in goal_ids
        assert "daily_public_decision" in goal_ids
        assert [goal["id"] for goal in view["goals"][:3]] == [
            "daily_public_decision",
            "inspect_tide_gate",
            "chart_keeper_first_stage",
        ]
    finally:
        service.close()


def test_daily_public_decision_is_a_real_tradeoff_and_reopens(tmp_path: Path) -> None:
    """One visible decision per day: the choice must move real world state."""

    from sim2.api.service import ControlService

    service = ControlService(create_instance(WORLD, "tidebound-daily-decision", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "steward", name="试玩者")["status"] == "accepted"
        view = service.player_view("hero")
        goal = next(item for item in view["goals"] if item["id"] == "daily_public_decision")
        assert goal["status"] == "ready"
        assert goal["ready_behavior_refs"] == [
            "behavior:ration_today",
            "behavior:repair_priority_today",
            "behavior:marsh_rest_today",
        ]
        assert view["world_properties"]["fresh_water_reserve"] == 24
        assert view["world_properties"]["public_decision_open"] == 1

        _start_and_finish(platform, "hero", "ration_today", "decision-ration")
        properties = platform.store.repositories.metadata.get("world_properties")
        assert properties["fresh_water_reserve"] == 30
        assert round(properties["public_confidence"], 3) == 0.43
        assert properties["public_decision_open"] == 0
        decision_events = [
            event for event in platform.store.repositories.events.recent_events(limit=40)
            if event["event_type"] == "public_decision.made"
        ]
        assert decision_events[-1]["summary"] == "公社决定今天收紧配给。淡水留住了，但住户之间的抱怨也多了起来。"
        assert decision_events[-1]["decision"] == "rationing"
        assert "daily_public_decision" not in {item["id"] for item in service.player_view("hero")["goals"]}

        with platform.store.transaction():
            platform.store.repositories.metadata.set("world_minute", 1439)
        platform.step()
        reopened = platform.store.repositories.metadata.get("world_properties")
        assert reopened["public_decision_open"] == 1
        assert any(
            event["event_type"] == "public_decision.due"
            for event in platform.store.repositories.events.recent_events(limit=12)
        )

        _start_and_finish(platform, "hero", "repair_priority_today", "decision-repair")
        properties = platform.store.repositories.metadata.get("world_properties")
        assert round(properties["tide_gate_integrity"], 3) == 0.33
        assert properties["fresh_water_reserve"] == 26
        assert properties["public_decision_open"] == 0

        with platform.store.transaction():
            platform.store.repositories.metadata.set("world_minute", 2879)
        platform.step()
        _start_and_finish(platform, "hero", "marsh_rest_today", "decision-rest")
        properties = platform.store.repositories.metadata.get("world_properties")
        assert round(properties["public_confidence"], 3) == 0.48
        assert round(properties["tide_gate_integrity"], 3) == 0.31
        assert properties["public_decision_open"] == 0
    finally:
        service.close()


def test_fallback_chapters_follow_storm_outcomes_without_a_model(tmp_path: Path) -> None:
    """Fixed chapters keep the arc readable with no Story or God calls."""

    from sim2.api.service import ControlService

    service = ControlService(create_instance(WORLD, "tidebound-fallback-chapters", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "forager", name="试玩者")["status"] == "accepted"
        chapter = service.player_view("hero")["chapter"]
        assert chapter["id"] == "storm_watch"
        assert chapter["title"] == "第一章 · 风暴季警戒"

        _set_world_properties(platform, tide_gate_integrity=0.85)
        platform.step()
        chapter = service.player_view("hero")["chapter"]
        assert chapter["id"] == "gate_ready"
        assert chapter["title"] == "第二章 · 潮门已加固"

        for integrity, expected in (
            (0.5, ("storm_breached", "第二章 · 潮门被撕开")),
            (0.85, ("storm_held", "第二章 · 潮门守住了")),
            (0.1, ("storm_flooded", "第二章 · 潮水进了码头")),
        ):
            if integrity == 0.85:
                # Crossing the 80% threshold is its own edge-triggered chapter.
                # Settle it first so the following storm event is the newest.
                _set_world_properties(platform, tide_gate_integrity=0.85)
                platform.step()
            _set_world_properties(
                platform,
                storm_eta_hours=0,
                storm_pressure=1.0,
                tide_gate_integrity=integrity,
                public_confidence=0.5,
            )
            platform.step()
            chapter = service.player_view("hero")["chapter"]
            assert (chapter["id"], chapter["title"]) == expected
            assert chapter["summary"]
    finally:
        service.close()


def test_personal_origin_goal_counts_only_the_owner(tmp_path: Path) -> None:
    """Another resident's harvest must not finish the player's first task."""

    from sim2.api.service import ControlService

    service = ControlService(create_instance(WORLD, "tidebound-personal-goal", tmp_path / "runtime"))
    platform = service.platform
    try:
        assert platform.enter_player("hero", "forager", name="试玩者")["status"] == "accepted"
        with platform.store.transaction():
            platform.store.repositories.events.append_event(
                "resource.gathered", "nia", 5, {"resource_id": "shellfish"},
            )
        goal = next(item for item in service.player_view("hero")["goals"] if item["id"] == "forager_first_haul")
        assert goal["progress"] == {"current": 0, "required": 1}

        with platform.store.transaction():
            platform.store.repositories.events.append_event(
                "resource.gathered", "hero", 6, {"resource_id": "shellfish"},
            )
        assert "forager_first_haul" not in {item["id"] for item in service.player_view("hero")["goals"]}
    finally:
        service.close()
