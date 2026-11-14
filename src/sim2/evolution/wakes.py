from __future__ import annotations

import json
from typing import Any


class EventWakeScheduler:
    """Queue Agent consumers from declarative event evidence.

    The scheduler knows only consumer names, event types, thresholds, and
    task persistence.  It does not know what a consumer means or which world
    behavior produced an event.  Content packages can therefore define a
    long-running synthesis threshold without Platform branches.
    """

    def __init__(self, store: Any):
        self.store = store

    def _policy(self, consumer: str) -> dict[str, Any]:
        root = self.store.get_meta("evolution", {}) or {}
        if isinstance(root, dict):
            root = root.get("evolution", root)
        policy = root.get("event_wake_policy", {}) if isinstance(root, dict) else {}
        if not isinstance(policy, dict):
            return {}
        consumers = policy.get("consumers", {})
        if isinstance(consumers, dict) and isinstance(consumers.get(str(consumer)), dict):
            return dict(consumers[str(consumer)])
        # A flat policy remains useful for small/custom packages while the
        # preferred form is ``consumers: {name: ...}``.
        return dict(policy)

    # Bounded memory of already-woken evidence keys per group.  A key older
    # than this window may wake the consumer again, which is the desired
    # behavior for a long-running world.
    _SEEN_KEY_LIMIT = 512

    def _novel_rows(
        self,
        group_state: dict[str, Any],
        event_types: list[str],
        distinct_by: list[str],
        last_event_id: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return pending events whose evidence key has not woken this group.

        ``distinct_by`` names event fields (payload fields, or the ``actor_id``
        and ``event_type`` columns).  Two events with the same values are the
        same evidence, so only the first one counts toward ``minimum_events``.
        Unlike a time cooldown this never suppresses genuinely new evidence.
        """

        scanned = int(group_state.get("scanned_event_id", last_event_id) or last_event_id)
        seen = [str(item) for item in (group_state.get("seen_keys") or [])]
        pending = [item for item in (group_state.get("pending") or []) if isinstance(item, dict) and "key" in item]
        known = set(seen) | {str(item["key"]) for item in pending}
        placeholders = ",".join("?" for _ in event_types)
        fetched = self.store.connection.execute(
            "SELECT event_id,event_type,actor_id,data_json FROM event_log "
            f"WHERE event_id>? AND event_type IN ({placeholders}) ORDER BY event_id",
            (scanned, *event_types),
        ).fetchall()
        for row in fetched:
            key = self._distinct_key(row, distinct_by)
            scanned = int(row["event_id"])
            if key in known:
                continue
            known.add(key)
            pending.append({
                "event_id": int(row["event_id"]),
                "event_type": str(row["event_type"]),
                "actor_id": row["actor_id"],
                "key": key,
            })
        pending = pending[-self._SEEN_KEY_LIMIT:]
        state = {
            "scanned_event_id": scanned,
            "seen_keys": seen[-self._SEEN_KEY_LIMIT:],
            "pending": pending,
        }
        return pending, state

    @staticmethod
    def _distinct_key(row: Any, distinct_by: list[str]) -> str:
        try:
            payload = json.loads(row["data_json"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        values: list[str] = []
        for key in distinct_by:
            if key in {"event_type", "actor_id"}:
                value = row[key]
            else:
                value = payload.get(key) if isinstance(payload, dict) else None
            values.append("" if value is None else str(value))
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"))

    def queue(
        self,
        consumer: str,
        event_id: int,
        event_type: str,
        actor_id: str | None,
        minute: int,
        *,
        task_type: str | None = None,
        explicit: bool = False,
    ) -> str | None:
        consumer = str(consumer or "").strip()
        if not consumer:
            return None
        policy = self._policy(consumer)
        if bool(policy.get("actor_scoped", False)) and not str(actor_id or "").strip():
            return None
        task_actor = str(actor_id) if bool(policy.get("actor_scoped", False)) else consumer
        configured_groups = [
            item for item in (policy.get("event_groups") or [])
            if isinstance(item, dict)
        ]
        groups = [
            item for item in configured_groups
            if isinstance(item, dict)
            and str(event_type) in {str(value) for value in (item.get("event_types") or [])}
        ]
        source = {"event_id": int(event_id), "event_type": str(event_type), "actor_id": actor_id}
        if groups:
            state_key = f"{consumer}:event_wake_group_state"
            states = dict(self.store.get_meta(state_key, {}) or {})
            for group in groups:
                group_id = str(group.get("id") or "group")
                group_state = states.get(group_id) or {}
                last_event_id = int(group_state.get("last_event_id", 0) or 0)
                cooldown_minutes = max(0, int(group.get("cooldown_minutes", 0) or 0))
                last_wake_minute = int(group_state.get("last_wake_minute", -10**9) or -10**9)
                if cooldown_minutes and int(minute) - last_wake_minute < cooldown_minutes:
                    continue
                event_types = [str(value) for value in (group.get("event_types") or [])]
                if not event_types:
                    continue
                minimum = max(1, int(group.get("minimum_events", 1) or 1))
                distinct_by = [str(value) for value in (group.get("distinct_by") or []) if str(value)]
                new_state: dict[str, Any] = {}
                if distinct_by:
                    rows, new_state = self._novel_rows(group_state, event_types, distinct_by, last_event_id)
                    if len(rows) < minimum:
                        # Keep the scan position and the novel evidence seen so
                        # far, so repeated events never re-enter the count.
                        states[group_id] = {**group_state, **new_state}
                        self.store.set_meta(state_key, states)
                        continue
                    # The pending evidence wakes the consumer now; its keys
                    # are spent and must not count toward a later wake.
                    new_state = {
                        "scanned_event_id": new_state["scanned_event_id"],
                        "seen_keys": (new_state["seen_keys"] + [str(item["key"]) for item in rows])[-self._SEEN_KEY_LIMIT:],
                        "pending": [],
                    }
                else:
                    placeholders = ",".join("?" for _ in event_types)
                    rows = self.store.connection.execute(
                        f"SELECT event_id,event_type,actor_id FROM event_log WHERE event_id>? AND event_type IN ({placeholders}) ORDER BY event_id",
                        (last_event_id, *event_types),
                    ).fetchall()
                    if len(rows) < minimum:
                        continue
                states[group_id] = {
                    **new_state,
                    "last_event_id": int(rows[-1]["event_id"]),
                    "observed_count": len(rows),
                    "minimum_events": minimum,
                    "cooldown_minutes": cooldown_minutes,
                    "last_wake_minute": int(minute),
                    "updated_minute": int(minute),
                }
                self.store.set_meta(state_key, states)
                task_id = f"{consumer}:event_group:{group_id}:{int(rows[-1]['event_id'])}"
                self.store.repositories.evolution.put_evolution_task(
                    task_id,
                    consumer,
                    task_actor,
                    f"event_group:{group_id}",
                    int(minute),
                    0,
                    status="ready",
                    data={
                        "event_group": group_id,
                        "source_events": [
                            {"event_id": int(row["event_id"]), "event_type": str(row["event_type"]), "actor_id": row["actor_id"]}
                            for row in rows[-64:]
                        ],
                        "minimum_events": minimum,
                        "agent_contract": dict(group.get("agent_contract") or {}) if isinstance(group.get("agent_contract"), dict) else {},
                    },
                )
                return task_id
            return None

        # Once a consumer declares evidence groups, an unmatched event is not
        # silently promoted to a direct wake.  That fallback made a one-character
        # configuration typo bypass thresholds and cooldowns.  Worlds can still
        # declare genuinely direct evidence or task channels explicitly.
        direct_events = {str(value) for value in (policy.get("direct_event_types") or [])}
        if configured_groups or direct_events:
            requested_task_type = str(task_type or f"event:{event_type}")
            direct_prefixes = [str(value) for value in (policy.get("direct_task_type_prefixes") or [])]
            if not explicit and str(event_type) not in direct_events and not any(
                requested_task_type.startswith(prefix) for prefix in direct_prefixes if prefix
            ):
                return None

        batch_minutes = int(policy.get("batch_minutes", 0) or 0)
        if batch_minutes <= 0:
            task_id = f"{consumer}:event:{int(event_id)}"
            self.store.repositories.evolution.put_evolution_task(
                task_id,
                consumer,
                task_actor,
                str(task_type or f"event:{event_type}"),
                int(minute),
                0,
                status="ready",
                data={"source_events": [source], "source_event_id": int(event_id), "source_event_type": str(event_type), "source_actor_id": actor_id},
            )
            return task_id

        batch_key = int(minute) // batch_minutes
        task_id = f"{consumer}:event_batch:{batch_key}"
        row = self.store.connection.execute(
            "SELECT status,data_json FROM evolution_task WHERE task_id=?", (task_id,)
        ).fetchone()
        if row is not None:
            if str(row["status"]) == "completed":
                return None
            data = json.loads(row["data_json"] or "{}")
            sources = list(data.get("source_events") or [])
            if not any(int(item.get("event_id", -1)) == int(event_id) for item in sources if isinstance(item, dict)):
                sources.append(source)
            data["source_events"] = sources[-64:]
            self.store.connection.execute(
                "UPDATE evolution_task SET data_json=? WHERE task_id=?",
                (json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")), task_id),
            )
            return task_id
        self.store.repositories.evolution.put_evolution_task(
            task_id,
            consumer,
            task_actor,
            str(task_type or f"event:{event_type}"),
            int(minute),
            0,
            status="ready",
            data={"source_events": [source], "batch_minutes": batch_minutes, "batch_key": batch_key},
        )
        return task_id
