from __future__ import annotations

from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform


WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"


def test_runtime_events_have_one_platform_owner(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "event-owner", tmp_path / "runtime"))
    try:
        service = platform._services["events"]
        assert platform._service_context.method_owners["append"] is service
        event_id = service.append("audit.fact", "luo_shan", 3, {"summary": "A fact was committed."})
        event = platform.store.events_by_ids([event_id])[0]
        assert event["world_instance_id"] == platform.store.get_meta("world_id")
        assert event["world_revision"] == int(platform.store.get_meta("world_revision", 0) or 0)
    finally:
        platform.close()


def test_event_service_preserves_explicit_causal_metadata(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "event-causality", tmp_path / "runtime"))
    try:
        event_id = platform._services["events"].append(
            "audit.followup",
            "mei_lan",
            7,
            {"summary": "A follow-up was committed."},
            causation_id="intent-7",
            correlation_id="activity-7",
        )
        event = platform.store.events_by_ids([event_id])[0]
        assert event["causation_id"] == "intent-7"
        assert event["correlation_id"] == "activity-7"
    finally:
        platform.close()


def test_information_observation_does_not_become_recovery_without_explicit_flag(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "observation-is-not-recovery", tmp_path / "runtime"))
    try:
        agent = platform.store.get_agent("luo_shan")
        action = {
            "id": "inspect_without_recovery_flag",
            "information": {
                "event_type": "observation.object_inspected",
                "summary": "The object was inspected.",
                "fact": "The object is present.",
                "result": "object_inspected",
            },
        }
        with platform.store.transaction():
            platform._services["activity"]._record_information_result(
                agent,
                action,
                {"object_id": "caravan_ledger"},
                0,
            )
        event_types = [item["event_type"] for item in platform.store.recent_events(limit=10)]
        assert "observation.object_inspected" in event_types
        assert "object.recovered" not in event_types
    finally:
        platform.close()

