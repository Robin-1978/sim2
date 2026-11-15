from __future__ import annotations

from pathlib import Path

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
    assert description["counts"]["locations"] == 5
    assert description["counts"]["recipes"] == 2

    platform = Platform(create_instance(WORLD, "tidebound-balance", tmp_path / "runtime"))
    try:
        assert platform.store.repositories.metadata.get("world_properties")["tide_gate_integrity"] == 0.28
        assert "hydration" in platform.store.repositories.metadata.get("attributes")[2]["id"]
        assert all(platform.decision_view(agent["id"])["executable_behaviors"] for agent in platform.store.repositories.entities.list_agents())
        for resource_id, cap in (("fresh_water", 20), ("shellfish", 24), ("driftwood", 16)):
            entity = platform.store.repositories.entities.get_entity(resource_id)
            assert float(entity["initial_state"]["quantity"]) <= cap
        # The first choice is meaningful: harvesting shellfish creates a real
        # carried input, then preservation turns it into three rations at a
        # time-and-distance cost. Nothing is minted by the world state.
        accepted = _start(platform, "ivo", "gather_shellfish", "tide-gather-1")
        assert accepted["status"] == "accepted", accepted
        _run_active(platform, "ivo")
        ivo = platform.store.repositories.entities.get_agent("ivo")
        assert ivo["inventory"].get("shellfish", 0) == 2
        shellfish_before = platform.store.repositories.entities.get_entity("shellfish")["initial_state"]["quantity"]
        assert shellfish_before == 22

        accepted = _start(platform, "ivo", "craft_rations", "tide-craft-1")
        assert accepted["status"] == "accepted", accepted
        spent = _run_active(platform, "ivo")
        ivo = platform.store.repositories.entities.get_agent("ivo")
        assert spent >= 100  # weighted return to the workshop is part of the choice
        assert ivo["inventory"].get("food_ration", 0) == 4
        assert ivo["inventory"].get("shellfish", 0) == 0
        assert ivo["inventory"].get("fresh_water", 0) == 0

        # Regeneration is bounded and does not erase the prior cost.
        for _ in range(720):
            platform.step()
        shellfish = platform.store.repositories.entities.get_entity("shellfish")
        assert 22 < shellfish["initial_state"]["quantity"] <= 24
        assert platform.store.repositories.metadata.get("world_properties")["tide_gate_integrity"] == 0.28
    finally:
        platform.close()


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
        assert "eating a ration is a separate behavior" in behaviors["behavior:craft_rations"]["purpose"]
        assert any("only collects or produces an item is preparation" in rule for rule in view["platform_rules"])

        agent["needs"]["satiety"] = 60
        platform.store.repositories.entities.update_agent("mara", agent)
        behaviors = {item["ref"]: item for item in platform.decision_view("mara")["executable_behaviors"]}
        assert behaviors["behavior:gather_shellfish"]["drive_tags"] == ["purpose"]
        assert behaviors["behavior:gather_shellfish"]["supports_drive_tags"] == ["satiety"]
        assert "does not restore satiety" in behaviors["behavior:gather_shellfish"]["purpose"]
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
        assert "Inspect Tide Gate" in goals["inspect_tide_gate"]["ready_behaviors"]
        assert goals["repair_tide_gate"]["status"] == "needs_support"
        assert "Salvage Driftwood" in goals["repair_tide_gate"]["support_behaviors"]
        assert any("repair kit" in item.lower() for item in goals["repair_tide_gate"]["missing_requirements"])
        selene = platform.decision_view("selene")
        chart = next(item for item in selene["progression_goals"] if item["id"] == "complete_drowned_chart")
        assert chart["status"] == "ready"
        assert "Research Drowned Chart" in chart["ready_behaviors"]
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
