"""Two world packs express supply conflicts through the same Platform contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform
from sim2.simulation.runner import SimulationRunner

WORLDS = Path(__file__).parents[1] / "worlds"


class CoolingProvider:
    def cooldown_remaining(self) -> float:
        return 1.0

    def complete_json(self, **_kwargs):
        pytest.fail("The scheduler called a cooling provider.")


@pytest.mark.parametrize(
    ("world", "steward", "batch", "defense_property"),
    [
        ("windward_skyfall", "alan", 3, "supply_spare_plates"),
        ("tidebound_commons", "mara", 4, "gate_supports"),
    ],
)
def test_two_unattended_supply_windows_change_the_next_choice(
    tmp_path: Path, world: str, steward: str, batch: int, defense_property: str,
) -> None:
    platform = Platform(create_instance(WORLDS / world, "headless-supply", tmp_path / "runtime"))
    try:
        runner = SimulationRunner(platform, {"story": CoolingProvider()})
        metadata = platform.store.repositories.metadata
        assert runner.run(720).end_minute == 720
        props = metadata.get("world_properties")
        assert props["emergency_stock"] == batch
        assert props["supply_consultation"] == 1
        before_defense = props[defense_property]
        refs = {choice["ref"] for choice in platform.decision_view(steward)["executable_behaviors"]}
        assert "behavior:allocate_emergency_stock_defense" in refs
        assert "behavior:allocate_emergency_stock_relief" in refs

        assert runner.run(250).end_minute == 970
        props = metadata.get("world_properties")
        assert props["supply_consultation"] == 2
        assert props["supply_relief_due"] == 1
        assert props["emergency_stock"] == 0
        assert props[defense_property] > before_defense

        assert runner.run(470).end_minute == 1440
        props = metadata.get("world_properties")
        assert props["emergency_stock"] == batch
        assert props["supply_consultation"] == 1
        refs = {choice["ref"] for choice in platform.decision_view(steward)["executable_behaviors"]}
        assert "behavior:allocate_emergency_stock_defense" not in refs
        assert "behavior:allocate_emergency_stock_relief" in refs

        report = runner.run(250)
        assert report.end_minute == 1690
        props = metadata.get("world_properties")
        assert props["supply_consultation"] == 3
        assert props["supply_relief_due"] == 0
        assert props["emergency_stock"] == 0
        if world == "windward_skyfall":
            depot = platform.store.repositories.entities.get_entity("outpost_copper_cache")
            assert depot["initial_state"]["quantity"] == batch
        assert not any(row.get("role") == "player" for row in platform.store.repositories.entities.list_agents())
        types = report.event_types
        assert (types["supply.claims_opened"], types["supply.reserved"], types["supply.shared"]) == (2, 1, 1)
        tasks = platform.ready_evolution_tasks()
        sources = {event["event_type"] for task in tasks if task["role"] == "story" for event in task.get("source_events", [])}
        assert {"supply.reserved", "supply.shared"} <= sources
    finally:
        platform.close()


def test_relief_water_waits_when_the_public_reserve_is_full(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLDS / "tidebound_commons", "relief-water-overflow", tmp_path / "runtime"))
    try:
        metadata = platform.store.repositories.metadata
        entities = platform.store.repositories.entities
        with platform.store.transaction():
            metadata.set("world_minute", 719)
            water = entities.get_entity("fresh_water")
            water["initial_state"]["quantity"] = 30
            entities.put_entity("fresh_water", "resource", water["name"], {"initial_state": water["initial_state"]})
            entities.sync_world_property_from_resource("fresh_water", 30)
        platform.step()
        result = platform.submit_behavior(BehaviorIntent(
            actor_id="mara", behavior_ref="behavior:allocate_emergency_stock_relief", target={}, parameters={},
            reason="Send the batch to residents.", confidence=1.0, intent_id="water-relief-test",
        ))
        assert result["status"] == "accepted", result
        for _ in range(22):
            platform.step()
        props = metadata.get("world_properties")
        assert (props["emergency_stock"], props["relief_water_waiting"], props["fresh_water_reserve"]) == (0, 4, 30)

        with platform.store.transaction():
            water = entities.get_entity("fresh_water")
            water["initial_state"]["quantity"] = 29
            entities.put_entity("fresh_water", "resource", water["name"], {"initial_state": water["initial_state"]})
            entities.sync_world_property_from_resource("fresh_water", 29)
        platform.step()
        props = metadata.get("world_properties")
        assert (props["relief_water_waiting"], props["fresh_water_reserve"]) == (3, 30)
    finally:
        platform.close()


def test_opted_in_world_still_waits_for_an_instant_dialogue_reply(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLDS / "windward_skyfall", "same-minute", tmp_path / "runtime"))
    try:
        assert platform.submit_dialogue("alan", "yanqiu", "船体", "这批铜料该怎么分？")["status"] == "accepted"
        report = SimulationRunner(platform, {"agent_decision": CoolingProvider()}).run(1)
        assert report.end_minute == 0
        assert report.stop_reason == "provider_cooldown:agent_decision"
    finally:
        platform.close()


def test_two_actors_cannot_spend_the_same_emergency_batch(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLDS / "windward_skyfall", "competing-allocations", tmp_path / "runtime"))
    try:
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        metadata = platform.store.repositories.metadata
        with platform.store.transaction():
            metadata.set("world_minute", 719)
        platform.step()
        for actor, branch in (("alan", "defense"), ("hero", "relief")):
            result = platform.submit_behavior(BehaviorIntent(
                actor_id=actor, behavior_ref=f"behavior:allocate_emergency_stock_{branch}",
                target={}, parameters={}, reason="Allocate the same batch.", confidence=1.0,
                intent_id=f"test-allocation-{branch}",
            ))
            assert result["status"] == "accepted", result

        for _ in range(22):
            platform.step()
        counts = platform.store.repositories.events.event_type_counts()
        assert counts.get("supply.reserved", 0) + counts.get("supply.shared", 0) == 1
        assert metadata.get("world_properties")["emergency_stock"] == 0
    finally:
        platform.close()
