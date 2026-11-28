from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from sim2.api.catalog import bundled_world_catalog
from sim2.api.runtime_supervisor import RuntimeSupervisor
from sim2.api.service import ControlService
from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.prompting.renderer import render_context
from worlds import world_path


WORLD = world_path("gate_cities")


def choices(service: ControlService) -> set[str]:
    return {action["ref"] for action in service.player_view("hero")["actions"]}


def choose(service: ControlService, behavior: str, **parameters) -> dict:
    ref = f"behavior:{behavior}"
    assert ref in choices(service)
    result = service.player_act({"player_id": "hero", "behavior_ref": ref, "parameters": parameters})
    assert result["status"] == "accepted", result
    service.player_wait("hero", minutes=60)
    return service.player_view("hero")


def test_gate_cities_appears_in_the_normal_world_picker() -> None:
    card = next(card for card in bundled_world_catalog() if card["id"] == "gate_cities")
    assert card["name"] == "诸城之门"
    assert card["locale"] == "zh-CN"


def test_foreground_wait_does_not_load_models_or_open_a_fallback_plan(tmp_path: Path) -> None:
    def unused_provider(name):
        pytest.fail(f"Manual time should not load the configured provider {name}")

    with closing(ControlService(create_instance(WORLD, "gate-manual", tmp_path))) as service:
        runtime = RuntimeSupervisor(service, provider_factory=unused_provider)
        result = runtime.advance_manual(91, provider_name="remote")
        assert result["status"] == "accepted", result
        assert result["report"]["end_minute"] == 91
        assert result["report"]["bt_decisions"] > 0
        assert not service.platform.store.repositories.metadata.get("season_plan")


@pytest.mark.parametrize(("lane", "target", "stock", "project", "property_id", "other"), [
    ("water", "low_city", "low_water", "accept_water_compact", "water_compact", "parts"),
    ("parts", "old_station", "yard_parts", "reinforce_dispatch_gate", "gate_reinforced", "water"),
])
def test_first_freight_changes_the_next_project_and_workers_keep_delivering(
    tmp_path: Path, lane: str, target: str, stock: str, project: str, property_id: str, other: str,
) -> None:
    db = create_instance(WORLD, f"gate-{lane}", tmp_path)
    with closing(ControlService(db)) as service:
        assert service.enter_player("hero", "dispatcher", "林晚")["status"] == "accepted"
        assert {"behavior:prioritize_water", "behavior:prioritize_parts"} <= choices(service)
        assert "behavior:handover_dispatch" not in choices(service)
        assert service.player_view("hero")["world_properties"]["gate_slots"] == 1

        choose(service, f"prioritize_{lane}")
        assert f"behavior:ship_{other}" not in choices(service)
        loaded = choose(service, f"ship_{lane}")
        assert loaded["world_properties"]["gate_slots"] == 0
        assert loaded["world_properties"][stock] == 0
        assert f"behavior:ship_{lane}" not in choices(service)

        delivered = choose(service, f"finish_{lane}")
        assert delivered["location"]["id"] == target
        assert delivered["world_properties"][stock] == 2
        outcome = choose(service, project)
        assert outcome["world_properties"][property_id] == 1
        assert outcome["world_properties"][stock] == (1 if lane == "water" else 0)
        assert outcome["world_properties"]["water_compact" if lane == "parts" else "gate_reinforced"] == 0
        choose(service, "travel", location_id="high_gate")
        choose(service, "handover_dispatch")
        queued = service.platform.store.repositories.evolution.ready_evolution_tasks()
        assert sum(task["role"] == "story" for task in queued) == 1

    with closing(ControlService(db)) as reloaded:
        report = reloaded.platform.run(70, providers={})
        assert report.stop_reason is None
        store = reloaded.platform.store
        assert store.connection.execute("SELECT COUNT(*) FROM llm_call").fetchone()[0] == 0
        properties = reloaded.player_view("hero")["world_properties"]
        assert properties["gate_managed"] == 1 and properties[property_id] == 1
        events = store.repositories.events.recent_events_by_types(
            ["gate.water_delivered", "gate.parts_delivered"], limit=20,
        )
        residents = [event for event in events if event["actor_id"].startswith("porter_")]
        assert len(residents) >= (3 if lane == "parts" else 2)
        assert any(event["event_type"] == f"gate.{lane}_delivered" and event["actor_id"] == "hero" for event in events)
        first_delivery = next(event for event in events if event["actor_id"] == "hero")
        story = reloaded.platform.view.build_decision_view(
            "story", role="story", source_event_id=first_delivery["event_id"],
        )
        assert story["validated_source"]["summary"]
        assert {"gate.priority_set", "gate.handover", "gate.water_compact" if lane == "water" else "gate.reinforced"} <= {
            event["event_type"] for event in story["recent_history"]
        }
        prompt = render_context(story, "Continue the current freight story")
        assert "班组接管（1 已接管） = 1" in prompt
        assert "已完成货运趟数" in prompt
        changes = [event for event in store.repositories.events.recent_events_by_types(["resource.changed"], limit=100)
                   if event.get("resource_id") == "gate_slots"]
        assert changes and all(event["after"] >= 0 for event in changes)
        assert max(event["after"] for event in changes) == (2 if lane == "parts" else 1)


def test_dispatcher_can_redirect_the_workers_when_water_is_low(tmp_path: Path) -> None:
    with closing(ControlService(create_instance(WORLD, "gate-emergency", tmp_path))) as service:
        assert service.enter_player("hero", "dispatcher", "林晚")["status"] == "accepted"
        for behavior in ("prioritize_parts", "ship_parts", "finish_parts", "reinforce_dispatch_gate"):
            choose(service, behavior)
        choose(service, "travel", location_id="high_gate")
        choose(service, "handover_dispatch")
        platform = service.platform
        platform.run(27, providers={})
        before = service.player_view("hero")["world_properties"]
        assert before["completed_deliveries"] == 3 and before["low_water"] == 2
        order_minute = platform.store.repositories.metadata.get("world_minute")
        result = platform.submit_behavior(BehaviorIntent(
            actor_id="warden",
            behavior_ref="behavior:activate_emergency_water",
            parameters={},
            target={},
            reason="低城的储水触及应急线",
            confidence=1.0,
            intent_id="gate-emergency-water",
        ))
        assert result["status"] == "accepted", result
        platform.run(30, providers={})
        after = service.player_view("hero")["world_properties"]
        assert after["dispatch_priority"] == 1
        water = platform.store.repositories.events.recent_events_by_types(["gate.water_delivered"], limit=3)
        assert any(event["world_minute"] > order_minute and event["actor_id"].startswith("porter_") for event in water)
