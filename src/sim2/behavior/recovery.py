from __future__ import annotations

from typing import Any


DEFAULT_RECOVERY_EVENTS = {
    "resource.regenerated",
    "resource.changed",
    "entity.created",
    "definition.registered",
    "behavior.registered",
    "institution.activated",
    "institution.allocation_completed",
    "story.beat.activated",
    "research.completed",
    "exploration.completed",
}


def failure_recovery_events(store: Any) -> set[str]:
    rules = store.get_meta("rules", {}) or {}
    configured = rules.get("failure_recovery_events")
    if isinstance(configured, str):
        configured = [configured]
    values = {str(item).strip() for item in (configured or []) if str(item).strip()}
    return values or set(DEFAULT_RECOVERY_EVENTS)


def failure_block_recovered(store: Any, block: dict[str, Any] | None) -> bool:
    """Return whether a failed activity has a persisted recovery signal."""

    if not isinstance(block, dict):
        return True
    failed_at = int(block.get("failed_at", -1) or -1)
    if failed_at < 0:
        return True
    events = failure_recovery_events(store)
    if not events:
        return False
    placeholders = ",".join("?" for _ in events)
    row = store.connection.execute(
        f"SELECT 1 FROM event_log WHERE world_minute>? AND event_type IN ({placeholders}) LIMIT 1",
        (failed_at, *sorted(events)),
    ).fetchone()
    return row is not None
