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


def describe_institution_terms(institution: dict[str, Any]) -> str:
    """Summarize an institution's persisted terms for a world-level reviewer.

    The text is derived from the stored policy and ledger only, so a reviewer
    can compare the terms with the recorded results without any per-world
    wording.
    """

    policy = institution.get("policy") if isinstance(institution.get("policy"), dict) else {}
    allocation = policy.get("allocation") if isinstance(policy.get("allocation"), dict) else {}
    parts: list[str] = []
    if allocation:
        category = str(allocation.get("category") or "any")
        parts.append(
            f"allocation: at least {float(allocation.get('minimum_per_subject', 0) or 0):g} per subject, "
            f"{float(allocation.get('contribution_share', 0) or 0):.0%} of the remainder by verified {category} contribution"
        )
    ledger = institution.get("ledger") if isinstance(institution.get("ledger"), dict) else {}
    selectors = ledger.get("selectors", ledger.get("evidence", []))
    counted: list[str] = []
    for selector in selectors if isinstance(selectors, list) else []:
        if not isinstance(selector, dict):
            continue
        sources: list[str] = []
        for keys in (("event_types", "events"), ("behavior_ids", "behaviors"), ("action_ids", "actions")):
            value = next((selector[key] for key in keys if key in selector), [])
            sources.extend([str(value)] if isinstance(value, str) else [str(item) for item in value or []])
        points = float(selector.get("points", selector.get("amount", 1)) or 0)
        counted.append(f"{'/'.join(sources) or 'any event'} +{points:g} {selector.get('category') or 'general'}")
    if counted:
        parts.append(f"ledger per {ledger.get('period', 'world')}: " + "; ".join(counted[:6]))
    revisions = int(institution.get("revision", 0) or 0)
    if revisions:
        parts.append(f"revised {revisions} time(s)")
    return "; ".join(parts)


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
        item for item in store.repositories.social.list_institutions(status="active", limit=200)
        if str(item.get("name", "")).casefold() == str(institution).casefold()
        or str(item.get("institution_id", "")) == str(institution)
    ]
    if len(candidates) != 1:
        return {"possible": False, "reason": "institution_unavailable", "available": 0.0, "required": 0.0}
    try:
        pool = store.repositories.entities.get_entity(str(pool_entity))
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
        subject_ids = [str(agent["id"]) for agent in store.repositories.entities.list_agents()]
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
