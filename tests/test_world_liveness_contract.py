"""Every shipped world must be able to keep evolving after its characters stall.

An evolution role is normally woken by events its characters produce, so a
world that has run out of productive behavior also stops producing the events
that would wake God.  The derived ``world.stagnation`` signal is the safety net
and incapacitation must not be permanent.  These tests pin that contract for
each package under ``worlds/`` instead of for one fixture world.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sim2.agents.engine import AgentEngine
from sim2.agent_view.tool_registry import ROLE_TOOLS
from sim2.bootstrap.service import create_instance
from sim2.evolution.config import normalize_evolution_config
from sim2.platform.runtime import Platform
from sim2.world.lifecycle import recovery_rule

WORLDS = Path(__file__).parents[1] / "worlds"
SHIPPED = sorted(path for path in WORLDS.iterdir() if (path / "manifest.yaml").is_file())
STAGNATION_SUMMARY = "No character gained a new executable option for 2 days (last growth at minute 1)."


class WaitProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.calls.append(context)
        return {"intent_type": "wait", "reason": "The evidence needs no new content."}


def _yaml(world: Path, name: str) -> dict:
    return yaml.safe_load((world / name).read_text(encoding="utf-8")) or {}


def _consumer_event_types(evolution: dict, role: str) -> set[str]:
    consumer = (evolution.get("event_wake_policy") or {}).get("consumers", {}).get(role, {})
    events = set(consumer.get("direct_event_types") or [])
    for group in consumer.get("event_groups") or []:
        events.update(group.get("event_types") or [])
    return events


def test_shipped_worlds_are_discovered() -> None:
    assert {"starter_village", "aster_relay"} <= {path.name for path in SHIPPED}


@pytest.mark.parametrize("world", SHIPPED, ids=lambda path: path.name)
def test_world_configures_stagnation_and_wakes_god_on_it(world: Path) -> None:
    evolution = normalize_evolution_config(_yaml(world, "evolution.yaml"))
    assert evolution.get("stagnation", {}).get("window_minutes", 0) > 0, f"{world.name} has no stagnation window"
    assert "world.stagnation" in _consumer_event_types(evolution, "god"), f"{world.name} never wakes God on stagnation"


@pytest.mark.parametrize("world", SHIPPED, ids=lambda path: path.name)
def test_wake_tools_are_offered_to_the_woken_role(world: Path) -> None:
    evolution = normalize_evolution_config(_yaml(world, "evolution.yaml"))
    consumers = (evolution.get("event_wake_policy") or {}).get("consumers", {})
    for role, consumer in consumers.items():
        for group in consumer.get("event_groups") or []:
            allowed = set((group.get("agent_contract") or {}).get("allowed_tools") or [])
            assert allowed <= set(ROLE_TOOLS.get(role, [])), (
                f"{world.name} wake {group.get('id')} offers {role} unknown tools {sorted(allowed - set(ROLE_TOOLS.get(role, [])))}"
            )


@pytest.mark.parametrize("world", SHIPPED, ids=lambda path: path.name)
def test_world_ends_incapacitation_on_its_own(world: Path) -> None:
    rules = _yaml(world, "rules.yaml")
    assert recovery_rule(rules) is not None, f"{world.name} leaves unconscious characters unconscious forever"


@pytest.mark.parametrize("world", SHIPPED, ids=lambda path: path.name)
def test_a_stagnation_signal_reaches_god_with_its_evidence(world: Path, tmp_path: Path) -> None:
    platform = Platform(create_instance(world, f"liveness-{world.name}", tmp_path / "runtime"))
    try:
        event_id = platform.store.repositories.events.append_event(
            "world.stagnation", None, 1,
            {"summary": STAGNATION_SUMMARY, "window_minutes": 1440, "stagnant_windows": 1, "stagnant_minutes": 1440},
        )
        platform.wakes.configured(event_id, "world.stagnation", None, 1)
        tasks = [
            item for item in platform.ready_evolution_tasks()
            if item["role"] == "god" and any(source.get("event_type") == "world.stagnation" for source in item.get("source_events") or [])
        ]
        assert len(tasks) == 1, f"{world.name}: expected one God task for the stagnation event"
        provider = WaitProvider()
        turn = AgentEngine(platform, provider).run(
            role="god", actor_id="god", task_type=tasks[0]["task_type"], source_events=tasks[0]["source_events"],
        )
        assert turn.result["status"] == "wait"
        assert "<triggering_evidence>" in provider.calls[0]
        assert STAGNATION_SUMMARY in provider.calls[0]
    finally:
        platform.close()
