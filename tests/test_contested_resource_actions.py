"""Phase 2A deterministic contract for contested resource actions.

Each test maps to one row of the frozen acceptance matrix in
``docs/design/social-authority-loop-phase2.md``.  These tests use only the
probe world; they never call a model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sim2.agents.tool_executor import AgentToolExecutor
from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform
from sim2.prompting.renderer import render_context

WORLD = Path(__file__).parents[1] / "worlds" / "social_authority_probe"


@pytest.fixture
def platform(tmp_path: Path):
    instance = Platform(create_instance(WORLD, "contested-actions", tmp_path / "runtime"))
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
            reason="phase two contract test",
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
    rows = platform.store.repositories.events.timeline_events(after_event_id=0, limit=1000, include_ticks=True)
    return [row for row in rows if event_type is None or row.get("event_type") == event_type]


def latest(platform: Platform, event_type: str) -> dict[str, Any]:
    rows = events(platform, event_type)
    assert rows, f"missing {event_type}"
    return rows[-1]


def resource_amount(platform: Platform) -> float:
    return float((platform.store.repositories.metadata.get("world_properties", {}) or {}).get("energy_reserve", 0) or 0)


def violations(platform: Platform) -> list[dict[str, Any]]:
    return platform.store.repositories.contested.list_violations()


def restrictions(platform: Platform) -> list[dict[str, Any]]:
    return platform.store.repositories.contested.list_restrictions()


def move(platform: Platform, actor: str, location_id: str) -> None:
    with platform.store.transaction():
        agent = platform.store.repositories.entities.get_agent(actor)
        state = dict(agent.get("initial_state") or {})
        state["location_id"] = location_id
        agent["initial_state"] = state
        platform.store.repositories.entities.update_agent(actor, agent)


def advance_until(platform: Platform, predicate, limit: int = 12) -> None:
    for _ in range(limit):
        if predicate():
            return
        platform.step()
    raise AssertionError("world did not reach the expected state")


def take_and_complete(platform: Platform, intent_id: str = "take-1") -> None:
    result = submit(platform, "tomas", "take_energy", intent_id)
    assert result["status"] == "accepted"
    advance_until(platform, lambda: bool(violations(platform)))


def open_request(platform: Platform, based_on_event: int, request_id: str = "req-2a") -> str:
    result = submit(
        platform,
        "tomas",
        "request_access",
        "request-2a",
        {"based_on_event": str(based_on_event), "reason": "Need energy after all"},
    )
    assert result["status"] == "accepted"
    return str(result["request_id"])


def grant_delegation(
    platform: Platform,
    *,
    delegation_id: str = "del-2b",
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


# ---------------------------------------------------------------------------
# PA-01 .. PA-12
# ---------------------------------------------------------------------------
def test_pa_01_take_is_executable_without_authorization(platform: Platform) -> None:
    take = behavior_entry(platform, "tomas", "take_energy")
    withdraw = behavior_entry(platform, "tomas", "withdraw_energy")

    assert take["eligibility"]["available"] is True
    assert take["eligibility"]["gated"] is False
    assert take["risk"]["observers"] == ["Mira"]
    assert take["risk"]["detection"] == "observed"
    assert withdraw["eligibility"]["available"] is False
    assert withdraw["eligibility"]["reasons"][0]["type"] == "missing_delegation"
    assert [item["choice"] for item in platform.decision_view("tomas")["contested_actions"]] == ["Take Energy"]


def test_pa_02_unwitnessed_take_is_undetected(platform: Platform) -> None:
    move(platform, "mira", "relay_yard")
    result = submit(platform, "tomas", "take_energy", "take-1")
    assert result["status"] == "accepted"
    assert resource_amount(platform) == 10

    advance_until(platform, lambda: bool(violations(platform)))

    assert resource_amount(platform) == 9
    taken = latest(platform, "resource.taken")
    assert taken["witnesses"] == []
    assert events(platform, "violation.observed") == []
    rows = violations(platform)
    assert len(rows) == 1
    assert rows[0]["status"] == "undetected"
    assert rows[0]["witnesses"] == []
    assert events(platform, "relationship.changed") == []


def test_pa_03_witnessed_take_drops_trust_and_creates_memory(platform: Platform) -> None:
    take_and_complete(platform)

    assert resource_amount(platform) == 9
    row = violations(platform)[0]
    assert row["status"] == "observed"
    assert row["witnesses"] == ["mira"]
    observed = latest(platform, "violation.observed")
    assert observed["violation_id"] == row["violation_id"]
    assert observed["witnesses"] == ["mira"]

    trust = next(
        item for item in platform.store.repositories.entities.list_relationships()
        if item.get("from") == "mira" and item.get("to") == "tomas" and item.get("type") == "trust"
    )
    assert trust["relation_strength"] == -0.25
    assert trust["trust"] == -0.25

    memories = [
        item for item in platform.store.repositories.memory.list_memories("mira", limit=20)
        if item.get("type") == "violation"
    ]
    assert len(memories) == 1
    assert memories[0]["violation_id"] == row["violation_id"]
    assert memories[0]["actor"] == "tomas"
    assert memories[0]["confidence"] == 0.9
    mira_prompt = render_context(platform.decision_view("mira"), "decide whether to review the violation")
    assert "<violations>" in mira_prompt
    assert "witnessed_violation:" in mira_prompt
    assert "unresolved_violation:" in mira_prompt


def test_pa_04_undetected_is_private_but_audited(platform: Platform) -> None:
    move(platform, "mira", "relay_yard")
    take_and_complete(platform)

    assert len(events(platform, "resource.taken")) == 1
    tomas_view = platform.decision_view("tomas")
    mira_view = platform.decision_view("mira")
    assert len(tomas_view["my_violations"]) == 1
    assert tomas_view["my_violations"][0]["status"] == "undetected"
    assert mira_view["unresolved_violations"] == []
    assert mira_view["witnessed_violations"] == []
    assert [
        item for item in platform.store.repositories.memory.list_memories("mira", limit=20)
        if item.get("type") == "violation"
    ] == []
    tomas_prompt = render_context(tomas_view, "decide what to do")
    mira_prompt = render_context(mira_view, "decide what to do")
    assert "<violations>" in tomas_prompt and "my_violation:" in tomas_prompt
    assert "<violations>" not in mira_prompt


def test_pa_05_take_does_not_use_the_authorization_evaluator(platform: Platform) -> None:
    result = submit(platform, "tomas", "take_energy", "take-1")

    assert result["status"] == "accepted"
    assert events(platform, "behavior.denied") == []
    assert events(platform, "behavior.requested") == []
    assert behavior_entry(platform, "tomas", "withdraw_energy")["eligibility"]["available"] is False
    assert resource_amount(platform) == 10


def test_pa_06_phase_one_chain_still_works_after_take(platform: Platform) -> None:
    take_and_complete(platform)
    assert resource_amount(platform) == 9

    denied = submit(platform, "tomas", "withdraw_energy", "deny-2a")
    assert denied["status"] == "rejected"
    assert denied["failed_check"] == "missing_delegation"
    denial = latest(platform, "behavior.denied")
    request_id = open_request(platform, int(denial["event_id"]))
    approved = submit(platform, "mira", "approve_withdrawal", "approve-2a", {"request_id": request_id})
    assert approved["status"] == "accepted"
    withdrawn = submit(platform, "tomas", "withdraw_energy", "withdraw-2a")
    assert withdrawn["status"] == "accepted"
    advance_until(platform, lambda: bool(events(platform, "resource.withdrawal.completed")))

    assert resource_amount(platform) == 8
    completed = latest(platform, "resource.withdrawal.completed")
    assert completed["intent_id"] == "withdraw-2a"


def test_pa_07_intent_retry_has_one_side_effect(platform: Platform) -> None:
    submit(platform, "tomas", "take_energy", "take-1")
    advance_until(platform, lambda: bool(violations(platform)))
    retry = submit(platform, "tomas", "take_energy", "take-1")

    assert retry["status"] == "accepted"
    assert resource_amount(platform) == 9
    assert len(events(platform, "resource.taken")) == 1
    assert len(violations(platform)) == 1


def test_pa_08_empty_resource_rejects_without_side_effects(platform: Platform) -> None:
    with platform.store.transaction():
        platform.resources._change_world_resource("energy_reserve", -10, actor_id="test")
    assert resource_amount(platform) == 0

    result = submit(platform, "tomas", "take_energy", "take-empty")

    assert result["status"] == "rejected"
    assert result["failed_check"] == "resource_empty"
    assert resource_amount(platform) == 0
    assert events(platform, "resource.taken") == []
    assert events(platform, "resource.take.failed") == []
    assert violations(platform) == []


def test_pa_09_completion_failure_rolls_back_everything(platform: Platform, monkeypatch: pytest.MonkeyPatch) -> None:
    original = platform.store.repositories.memory.put_memory
    calls = {"count": 0}

    def flaky_put_memory(*args: Any, **kwargs: Any) -> int:
        calls["count"] += 1
        if calls["count"] >= 2:
            raise RuntimeError("injected completion failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(platform.store.repositories.memory, "put_memory", flaky_put_memory)
    submit(platform, "tomas", "take_energy", "take-1")
    advance_until(platform, lambda: bool(events(platform, "resource.take.failed")))

    failed = latest(platform, "resource.take.failed")
    assert failed["reason"].startswith("resource.take.failed:")
    assert resource_amount(platform) == 10
    assert events(platform, "resource.taken") == []
    assert events(platform, "violation.observed") == []
    assert violations(platform) == []
    assert not [
        item for item in platform.store.repositories.entities.list_relationships()
        if item.get("type") == "trust"
    ]
    assert [
        item for item in platform.store.repositories.memory.list_memories("tomas", limit=20)
        if item.get("type") == "violation"
    ] == []
    actor = platform.store.repositories.entities.get_agent("tomas")
    assert actor["initial_state"]["status"] == "failed"
    assert actor["initial_state"].get("active_bt_instance") is None
    assert events(platform, "behavior.failed")


def test_pa_10_event_order_and_required_fields(platform: Platform) -> None:
    take_and_complete(platform)

    ordered_types = ["behavior.started", "resource.taken", "violation.observed", "relationship.changed", "behavior.completed"]
    ordered_ids = []
    for event_type in ordered_types:
        rows = events(platform, event_type)
        assert rows, f"missing {event_type}"
        ordered_ids.append(int(rows[0]["event_id"]))
    assert ordered_ids == sorted(ordered_ids)

    taken = latest(platform, "resource.taken")
    for field in ("intent_id", "actor", "target", "scope", "amount", "before", "after", "mode", "witnesses", "violation_id", "minute"):
        assert field in taken, f"resource.taken missing {field}"
    assert taken["intent_id"] == "take-1"
    assert taken["before"] == 10
    assert taken["after"] == 9
    assert taken["mode"] == "covert"

    observed = latest(platform, "violation.observed")
    for field in ("violation_id", "actor", "target", "scope", "witnesses", "minute"):
        assert field in observed, f"violation.observed missing {field}"


def test_pa_11_preview_is_side_effect_free_and_agrees_with_submit(platform: Platform) -> None:
    def snapshot() -> dict[str, Any]:
        return {
            "amount": resource_amount(platform),
            "violations": len(violations(platform)),
            "restrictions": len(restrictions(platform)),
            "events": len(events(platform)),
        }

    before = snapshot()
    preview = AgentToolExecutor(platform, role="agent_decision", actor_id="tomas").execute(
        "preview_behavior", {"choice": "Take Energy"},
    )
    after = snapshot()
    assert before == after
    assert preview["behavior"]["risk"]["observers"] == ["Mira"]
    assert preview["behavior"]["risk"]["detection"] == "observed"

    take_and_complete(platform)
    taken = latest(platform, "resource.taken")
    assert taken["witnesses"] == ["mira"]
    assert preview["behavior"]["risk"]["detection"] == "observed"


def test_pa_12_negative_space(platform: Platform) -> None:
    take_and_complete(platform)

    event_types = {str(item.get("event_type")) for item in events(platform)}
    assert not {value for value in event_types if value.startswith("resource.withdrawal.")}
    assert "access.approved" not in event_types
    assert "behavior.denied" not in event_types
    assert "attack" not in event_types
    assert "combat" not in event_types
    assert platform.store.repositories.authorization.list_delegations() == []
    assert int(platform.store.connection.execute("SELECT COUNT(*) FROM evolution_task").fetchone()[0]) == 0
    assert len(violations(platform)) == 1


# ---------------------------------------------------------------------------
# PB-01 .. PB-07
# ---------------------------------------------------------------------------
def review(platform: Platform, violation_id: str, decision: str, intent_id: str) -> dict[str, Any]:
    return submit(
        platform,
        "mira",
        "review_violation",
        intent_id,
        {"violation_id": violation_id, "decision": decision, "reason": "contract review"},
    )


def test_pb_01_sanction_creates_restriction_and_blocks_delegated_withdrawal(platform: Platform) -> None:
    grant_delegation(platform)
    take_and_complete(platform)
    violation = violations(platform)[0]
    review_entry = behavior_entry(platform, "mira", "review_violation")
    assert review_entry["eligibility"]["available"] is True

    result = review(platform, str(violation["violation_id"]), "sanction", "review-1")

    assert result["status"] == "accepted"
    assert result["authorization_result"]["status"] == "sanctioned"
    restriction = platform.store.repositories.contested.active_restriction(
        "tomas", "energy_reserve", "energy_withdrawal",
    )
    assert restriction is not None
    assert restriction["created_by"] == "mira"
    assert platform.store.repositories.contested.get_violation(violation["violation_id"])["status"] == "sanctioned"
    sanctioned = latest(platform, "violation.sanctioned")
    assert sanctioned["violation_id"] == violation["violation_id"]
    assert sanctioned["restriction_id"] == restriction["restriction_id"]
    assert behavior_entry(platform, "mira", "review_violation")["eligibility"]["available"] is True

    denied = submit(platform, "tomas", "withdraw_energy", "withdraw-blocked")
    assert denied["status"] == "rejected"
    assert denied["failed_check"] == "access_restricted"
    assert denied["retryable"] is True
    assert resource_amount(platform) == 9


def test_pb_02_forgive_revokes_restriction_and_restores_withdrawal(platform: Platform) -> None:
    grant_delegation(platform)
    take_and_complete(platform)
    violation = violations(platform)[0]
    review(platform, str(violation["violation_id"]), "sanction", "review-1")
    restriction = platform.store.repositories.contested.active_restriction(
        "tomas", "energy_reserve", "energy_withdrawal",
    )
    assert restriction is not None

    result = review(platform, str(violation["violation_id"]), "forgive", "review-2")

    assert result["status"] == "accepted"
    assert result["authorization_result"]["status"] == "forgiven"
    assert platform.store.repositories.contested.get_violation(violation["violation_id"])["status"] == "forgiven"
    assert platform.store.repositories.contested.active_restriction(
        "tomas", "energy_reserve", "energy_withdrawal",
    ) is None
    forgiven = latest(platform, "violation.forgiven")
    assert forgiven["restriction_id"] == restriction["restriction_id"]

    withdrawn = submit(platform, "tomas", "withdraw_energy", "withdraw-after-forgive")
    assert withdrawn["status"] == "accepted"
    advance_until(platform, lambda: bool(events(platform, "resource.withdrawal.completed")))
    assert resource_amount(platform) == 8


def test_pb_03_non_steward_review_is_denied(platform: Platform) -> None:
    take_and_complete(platform)
    violation = violations(platform)[0]

    result = submit(
        platform,
        "tomas",
        "review_violation",
        "review-non-steward",
        {"violation_id": str(violation["violation_id"]), "decision": "sanction"},
    )

    assert result["status"] == "rejected"
    assert result["failed_check"] == "missing_stewardship"
    assert platform.store.repositories.contested.get_violation(violation["violation_id"])["status"] == "observed"
    assert restrictions(platform) == []


def test_pb_04_undetected_and_resolved_reviews_are_denied(platform: Platform) -> None:
    move(platform, "mira", "relay_yard")
    take_and_complete(platform, intent_id="take-hidden")
    hidden = violations(platform)[0]
    assert hidden["status"] == "undetected"

    hidden_result = review(platform, str(hidden["violation_id"]), "sanction", "review-hidden")
    assert hidden_result["status"] == "rejected"
    assert hidden_result["failed_check"] == "violation_not_observed"

    move(platform, "mira", "relay_station")
    assert submit(platform, "tomas", "take_energy", "take-seen")["status"] == "accepted"
    advance_until(platform, lambda: any(item["status"] == "observed" for item in violations(platform)))
    seen = [item for item in violations(platform) if item["status"] == "observed"][0]
    assert review(platform, str(seen["violation_id"]), "sanction", "review-1")["status"] == "accepted"

    resolved = review(platform, str(seen["violation_id"]), "sanction", "review-2")
    assert resolved["status"] == "rejected"
    assert resolved["failed_check"] == "violation_already_resolved"


def test_pb_05_review_failure_rolls_back(platform: Platform, monkeypatch: pytest.MonkeyPatch) -> None:
    take_and_complete(platform)
    violation = violations(platform)[0]

    def fail_restriction(_: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("injected review failure")

    monkeypatch.setattr(platform.store.repositories.contested, "put_restriction", fail_restriction)
    result = review(platform, str(violation["violation_id"]), "sanction", "review-fail")

    assert result["status"] == "rejected"
    assert result["failed_check"] == "violation_review_failed"
    assert platform.store.repositories.contested.get_violation(violation["violation_id"])["status"] == "observed"
    assert restrictions(platform) == []
    assert events(platform, "violation.sanctioned") == []


def test_pb_06_no_restriction_keeps_phase_one_chain(platform: Platform) -> None:
    grant_delegation(platform)
    take_and_complete(platform)
    assert restrictions(platform) == []

    withdrawn = submit(platform, "tomas", "withdraw_energy", "withdraw-normal")
    assert withdrawn["status"] == "accepted"
    advance_until(platform, lambda: bool(events(platform, "resource.withdrawal.completed")))
    assert latest(platform, "resource.withdrawal.completed")["intent_id"] == "withdraw-normal"
    assert resource_amount(platform) == 8


def test_pb_07_restriction_does_not_block_steward(platform: Platform) -> None:
    take_and_complete(platform)
    violation = violations(platform)[0]
    review(platform, str(violation["violation_id"]), "sanction", "review-1")

    steward = submit(platform, "mira", "withdraw_energy", "withdraw-steward")
    assert steward["status"] == "accepted"
    advance_until(platform, lambda: bool(events(platform, "resource.withdrawal.completed")))
    assert resource_amount(platform) == 8

    denied = submit(platform, "tomas", "withdraw_energy", "withdraw-restricted")
    assert denied["failed_check"] == "access_restricted"
