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

from sim2.agents.engine import AgentEngine
from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform
from sim2.world.growth import build_growth_ledger
from sim2.world.authorization import authorization_for_behavior
from worlds.dynamic_social_probe.proposals import (
    APPROVE,
    FILL,
    FUEL_DEPOT,
    FUEL_RESERVE,
    FUEL_RULES,
    FUEL_STEWARDSHIP,
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
    install_second_resource,
    install_third_resource,
)

WORLD = Path(__file__).parents[1] / "worlds" / "dynamic_social_probe"
SRC = Path(__file__).parents[1] / "src" / "sim2"


def _set_required_content(platform: Platform, required: dict[str, list[str]]) -> None:
    with platform.store.transaction():
        evolution = dict(platform.store.repositories.metadata.get("evolution", {}) or {})
        contracts = dict(evolution.get("contracts", {}) or {})
        contract = dict(contracts.get("god_structural", {}) or {})
        contract.pop("stages", None)
        contract["required_content"] = required
        contracts["god_structural"] = contract
        evolution["contracts"] = contracts
        platform.store.repositories.metadata.set("evolution", evolution)


def _set_stages(platform: Platform, stages: list[dict[str, Any]]) -> None:
    with platform.store.transaction():
        evolution = dict(platform.store.repositories.metadata.get("evolution", {}) or {})
        contracts = dict(evolution.get("contracts", {}) or {})
        contract = dict(contracts.get("god_structural", {}) or {})
        contract["stages"] = stages
        contract["required_content"] = {}
        contracts["god_structural"] = contract
        evolution["contracts"] = contracts
        platform.store.repositories.metadata.set("evolution", evolution)


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
    params = dict(parameters or {})
    if behavior in {"fill_resource", "withdraw_resource", "take_resource"}:
        params.setdefault("resource_id", "Water Reserve")
    return platform.submit_behavior(
        BehaviorIntent(
            actor_id=actor,
            behavior_ref=f"behavior:{behavior}",
            target={},
            parameters=params,
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
    return resource_amount_of(platform, "water_reserve")


def resource_amount_of(platform: Platform, resource_id: str) -> float:
    entity = platform.store.repositories.entities.get_entity(resource_id)
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
    result = submit(platform, "mira", "fill_resource", intent_id, {"resource_id": "Water Reserve"})
    assert result["status"] == "accepted"
    advance_until(platform, lambda: resource_amount(platform) >= 10)


def fill_fuel(platform: Platform, intent_id: str = "fill-fuel-1") -> None:
    result = submit(platform, "mira", "fill_resource", intent_id, {"resource_id": "Fuel Reserve"})
    assert result["status"] == "accepted"
    advance_until(platform, lambda: resource_amount_of(platform, "fuel_reserve") >= 10)


def fill_coolant(platform: Platform, intent_id: str = "fill-coolant-1") -> None:
    result = submit(platform, "mira", "fill_resource", intent_id, {"resource_id": "Coolant Reserve"})
    assert result["status"] == "accepted"
    advance_until(platform, lambda: resource_amount_of(platform, "coolant_reserve") >= 10)


def grant_delegation_for(
    platform: Platform,
    behavior_id: str,
    suffix: str,
    resource: str = "Water Reserve",
) -> str:
    denied = submit(platform, "tomas", behavior_id, f"deny-{suffix}", {"resource_id": resource})
    assert denied["failed_check"] == "missing_delegation"
    denial = latest(platform, "behavior.denied")
    assert denial["target"] == resource_id_of(platform, resource)
    requested = submit(
        platform,
        "tomas",
        "request_access",
        f"request-{suffix}",
        {"based_on_event": str(denial["event_id"]), "reason": f"Need {resource}", "target": resource},
    )
    assert requested["status"] == "accepted"
    approved = submit(platform, "mira", "approve_withdrawal", f"approve-{suffix}", {"request_id": requested["request_id"]})
    assert approved["status"] == "accepted"
    return str(approved["delegation_id"])


def resource_id_of(platform: Platform, resource: str) -> str:
    return str(platform.targets.canonical_id(resource) or resource)


def grant_delegation(platform: Platform) -> str:
    denied = submit(platform, "tomas", "withdraw_resource", "deny-before-request", {"resource_id": "Water Reserve"})
    assert denied["failed_check"] == "missing_delegation"
    denial = latest(platform, "behavior.denied")
    requested = submit(
        platform,
        "tomas",
        "request_access",
        "request-for-delegation",
        {"based_on_event": str(denial["event_id"]), "reason": "Need water", "target": "Water Reserve"},
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
    assert not definitions & {"fill_resource", "withdraw_resource", "take_resource", "review_violation"}
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
    assert {"fill_resource", "withdraw_resource", "take_resource", "review_violation"} <= actions
    assert {"fill_resource", "withdraw_resource", "take_resource", "review_violation"} <= behaviors
    assert not {"fill_water_reserve", "withdraw_water", "take_water", "review_water_violation"} & actions
    assert {"water_reserve_stewardship", "water_approval_authority", "water_revocation_authority", "water_violation_authority"} <= {
        item["id"] for item in (platform.store.repositories.metadata.get("rules", {}) or {}).get("world_rules", [])
    }
    assert any(item["event_type"] == "definition.registered" for item in events(platform))
    assert any(item["event_type"] == "entity.created" for item in events(platform))
    assert any(item["event_type"].startswith("world.change.") for item in events(platform))

    tomas_withdraw = behavior_entry(platform, "tomas", "withdraw_resource")
    mira_withdraw = behavior_entry(platform, "mira", "withdraw_resource")
    take = behavior_entry(platform, "tomas", "take_resource")
    assert tomas_withdraw["eligibility"]["available"] is False
    assert tomas_withdraw["eligibility"]["reasons"][0]["type"] == "missing_delegation"
    assert mira_withdraw["eligibility"]["available"] is True
    assert take["eligibility"]["available"] is True
    assert take["eligibility"]["gated"] is False
    assert behavior_entry(platform, "mira", "fill_resource")["eligibility"]["available"] is True
    assert behavior_entry(platform, "mira", "review_violation")["eligibility"]["available"] is False

    move(platform, "mira", "water_plant")
    move(platform, "tomas", "water_plant")
    assert behavior_entry(platform, "tomas", "take_resource")["risk"]["observers"] == ["Mira"]
    assert behavior_entry(platform, "tomas", "take_resource")["risk"]["detection"] == "observed"


def test_generic_capability_does_not_borrow_another_resources_rule(platform: Platform) -> None:
    """A stock without a rule is not silently governed by the first stock's rule."""

    install_social_content(platform)
    with platform.store.transaction():
        platform.store.repositories.entities.put_entity(
            "unruled_stock",
            "resource",
            "Unruled Stock",
            {"initial_state": {"location_id": "water_plant", "quantity": 0}},
        )
    behavior = next(
        item for item in platform.store.repositories.entities.get_definitions("bt_def")
        if item.get("id") == "withdraw_resource"
    )
    tomas = platform.store.repositories.entities.get_agent("tomas")
    unruled = authorization_for_behavior(
        platform.store,
        tomas,
        behavior,
        {"resource_id": "unruled_stock"},
        rule_compiler=platform.rule_compiler,
    )
    governed = authorization_for_behavior(
        platform.store,
        tomas,
        behavior,
        {"resource_id": "water_reserve"},
        rule_compiler=platform.rule_compiler,
    )
    assert unruled["gated"] is False
    assert governed["gated"] is True
    assert governed["available"] is False


def test_da_03_controlled_withdrawal_chain_on_generated_content(platform: Platform) -> None:
    install_social_content(platform)
    fill_reserve(platform)

    denied = submit(platform, "tomas", "withdraw_resource", "deny-1")
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
    withdrawn = submit(platform, "tomas", "withdraw_resource", "withdraw-1")
    assert withdrawn["status"] == "accepted"
    advance_until(platform, lambda: bool(events(platform, "resource.withdrawal.completed")))
    assert resource_amount(platform) == 9
    assert latest(platform, "resource.withdrawal.completed")["intent_id"] == "withdraw-1"

    revoked = submit(platform, "mira", "revoke_delegation", "revoke-1", {"delegation_id": delegation_id})
    assert revoked["status"] == "accepted"
    after = submit(platform, "tomas", "withdraw_resource", "withdraw-2")
    assert after["failed_check"] == "delegation_revoked"
    assert resource_amount(platform) == 9


def test_da_04_violation_sanction_and_forgiveness_on_generated_content(platform: Platform) -> None:
    install_social_content(platform)
    fill_reserve(platform)
    grant_delegation(platform)
    move(platform, "mira", "water_plant")
    move(platform, "tomas", "water_plant")

    take = submit(platform, "tomas", "take_resource", "take-1")
    assert take["status"] == "accepted"
    advance_until(platform, lambda: bool(platform.store.repositories.contested.list_violations()))
    observed = platform.store.repositories.contested.list_violations()[0]
    assert observed["status"] == "observed"
    assert observed["witnesses"] == ["mira"]
    assert resource_amount(platform) == 9
    assert behavior_entry(platform, "mira", "review_violation")["eligibility"]["available"] is True

    sanctioned = submit(platform, "mira", "review_violation", "sanction-1", {"violation_id": observed["violation_id"], "decision": "sanction"})
    assert sanctioned["status"] == "accepted"
    assert platform.store.repositories.contested.active_restriction("tomas", "water_reserve", "resource_withdrawal") is not None
    blocked = submit(platform, "tomas", "withdraw_resource", "withdraw-blocked")
    assert blocked["failed_check"] == "access_restricted"

    forgiven = submit(platform, "mira", "review_violation", "forgive-1", {"violation_id": observed["violation_id"], "decision": "forgive"})
    assert forgiven["status"] == "accepted"
    assert platform.store.repositories.contested.active_restriction("tomas", "water_reserve", "resource_withdrawal") is None
    restored = submit(platform, "tomas", "withdraw_resource", "withdraw-restored")
    assert restored["status"] == "accepted"
    advance_until(platform, lambda: len(events(platform, "resource.withdrawal.completed")) >= 1)
    assert resource_amount(platform) == 8

    move(platform, "mira", "relay_yard")
    move(platform, "tomas", "water_plant")
    hidden_take = submit(platform, "tomas", "take_resource", "take-hidden")
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


def test_contract_gate_accepts_the_generic_capability_pack(platform: Platform) -> None:
    """The world's own contract recognizes one bound transfer family as complete."""

    install_social_content(platform)
    evolution = dict(platform.store.repositories.metadata.get("evolution", {}) or {})
    contract = dict((evolution.get("contracts", {}) or {}).get("god_structural", {}) or {})
    required = dict(contract.get("required_content") or {})
    assert required.get("generic_parameters") == ["resource_id"]
    _set_required_content(platform, required)
    provider = _SequenceProvider([{"intent_type": "wait", "reason": "The generic pack is complete."}])
    turn = AgentEngine(platform, provider, max_iterations=2).run(
        role="god",
        actor_id="god",
        task_type="god_structural",
    )
    assert turn.result["status"] == "wait", turn.result
    assert turn.result.get("failed_check") != "required_content", turn.result


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
    definition["effects"] = [{"type": "resource_withdrawal", "resource_id": "missing_reserve", "amount": 1, "scope": "resource_withdrawal"}]
    rejected({**base_action, "name": "Bad Missing Resource", "definition": definition, "followup_behavior": {**base_action["followup_behavior"], "name": "Bad Missing Resource", "definition": {**base_action["followup_behavior"]["definition"], "id": "bad_missing_resource"}}}, kind="definition")

    definition = dict(base_action["definition"])
    definition["id"] = "bad_wrong_target"
    definition["effects"] = [{"type": "resource_withdrawal", "resource_id": "relay_station", "amount": 1, "scope": "resource_withdrawal"}]
    rejected({**base_action, "name": "Bad Wrong Target", "definition": definition, "followup_behavior": {**base_action["followup_behavior"], "name": "Bad Wrong Target", "definition": {**base_action["followup_behavior"]["definition"], "id": "bad_wrong_target"}}}, kind="definition")

    definition = dict(base_action["definition"])
    definition["id"] = "bad_empty_scope"
    definition["effects"] = [{"type": "resource_withdrawal", "resource_id": "water_reserve", "amount": 1, "scope": ""}]
    rejected({**base_action, "name": "Bad Empty Scope", "definition": definition, "followup_behavior": {**base_action["followup_behavior"], "name": "Bad Empty Scope", "definition": {**base_action["followup_behavior"]["definition"], "id": "bad_empty_scope"}}}, kind="definition")

    take_definition = dict(TAKE["definition"])
    take_definition["id"] = "bad_take_mode"
    take_definition["effects"] = [{"type": "resource_take", "resource_id": "water_reserve", "amount": 1, "scope": "resource_withdrawal", "mode": "public"}]
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
            "effect": {"type": "authorization", "mode": "resource_access", "target": "missing_reserve", "scope": "resource_withdrawal", "behavior_ids": ["travel"]},
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
        "water_reserve_stewardship",
        "water_approval_authority",
        "water_revocation_authority",
        "water_violation_authority",
        "fuel_depot",
        "fuel_reserve",
        "fuel_reserve_stewardship",
        "fuel_violation_authority",
        "coolant_shed",
        "coolant_reserve",
        "coolant_reserve_stewardship",
        "coolant_violation_authority",
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

    denied = submit(platform, "tomas", "withdraw_resource", "deny-preserved")
    assert denied["failed_check"] == "missing_delegation"
    assert denied["rule_id"] == "water_reserve_stewardship"

    move(platform, "mira", "water_plant")
    move(platform, "tomas", "water_plant")
    assert submit(platform, "tomas", "take_resource", "take-preserved")["status"] == "accepted"
    advance_until(platform, lambda: bool(platform.store.repositories.contested.list_violations()))
    assert latest(platform, "violation.observed")["witnesses"] == ["mira"]


def test_da_09_negative_space(platform: Platform) -> None:
    install_social_content(platform)
    fill_reserve(platform)
    move(platform, "mira", "water_plant")
    move(platform, "tomas", "water_plant")
    assert submit(platform, "tomas", "take_resource", "take-negative")["status"] == "accepted"
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
    story_tasks = [task for task in tasks if task.get("role") == "story"]
    assert len(story_tasks) == 1
    assert story_tasks[0]["source_event_type"] == "violation.observed"
    assert not [task for task in tasks if task.get("role") == "god"]
    assert all(
        str(task.get("task_type") or "").startswith("behavior_assigned:")
        for task in tasks
        if task.get("role") != "story"
    )


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
    denied = submit(platform, "tomas", "withdraw_resource", "visible-deny")
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
    withdrawn = submit(platform, "tomas", "withdraw_resource", "visible-withdraw")
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
    bad_definition["effects"] = [{"type": "resource_withdrawal", "resource_id": "missing_reserve", "amount": 1, "scope": "resource_withdrawal"}]
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


def test_da_12_second_resource_reuses_same_primitives(platform: Platform) -> None:
    """A second resource reuses the same actions, rules, witnesses and review path."""

    water = install_social_content(platform)
    before = content_counts(platform)
    fuel = install_second_resource(platform)
    after = content_counts(platform)
    assert all(item["status"] == "accepted" for item in {**water, **fuel}.values())
    assert after["actions"] == before["actions"]
    assert after["behaviors"] == before["behaviors"]
    assert resource_amount_of(platform, "water_reserve") == 0
    assert resource_amount_of(platform, "fuel_reserve") == 0

    fill_reserve(platform, "fill-water-second")
    fill_fuel(platform)
    assert resource_amount(platform) == 10
    assert resource_amount_of(platform, "fuel_reserve") == 10

    grant_delegation_for(platform, "withdraw_resource", "water")
    grant_delegation_for(platform, "withdraw_resource", "fuel", "Fuel Reserve")

    move(platform, "mira", "fuel_depot")
    move(platform, "tomas", "fuel_depot")
    assert submit(platform, "tomas", "take_resource", "take-fuel-1", {"resource_id": "Fuel Reserve"})["status"] == "accepted"
    advance_until(platform, lambda: bool(platform.store.repositories.contested.list_violations()))
    violation = platform.store.repositories.contested.list_violations()[0]
    assert violation["target"] == "fuel_reserve"
    assert violation["witnesses"] == ["mira"]
    assert violation["scope"] == "resource_withdrawal"
    assert resource_amount_of(platform, "fuel_reserve") == 9

    sanctioned = submit(
        platform,
        "mira",
        "review_violation",
        "sanction-fuel-1",
        {"violation_id": violation["violation_id"], "decision": "sanction"},
    )
    assert sanctioned["status"] == "accepted"
    assert platform.store.repositories.contested.active_restriction("tomas", "fuel_reserve", "resource_withdrawal") is not None
    assert platform.store.repositories.contested.active_restriction("tomas", "water_reserve", "resource_withdrawal") is None

    water_allowed = submit(platform, "tomas", "withdraw_resource", "water-after-fuel-sanction")
    assert water_allowed["status"] == "accepted"
    advance_until(platform, lambda: bool(events(platform, "resource.withdrawal.completed")))
    fuel_blocked = submit(platform, "tomas", "withdraw_resource", "fuel-after-sanction", {"resource_id": "Fuel Reserve"})
    assert fuel_blocked["failed_check"] == "access_restricted"

    forgiven = submit(
        platform,
        "mira",
        "review_violation",
        "forgive-fuel-1",
        {"violation_id": violation["violation_id"], "decision": "forgive"},
    )
    assert forgiven["status"] == "accepted"
    assert platform.store.repositories.contested.active_restriction("tomas", "fuel_reserve", "resource_withdrawal") is None
    fuel_restored = submit(platform, "tomas", "withdraw_resource", "fuel-after-forgive", {"resource_id": "Fuel Reserve"})
    assert fuel_restored["status"] == "accepted"


def test_da_13_third_resource_has_independent_scope(platform: Platform) -> None:
    """A third resource proves the same DSL scales without cross-resource leaks."""

    water = install_social_content(platform)
    before = content_counts(platform)
    results = {
        **water,
        **install_second_resource(platform),
        **install_third_resource(platform),
    }
    after = content_counts(platform)
    assert all(item["status"] == "accepted" for item in results.values())
    assert after["actions"] == before["actions"]
    assert after["behaviors"] == before["behaviors"]
    fill_reserve(platform, "fill-water-third")
    fill_fuel(platform)
    fill_coolant(platform)
    assert resource_amount(platform) == 10
    assert resource_amount_of(platform, "fuel_reserve") == 10
    assert resource_amount_of(platform, "coolant_reserve") == 10

    grant_delegation_for(platform, "withdraw_resource", "water-third")
    grant_delegation_for(platform, "withdraw_resource", "fuel-third", "Fuel Reserve")
    grant_delegation_for(platform, "withdraw_resource", "coolant-third", "Coolant Reserve")

    move(platform, "mira", "coolant_shed")
    move(platform, "tomas", "coolant_shed")
    assert submit(platform, "tomas", "take_resource", "take-coolant-1", {"resource_id": "Coolant Reserve"})["status"] == "accepted"
    advance_until(platform, lambda: bool(platform.store.repositories.contested.list_violations()))
    violation = platform.store.repositories.contested.list_violations()[0]
    assert violation["target"] == "coolant_reserve"
    assert violation["scope"] == "resource_withdrawal"
    assert violation["witnesses"] == ["mira"]

    sanctioned = submit(
        platform,
        "mira",
        "review_violation",
        "sanction-coolant-1",
        {"violation_id": violation["violation_id"], "decision": "sanction"},
    )
    assert sanctioned["status"] == "accepted"
    assert platform.store.repositories.contested.active_restriction("tomas", "coolant_reserve", "resource_withdrawal") is not None
    assert platform.store.repositories.contested.active_restriction("tomas", "water_reserve", "resource_withdrawal") is None
    assert platform.store.repositories.contested.active_restriction("tomas", "fuel_reserve", "resource_withdrawal") is None

    entry = behavior_entry(platform, "tomas", "withdraw_resource")
    options = {str(option["value"]): option for option in entry["parameter_options"]["resource_id"]}
    assert options["water_reserve"]["available"] is True
    assert options["fuel_reserve"]["available"] is True
    assert options["coolant_reserve"]["available"] is False
    assert "restriction" in options["coolant_reserve"]["blocked_reason"]

    water_allowed = submit(platform, "tomas", "withdraw_resource", "water-after-coolant-sanction", {"resource_id": "Water Reserve"})
    assert water_allowed["status"] == "accepted"
    advance_until(platform, lambda: bool(events(platform, "resource.withdrawal.completed")))
    fuel_allowed = submit(platform, "tomas", "withdraw_resource", "fuel-after-coolant-sanction", {"resource_id": "Fuel Reserve"})
    assert fuel_allowed["status"] == "accepted"
    advance_until(platform, lambda: len(events(platform, "resource.withdrawal.completed")) >= 2)
    coolant_blocked = submit(platform, "tomas", "withdraw_resource", "coolant-after-sanction", {"resource_id": "Coolant Reserve"})
    assert coolant_blocked["failed_check"] == "access_restricted"

    forgiven = submit(
        platform,
        "mira",
        "review_violation",
        "forgive-coolant-1",
        {"violation_id": violation["violation_id"], "decision": "forgive"},
    )
    assert forgiven["status"] == "accepted"
    assert platform.store.repositories.contested.active_restriction("tomas", "coolant_reserve", "resource_withdrawal") is None
    assert submit(platform, "tomas", "withdraw_resource", "coolant-after-forgive", {"resource_id": "Coolant Reserve"})["status"] == "accepted"


class _SequenceProvider:
    """Return one scripted model response per ReAct iteration."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.calls += 1
        return dict(self.responses.pop(0))


class _ToolSequenceProvider:
    def __init__(self, names: list[str]) -> None:
        self.names = list(names)
        self.calls = 0

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.calls += 1
        name = self.names[min(self.calls - 1, len(self.names) - 1)]
        return {"tool_calls": [{
            "id": f"call-{self.calls}",
            "type": "function",
            "function": {
                "name": "propose_entity",
                "arguments": {"entity_type": "faction", "name": name, "description": f"Faction {name}."},
            },
        }]}


def test_god_structural_accepts_multiple_proposals_in_one_turn(platform: Platform) -> None:
    """The broad structural contract may submit several proposals without ending at the first."""

    _set_required_content(platform, {})
    provider = _SequenceProvider([
        {
            "intent_type": "propose_entity",
            "entity_type": "faction",
            "name": "Multi Proposal Faction A",
            "description": "A faction created in a multi-proposal God turn.",
        },
        {
            "intent_type": "propose_entity",
            "entity_type": "faction",
            "name": "Multi Proposal Faction B",
            "description": "A second faction created in the same God turn.",
        },
        {"intent_type": "wait", "reason": "Both structural proposals are submitted."},
    ])
    turn = AgentEngine(platform, provider, max_iterations=5).run(
        role="god",
        actor_id="god",
        task_type="god_structural",
    )
    assert turn.result["status"] == "wait", turn.result
    factions = {
        str(item.get("name"))
        for item in platform.store.repositories.entities.entities_of_type("faction")
    }
    assert {"Multi Proposal Faction A", "Multi Proposal Faction B"} <= factions
    assert provider.calls == 3


def _set_contract(platform: Platform, **fields: Any) -> None:
    with platform.store.transaction():
        evolution = dict(platform.store.repositories.metadata.get("evolution", {}) or {})
        contracts = dict(evolution.get("contracts", {}) or {})
        contract = dict(contracts.get("god_structural", {}) or {})
        contract.pop("stages", None)
        contract.update(fields)
        contracts["god_structural"] = contract
        evolution["contracts"] = contracts
        platform.store.repositories.metadata.set("evolution", evolution)


def _set_season_plan(platform: Platform, target_cell: str) -> None:
    baseline = int((build_growth_ledger(platform.store)["cells"].get(target_cell) or {}).get("count", 0))
    with platform.store.transaction():
        platform.store.repositories.metadata.set("season_plan", {
            "plan_id": f"plan:test:{target_cell}",
            "target_cell": target_cell,
            "baseline": baseline,
            "created_minute": 1,
            "expires_minute": 1000,
            "reason": "test plan",
            "status": "active",
        })


def test_structural_contract_stops_at_max_proposals(platform: Platform) -> None:
    """A one-unit contract must not keep the God turn open after its proposal."""

    _set_required_content(platform, {})
    _set_contract(platform, max_proposals=1)
    provider = _SequenceProvider([
        {
            "intent_type": "propose_entity",
            "entity_type": "faction",
            "name": "Bounded Proposal Faction",
            "description": "The only proposal this contract allows.",
        },
        {
            "intent_type": "propose_entity",
            "entity_type": "faction",
            "name": "Rejected Extra Faction",
            "description": "This proposal must never be requested.",
        },
        {"intent_type": "wait", "reason": "Nothing else is required."},
    ])
    turn = AgentEngine(platform, provider, max_iterations=5).run(
        role="god",
        actor_id="god",
        task_type="god_structural",
    )
    assert turn.result["status"] == "accepted", turn.result
    assert provider.calls == 1
    factions = {str(item.get("name")) for item in platform.store.repositories.entities.entities_of_type("faction")}
    assert "Bounded Proposal Faction" in factions
    assert "Rejected Extra Faction" not in factions


def test_growth_cell_contract_requires_the_target_cell_to_increase(platform: Platform) -> None:
    """A proposal outside the Director's cell must be corrected, not accepted as growth."""

    _set_required_content(platform, {"growth_cell": True})
    _set_contract(platform, max_proposals=3)
    _set_season_plan(platform, "social")
    provider = _SequenceProvider([
        {
            "intent_type": "propose_entity",
            "entity_type": "agent",
            "name": "Uncounted Helper",
            "description": "A character does not increase the social cell.",
            "state": {
                "role": "helper",
                "ultimate_goal": "Help the relay.",
                "initial_state": {"location_id": "relay_lab", "status": "idle"},
            },
        },
        {
            "intent_type": "propose_entity",
            "entity_type": "faction",
            "name": "Relay Bargain Circle",
            "description": "A faction that increases the social cell.",
        },
    ])
    turn = AgentEngine(platform, provider, max_iterations=5).run(
        role="god",
        actor_id="god",
        task_type="god_structural",
    )
    assert turn.result["status"] == "accepted", turn.result
    assert provider.calls == 2
    factions = {str(item.get("name")) for item in platform.store.repositories.entities.entities_of_type("faction")}
    assert "Relay Bargain Circle" in factions
    assert build_growth_ledger(platform.store)["cells"]["social"]["count"] == 1


def test_structural_tool_budget_ends_the_session_without_spinning(platform: Platform) -> None:
    """Once no proposal can be submitted, more model calls cannot help."""

    _set_required_content(platform, {})
    _set_contract(platform, max_proposals=3)
    provider = _ToolSequenceProvider(["Budget Faction 1", "Budget Faction 2", "Budget Faction 3"])
    turn = AgentEngine(platform, provider, max_iterations=8, max_tool_calls=1).run(
        role="god",
        actor_id="god",
        task_type="god_structural",
    )
    assert turn.result["status"] == "rejected", turn.result
    assert turn.result["failed_check"] == "budget_exhausted"
    assert provider.calls == 2
    factions = {str(item.get("name")) for item in platform.store.repositories.entities.entities_of_type("faction")}
    assert "Budget Faction 1" in factions
    assert "Budget Faction 2" not in factions


def test_behavior_with_social_parameters_requires_authorization_operation(platform: Platform) -> None:
    """An inert administrative behavior must not pass only because the field is optional."""

    payload = copy.deepcopy(REQUEST)
    payload["followup_behavior"]["definition"].pop("authorization_operation", None)
    result = platform.submit_definition_proposal("god", payload)
    assert result["status"] == "rejected", result
    errors = " ".join(str(item) for item in result.get("errors") or [])
    assert "authorization_operation" in errors


def test_god_structural_wait_is_rejected_when_required_content_is_missing(platform: Platform) -> None:
    """A premature wait is returned to the model with the concrete missing list."""

    _set_required_content(platform, {"entities": ["Required Test Place"]})
    provider = _SequenceProvider([
        {"intent_type": "wait", "reason": "The pack looks complete."},
        {
            "intent_type": "propose_entity",
            "entity_type": "location",
            "name": "Required Test Place",
            "description": "A required place created after the contract correction.",
            "state": {"routes": ["relay_station"], "route_distances": {"relay_station": 1}},
        },
        {"intent_type": "wait", "reason": "The required place now exists."},
    ])
    turn = AgentEngine(platform, provider, max_iterations=5).run(
        role="god",
        actor_id="god",
        task_type="god_structural",
    )
    assert turn.result["status"] == "accepted", turn.result
    locations = [item for item in platform.store.repositories.entities.entities_of_type("location") if item.get("name") == "Required Test Place"]
    assert locations
    assert provider.calls == 2


def test_structural_progress_finishes_when_the_required_pack_is_complete(platform: Platform) -> None:
    """The engine stops as soon as the semantic pack is complete, without a final wait call."""

    _set_required_content(platform, {"entities": ["Required Place A", "Required Place B"]})
    provider = _SequenceProvider([
        {
            "intent_type": "propose_entity",
            "entity_type": "location",
            "name": "Required Place A",
            "description": "The first required place.",
            "state": {"routes": ["relay_station"], "route_distances": {"relay_station": 1}},
        },
        {
            "intent_type": "propose_entity",
            "entity_type": "location",
            "name": "Required Place B",
            "description": "The second required place.",
            "state": {"routes": ["relay_station"], "route_distances": {"relay_station": 1}},
        },
    ])
    turn = AgentEngine(platform, provider, max_iterations=5).run(
        role="god",
        actor_id="god",
        task_type="god_structural",
    )
    assert turn.result["status"] == "accepted", turn.result
    names = {item.get("name") for item in platform.store.repositories.entities.entities_of_type("location")}
    assert {"Required Place A", "Required Place B"} <= names
    assert provider.calls == 2


def test_structural_stages_advance_automatically(platform: Platform) -> None:
    """A contract can decompose one broad task into sequential atomic stages."""

    _set_stages(platform, [
        {
            "instruction": "Create Required Stage Place A.",
            "required_content": {"entities": ["Required Stage Place A"]},
        },
        {
            "instruction": "Create Required Stage Place B.",
            "required_content": {"entities": ["Required Stage Place B"]},
        },
    ])
    provider = _SequenceProvider([
        {
            "intent_type": "propose_entity",
            "entity_type": "location",
            "name": "Required Stage Place A",
            "description": "The first automatic stage.",
            "state": {"routes": ["relay_station"], "route_distances": {"relay_station": 1}},
        },
        {
            "intent_type": "propose_entity",
            "entity_type": "location",
            "name": "Required Stage Place B",
            "description": "The second automatic stage.",
            "state": {"routes": ["relay_station"], "route_distances": {"relay_station": 1}},
        },
    ])
    turn = AgentEngine(platform, provider, max_iterations=5).run(
        role="god",
        actor_id="god",
        task_type="god_structural",
    )
    assert turn.result["status"] == "accepted", turn.result
    names = {item.get("name") for item in platform.store.repositories.entities.entities_of_type("location")}
    assert {"Required Stage Place A", "Required Stage Place B"} <= names
    assert provider.calls == 2
    progress = platform.store.repositories.metadata.get("structural_progress", {}) or {}
    entry = progress.get("god:god_structural:no-plan") or {}
    assert entry.get("status") == "completed"
    assert entry.get("stage_index") == 2
    assert len(entry.get("accepted") or []) == 2


class _ProgressAwareProvider:
    """Record each prompt so a resumed session can assert its stage boundary."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.prompts: list[str] = []
        self.messages: list[list[dict[str, Any]]] = []

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.calls += 1
        self.prompts.append(str(context))
        self.messages.append(list(messages or []))
        return dict(self.responses.pop(0))


def test_structural_progress_resumes_at_the_persisted_stage(platform: Platform) -> None:
    """An interrupted structural session continues at its saved stage, not stage 1."""

    stages = [
        {
            "instruction": "Create Resumed Place A.",
            "required_content": {"entities": ["Resumed Place A"]},
        },
        {
            "instruction": "Create Resumed Place B.",
            "required_content": {"entities": ["Resumed Place B"]},
        },
    ]
    _set_stages(platform, stages)
    # Stage A is already committed and recorded; only stage B remains.
    with platform.store.transaction():
        entities = platform.store.repositories.entities
        entities.put_entity(
            "resumed_place_a", "location", "Resumed Place A",
            {"description": "The first resumed stage.", "initial_state": {"routes": ["relay_station"], "route_distances": {"relay_station": 1}}},
        )
        platform.store.repositories.metadata.set("structural_progress", {
            "god:god_structural:no-plan": {
                "stage_index": 1,
                "status": "in_progress",
                "accepted": ["Resumed Place A"],
            },
        })
    provider = _ProgressAwareProvider([
        {
            "intent_type": "propose_entity",
            "entity_type": "location",
            "name": "Resumed Place B",
            "description": "The second resumed stage.",
            "state": {"routes": ["relay_station"], "route_distances": {"relay_station": 1}},
        },
    ])
    turn = AgentEngine(platform, provider, max_iterations=5).run(
        role="god",
        actor_id="god",
        task_type="god_structural",
    )
    assert turn.result["status"] == "accepted", turn.result
    assert provider.calls == 1
    joined_messages = "\n".join(
        str(item.get("content"))
        for message in provider.messages
        for item in message
        if isinstance(item, dict)
    )
    assert "Structural stage 2/2: Create Resumed Place B." in joined_messages
    assert "Structural progress already accepted: Resumed Place A." in joined_messages
    names = {item.get("name") for item in platform.store.repositories.entities.entities_of_type("location")}
    assert "Resumed Place B" in names


def test_access_request_publishes_the_configured_story_wake(platform: Platform) -> None:
    """A validated access request can wake Story through the configured event path."""

    install_social_content(platform)
    fill_reserve(platform)
    move(platform, "mira", "water_plant")
    move(platform, "tomas", "water_plant")
    denied = submit(platform, "tomas", "withdraw_resource", "wake-denied")
    assert denied["failed_check"] == "missing_delegation"
    denial = latest(platform, "behavior.denied")
    requested = submit(
        platform,
        "tomas",
        "request_access",
        "wake-request",
        {"based_on_event": str(denial["event_id"])},
    )
    assert requested["status"] == "accepted"
    story_tasks = [
        item for item in platform.ready_evolution_tasks()
        if item.get("role") == "story" and item.get("source_event_type") == "access.requested"
    ]
    assert story_tasks
