from __future__ import annotations

import json
from typing import Any


ROLE_IDS = {"story", "god"}


def update_role_memory_status(
    store: Any,
    role: str,
    *,
    beat_id: str,
    status: str,
    world_minute: int,
    behavior_id: str | None = None,
) -> int:
    """Close Story thread memories when their persisted beat closes.

    ``story_state`` is authoritative, but Story also receives long-term
    thread summaries. Keeping a completed summary marked active makes a later
    Story prompt treat an old chapter as current and can recreate it.
    """

    role = str(role or "").strip()
    beat_id = str(beat_id or "").strip()
    status = str(status or "").strip()
    if role not in ROLE_IDS or not beat_id or not status:
        return 0
    rows = store.connection.execute(
        "SELECT id,data_json FROM memory_item WHERE agent_id=? AND memory_type='story_thread'",
        (role,),
    ).fetchall()
    changed = 0
    for row in rows:
        try:
            data = json.loads(row["data_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or str(data.get("beat_id") or "") != beat_id:
            continue
        data["status"] = status
        data["closed_minute"] = int(world_minute)
        if behavior_id:
            data["closed_by_behavior"] = str(behavior_id)
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        store.connection.execute("UPDATE memory_item SET data_json=? WHERE id=?", (encoded, int(row["id"])))
        store.connection.execute("UPDATE memory_fts SET content=? WHERE memory_id=?", (encoded, str(row["id"])))
        changed += 1
    return changed


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
