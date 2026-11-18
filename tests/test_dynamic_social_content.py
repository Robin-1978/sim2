"""Phase 3A deterministic contract for dynamic social content.

Each test maps to one row of the frozen acceptance matrix in
``docs/design/social-authority-loop-phase3.md``.  The world starts with only
movement content; every social resource, rule and behavior is created through
the existing God proposal services.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform
from sim2.world.growth import build_growth_ledger
from worlds.dynamic_social_probe.proposals import (
    APPROVE,
    FILL,
    REJECT,
    REQUEST,
    REVIEW,
    REVOKE,
    RULES,
    STEWARDSHIP,
    TAKE,
    WATER_PLANT,
    WATER_RESERVE,
    WITHDRAW,
    install_social_content,
)

WORLD = Path(__file__).parents[1] / "worlds" / "dynamic_social_probe"
SRC = Path(__file__).parents[1] / "src" / "sim2"


@pytest.fixture
def platform(tmp_path: Path):
    instance = Platform(create_instance(WORLD, "dynamic-social", tmp_path / "runtime"))
    try:
        yield instance
    finally:
        instance.close()


def submit(
    platform: Platform,
    actor: str,
    behavior: str,
    intent_id: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return platform.submit_behavior(
        BehaviorIntent(
            actor_id=actor,
            behavior_ref=f"behavior:{behavior}",
            target={},
            parameters=dict(parameters or {}),
            reason="phase three contract test",
            confidence=0.8,
            intent_id=intent_id,
        )
    )


def behavior_entry(platform: Platform, actor: str, behavior: str) -> dict[str, Any]:
    view = platform.decision_view(actor)
    for collection in ("executable_behaviors", "blocked_behaviors"):
        for item in view.get(collection) or []:
            if item.get("ref") == f"behavior:{behavior}":
                return item
    raise AssertionError(f"{behavior} is absent from {actor}'s decision view")


def events(platform: Platform, event_type: str | None = None) -> list[dict[str, Any]]:
    rows = platform.store.repositories.events.timeline_events(after_event_id=0, limit=2000, include_ticks=True)
    return [row for row in rows if event_type is None or row.get("event_type") == event_type]


def latest(platform: Platform, event_type: str) -> dict[str, Any]:
    rows = events(platform, event_type)
    assert rows, f"missing {event_type}"
    return rows[-1]


def resource_amount(platform: Platform) -> float:
    entity = platform.store.repositories.entities.get_entity("water_reserve")
    state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
    return float(state.get("quantity", 0) or 0)


def move(platform: Platform, actor: str, location_id: str) -> None:
    with platform.store.transaction():
        agent = platform.store.repositories.entities.get_agent(actor)
        state = dict(agent.get("initial_state") or {})
        state["location_id"] = location_id
        agent["initial_state"] = state
        platform.store.repositories.entities.update_agent(actor, agent)


def advance_until(platform: Platform, predicate, limit: int = 20) -> None:
    for _ in range(limit):
        if predicate():
            return
        platform.step()
    raise AssertionError("world did not reach the expected state")


def tick_to(platform: Platform, minute: int) -> None:
    advance_until(platform, lambda: int(platform.store.repositories.metadata.get("world_minute", 0)) >= minute, limit=minute + 2)


def fill_reserve(platform: Platform, intent_id: str = "fill-1") -> None:
    result = submit(platform, "mira", "fill_water_reserve", intent_id)
    assert result["status"] == "accepted"
    advance_until(platform, lambda: resource_amount(platform) >= 10)


def grant_delegation(platform: Platform) -> str:
    denied = submit(platform, "tomas", "withdraw_water", "deny-before-request")
    assert denied["failed_check"] == "missing_delegation"
    denial = latest(platform, "behavior.denied")
    requested = submit(
        platform,
        "tomas",
        "request_access",
        "request-for-delegation",
        {"based_on_event": str(denial["event_id"]), "reason": "Need water"},
    )
    assert requested["status"] == "accepted"
    approved = submit(platform, "mira", "approve_withdrawal", "approve-for-delegation", {"request_id": requested["request_id"]})
    assert approved["status"] == "accepted"
    return str(approved["delegation_id"])


def content_counts(platform: Platform) -> dict[str, int]:
    connection = platform.store.connection
    return {
        "entities": int(connection.execute("SELECT COUNT(*) FROM entity").fetchone()[0]),
        "actions": int(connection.execute("SELECT COUNT(*) FROM action_def").fetchone()[0]),
        "behaviors": int(connection.execute("SELECT COUNT(*) FROM bt_def").fetchone()[0]),
        "relationships": int(connection.execute("SELECT COUNT(*) FROM relationship").fetchone()[0]),
        "rules": len((platform.store.repositories.metadata.get("rules", {}) or {}).get("world_rules", []) or []),
        "violations": int(connection.execute("SELECT COUNT(*) FROM violation").fetchone()[0]),
        "restrictions": int(connection.execute("SELECT COUNT(*) FROM access_restriction").fetchone()[0]),
    }


# ---------------------------------------------------------------------------
# DA-01 .. DA-10
# ---------------------------------------------------------------------------
def test_da_01_empty_world_has_no_social_content(platform: Platform) -> None:
    assert all(item["id"] not in {"water_plant", "water_reserve"} for item in platform.store.repositories.entities.entities_of_type("location") + platform.store.repositories.entities.entities_of_type("resource"))
    definitions = {item["id"] for table in ("action_def", "bt_def") for item in platform.store.repositories.entities.get_definitions(table)}
    assert not definitions & {"fill_water_reserve", "withdraw_water", "take_water", "review_water_violation"}
    assert platform.store.repositories.entities.list_relationships() == []
    assert (platform.store.repositories.metadata.get("rules", {}) or {}).get("world_rules", []) == []
    assert platform.store.repositories.contested.count_violations() == 0
    assert platform.store.repositories.contested.count_restrictions() == 0
    assert build_growth_ledger(platform.store)["cells"]["resource"]["count"] == 0

    view = platform.decision_view("tomas")
    assert "travel" in {item["id"] for item in platform.store.repositories.entities.get_definitions("bt_def")}
    assert not [item for item in view["executable_behaviors"] if "water" in item["ref"]]


def test_da_02_god_creates_resource_rules_and_behaviors(platform: Platform) -> None:
    results = install_social_content(platform)

    assert all(result["status"] == "accepted" for result in results.values()), results
    actions = {item["id"] for item in platform.store.repositories.entities.get_definitions("action_def")}
    behaviors = {item["id"] for item in platform.store.repositories.entities.get_definitions("bt_def")}
    assert {"fill_water_reserve", "withdraw_water", "take_water", "review_water_violation"} <= actions
    assert {"fill_water_reserve", "withdraw_water", "take_water", "review_water_violation"} <= behaviors
    assert {"water_reserve_stewardship", "water_approval_authority", "water_revocation_authority", "water_violation_authority"} <= {
        item["id"] for item in (platform.store.repositories.metadata.get("rules", {}) or {}).get("world_rules", [])
    }
    assert any(item["event_type"] == "definition.registered" for item in events(platform))
    assert any(item["event_type"] == "entity.created" for item in events(platform))
    assert any(item["event_type"].startswith("world.change.") for item in events(platform))

    tomas_withdraw = behavior_entry(platform, "tomas", "withdraw_water")
    mira_withdraw = behavior_entry(platform, "mira", "withdraw_water")
    take = behavior_entry(platform, "tomas", "take_water")
    assert tomas_withdraw["eligibility"]["available"] is False
    assert tomas_withdraw["eligibility"]["reasons"][0]["type"] == "missing_delegation"
    assert mira_withdraw["eligibility"]["available"] is True
    assert take["eligibility"]["available"] is True
    assert take["eligibility"]["gated"] is False
    assert behavior_entry(platform, "mira", "fill_water_reserve")["eligibility"]["available"] is True
    assert behavior_entry(platform, "mira", "review_water_violation")["eligibility"]["available"] is False

    move(platform, "mira", "water_plant")
    move(platform, "tomas", "water_plant")
    assert behavior_entry(platform, "tomas", "take_water")["risk"]["observers"] == ["Mira"]
    assert behavior_entry(platform, "tomas", "take_water")["risk"]["detection"] == "observed"


def test_da_03_controlled_withdrawal_chain_on_generated_content(platform: Platform) -> None:
    install_social_content(platform)
    fill_reserve(platform)

    denied = submit(platform, "tomas", "withdraw_water", "deny-1")
    assert denied["failed_check"] == "missing_delegation"
    denial = latest(platform, "behavior.denied")
    request = submit(
        platform,
        "tomas",
        "request_access",
        "request-1",
        {"based_on_event": str(denial["event_id"]), "reason": "Need water for the relay"},
    )
    assert request["status"] == "accepted"
    approved = submit(platform, "mira", "approve_withdrawal", "approve-1", {"request_id": request["request_id"]})
    assert approved["status"] == "accepted"
    delegation_id = str(approved["delegation_id"])
    withdrawn = submit(platform, "tomas", "withdraw_water", "withdraw-1")
    assert withdrawn["status"] == "accepted"
    advance_until(platform, lambda: bool(events(platform, "resource.withdrawal.completed")))
    assert resource_amount(platform) == 9
    assert latest(platform, "resource.withdrawal.completed")["intent_id"] == "withdraw-1"

    revoked = submit(platform, "mira", "revoke_delegation", "revoke-1", {"delegation_id": delegation_id})
    assert revoked["status"] == "accepted"
    after = submit(platform, "tomas", "withdraw_water", "withdraw-2")
    assert after["failed_check"] == "delegation_revoked"
    assert resource_amount(platform) == 9


def test_da_04_violation_sanction_and_forgiveness_on_generated_content(platform: Platform) -> None:
    install_social_content(platform)
    fill_reserve(platform)
    grant_delegation(platform)
    move(platform, "mira", "water_plant")
    move(platform, "tomas", "water_plant")

    take = submit(platform, "tomas", "take_water", "take-1")
    assert take["status"] == "accepted"
    advance_until(platform, lambda: bool(platform.store.repositories.contested.list_violations()))
    observed = platform.store.repositories.contested.list_violations()[0]
    assert observed["status"] == "observed"
    assert observed["witnesses"] == ["mira"]
    assert resource_amount(platform) == 9
    assert behavior_entry(platform, "mira", "review_water_violation")["eligibility"]["available"] is True

    sanctioned = submit(platform, "mira", "review_water_violation", "sanction-1", {"violation_id": observed["violation_id"], "decision": "sanction"})
    assert sanctioned["status"] == "accepted"
    assert platform.store.repositories.contested.active_restriction("tomas", "water_reserve", "water_withdrawal") is not None
    blocked = submit(platform, "tomas", "withdraw_water", "withdraw-blocked")
    assert blocked["failed_check"] == "access_restricted"

    forgiven = submit(platform, "mira", "review_water_violation", "forgive-1", {"violation_id": observed["violation_id"], "decision": "forgive"})
    assert forgiven["status"] == "accepted"
    assert platform.store.repositories.contested.active_restriction("tomas", "water_reserve", "water_withdrawal") is None
    restored = submit(platform, "tomas", "withdraw_water", "withdraw-restored")
    assert restored["status"] == "accepted"
    advance_until(platform, lambda: len(events(platform, "resource.withdrawal.completed")) >= 1)
    assert resource_amount(platform) == 8

    move(platform, "mira", "relay_yard")
    move(platform, "tomas", "water_plant")
    hidden_take = submit(platform, "tomas", "take_water", "take-hidden")
    assert hidden_take["status"] == "accepted"
    advance_until(platform, lambda: len(platform.store.repositories.contested.list_violations()) >= 2)
    hidden = [item for item in platform.store.repositories.contested.list_violations() if item["status"] == "undetected"][0]
    assert hidden["witnesses"] == []
    assert platform.decision_view("mira")["unresolved_violations"] == []


def test_da_05_director_demand_and_completion(platform: Platform) -> None:
    tick_to(platform, 5)
    plan = platform.store.repositories.metadata.get("season_plan")
    assert plan["status"] == "active"
    assert plan["target_cell"] == "resource"
    assert plan["baseline"] == 0
    assert any(task["task_type"] == "god_structural" for task in platform.ready_evolution_tasks())

    install_social_content(platform)
    tick_to(platform, 10)
    completed = platform.store.repositories.metadata.get("last_completed_season_plan")
    assert completed["status"] == "completed"
    assert completed["target_cell"] == "resource"
    assert completed["baseline"] == 0
    next_plan = platform.store.repositories.metadata.get("season_plan")
    assert next_plan["baseline"] == 1
    assert next_plan["plan_id"] != completed["plan_id"]


def test_da_06_invalid_proposals_leave_no_partial_content(platform: Platform) -> None:
    def rejected(payload: dict[str, Any], *, kind: str) -> dict[str, Any]:
        before = content_counts(platform)
        if kind == "entity":
            result = platform.submit_entity_proposal("god", payload)
        elif kind == "definition":
            result = platform.submit_definition_proposal("god", payload)
        else:
            result = platform.submit_world_change("god", payload)
        assert result["status"] == "rejected", result
        assert result.get("failed_check")
        assert content_counts(platform) == before
        return result

    rejected({**WATER_RESERVE, "entity_id": "stocked_reserve", "name": "Stocked Reserve", "state": {"quantity": 5}}, kind="entity")

    base_action = dict(FILL)
    definition = dict(base_action["definition"])
    definition["id"] = "bad_missing_resource"
    definition["effects"] = [{"type": "resource_withdrawal", "resource_id": "missing_reserve", "amount": 1, "scope": "water_withdrawal"}]
    rejected({**base_action, "name": "Bad Missing Resource", "definition": definition, "followup_behavior": {**base_action["followup_behavior"], "name": "Bad Missing Resource", "definition": {**base_action["followup_behavior"]["definition"], "id": "bad_missing_resource"}}}, kind="definition")

    definition = dict(base_action["definition"])
    definition["id"] = "bad_wrong_target"
    definition["effects"] = [{"type": "resource_withdrawal", "resource_id": "relay_station", "amount": 1, "scope": "water_withdrawal"}]
    rejected({**base_action, "name": "Bad Wrong Target", "definition": definition, "followup_behavior": {**base_action["followup_behavior"], "name": "Bad Wrong Target", "definition": {**base_action["followup_behavior"]["definition"], "id": "bad_wrong_target"}}}, kind="definition")

    definition = dict(base_action["definition"])
    definition["id"] = "bad_empty_scope"
    definition["effects"] = [{"type": "resource_withdrawal", "resource_id": "water_reserve", "amount": 1, "scope": ""}]
    rejected({**base_action, "name": "Bad Empty Scope", "definition": definition, "followup_behavior": {**base_action["followup_behavior"], "name": "Bad Empty Scope", "definition": {**base_action["followup_behavior"]["definition"], "id": "bad_empty_scope"}}}, kind="definition")

    take_definition = dict(TAKE["definition"])
    take_definition["id"] = "bad_take_mode"
    take_definition["effects"] = [{"type": "resource_take", "resource_id": "water_reserve", "amount": 1, "scope": "water_withdrawal", "mode": "public"}]
    rejected({**TAKE, "name": "Bad Take Mode", "definition": take_definition, "followup_behavior": {**TAKE["followup_behavior"], "name": "Bad Take Mode", "definition": {**TAKE["followup_behavior"]["definition"], "id": "bad_take_mode"}}}, kind="definition")

    rejected({
        "definition_type": "behavior",
        "name": "Unassigned Water Work",
        "definition": {"nodes": [{"type": "action", "action_id": "travel", "parameters": {"location_id": "relay_yard"}}]},
    }, kind="definition")

    rejected({
        "definition_type": "behavior",
        "name": "Bad Authorization Operation",
        "assign_to": ["mira"],
        "definition": {
            "authorization_operation": "do_something",
            "nodes": [{"type": "action", "action_id": "travel", "parameters": {"location_id": "relay_yard"}}],
        },
    }, kind="definition")

    rejected({
        "definition_type": "behavior",
        "name": "Bad Review Parameters",
        "assign_to": ["mira"],
        "definition": {
            "authorization_operation": "review_violation",
            "nodes": [{"type": "action", "action_id": "travel", "parameters": {"location_id": "relay_yard"}}],
        },
    }, kind="definition")

    rejected({
        "change_type": "rule",
        "summary": "Bad authorization target.",
        "rule": {
            "id": "bad_authorization_target",
            "condition": "true",
            "effect": {"type": "authorization", "mode": "resource_access", "target": "missing_reserve", "scope": "water_withdrawal", "behavior_ids": ["travel"]},
        },
    }, kind="world_change")

    rejected({
        "change_type": "rule",
        "summary": "Bad review behavior.",
        "rule": {
            "id": "bad_review_behavior",
            "condition": "true",
            "effect": {"type": "authorization", "mode": "violation_review", "behavior_ids": ["travel"]},
        },
    }, kind="world_change")


def test_da_07_platform_source_has_no_water_content(platform: Platform) -> None:
    identifiers = (
        "water_plant",
        "water_reserve",
        "fill_water_reserve",
        "withdraw_water",
        "take_water",
        "review_water_violation",
        "water_reserve_stewardship",
        "water_approval_authority",
        "water_revocation_authority",
        "water_violation_authority",
    )
    hits = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for identifier in identifiers:
            if identifier in text:
                hits.append(f"{path.relative_to(SRC)}: {identifier}")
    assert hits == []


def test_da_08_phase_one_and_two_codes_are_preserved(platform: Platform) -> None:
    install_social_content(platform)
    fill_reserve(platform)

    denied = submit(platform, "tomas", "withdraw_water", "deny-preserved")
    assert denied["failed_check"] == "missing_delegation"
    assert denied["rule_id"] == "water_reserve_stewardship"

    move(platform, "mira", "water_plant")
    move(platform, "tomas", "water_plant")
    assert submit(platform, "tomas", "take_water", "take-preserved")["status"] == "accepted"
    advance_until(platform, lambda: bool(platform.store.repositories.contested.list_violations()))
    assert latest(platform, "violation.observed")["witnesses"] == ["mira"]


def test_da_09_negative_space(platform: Platform) -> None:
    install_social_content(platform)
    fill_reserve(platform)
    move(platform, "mira", "water_plant")
    move(platform, "tomas", "water_plant")
    assert submit(platform, "tomas", "take_water", "take-negative")["status"] == "accepted"
    advance_until(platform, lambda: bool(platform.store.repositories.contested.list_violations()))

    event_types = {str(item.get("event_type")) for item in events(platform)}
    assert not {value for value in event_types if value.startswith("resource.withdrawal.")}
    assert "access.approved" not in event_types
    assert "attack" not in event_types
    assert "combat" not in event_types
    assert "deception" not in event_types
    assert "stealth" not in event_types
    assert platform.store.repositories.authorization.list_delegations() == []
    tasks = platform.ready_evolution_tasks()
    assert not [task for task in tasks if task.get("role") in {"story", "god"}]
    assert all(str(task.get("task_type") or "").startswith("behavior_assigned:") for task in tasks)


def test_da_11_visible_resource_names_are_canonicalized(platform: Platform) -> None:
    """A model may use the visible resource name in effects and authorization rules."""

    platform.submit_entity_proposal("god", WATER_PLANT)
    platform.submit_entity_proposal("god", WATER_RESERVE)
    platform.submit_world_change("god", STEWARDSHIP)

    def visible(payload: dict[str, Any]) -> dict[str, Any]:
        value = copy.deepcopy(payload)
        for effect in value["definition"].get("effects", []) or []:
            if effect.get("resource_id") == "water_reserve":
                effect["resource_id"] = "Water Reserve"
        return value

    for payload in (FILL, WITHDRAW, TAKE):
        assert platform.submit_definition_proposal("god", visible(payload))["status"] == "accepted"
    for payload in (REQUEST, APPROVE, REJECT, REVOKE, REVIEW):
        assert platform.submit_definition_proposal("god", payload)["status"] == "accepted"
    for payload in RULES:
        value = copy.deepcopy(payload)
        effect = value["rule"]["effect"]
        if str(effect.get("target") or "") == "water_reserve":
            effect["target"] = "Water Reserve"
        assert platform.submit_world_change("god", value)["status"] == "accepted"

    fill_reserve(platform)
    denied = submit(platform, "tomas", "withdraw_water", "visible-deny")
    assert denied["failed_check"] == "missing_delegation"
    denial = latest(platform, "behavior.denied")
    requested = submit(
        platform,
        "tomas",
        "request_access",
        "visible-request",
        {"based_on_event": str(denial["event_id"])},
    )
    assert requested["status"] == "accepted"
    approved = submit(platform, "mira", "approve_withdrawal", "visible-approve", {"request_id": requested["request_id"]})
    assert approved["status"] == "accepted", approved
    assert approved.get("delegation_id")
    withdrawn = submit(platform, "tomas", "withdraw_water", "visible-withdraw")
    assert withdrawn["status"] == "accepted"
    advance_until(platform, lambda: bool(events(platform, "resource.withdrawal.completed")))
    assert resource_amount(platform) == 9


def test_da_10_duplicate_and_failed_proposals_are_idempotent(platform: Platform) -> None:
    install_social_content(platform)
    before = content_counts(platform)

    duplicate_entity = platform.submit_entity_proposal("god", WATER_PLANT)
    duplicate_resource = platform.submit_entity_proposal("god", WATER_RESERVE)
    duplicate_definition = platform.submit_definition_proposal("god", FILL)
    duplicate_rule = platform.submit_world_change("god", RULES[0])
    for result in (duplicate_entity, duplicate_resource, duplicate_definition, duplicate_rule):
        assert result["status"] == "rejected", result
    assert content_counts(platform) == before

    bad = dict(WITHDRAW)
    bad_definition = dict(bad["definition"])
    bad_definition["id"] = "bad_failed_proposal"
    bad_definition["effects"] = [{"type": "resource_withdrawal", "resource_id": "missing_reserve", "amount": 1, "scope": "water_withdrawal"}]
    failed = platform.submit_definition_proposal(
        "god",
        {
            **bad,
            "name": "Bad Failed Proposal",
            "definition": bad_definition,
            "followup_behavior": {**bad["followup_behavior"], "name": "Bad Failed Proposal", "definition": {**bad["followup_behavior"]["definition"], "id": "bad_failed_proposal"}},
        },
    )
    assert failed["status"] == "rejected"
    assert content_counts(platform) == before
