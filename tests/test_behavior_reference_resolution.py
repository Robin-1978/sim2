from __future__ import annotations

import importlib.util
from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform


WORLD = Path(__file__).parents[1] / "worlds" / "starter_village"


def test_visible_location_name_is_normalized_before_action_execution(tmp_path: Path):
    """A model-facing target name and the SQLite entity ID are one location."""

    platform = Platform(create_instance(WORLD, "visible-location", tmp_path / "runtime"))
    try:
        actor = platform.store.repositories.entities.get_agent("liang_he")
        assert actor["initial_state"]["location_id"] == "granary"
        before = actor["inventory"].get("grain", 0)

        result = platform.submit_behavior(
            BehaviorIntent(
                actor_id="liang_he",
                behavior_ref="behavior:gather_resource",
                target={"resource": "谷物", "location": "粮仓"},
                parameters={},
                reason="Collect grain at the current visible location.",
                confidence=0.8,
                intent_id="visible-location-gather",
            )
        )

        assert result["status"] == "accepted"
        row = platform.store.connection.execute(
            "SELECT data_json FROM bt_instance WHERE execution_id=?",
            ("visible-location-gather",),
        ).fetchone()
        assert '"location_id":"granary"' in row["data_json"]

        for _ in range(result["remaining_minutes"]):
            platform.step()

        completed = platform.store.connection.execute(
            "SELECT 1 FROM event_log WHERE event_type='behavior.completed' "
            "AND json_extract(data_json, '$.execution_id')=?",
            ("visible-location-gather",),
        ).fetchone()
        assert completed is not None
        assert platform.store.repositories.entities.get_agent("liang_he")["inventory"]["grain"] == before + 2
    finally:
        platform.close()


def test_deterministic_long_run_provider_skips_choices_requiring_semantic_input():
    script = Path(__file__).parents[1] / "scripts" / "run_long_simulation.py"
    spec = importlib.util.spec_from_file_location("sim2_run_long_simulation", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    DeterministicProvider = module.DeterministicProvider
    provider = DeterministicProvider()
    context = """<executable_behaviors>
      <behavior>
        parameters: message (required string): Actual words.; topic (required string): Subject.
        choice: Exchange information.
      </behavior>
      <behavior>
        parameters: none
        choice: Recover at a safe location.
      </behavior>
    </executable_behaviors>"""

    response = provider.complete_json(system="Decision Agent", context=context)

    assert response["choice"] == "Recover at a safe location."


def test_remote_fixed_object_is_not_offered_or_executed(tmp_path: Path):
    platform = Platform(create_instance(WORLD, "object-location", tmp_path / "runtime"))
    try:
        actor = platform.store.repositories.entities.get_agent("su_wu")
        actor["initial_state"]["location_id"] = "village_square"
        platform.store.repositories.entities.update_agent("su_wu", actor)
        platform.store.connection.commit()

        offered = {item["ref"] for item in platform.decision_view("su_wu")["executable_behaviors"]}
        assert "behavior:inspect_object" not in offered
        view = platform.decision_view("su_wu")

        rejected = platform.submit_behavior(
            BehaviorIntent(
                actor_id="su_wu",
                behavior_ref="behavior:inspect_object",
                target={"object": "旧契约石板"},
                parameters={},
                reason="Inspect the tablet from the village square.",
                confidence=0.8,
                intent_id="remote-object-inspection",
                observed_world_revision=view["world_revision"],
                observed_agent_revision=view["agent_revision"],
            )
        )
        assert rejected["status"] == "rejected"
        assert rejected["failed_check"] == "behavior_unavailable"

        actor = platform.store.repositories.entities.get_agent("su_wu")
        actor["initial_state"]["location_id"] = "old_well"
        platform.store.repositories.entities.update_agent("su_wu", actor)
        platform.store.connection.commit()
        offered = {item["ref"] for item in platform.decision_view("su_wu")["executable_behaviors"]}
        assert "behavior:inspect_object" in offered
    finally:
        platform.close()
