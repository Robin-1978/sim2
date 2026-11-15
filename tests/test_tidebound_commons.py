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


def test_tidebound_research_can_expand_the_map_through_god_route_proposals(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "tidebound-expansion", tmp_path / "runtime"))
    try:
        created = platform.submit_entity_proposal("god", {
            "entity_type": "location",
            "name": "Outer Shelf",
            "description": "A newly exposed shelf beyond the drowned observatory.",
            "state": {
                "routes": ["drowned_observatory"],
                "tags": ["exploration", "frontier"],
                "public": True,
                "safe": False,
                "map_position": {"x": 0, "y": 3},
            },
        })
        assert created["status"] == "accepted", created
        linked = platform.submit_world_change("god", {
            "change_type": "route",
            "summary": "The verified chart supports a weighted route to the Outer Shelf.",
            "route": {"from": "Drowned Observatory", "to": "Outer Shelf", "distance": 4},
            "expected_effects": ["A reciprocal route is registered."],
        })
        assert linked["status"] == "accepted", linked
        observatory = platform.store.repositories.entities.get_entity("drowned_observatory")
        shelf = platform.store.repositories.entities.get_entity(created["entity_id"])
        assert created["entity_id"] in observatory["routes"]
        assert "drowned_observatory" in shelf["routes"]
        assert float(observatory["route_distances"][created["entity_id"]]) == 4.0
        assert any(event["event_type"] == "world.change.route" for event in platform.store.repositories.events.recent_events(limit=20))
    finally:
        platform.close()
