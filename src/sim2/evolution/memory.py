from __future__ import annotations

import json
from typing import Any


ROLE_IDS = {"story", "god"}


def remember_role(
    store: Any,
    role: str,
    memory_type: str,
    data: dict[str, Any],
    *,
    memory_key: str,
    source_event_id: int | None = None,
) -> int | None:
    """Persist one bounded, provenance-bearing Story/God memory.

    Role memories use the same memory service as character memories, but are
    namespaced by the role id. They are summaries of validated outcomes, not a
    second event log. ``memory_key`` makes retries and repeated wakeups
    idempotent.
    """

    role = str(role or "").strip()
    if role not in ROLE_IDS:
        return None
    memory_key = str(memory_key or "").strip()
    if not memory_key:
        return None
    row = store.connection.execute(
        "SELECT id FROM memory_item WHERE agent_id=? AND json_extract(data_json,'$.memory_key')=? LIMIT 1",
        (role, memory_key),
    ).fetchone()
    if row is not None:
        return int(row["id"])
    payload = dict(data or {})
    payload.update({
        "memory_key": memory_key,
        "role": role,
        "claim_status": str(payload.get("claim_status") or "confirmed"),
        "importance": float(payload.get("importance", 0.75) or 0.75),
        "salience": float(payload.get("salience", 0.7) or 0.7),
    })
    if source_event_id is not None:
        payload["source_event_id"] = int(source_event_id)
    return store.put_memory(role, memory_type, payload, tier="long_term")


def role_memory_query(store: Any, role: str, query: str, *, limit: int = 6) -> list[dict[str, Any]]:
    """Read a small role-scoped long-term slice for a decision context."""

    from ..memory.service import MemoryService

    service = MemoryService(store)
    ranked = service.retrieve(str(role), query=str(query or ""), limit=limit, scope="long_term", touch=False)
    # A role's bounded thread/diagnosis summaries are all relevant when the
    # current trigger has no shared wording. Keep a small recency fallback;
    # deeper semantic recall remains available through the tool.
    if not ranked and str(query or "").strip():
        ranked = service.retrieve(str(role), query="", limit=limit, scope="long_term", touch=False)
    return ranked
