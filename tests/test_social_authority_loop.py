"""Phase 1A deterministic contract for the Social Authority Loop.

Each test maps to one row of the frozen acceptance matrix in
``docs/design/social-authority-loop-phase1.md``.  These tests use only the
probe world; they do not call a model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sim2.agents.tool_executor import AgentToolExecutor
from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform

WORLD = Path(__file__).parents[1] / "worlds" / "social_authority_probe"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def platform(tmp_path: Path):
    instance = Platform(create_instance(WORLD, "social-authority", tmp_path / "runtime"))
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
            reason="contract test",
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
    rows = platform.store.repositories.events.timeline_events(after_event_id=0, limit=500, include_ticks=True)
    return [row for row in rows if event_type is None or row.get("event_type") == event_type]


def latest(platform: Platform, event_type: str) -> dict[str, Any]:
    rows = events(platform, event_type)
    assert rows, f"missing {event_type}"
    return rows[-1]


def resource_amount(platform: Platform) -> float:
    return float((platform.store.repositories.metadata.get("world_properties", {}) or {}).get("energy_reserve", 0) or 0)


def state_snapshot(platform: Platform) -> dict[str, Any]:
    return {
        "revision": int(platform.store.repositories.metadata.get("world_revision", 0) or 0),
        "events": int(platform.store.connection.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]),
        "requests": platform.store.repositories.authorization.count_requests(),
        "delegations": platform.store.repositories.authorization.count_delegations(),
        "resource": resource_amount(platform),
    }


def deny_tomas(platform: Platform, intent_id: str = "deny-1") -> dict[str, Any]:
    result = submit(platform, "tomas", "withdraw_resource", intent_id)
    assert result["status"] == "rejected"
    return latest(platform, "behavior.denied")


def open_request(
    platform: Platform,
    *,
    requester: str = "tomas",
    request_id: str = "req-test",
    behavior: str = "Withdraw Resource",
    behavior_id: str = "withdraw_resource",
    target: str = "energy_reserve",
    scope: str = "energy_withdrawal",
    reason: str = "test request",
    minute: int | None = None,
    validate_causal: bool = False,
) -> dict[str, Any]:
    agent = platform.store.repositories.entities.get_agent(requester)
    return platform._services["authorization"].create_request(
        agent,
        {
            "request_id": request_id,
            "behavior": behavior,
            "behavior_id": behavior_id,
            "target": target,
            "scope": scope,
            "reason": reason,
        },
        minute=minute,
        validate_causal=validate_causal,
    )["request"]


def grant_delegation(
    platform: Platform,
    *,
    delegation_id: str = "del-injected",
    source: str = "tomas",
    target: str = "energy_reserve",
    scope: str = "energy_withdrawal",
) -> dict[str, Any]:
    delegation = {
        "delegation_id": delegation_id,
        "source": source,
        "target": target,
        "type": "delegation",
        "granted_by": "mira",
        "scope": scope,
        "status": "active",
        "granted_at": 0,
        "expires_minute": None,
        "revoked_at": None,
        "revoked_by": None,
        "request_id": None,
    }
    with platform.store.transaction():
        platform.store.repositories.authorization.put_delegation(delegation)
    return delegation


def advance_until(platform: Platform, predicate, limit: int = 12) -> None:
    for _ in range(limit):
        if predicate():
            return
        platform.step()
    raise AssertionError("world did not reach the expected state")


# ---------------------------------------------------------------------------
# SA-01 .. SA-06: main chain
# ---------------------------------------------------------------------------
def test_sa_01_preview_discriminates_steward(platform: Platform) -> None:
    before = state_snapshot(platform)
    mira = behavior_entry(platform, "mira", "withdraw_resource")
    tomas = behavior_entry(platform, "tomas", "withdraw_resource")

    assert mira["eligibility"]["available"] is True
    assert mira["eligibility"]["authorization"] == {"type": "stewardship", "id": "energy_reserve"}
    steward_preview = AgentToolExecutor(platform, role="agent_decision", actor_id="mira").execute(
        "preview_behavior", {"choice": "Withdraw Resource"},
    )
    assert steward_preview["behavior"]["eligibility"]["available"] is True
    assert tomas["eligibility"]["available"] is False
    assert tomas["eligibility"]["reasons"][0]["type"] == "missing_delegation"
    assert tomas["eligibility"]["reasons"][0]["attemptable"] is True
    assert tomas["eligibility"]["reasons"][0]["rule_id"] == "energy_reserve_stewardship"
    assert tomas["attemptable"] is True
    assert state_snapshot(platform) == before


def test_sa_02_non_steward_withdraw_denied(platform: Platform) -> None:
    result = submit(platform, "tomas", "withdraw_resource", "deny-1")

    assert result["status"] == "rejected"
    assert result["failed_check"] == "missing_delegation"
    assert result["rule_id"] == "energy_reserve_stewardship"
    assert result["authority"] == {"type": "rule", "id": "energy_reserve_stewardship"}

    requested = latest(platform, "behavior.requested")
    denied = latest(platform, "behavior.denied")
    assert requested["event_id"] < denied["event_id"]
    assert requested["behavior"] == "Withdraw Resource"
    assert requested["target"] == "energy_reserve"
    assert denied["reason"] == "missing_delegation"
    assert denied["rule_id"] == "energy_reserve_stewardship"
    assert denied["authority"] == {"type": "rule", "id": "energy_reserve_stewardship"}

    assert resource_amount(platform) == 10
    assert platform.store.repositories.authorization.list_delegations() == []
    assert events(platform, "access.approved") == []
    assert events(platform, "resource.withdrawal.completed") == []
    memory = platform.decision_view("tomas")["denial_memories"]
    assert memory and memory[0]["event_id"] == denied["event_id"]
    assert memory[0]["reason"] == "missing_delegation"


def test_sa_03_request_cites_denial(platform: Platform) -> None:
    denied = deny_tomas(platform)
    result = submit(
        platform, "tomas", "request_access", "request-1",
        {"based_on_event": str(denied["event_id"]), "reason": "Need energy for relay maintenance"},
    )

    assert result["status"] == "accepted"
    request = platform.store.repositories.authorization.get_request(result["request_id"])
    assert request["status"] == "pending"
    assert request["behavior"] == "Withdraw Resource"
    assert request["target"] == "energy_reserve"
    assert request["scope"] == "energy_withdrawal"
    assert request["based_on_event"] == denied["event_id"]

    event = latest(platform, "access.requested")
    assert event["request_id"] == result["request_id"]
    assert event["requester"] == "tomas"
    assert event["behavior"] == "Withdraw Resource"
    assert event["target"] == "energy_reserve"
    assert event["scope"] == "energy_withdrawal"
    assert event["reason"] == "Need energy for relay maintenance"
    assert event["based_on_event"] == denied["event_id"]


def test_sa_04_approval_creates_delegation(platform: Platform) -> None:
    denied = deny_tomas(platform)
    request = submit(
        platform, "tomas", "request_access", "request-1",
        {"based_on_event": str(denied["event_id"]), "reason": "Need energy"},
    )
    result = submit(platform, "mira", "approve_withdrawal", "approve-1", {"request_id": request["request_id"]})

    assert result["status"] == "accepted"
    request_row = platform.store.repositories.authorization.get_request(request["request_id"])
    assert request_row["status"] == "approved"
    delegations = platform.store.repositories.authorization.list_delegations()
    assert len(delegations) == 1
    delegation = delegations[0]
    assert delegation["status"] == "active"
    assert delegation["source"] == "tomas"
    assert delegation["target"] == "energy_reserve"
    assert delegation["scope"] == "energy_withdrawal"
    assert delegation["granted_by"] == "mira"

    approved = latest(platform, "access.approved")
    assert approved["request_id"] == request["request_id"]
    assert approved["approver"] == "mira"
    assert approved["subject"] == "tomas"
    assert approved["scope"] == "energy_withdrawal"
    assert approved["delegation_id"] == delegation["delegation_id"]


def test_sa_05_withdrawal_decrements_once(platform: Platform) -> None:
    denied = deny_tomas(platform)
    request = submit(
        platform, "tomas", "request_access", "request-1",
        {"based_on_event": str(denied["event_id"]), "reason": "Need energy"},
    )
    submit(platform, "mira", "approve_withdrawal", "approve-1", {"request_id": request["request_id"]})

    result = submit(platform, "tomas", "withdraw_resource", "withdraw-1")
    assert result["status"] == "accepted"
    assert resource_amount(platform) == 10
    started = latest(platform, "resource.withdrawal.started")
    assert started["intent_id"] == "withdraw-1"
    assert started["authorization"]["type"] == "delegation"

    advance_until(platform, lambda: bool(events(platform, "resource.withdrawal.completed")))
    completed = latest(platform, "resource.withdrawal.completed")
    assert completed["intent_id"] == "withdraw-1"
    assert completed["actor"] == "tomas"
    assert completed["target"] == "energy_reserve"
    assert completed["authorization"] == started["authorization"]
    assert completed["amount"] == 1
    assert completed["before"] == 10
    assert completed["after"] == 9
    assert resource_amount(platform) == 9
    assert len(events(platform, "resource.withdrawal.completed")) == 1


def test_sa_06_revocation_denies_withdrawal(platform: Platform) -> None:
    denied = deny_tomas(platform)
    request = submit(
        platform, "tomas", "request_access", "request-1",
        {"based_on_event": str(denied["event_id"]), "reason": "Need energy"},
    )
    approved = submit(platform, "mira", "approve_withdrawal", "approve-1", {"request_id": request["request_id"]})
    delegation_id = approved["delegation_id"]
    submit(platform, "tomas", "withdraw_resource", "withdraw-1")
    advance_until(platform, lambda: bool(events(platform, "resource.withdrawal.completed")))
    assert resource_amount(platform) == 9

    non_steward = submit(platform, "tomas", "revoke_delegation", "revoke-denied", {"delegation_id": delegation_id})
    assert non_steward["failed_check"] == "missing_stewardship"
    assert platform.store.repositories.authorization.get_delegation(delegation_id)["status"] == "active"

    revoked = submit(platform, "mira", "revoke_delegation", "revoke-1", {"delegation_id": delegation_id})
    assert revoked["status"] == "accepted"
    row = platform.store.repositories.authorization.get_delegation(delegation_id)
    assert row["status"] == "revoked"
    assert row["revoked_by"] == "mira"
    revoked_event = latest(platform, "delegation.revoked")
    assert revoked_event["delegation_id"] == delegation_id

    after = submit(platform, "tomas", "withdraw_resource", "withdraw-2")
    assert after["status"] == "rejected"
    assert after["failed_check"] == "delegation_revoked"
    assert resource_amount(platform) == 9
    assert len(events(platform, "resource.withdrawal.started")) == 1
    assert len(events(platform, "resource.withdrawal.completed")) == 1


# ---------------------------------------------------------------------------
# SA-07 .. SA-09: independent decision fixtures
# ---------------------------------------------------------------------------
def test_sa_07_self_approval_denied(platform: Platform) -> None:
    request = open_request(platform, requester="mira", request_id="req-self")
    preview = behavior_entry(platform, "mira", "approve_withdrawal")
    assert preview["eligibility"]["reasons"][0]["type"] == "self_approval_forbidden"

    result = submit(platform, "mira", "approve_withdrawal", "self-1", {"request_id": request["request_id"]})
    assert result["failed_check"] == "self_approval_forbidden"
    assert result["authority"] == {"type": "institution", "id": "approval_authority"}
    self_reject = submit(platform, "mira", "reject_withdrawal", "self-2", {"request_id": request["request_id"]})
    assert self_reject["failed_check"] == "self_approval_forbidden"
    assert platform.store.repositories.authorization.get_request("req-self")["status"] == "pending"
    assert platform.store.repositories.authorization.list_delegations() == []
    assert events(platform, "access.approved") == []
    assert events(platform, "access.rejected") == []
    assert events(platform, "resource.withdrawal.started") == []
    assert latest(platform, "behavior.denied")["reason"] == "self_approval_forbidden"


def test_sa_08_non_steward_approver_denied(platform: Platform) -> None:
    request = open_request(platform, requester="mira", request_id="req-mira")
    result = submit(platform, "tomas", "approve_withdrawal", "non-steward-1", {"request_id": request["request_id"]})

    assert result["failed_check"] == "missing_stewardship"
    assert result["authority"] == {"type": "institution", "id": "approval_authority"}
    assert platform.store.repositories.authorization.get_request("req-mira")["status"] == "pending"
    assert platform.store.repositories.authorization.list_delegations() == []
    assert events(platform, "access.approved") == []
    assert events(platform, "resource.withdrawal.started") == []


def test_sa_09_reject_branch(platform: Platform) -> None:
    denied = deny_tomas(platform)
    request = submit(
        platform, "tomas", "request_access", "request-1",
        {"based_on_event": str(denied["event_id"]), "reason": "Need energy"},
    )
    result = submit(platform, "mira", "reject_withdrawal", "reject-1", {"request_id": request["request_id"], "reason": "Not now"})

    assert result["status"] == "accepted"
    assert platform.store.repositories.authorization.get_request(request["request_id"])["status"] == "rejected"
    rejected = latest(platform, "access.rejected")
    assert rejected["request_id"] == request["request_id"]
    assert rejected["approver"] == "mira"
    assert rejected["reason"] == "Not now"
    assert platform.store.repositories.authorization.list_delegations() == []
    assert events(platform, "access.approved") == []
    assert events(platform, "resource.withdrawal.started") == []
    assert events(platform, "resource.withdrawal.completed") == []
    assert resource_amount(platform) == 10

    after = submit(platform, "tomas", "withdraw_resource", "withdraw-after-reject")
    assert after["failed_check"] == "missing_delegation"
    assert resource_amount(platform) == 10


# ---------------------------------------------------------------------------
# SA-10 .. SA-12: approval and scope invariants
# ---------------------------------------------------------------------------
def test_sa_10_approval_mismatch_codes(platform: Platform) -> None:
    request = open_request(platform, requester="tomas", request_id="req-mismatch")
    cases = [
        ({"subject": "someone_else"}, "request_subject_mismatch"),
        ({"target": "other_reserve"}, "request_target_mismatch"),
        ({"scope": "other_scope"}, "request_scope_mismatch"),
    ]
    for index, (parameters, code) in enumerate(cases):
        result = submit(
            platform, "mira", "approve_withdrawal", f"mismatch-{index}",
            {"request_id": request["request_id"], **parameters},
        )
        assert result["failed_check"] == code
    assert platform.store.repositories.authorization.get_request("req-mismatch")["status"] == "pending"
    assert platform.store.repositories.authorization.list_delegations() == []

    approved = submit(platform, "mira", "approve_withdrawal", "approve-1", {"request_id": request["request_id"]})
    assert approved["status"] == "accepted"
    resolved = submit(platform, "mira", "approve_withdrawal", "approve-2", {"request_id": request["request_id"]})
    assert resolved["failed_check"] == "request_not_pending"
    assert len(platform.store.repositories.authorization.list_delegations()) == 1


def test_sa_11_duplicate_delegation_rejected(platform: Platform) -> None:
    open_request(platform, requester="tomas", request_id="req-first")
    first = submit(platform, "mira", "approve_withdrawal", "approve-first", {"request_id": "req-first"})
    assert first["status"] == "accepted"
    open_request(platform, requester="tomas", request_id="req-second")
    second = submit(platform, "mira", "approve_withdrawal", "approve-second", {"request_id": "req-second"})

    assert second["failed_check"] == "duplicate_delegation"
    assert platform.store.repositories.authorization.get_request("req-second")["status"] == "pending"
    assert len(platform.store.repositories.authorization.list_delegations()) == 1
    assert len(events(platform, "access.approved")) == 1


def test_sa_12_wrong_scope_delegation_denied(platform: Platform) -> None:
    grant_delegation(platform, delegation_id="del-wrong-scope", scope="reserve_management")
    preview = behavior_entry(platform, "tomas", "withdraw_resource")
    assert preview["eligibility"]["reasons"][0]["type"] == "missing_delegation"
    result = submit(platform, "tomas", "withdraw_resource", "wrong-scope-1")
    assert result["failed_check"] == "missing_delegation"
    assert resource_amount(platform) == 10
    assert events(platform, "resource.withdrawal.started") == []


# ---------------------------------------------------------------------------
# SA-13: idempotency
# ---------------------------------------------------------------------------
def test_sa_13_idempotent_retries(platform: Platform) -> None:
    denied = deny_tomas(platform)
    first_request = submit(
        platform, "tomas", "request_access", "request-once",
        {"based_on_event": str(denied["event_id"]), "request_id": "req-once", "reason": "Need energy"},
    )
    retry_request = submit(
        platform, "tomas", "request_access", "request-once",
        {"based_on_event": str(denied["event_id"]), "request_id": "req-once", "reason": "Need energy"},
    )
    assert retry_request == first_request
    assert len(events(platform, "access.requested")) == 1

    approved = submit(platform, "mira", "approve_withdrawal", "approval-once", {"request_id": "req-once", "delegation_id": "del-once"})
    retry_approval = submit(platform, "mira", "approve_withdrawal", "approval-once", {"request_id": "req-once", "delegation_id": "del-once"})
    assert retry_approval == approved
    assert len(events(platform, "access.approved")) == 1
    assert len(platform.store.repositories.authorization.list_delegations()) == 1

    accepted = submit(platform, "tomas", "withdraw_resource", "withdraw-once")
    retry_withdraw = submit(platform, "tomas", "withdraw_resource", "withdraw-once")
    assert retry_withdraw == accepted
    assert len(events(platform, "resource.withdrawal.started")) == 1

    advance_until(platform, lambda: bool(events(platform, "resource.withdrawal.completed")))
    assert resource_amount(platform) == 9
    assert len(events(platform, "resource.withdrawal.completed")) == 1

    second = submit(platform, "tomas", "withdraw_resource", "withdraw-2")
    assert second["status"] == "accepted"
    advance_until(platform, lambda: len(events(platform, "resource.withdrawal.completed")) == 2)
    assert resource_amount(platform) == 8

    revoke = submit(platform, "mira", "revoke_delegation", "revoke-once", {"delegation_id": "del-once"})
    retry_revoke = submit(platform, "mira", "revoke_delegation", "revoke-once", {"delegation_id": "del-once"})
    assert retry_revoke == revoke
    assert len(events(platform, "delegation.revoked")) == 1
    assert resource_amount(platform) == 8


# ---------------------------------------------------------------------------
# SA-14: transaction failures
# ---------------------------------------------------------------------------
def test_sa_14_transaction_failure_rollbacks(platform: Platform, monkeypatch: pytest.MonkeyPatch) -> None:
    service = platform._services["events"]
    original = service.append

    open_request(platform, requester="tomas", request_id="req-fail")
    state = {"raised": False}

    def fail_approval(event_type: str, *args: Any, **kwargs: Any) -> int:
        if event_type == "access.approved" and not state["raised"]:
            state["raised"] = True
            raise RuntimeError("injected approval failure")
        return original(event_type, *args, **kwargs)

    monkeypatch.setattr(service, "append", fail_approval)
    with pytest.raises(RuntimeError, match="injected approval failure"):
        submit(platform, "mira", "approve_withdrawal", "approve-fail", {"request_id": "req-fail"})
    monkeypatch.setattr(service, "append", original)
    assert platform.store.repositories.authorization.get_request("req-fail")["status"] == "pending"
    assert platform.store.repositories.authorization.list_delegations() == []
    assert events(platform, "access.approved") == []
    assert platform.store.repositories.evolution.get_intent("approve-fail") is None

    grant_delegation(platform, delegation_id="del-fail")
    state["raised"] = False

    def fail_start(event_type: str, *args: Any, **kwargs: Any) -> int:
        if event_type == "resource.withdrawal.started" and not state["raised"]:
            state["raised"] = True
            raise RuntimeError("injected start failure")
        return original(event_type, *args, **kwargs)

    monkeypatch.setattr(service, "append", fail_start)
    with pytest.raises(RuntimeError, match="injected start failure"):
        submit(platform, "tomas", "withdraw_resource", "withdraw-start-fail")
    monkeypatch.setattr(service, "append", original)
    assert platform.store.repositories.entities.get_agent("tomas")["initial_state"]["status"] == "idle"
    assert events(platform, "resource.withdrawal.started") == []
    assert platform.store.repositories.evolution.get_intent("withdraw-start-fail") is None
    assert platform.store.connection.execute(
        "SELECT COUNT(*) FROM bt_instance WHERE execution_id='withdraw-start-fail'"
    ).fetchone()[0] == 0

    accepted = submit(platform, "tomas", "withdraw_resource", "withdraw-complete")
    assert accepted["status"] == "accepted"
    assert resource_amount(platform) == 10
    state["raised"] = False

    def fail_complete(event_type: str, *args: Any, **kwargs: Any) -> int:
        if event_type == "resource.withdrawal.completed" and not state["raised"]:
            state["raised"] = True
            raise RuntimeError("injected completion failure")
        return original(event_type, *args, **kwargs)

    monkeypatch.setattr(service, "append", fail_complete)
    advance_until(platform, lambda: bool(events(platform, "resource.withdrawal.failed")))
    monkeypatch.setattr(service, "append", original)
    assert resource_amount(platform) == 10
    assert len(events(platform, "resource.withdrawal.started")) == 1
    assert events(platform, "resource.withdrawal.completed") == []
    failed = events(platform, "resource.withdrawal.failed")
    assert len(failed) == 1
    assert {"intent_id", "actor", "target", "reason", "minute"} <= set(failed[0])
    tomas = platform.store.repositories.entities.get_agent("tomas")["initial_state"]
    assert tomas["status"] != "executing"
    follow_up = submit(
        platform, "tomas", "request_access", "after-completion-failure",
        {"based_on_event": str(_inject_denial(platform, "tomas", 0, "Withdraw Resource", "energy_reserve"))},
    )
    assert follow_up["status"] == "accepted"

    revoke_fail_state = {"raised": False}

    def fail_revoke(event_type: str, *args: Any, **kwargs: Any) -> int:
        if event_type == "delegation.revoked" and not revoke_fail_state["raised"]:
            revoke_fail_state["raised"] = True
            raise RuntimeError("injected revocation failure")
        return original(event_type, *args, **kwargs)

    monkeypatch.setattr(service, "append", fail_revoke)
    with pytest.raises(RuntimeError, match="injected revocation failure"):
        submit(platform, "mira", "revoke_delegation", "revoke-fail", {"delegation_id": "del-fail"})
    monkeypatch.setattr(service, "append", original)
    assert platform.store.repositories.authorization.get_delegation("del-fail")["status"] == "active"
    assert events(platform, "delegation.revoked") == []


# ---------------------------------------------------------------------------
# SA-15: preview / submit equivalence
# ---------------------------------------------------------------------------
def test_sa_15_preview_submit_equivalence(platform: Platform) -> None:
    before = state_snapshot(platform)
    preview = behavior_entry(platform, "tomas", "withdraw_resource")
    tool_preview = AgentToolExecutor(platform, role="agent_decision", actor_id="tomas").execute(
        "preview_behavior", {"choice": "Withdraw Resource"},
    )
    assert tool_preview["behavior"]["eligibility"]["available"] is False
    assert tool_preview["behavior"]["eligibility"]["reasons"][0]["type"] == "missing_delegation"
    assert state_snapshot(platform) == before
    denied = submit(platform, "tomas", "withdraw_resource", "equivalent-deny")
    assert denied["failed_check"] == preview["eligibility"]["reasons"][0]["type"]
    assert latest(platform, "behavior.denied")["reason"] == preview["eligibility"]["reasons"][0]["type"]

    open_request(platform, requester="tomas", request_id="req-preview")
    allow_preview = behavior_entry(platform, "mira", "approve_withdrawal")
    assert allow_preview["eligibility"]["available"] is True
    approved = submit(platform, "mira", "approve_withdrawal", "equivalent-approve", {"request_id": "req-preview"})
    assert approved["status"] == "accepted"

    frozen = state_snapshot(platform)
    withdraw_preview = behavior_entry(platform, "tomas", "withdraw_resource")
    assert withdraw_preview["eligibility"]["available"] is True
    assert state_snapshot(platform) == frozen
    accepted = submit(platform, "tomas", "withdraw_resource", "equivalent-withdraw")
    assert accepted["status"] == "accepted"
    assert latest(platform, "resource.withdrawal.started")["intent_id"] == "equivalent-withdraw"

    self_preview = behavior_entry(platform, "mira", "request_access")
    assert self_preview["eligibility"]["available"] is True


# ---------------------------------------------------------------------------
# SA-16: event schema and causal order
# ---------------------------------------------------------------------------
def test_sa_16_event_schema_and_order(platform: Platform) -> None:
    # Walk the frozen timeline: 5 denial, 10 request, 12 approval, 13 start,
    # 13+d completion, 20 revocation, 21 denied withdrawal.
    for _ in range(5):
        platform.step()
    denied = deny_tomas(platform)
    for _ in range(5):
        platform.step()
    request = submit(
        platform, "tomas", "request_access", "request-1",
        {"based_on_event": str(denied["event_id"]), "reason": "Need energy"},
    )
    for _ in range(2):
        platform.step()
    approved = submit(platform, "mira", "approve_withdrawal", "approve-1", {"request_id": request["request_id"]})
    platform.step()
    submit(platform, "tomas", "withdraw_resource", "withdraw-1")
    advance_until(platform, lambda: bool(events(platform, "resource.withdrawal.completed")))
    for _ in range(5):
        platform.step()
    submit(platform, "mira", "revoke_delegation", "revoke-1", {"delegation_id": approved["delegation_id"]})
    platform.step()
    final = submit(platform, "tomas", "withdraw_resource", "withdraw-final")
    assert (
        denied["world_minute"],
        latest(platform, "access.requested")["minute"],
        latest(platform, "access.approved")["minute"],
    ) == (5, 10, 12)
    assert latest(platform, "resource.withdrawal.started")["minute"] == 13
    assert latest(platform, "resource.withdrawal.completed")["minute"] == 15
    assert latest(platform, "delegation.revoked")["minute"] == 20
    assert int(platform.store.repositories.metadata.get("world_minute", 0)) == 21

    chain = [
        denied,
        latest(platform, "access.requested"),
        latest(platform, "access.approved"),
        latest(platform, "resource.withdrawal.started"),
        latest(platform, "resource.withdrawal.completed"),
        latest(platform, "delegation.revoked"),
        final,
    ]
    ordered = sorted(chain[:-1], key=lambda item: item["event_id"])
    assert ordered == chain[:-1]
    assert chain[1]["based_on_event"] == chain[0]["event_id"]
    assert chain[2]["request_id"] == chain[1]["request_id"]
    assert chain[2]["delegation_id"] == approved["delegation_id"]
    assert chain[3]["authorization"] == {"type": "delegation", "id": approved["delegation_id"]}
    assert chain[4]["authorization"] == chain[3]["authorization"]
    assert chain[4]["amount"] == 1 and chain[4]["before"] == 10 and chain[4]["after"] == 9
    assert final["failed_check"] == "delegation_revoked"

    required = {
        "behavior.requested": {"intent_id", "actor", "behavior", "target", "minute"},
        "behavior.denied": {"intent_id", "actor", "behavior", "target", "rule_id", "reason", "authority", "minute"},
        "access.requested": {"request_id", "requester", "behavior", "target", "scope", "reason", "based_on_event", "minute"},
        "access.approved": {"request_id", "approver", "subject", "scope", "delegation_id", "minute"},
        "delegation.revoked": {"delegation_id", "revoked_by", "reason", "minute"},
        "resource.withdrawal.started": {"intent_id", "actor", "target", "authorization", "minute"},
        "resource.withdrawal.completed": {"intent_id", "actor", "target", "authorization", "amount", "before", "after", "minute"},
    }
    for event_type, fields in required.items():
        event = latest(platform, event_type)
        missing = fields - set(event)
        assert not missing, f"{event_type} missing {sorted(missing)}"


# ---------------------------------------------------------------------------
# SA-17: denial memory and causal reference
# ---------------------------------------------------------------------------
def test_sa_17_denial_memory_and_causal_reference(platform: Platform) -> None:
    denied = deny_tomas(platform)
    view = platform.decision_view("tomas")
    memory = view["denial_memories"][0]
    assert memory["event_id"] == denied["event_id"]
    assert memory["rule"] == "energy_reserve_stewardship"
    assert memory["reason"] == "missing_delegation"
    assert memory["authority"] == {"type": "rule", "id": "energy_reserve_stewardship"}
    assert memory["target"] == "energy_reserve"
    assert platform.decision_view("mira")["denial_memories"] == []

    invalid_cases = [
        ("missing", {}),
        ("nonexistent", {"based_on_event": "99999"}),
        ("wrong_actor", {"based_on_event": str(_inject_denial(platform, "mira", 0, "Withdraw Resource", "energy_reserve"))}),
        ("wrong_type", {"based_on_event": str(_inject_event(platform, "behavior.started", "tomas", 0))}),
        ("wrong_behavior", {"based_on_event": str(denied["event_id"]), "behavior": "Approve Withdrawal"}),
        ("wrong_target", {"based_on_event": str(denied["event_id"]), "target": "other_reserve"}),
        ("future", {"based_on_event": str(_inject_denial(platform, "tomas", 100, "Withdraw Resource", "energy_reserve"))}),
    ]
    before = len(events(platform, "access.requested"))
    for index, (name, parameters) in enumerate(invalid_cases):
        result = submit(platform, "tomas", "request_access", f"invalid-{index}", parameters)
        assert result["failed_check"] == "invalid_causal_reference", name
    assert len(events(platform, "access.requested")) == before
    assert platform.store.repositories.authorization.count_requests() == 0


# ---------------------------------------------------------------------------
# SA-18: negative space
# ---------------------------------------------------------------------------
def test_sa_18_negative_space(platform: Platform) -> None:
    denied = deny_tomas(platform)
    assert platform.store.connection.execute("SELECT COUNT(*) FROM evolution_task").fetchone()[0] == 0
    for _ in range(5):
        platform.step()
    assert platform.store.connection.execute("SELECT COUNT(*) FROM evolution_task").fetchone()[0] == 0
    assert events(platform, "resource.withdrawal.started") == []
    assert events(platform, "resource.withdrawal.completed") == []
    assert platform.store.repositories.authorization.list_delegations() == []

    request = submit(
        platform, "tomas", "request_access", "request-1",
        {"based_on_event": str(denied["event_id"]), "reason": "Need energy"},
    )
    submit(platform, "mira", "reject_withdrawal", "reject-1", {"request_id": request["request_id"]})
    assert events(platform, "resource.withdrawal.started") == []
    assert events(platform, "resource.withdrawal.completed") == []
    assert platform.store.repositories.authorization.list_delegations() == []

    open_request(platform, requester="mira", request_id="req-self")
    self_denied = submit(platform, "mira", "approve_withdrawal", "self-1", {"request_id": "req-self"})
    assert self_denied["failed_check"] == "self_approval_forbidden"
    assert events(platform, "access.approved") == []
    assert platform.store.repositories.authorization.list_delegations() == []

    open_request(platform, requester="mira", request_id="req-non-steward")
    assert submit(platform, "tomas", "approve_withdrawal", "non-1", {"request_id": "req-non-steward"})["failed_check"] == "missing_stewardship"
    assert events(platform, "access.approved") == []
    assert platform.store.repositories.authorization.list_delegations() == []

    for event_type in ("story.task_created", "god.task_created", "director.task_created"):
        assert events(platform, event_type) == []


def _inject_event(platform: Platform, event_type: str, actor: str, minute: int) -> int:
    with platform.store.transaction():
        return platform.store.repositories.events.append_event(
            event_type, actor, minute,
            {"intent_id": "injected", "behavior": "Withdraw Resource", "behavior_id": "withdraw_resource", "target": "energy_reserve", "reason": "missing_delegation"},
        )


def _inject_denial(platform: Platform, actor: str, minute: int, behavior: str, target: str) -> int:
    with platform.store.transaction():
        return platform.store.repositories.events.append_event(
            "behavior.denied", actor, minute,
            {
                "intent_id": "injected",
                "actor": actor,
                "behavior": behavior,
                "behavior_id": "withdraw_resource",
                "target": target,
                "rule_id": "energy_reserve_stewardship",
                "reason": "missing_delegation",
                "authority": {"type": "rule", "id": "energy_reserve_stewardship"},
                "minute": minute,
            },
        )
