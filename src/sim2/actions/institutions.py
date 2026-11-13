from __future__ import annotations

from typing import Any


def entity_quantity(entity: dict[str, Any]) -> float:
    """Read a configured stock quantity without knowing its world name."""

    state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
    value = entity.get("amount")
    if value is None:
        value = state.get("quantity", state.get("amount", 0))
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def allocation_preflight(
    store: Any,
    institution: Any,
    pool_entity: Any,
    subjects: list[str] | None = None,
) -> dict[str, Any]:
    """Check a declarative institution allocation before a long Action starts.

    The check reads only the persisted institution policy and stock entity. It
    therefore works for water, food, fuel, wages, or any other configured
    settlement resource without a world-specific branch.
    """

    candidates = [
        item for item in store.list_institutions(status="active", limit=200)
        if str(item.get("name", "")).casefold() == str(institution).casefold()
        or str(item.get("institution_id", "")) == str(institution)
    ]
    if len(candidates) != 1:
        return {"possible": False, "reason": "institution_unavailable", "available": 0.0, "required": 0.0}
    try:
        pool = store.get_entity(str(pool_entity))
    except KeyError:
        return {"possible": False, "reason": "pool_unavailable", "available": 0.0, "required": 0.0}
    policy = candidates[0].get("policy") if isinstance(candidates[0].get("policy"), dict) else {}
    allocation = policy.get("allocation") if isinstance(policy.get("allocation"), dict) else {}
    try:
        minimum = float(allocation.get("minimum_per_subject", 0) or 0)
    except (TypeError, ValueError):
        minimum = 0.0
    subject_ids = [str(value) for value in (subjects or []) if str(value)]
    if not subject_ids or subject_ids == ["all_agents"]:
        subject_ids = [str(agent["id"]) for agent in store.list_agents()]
    available = entity_quantity(pool)
    required = max(0.0, minimum) * len(subject_ids)
    return {
        "possible": available + 1e-9 >= required,
        "reason": "ok" if available + 1e-9 >= required else "minimum_allocation",
        "available": available,
        "required": required,
        "institution_id": candidates[0].get("institution_id"),
        "subjects": subject_ids,
    }
