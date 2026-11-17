from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform

WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
ACTIVITY_TABLES = ("intent", "bt_instance", "event_log", "agent_state")


def intent(actor: str, ref: str, *, intent_id: str, parameters: dict[str, Any] | None = None, target: dict[str, Any] | None = None, **extra: Any) -> BehaviorIntent:
    return BehaviorIntent(actor, ref, target or {}, parameters or {}, "Because the view lists it.", 0.8, intent_id, **extra)


def submit(platform: Platform, *args: Any, **kwargs: Any) -> dict[str, Any]:
    return platform.submit_behavior(intent(*args, **kwargs))


def stored_intent(platform: Platform, intent_id: str) -> dict[str, Any]:
    row = platform.store.connection.execute("SELECT status,data_json,result_json FROM intent WHERE intent_id=?", (intent_id,)).fetchone()
    return {"status": row["status"], "data": json.loads(row["data_json"]), "result": json.loads(row["result_json"])}


def set_state(platform: Platform, agent_id: str, **changes: Any) -> None:
    with platform.store.transaction():
        agent = platform.store.repositories.entities.get_agent(agent_id)
        agent["initial_state"] = {**agent["initial_state"], **changes}
        platform.store.repositories.entities.update_agent(agent_id, agent)


def register_visit_behavior(platform: Platform, agent_id: str, **fields: Any) -> None:
    """A one-step travel behavior that the character may use; extra fields override its definition."""

    definition = {
        "id": "visit",
        "agent_label": "Visit",
        "summary": "Visit a place.",
        "entry_conditions": ["actor.alive"],
        "nodes": [{"type": "action", "action_id": "travel_to", "parameters": {"location_id": "context.location_id"}}],
        "success_results": ["arrived"],
        "failure_results": ["blocked"],
        "definition_version": 1,
        **fields,
    }
    with platform.store.transaction():
        platform.store.repositories.entities.put_definition("bt_def", "visit", definition)
        agent = platform.store.repositories.entities.get_agent(agent_id)
        agent["unlocked_bt_ids"] = [*agent.get("unlocked_bt_ids", []), "visit"]
        platform.store.repositories.entities.update_agent(agent_id, agent)


def snapshot(platform: Platform) -> dict[str, list[tuple[Any, ...]]]:
    connection = platform.store.connection
    return {
        table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()]
        for table in ACTIVITY_TABLES
    }


@pytest.fixture
def platform(tmp_path: Path):
    instance = Platform(create_instance(WORLD, "behavior-intent", tmp_path / "runtime"))
    try:
        yield instance
    finally:
        instance.close()


def test_accepted_behavior_opens_its_activity_in_one_commit(platform: Platform) -> None:
    result = submit(platform, "mei_lan", "behavior:inspect_cistern", intent_id="inspect-1")

    assert result["status"] == "accepted"
    assert result["execution_id"] == "inspect-1"
    assert stored_intent(platform, "inspect-1")["status"] == "accepted"
    instance = platform.store.connection.execute("SELECT behavior_id,status FROM bt_instance WHERE execution_id='inspect-1'").fetchone()
    assert (instance["behavior_id"], instance["status"]) == ("inspect_cistern", "executing")
    started = platform.store.connection.execute("SELECT data_json FROM event_log WHERE event_type='behavior.started'").fetchall()
    assert [json.loads(row["data_json"])["intent_id"] for row in started] == ["inspect-1"]
    state = platform.store.repositories.entities.get_agent("mei_lan")["initial_state"]
    assert (state["status"], state["active_behavior_id"], state["active_bt_instance"]) == ("executing", "inspect_cistern", "inspect-1")
    assert state["remaining_minutes"] == result["remaining_minutes"] > 0


def test_resubmitting_an_intent_returns_the_stored_result_without_a_second_activity(platform: Platform) -> None:
    first = submit(platform, "mei_lan", "behavior:inspect_cistern", intent_id="inspect-once")
    second = submit(platform, "mei_lan", "behavior:inspect_cistern", intent_id="inspect-once")

    assert second == first
    assert platform.store.connection.execute("SELECT COUNT(*) FROM bt_instance").fetchone()[0] == 1


def test_failure_while_opening_the_activity_rolls_everything_back(platform: Platform, monkeypatch: pytest.MonkeyPatch) -> None:
    before = snapshot(platform)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("cannot compile")

    monkeypatch.setattr(platform._services["execution"], "compile_behavior_actions", fail)
    with pytest.raises(RuntimeError, match="cannot compile"):
        submit(platform, "mei_lan", "behavior:inspect_cistern", intent_id="inspect-fails")

    assert snapshot(platform) == before


def test_admission_checks_run_in_a_fixed_order(platform: Platform) -> None:
    set_state(platform, "mei_lan", status="paused", blocked_behaviors={"inspect_cistern": {"result": "failed", "failed_at": 10**6}})

    unregistered = submit(platform, "mei_lan", "behavior:no_such_behavior", intent_id="order-1")
    locked = submit(platform, "mei_lan", "behavior:gather_reeds", intent_id="order-2")
    unknown_parameter = submit(platform, "mei_lan", "behavior:inspect_cistern", intent_id="order-3", parameters={"bogus": 1})
    busy = submit(platform, "mei_lan", "behavior:inspect_cistern", intent_id="order-4", parameters={})
    set_state(platform, "mei_lan", status="idle")
    blocked = submit(platform, "mei_lan", "behavior:inspect_cistern", intent_id="order-5", parameters={})

    assert [item["failed_check"] for item in (unregistered, locked, unknown_parameter, busy, blocked)] == [
        "behavior_ref",
        "capability",
        "parameters",
        "active_behavior",
        "failed_behavior_blocked",
    ]


def test_unknown_actor_is_rejected_without_a_record(platform: Platform) -> None:
    result = submit(platform, "nobody", "behavior:inspect_cistern", intent_id="ghost-1")

    assert result["failed_check"] == "actor"
    assert platform.store.connection.execute("SELECT COUNT(*) FROM intent WHERE intent_id='ghost-1'").fetchone()[0] == 0


def test_rejection_records_the_parameters_as_they_were_at_the_failed_step(platform: Platform) -> None:
    # Defaults are applied before the capability check, values derived from the
    # world only after it: the stored intent shows exactly that difference.
    locked = submit(platform, "mei_lan", "behavior:research_boundary_stone", intent_id="record-locked")
    assert locked["failed_check"] == "capability"
    assert stored_intent(platform, "record-locked")["data"]["parameters"] == {"source_id": "boundary_stone"}

    platform.store.repositories.entities.put_definition(
        "bt_def",
        "research_boundary_stone",
        {**next(item for item in platform.store.repositories.entities.get_definitions("bt_def") if item["id"] == "research_boundary_stone"), "entry_conditions": ["actor.incapacitated"]},
    )
    refused = submit(platform, "luo_shan", "behavior:research_boundary_stone", intent_id="record-entry")
    assert refused["failed_check"] == "entry_condition"
    recorded = stored_intent(platform, "record-entry")["data"]["parameters"]
    source = platform.store.repositories.entities.get_entity("boundary_stone")
    assert recorded["source_id"] == "boundary_stone"
    assert recorded["source_location_id"] == (source.get("initial_state") or {}).get("location_id") or source.get("location_id")


def test_destination_aliases_and_visible_names_become_the_registered_location_parameter(platform: Platform) -> None:
    register_visit_behavior(platform, "mei_lan")

    result = submit(platform, "mei_lan", "behavior:visit", intent_id="alias-1", parameters={"destination": "旧商路"})

    assert result["status"] == "accepted"
    assert stored_intent(platform, "alias-1")["data"]["parameters"] == {"location_id": "old_road"}


def test_target_must_be_a_visible_participant(platform: Platform) -> None:
    params = {"message": "The cistern needs a check today.", "topic": "cistern"}

    hidden = submit(platform, "luo_shan", "behavior:communicate", intent_id="target-hidden", parameters={**params, "target_id": "han_qiu"})
    wrong_name = submit(platform, "luo_shan", "behavior:communicate", intent_id="target-name", parameters=params, target={"name": "韩秋"})

    assert hidden["failed_check"] == "target_visibility"
    assert hidden["available_targets"] == ["梅岚"]
    assert wrong_name["failed_check"] == "target_choice"
    assert wrong_name["available_targets"] == ["梅岚"]


def test_the_only_visible_participant_is_filled_in_and_a_named_one_is_resolved(platform: Platform) -> None:
    params = {"message": "The cistern needs a check today.", "topic": "cistern"}

    implied = submit(platform, "mei_lan", "behavior:communicate", intent_id="target-implied", parameters=params)
    assert implied["status"] == "accepted"
    assert platform.store.repositories.entities.get_agent("mei_lan")["initial_state"]["active_parameters"]["target_id"] == "luo_shan"

    named = submit(platform, "luo_shan", "behavior:communicate", intent_id="target-named", parameters=params, target={"name": "梅岚"})
    assert named["status"] == "accepted"
    assert stored_intent(platform, "target-named")["data"]["parameters"]["target_id"] == "mei_lan"


def test_several_visible_participants_require_an_explicit_choice(platform: Platform) -> None:
    set_state(platform, "han_qiu", location_id="lantern_square")

    result = submit(platform, "mei_lan", "behavior:communicate", intent_id="target-required", parameters={"message": "The cistern needs a check today.", "topic": "cistern"})

    assert result["failed_check"] == "target_required"
    assert sorted(result["available_targets"]) == ["罗杉", "韩秋"]


def test_choice_made_from_a_stale_character_view_is_rejected(platform: Platform) -> None:
    view = platform.decision_view("mei_lan")
    assert "behavior:inspect_cistern" in [item["ref"] for item in view["executable_behaviors"]]

    result = submit(
        platform, "mei_lan", "behavior:inspect_cistern", intent_id="stale-1",
        observed_world_revision=view["world_revision"], observed_agent_revision=view["agent_revision"] + 1,
    )

    assert result["failed_check"] == "stale_agent_revision"
    assert result["observed_agent_revision"] == view["agent_revision"] + 1
    assert result["current_agent_revision"] == view["agent_revision"]
