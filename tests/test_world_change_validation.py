from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform

WORLD = Path(__file__).parents[1] / "worlds" / "starter_village"
CHANGE_TABLES = ("intent", "event_log", "world_proposal", "relationship", "entity", "instance_meta")


@pytest.fixture
def platform(tmp_path: Path):
    instance = Platform(create_instance(WORLD, "world-change", tmp_path / "runtime"))
    try:
        yield instance
    finally:
        instance.close()


def snapshot(platform: Platform) -> dict[str, list[tuple[Any, ...]]]:
    connection = platform.store.connection
    return {
        table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()]
        for table in CHANGE_TABLES
    }


def last_intent(platform: Platform) -> dict[str, Any]:
    row = platform.store.connection.execute("SELECT status,data_json,result_json FROM intent ORDER BY rowid DESC LIMIT 1").fetchone()
    return {"status": row["status"], "data": json.loads(row["data_json"]), "result": json.loads(row["result_json"])}


def properties(platform: Platform) -> dict[str, Any]:
    return platform.store.repositories.metadata.get("world_properties", {}) or {}


def set_metadata(platform: Platform, key: str, value: Any) -> None:
    with platform.store.transaction():
        platform.store.repositories.metadata.set(key, value)


def property_change(value: Any, *, property_name: str = "fog_level", change_type: str = "world_property", **extra: Any) -> dict[str, Any]:
    return {"change_type": change_type, "summary": "Change a world property.", "property": property_name, "value": value, **extra}


def rule_change(rule_id: str, **overrides: Any) -> dict[str, Any]:
    rule = {
        "id": rule_id,
        "condition": "event.recent.clinic.fever_reported == true",
        "effect": "clinic.triage_alert",
        "behavior_names": ["Heal Target"],
        "wake": ["god"],
        **overrides,
    }
    return {"change_type": "rule", "summary": "Review the clinic event.", "rule": rule}


def unconnected_locations(platform: Platform) -> tuple[str, str]:
    entities = platform.store.repositories.entities
    ids = [row["id"] for row in platform.store.connection.execute("SELECT id FROM entity WHERE entity_type='location' ORDER BY id").fetchall()]
    for source in ids:
        for target in ids:
            joined = target in (entities.get_entity(source).get("routes") or []) or source in (entities.get_entity(target).get("routes") or [])
            if source != target and not joined:
                return source, target
    raise AssertionError("the world connects every pair of locations")


def test_missing_fields_are_reported_before_the_change_type(platform: Platform) -> None:
    missing = platform.submit_world_change("operator", {"change_type": "teleport"})
    unsupported = platform.submit_world_change("operator", {"change_type": "teleport", "summary": "Move the river."})

    assert missing == {"status": "rejected", "message": "change_type and summary are required.", "retryable": True}
    assert unsupported["failed_check"] == "change_type"
    assert last_intent(platform)["status"] == "rejected"


def test_god_may_set_only_properties_the_package_opens_to_it(platform: Platform) -> None:
    proposal = property_change(0.0, property_name="Fog Level")

    rejected = platform.submit_world_change("god", proposal)
    stored = last_intent(platform)

    assert rejected["failed_check"] == "god_direct_property_change"
    assert stored["data"]["property"] == "fog_level"  # the rejection keeps the canonical key
    assert properties(platform)["fog_level"] == 0.1
    assert platform.submit_world_change("operator", proposal)["status"] == "accepted"
    assert properties(platform)["fog_level"] == 0.0

    rules = {**(platform.store.repositories.metadata.get("rules", {}) or {}), "world_change_policy": {"god_direct_properties": ["public_trust"]}}
    set_metadata(platform, "rules", rules)
    assert platform.submit_world_change("god", property_change(0.9, property_name="public_trust"))["status"] == "accepted"
    assert platform.submit_world_change("god", property_change(10, property_name="village_food"))["failed_check"] == "god_direct_property_change"


def test_world_property_change_needs_a_property(platform: Platform) -> None:
    rejected = platform.submit_world_change("operator", {"change_type": "world_property", "summary": "Change something.", "value": 3})

    assert rejected["failed_check"] == "property"


def test_property_value_follows_the_type_of_the_current_value(platform: Platform) -> None:
    assert platform.submit_world_change("operator", property_change(" 0.4 "))["status"] == "accepted"
    assert platform.submit_world_change("operator", property_change(350.0, property_name="village_food"))["status"] == "accepted"
    repeated = platform.submit_world_change("operator", property_change("0.4"))

    values = properties(platform)
    assert values["fog_level"] == 0.4 and isinstance(values["fog_level"], float)
    assert values["village_food"] == 350 and isinstance(values["village_food"], int)
    assert repeated["failed_check"] == "no_state_change"
    assert last_intent(platform)["data"]["value"] == 0.4  # the rejection keeps the coerced value


@pytest.mark.parametrize(
    ("property_name", "value", "message"),
    [
        ("fog_level", True, "World property fog_level is numeric; value must be a number."),
        ("fog_level", "dense", "World property fog_level is numeric; value must be a number."),
        ("fog_level", ["thick"], "World property fog_level is numeric; value must be a number."),
        ("gate_open", "yes", "World property gate_open is boolean; value must be true or false."),
        ("gate_open", 1, "World property gate_open is boolean; value must be true or false."),
    ],
)
def test_property_value_of_the_wrong_type_is_rejected(platform: Platform, property_name: str, value: Any, message: str) -> None:
    set_metadata(platform, "world_properties", {**properties(platform), "gate_open": False})

    rejected = platform.submit_world_change("operator", property_change(value, property_name=property_name))

    assert (rejected["failed_check"], rejected["message"]) == ("property_type", message)


def test_event_with_a_property_sets_it_and_without_one_stays_narrative(platform: Platform) -> None:
    typed = platform.submit_world_change("operator", {"change_type": "event", "summary": "Fire.", "effects": [{"type": "state_delta", "amount": 1}]})
    narrative = platform.submit_world_change("operator", {"change_type": "event", "summary": "Fire.", "effects": ["Smoke rises."]})
    structured = platform.submit_world_change("operator", property_change(0.3, property_name="north_gate_threat_level", change_type="event"))

    assert typed["failed_check"] == "event_effects"
    assert narrative["status"] == "accepted" and narrative["effect"] == {"event_recorded": True, "state_changed": False}
    assert narrative["applied_effects"] is False
    assert structured["applied_effects"] is True and properties(platform)["north_gate_threat_level"] == 0.3


def test_route_is_stored_with_canonical_endpoints_and_connects_both_ways(platform: Platform) -> None:
    source, target = unconnected_locations(platform)
    entities = platform.store.repositories.entities
    names = (entities.get_entity(source)["name"], entities.get_entity(target)["name"])
    proposal = {"change_type": "route", "summary": "Open a road.", "route": {"from": names[0], "to": names[1], "distance": 3}}

    accepted = platform.submit_world_change("operator", proposal)

    assert accepted["effect"] == {"route_added": {"from": source, "to": target, "distance": 3.0}}
    assert last_intent(platform)["data"]["route"] == {"from": source, "to": target, "distance": 3.0}
    assert target in entities.get_entity(source)["routes"] and source in entities.get_entity(target)["routes"]
    assert entities.get_entity(source)["route_distances"][target] == 3.0
    assert platform.submit_world_change("operator", proposal)["failed_check"] == "route_duplicate"


@pytest.mark.parametrize(
    ("route", "failed_check"),
    [
        ({"from": "nowhere", "to": "river_bridge", "distance": 2}, "route_endpoint"),
        ({"from": "river_bridge", "to": "river_bridge", "distance": 2}, "route_cycle"),
        ({"from": "river_bridge", "to": "inn", "distance": 0}, "route_distance"),
        ({"from": "river_bridge", "to": "inn", "distance": True}, "route_distance"),
        ("a road", "route_endpoint"),
    ],
)
def test_route_must_join_two_locations_with_a_positive_distance(platform: Platform, route: Any, failed_check: str) -> None:
    rejected = platform.submit_world_change("operator", {"change_type": "route", "summary": "Open a road.", "route": route})

    assert rejected["failed_check"] == failed_check


@pytest.mark.parametrize(
    ("change", "failed_check"),
    [
        ({"change_type": "rule", "summary": "Adjust policy.", "rule": "no object"}, "rule"),
        ({"change_type": "relationship", "summary": "Bind them.", "relationship": ["lin_bo", "he_yan"]}, "relationship"),
        ({"change_type": "faction", "summary": "Reform it.", "faction": None}, "faction"),
    ],
)
def test_structured_changes_need_their_payload_object(platform: Platform, change: dict[str, Any], failed_check: str) -> None:
    rejected = platform.submit_world_change("operator", change)

    assert rejected["failed_check"] == failed_check
    assert rejected["message"] == f"{failed_check} object is required for a {failed_check} change."


def test_relationship_is_stored_between_canonical_ids(platform: Platform) -> None:
    accepted = platform.submit_world_change("operator", {
        "change_type": "relationship",
        "summary": "Two villagers trust each other.",
        "relationship": {"participants": ["林泊", "何砚"], "type": "ally", "strength": 0.7},
    })

    assert accepted["effect"] == {"relationship": {"from": "lin_bo", "to": "he_yan", "type": "ally", "strength": 0.7}}
    row = platform.store.connection.execute("SELECT data_json FROM relationship WHERE from_id='lin_bo' AND to_id='he_yan' AND relation_type='ally'").fetchone()
    assert json.loads(row["data_json"]) == {"strength": 0.7}


def test_god_relationship_delta_changes_strength_and_preserves_history(platform: Platform) -> None:
    bond = {"from": "林泊", "to": "何砚", "type": "convoy"}
    def propose(**fields):
        return platform.submit_world_change("god", {"change_type": "relationship", "summary": "护航之约",
                                                     "relationship": {**bond, **fields}})

    assert propose(delta=0.2, description="救回斥候")["effect"]["relationship"]["relation_strength"] == 0.2
    assert propose(delta=0.9)["effect"]["relationship"]["relation_strength"] == 1.0
    for invalid in ({"delta": "两成"}, {"delta": True}, {"delta": 0.1, "relation_strength": 0.5}):
        assert propose(**invalid)["failed_check"] == "relationship.delta"
    row = platform.store.connection.execute("SELECT data_json FROM relationship WHERE relation_type='convoy'").fetchone()
    assert json.loads(row["data_json"]) == {"description": "救回斥候", "relation_strength": 1.0}


@pytest.mark.parametrize(
    ("change", "message", "failed_check"),
    [
        ({"change_type": "relationship", "summary": "Bind them.", "relationship": {"type": "ally"}}, "relationship from and to are required.", "relationship"),
        ({"change_type": "relationship", "summary": "Bind them.", "relationship": {"from": "lin_bo", "to": "nobody here"}}, "relationship from and to are required.", "relationship"),
        ({"change_type": "faction", "summary": "Reform it.", "faction": {"strategy": "trade"}}, "faction id is required.", "faction.id"),
        ({"change_type": "faction", "summary": "Reform it.", "faction": {"id": "ghost_faction"}}, "faction does not exist.", "faction.id"),
    ],
)
def test_change_found_unusable_while_applying_is_stored_as_a_rejected_intent(platform: Platform, change: dict[str, Any], message: str, failed_check: str) -> None:
    before = snapshot(platform)

    rejected = platform.submit_world_change("operator", change)

    assert rejected == {"status": "rejected", "message": message, "failed_check": failed_check, "retryable": True}
    stored = last_intent(platform)
    assert (stored["status"], stored["result"]) == ("rejected", rejected)
    after = snapshot(platform)
    assert after["intent"][:-1] == before["intent"]
    # Only the audit record is committed; the commit itself advances the world revision.
    for table in ("event_log", "world_proposal", "relationship", "entity"):
        assert after[table] == before[table]
    assert [row for row in after["instance_meta"] if row[0] != "world_revision"] == [row for row in before["instance_meta"] if row[0] != "world_revision"]


def test_faction_fields_are_merged_into_the_existing_faction(platform: Platform) -> None:
    accepted = platform.submit_world_change("operator", {
        "change_type": "faction",
        "summary": "The watch changes patrol policy.",
        "faction": {"faction_id": "village_watch", "strategy": "protect_boundary"},
    })

    assert accepted["effect"] == {"faction_id": "village_watch", "changed_fields": ["strategy"], "after": {"strategy": "protect_boundary"}}
    assert platform.store.repositories.entities.get_entity("village_watch")["strategy"] == "protect_boundary"


def test_rule_gates_stop_at_the_first_failed_check(platform: Platform) -> None:
    schema = platform.submit_world_change("god", rule_change("no-effect", effect=None))
    declared = platform.submit_world_change("god", {**rule_change("declared"), "conditions": ["world.fog_level < 1"]})
    typed = platform.submit_world_change("god", rule_change("typed", effect={"type": "signal", "enforcement": "behavior_constraint"}))
    self_wake = platform.submit_world_change("god", rule_change("self-wake"), origin_role="god")

    assert [item["failed_check"] for item in (schema, declared, typed, self_wake)] == [
        "rule_schema", "rule_condition_semantics", "rule_effect_semantics", "rule_self_wake",
    ]
    assert not any(item.get("id") in {"no-effect", "declared", "typed", "self-wake"} for item in (platform.store.repositories.metadata.get("rules", {}) or {}).get("world_rules", []))


def test_accepted_rule_is_registered_and_guards_against_repeats(platform: Platform) -> None:
    count = len(platform.rule_compiler.all_rules())

    accepted = platform.submit_world_change("god", rule_change("fever_signal"), origin_role="story")
    repeated_signal = platform.submit_world_change("god", rule_change("fever_signal_again"))
    repeated_id = platform.submit_world_change("god", rule_change("fever_signal", condition="event.recent.clinic.other == true"))
    chained = platform.submit_world_change("god", rule_change("chained", condition="event.recent.world.rule.fever_signal == true", effect="policy.chain", behavior_names=[]))

    assert accepted["effect"]["rule_count"] == count + 1
    assert any(item["id"] == "fever_signal" for item in platform.rule_compiler.all_rules())
    assert (repeated_signal["failed_check"], "(fever_signal)" in repeated_signal["message"]) == ("rule_duplicate", True)
    assert (repeated_id["failed_check"], repeated_id["message"].startswith("A world rule with this name")) == ("rule_duplicate", True)
    assert chained["failed_check"] == "rule_signal_cycle"
    assert len(platform.rule_compiler.all_rules()) == count + 1


@pytest.mark.parametrize(("window", "minutes"), [(None, 1440), (0, 1440), (-5, 1), (90, 90), (10**9, 43200)])
def test_evaluation_window_is_bounded(platform: Platform, window: int | None, minutes: int) -> None:
    world_minute = int(platform.store.repositories.metadata.get("world_minute", 0))

    result = platform.submit_world_change("operator", property_change(0.4, evaluation_window_minutes=window))

    assert result["evaluation_due_minute"] == world_minute + minutes
    row = platform.store.connection.execute("SELECT status,evaluation_due_minute FROM world_proposal WHERE proposal_id=?", (result["proposal_id"],)).fetchone()
    assert (row["status"], row["evaluation_due_minute"]) == ("pending_evaluation", world_minute + minutes)


def test_accepted_change_publishes_one_event_and_a_pending_proposal(platform: Platform) -> None:
    result = platform.submit_world_change("operator", property_change(0.4, expected_effects=["Fog is recorded as 0.4."]))

    event = platform.store.connection.execute("SELECT event_type,data_json FROM event_log WHERE event_type='world.change.world_property'").fetchone()
    data = json.loads(event["data_json"])
    assert result["status"] == "accepted" and result["expected_effects"] == ["Fog is recorded as 0.4."]
    assert (data["proposal_id"], data["effect"]["after"]) == (result["proposal_id"], 0.4)
    assert last_intent(platform)["status"] == "accepted"


def test_failure_while_recording_the_change_rolls_everything_back(platform: Platform, monkeypatch: pytest.MonkeyPatch) -> None:
    before = snapshot(platform)

    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("cannot publish")

    monkeypatch.setattr(platform._services["events"], "append", fail)
    with pytest.raises(RuntimeError, match="cannot publish"):
        platform.submit_world_change("operator", property_change(0.4))

    assert snapshot(platform) == before
    assert properties(platform)["fog_level"] == 0.1


def test_rule_signal_event_and_wake_are_lifted_to_top_level(platform: Platform) -> None:
    proposal = {
        "change_type": "rule",
        "summary": "Market stock is low; wake Story.",
        "rule": {
            "id": "market_signal_top_level",
            "condition": "event.recent.clinic.fever_reported == true",
            "effect": {"type": "signal", "signal": "market.restock", "wake": ["story"]},
        },
    }

    accepted = platform.submit_world_change("god", proposal, origin_role="god")

    assert accepted["status"] == "accepted", accepted
    rules = (platform.store.repositories.metadata.get("rules", {}) or {}).get("world_rules", [])
    stored = next(item for item in rules if item.get("id") == "market_signal_top_level")
    assert stored["event_type"] == "market.restock"
    assert stored["wake"] == ["story"]
