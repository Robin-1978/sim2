from __future__ import annotations

from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform


def _town(tmp_path: Path, instance: str) -> Platform:
    return Platform(create_instance(Path(__file__).parents[1] / "worlds" / "three_lanterns_town", instance, tmp_path / "runtime"))


def _step(platform: Platform, minutes: int) -> None:
    for _ in range(int(minutes)):
        platform.step()


def _active_institution(platform: Platform, name: str = "Test Ledger") -> None:
    with platform.store.transaction():
        for minute in (1, 2, 3):
            platform.store.append_event("cistern.reviewed", "mei_lan", minute, {"behavior_id": "inspect_cistern"})
    proposal = platform.submit_institution_proposal("god", {
        "name": name,
        "purpose": "Record verified public work before shared stock is allocated.",
        "evidence_sources": [{"event_types": ["cistern.reviewed"]}],
        "minimum_evidence": 3,
        "policy": {"allocation": {"minimum_per_subject": 1, "contribution_share": 0}},
    })
    assert proposal["status"] == "accepted"
    assert platform.activate_institution("god", name, "Three verified reviews.")["status"] == "accepted"


def test_gathering_emits_a_story_source_and_keeps_the_resource_result(tmp_path: Path):
    platform = _town(tmp_path, "gathering-source")
    try:
        started = platform.submit_behavior(BehaviorIntent(
            "luo_shan", "behavior:gather_reeds", {}, {},
            "Collect the configured reed resource.", 0.9, "gather-reeds",
        ))
        assert started["status"] == "accepted"
        _step(platform, started["remaining_minutes"])
        gathered = [item for item in platform.store.recent_events(limit=200) if item["event_type"] == "resource.gathered"]
        assert gathered
        assert gathered[-1]["resource_id"] == "reed_bundle"
        assert platform.store.get_agent("luo_shan")["inventory"]["reed_bundle"] == 2.0
        story_tasks = [item for item in platform.ready_evolution_tasks() if item["role"] == "story" and item["source_event_type"] == "resource.gathered"]
        assert story_tasks
    finally:
        platform.close()


def test_failed_allocation_is_hidden_until_stock_recovers(tmp_path: Path):
    platform = _town(tmp_path, "allocation-recovery-guard")
    try:
        _active_institution(platform)
        definition = platform.submit_definition_proposal("god", {
            "definition_type": "behavior",
            "name": "Attempt Test Allocation",
            "assign_to": ["梅岚"],
            "definition": {
                "summary": "Apply the active ledger once.",
                "nodes": [{"type": "action", "action_id": "Settle Institution Allocation", "parameters": {
                    "institution": "Test Ledger", "pool_entity": "蓄水", "item": "蓄水", "period": "week",
                }}],
                "success_results": ["allocation_completed"],
                "failure_results": ["allocation_failed"],
            },
        })
        assert definition["status"] == "accepted"
        pool = platform.store.get_entity("cistern_water")
        pool["initial_state"]["quantity"] = 0
        platform.store.put_entity(pool["id"], pool["entity_type"], pool["name"], {key: value for key, value in pool.items() if key not in {"id", "entity_type", "name"}})
        behavior_id = next(item["id"] for item in platform.store.get_definitions("bt_def") if item["agent_label"] == "Attempt Test Allocation")
        started = platform.submit_behavior(BehaviorIntent(
            "mei_lan", f"behavior:{behavior_id}", {}, {}, "Attempt the allocation.", 0.9, "allocation-attempt",
        ))
        assert started["status"] == "accepted"
        _step(platform, started["remaining_minutes"])
        state = platform.store.get_agent("mei_lan")["initial_state"]
        assert "minimum allocation" in state["blocked_behaviors"][behavior_id]["result"]
        assert not any(item["ref"] == f"behavior:{behavior_id}" for item in platform.decision_view("mei_lan")["executable_behaviors"])
        pool = platform.store.get_entity("cistern_water")
        pool["initial_state"]["quantity"] = 48
        platform.store.put_entity(pool["id"], pool["entity_type"], pool["name"], {key: value for key, value in pool.items() if key not in {"id", "entity_type", "name"}})
        platform.store.append_event("resource.regenerated", None, int(platform.store.get_meta("world_minute")) + 1, {"resource_id": "cistern_water", "amount": 48})
        assert any(item["ref"] == f"behavior:{behavior_id}" for item in platform.decision_view("mei_lan")["executable_behaviors"])
    finally:
        platform.close()
