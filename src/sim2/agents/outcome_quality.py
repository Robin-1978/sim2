"""Grade an admitted behavior against its committed simulation outcome."""

from __future__ import annotations

from math import isclose
from typing import Any

from ..persistence.repositories.events import EventsRepository
from .session import AgentTurn


def evaluate_behavior_outcome(
    turn: AgentTurn, events: EventsRepository, *, actor_id: str, deadline_minute: int,
    resource_deltas: dict[str, float] | None = None, observed_minute: int | None = None,
) -> dict[str, Any]:
    """Read causal evidence after advancing Platform through the scenario horizon."""

    execution_id = turn.result.get("execution_id")
    if turn.result.get("status") != "accepted" or not execution_id:
        return {"passed": False, "grade": "no_behavior_admitted", "submission_status": turn.result.get("status")}
    terminal, changes = events.behavior_outcome_evidence(actor_id, execution_id)
    if terminal is None:
        grade = "deadline_missed" if observed_minute is not None and observed_minute >= deadline_minute else "outcome_pending"
        return {"passed": False, "grade": grade, "execution_id": execution_id}
    expected = resource_deltas or {}
    actual = {resource: 0.0 for resource in expected}
    for change in changes:
        resource = change.get("resource_id")
        if resource in actual:
            actual[resource] += float(change["after"]) - float(change["before"])
    checks = {
        "behavior_completed": terminal["event_type"] == "behavior.completed",
        "by_deadline": terminal["world_minute"] <= deadline_minute,
        "resource_deltas_match": all(isclose(actual[key], delta, abs_tol=1e-9) for key, delta in expected.items()),
    }
    passed = all(checks.values())
    return {
        "passed": passed,
        "grade": "verified_outcome" if passed else "outcome_mismatch",
        "execution_id": execution_id,
        "outcome_event_id": terminal["event_id"],
        "outcome_minute": terminal["world_minute"],
        "result": terminal.get("result") or terminal.get("reason"),
        "resource_deltas": actual,
        "checks": checks,
    }
