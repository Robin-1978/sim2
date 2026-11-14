from __future__ import annotations

from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform


WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"


def test_platform_is_a_facade_over_explicit_services(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "platform-composition", tmp_path / "runtime"))
    try:
        assert Platform.__bases__ == (object,)
        assert set(platform._services) >= {
            "api",
            "intent",
            "execution",
            "tick",
            "evolution",
            "activity",
            "definitions",
            "entities",
            "story",
            "world_changes",
        }
        assert platform.status()["world_minute"] == 0
        assert platform.tick_snapshot()["world_minute"] == 0
        assert platform._services["intent"].submit_behavior.__self__ is platform._services["intent"]
    finally:
        platform.close()


def test_new_behavior_assignment_returns_as_actor_work(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "behavior-assignment-feedback", tmp_path / "runtime"))
    try:
        result = platform.submit_definition_proposal(
            "god",
            {
                "definition_type": "action",
                "name": "Record Assignment Result",
                "definition": {
                    "duration": {"minutes": 1},
                    "effects": [{"type": "world_property_delta", "property": "fog_pressure", "delta": 0.01}],
                    "success": {"result": "assignment recorded"},
                    "failure": {"result": "assignment failed"},
                },
                "followup_behavior": {
                    "name": "Record Assigned Work",
                    "assign_to": ["梅岚"],
                    "definition": {
                        "goal": "Record the verified assignment result before the next review.",
                        "nodes": [{"type": "action", "action_id": "Record Assignment Result"}],
                        "success_results": ["assignment recorded"],
                        "failure_results": ["assignment failed"],
                    },
                },
            },
        )
        assert result["status"] == "accepted", result
        agent = platform.store.repositories.entities.get_agent("mei_lan")
        assignments = [item for item in agent.get("behavior_assignments", []) if item.get("status") == "assigned"]
        assert assignments and assignments[-1]["goal"] == "Record the verified assignment result before the next review."
        tasks = [
            item for item in platform.ready_evolution_tasks()
            if item.get("actor_id") == "mei_lan" and str(item.get("task_type", "")).startswith("behavior_assigned:")
        ]
        assert tasks
        view = platform.decision_view("mei_lan")
        assert any("Record the verified assignment result" in str(item) for item in view.get("behavior_assignments", []))
    finally:
        platform.close()
