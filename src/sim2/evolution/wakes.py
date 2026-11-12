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

    def queue(
        self,
        consumer: str,
        event_id: int,
        event_type: str,
        actor_id: str | None,
        minute: int,
        *,
        task_type: str | None = None,
    ) -> str | None:
        consumer = str(consumer or "").strip()
        if not consumer:
            return None
        policy = self._policy(consumer)
        if bool(policy.get("actor_scoped", False)) and not str(actor_id or "").strip():
            return None
        task_actor = str(actor_id) if bool(policy.get("actor_scoped", False)) else consumer
        configured_groups = [
            item for item in (policy.get("event_groups") or policy.get("groups") or [])
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
                placeholders = ",".join("?" for _ in event_types)
                rows = self.store.connection.execute(
                    f"SELECT event_id,event_type,actor_id FROM event_log WHERE event_id>? AND event_type IN ({placeholders}) ORDER BY event_id",
                    (last_event_id, *event_types),
                ).fetchall()
                minimum = max(1, int(group.get("minimum_events", 1) or 1))
                if len(rows) < minimum:
                    continue
                states[group_id] = {
                    "last_event_id": int(rows[-1]["event_id"]),
                    "observed_count": len(rows),
                    "minimum_events": minimum,
                    "cooldown_minutes": cooldown_minutes,
                    "last_wake_minute": int(minute),
                    "updated_minute": int(minute),
                }
                self.store.set_meta(state_key, states)
                task_id = f"{consumer}:event_group:{group_id}:{int(rows[-1]['event_id'])}"
                self.store.put_evolution_task(
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
        if configured_groups:
            direct_events = {str(value) for value in (policy.get("direct_event_types") or [])}
            requested_task_type = str(task_type or f"event:{event_type}")
            direct_prefixes = [str(value) for value in (policy.get("direct_task_type_prefixes") or [])]
            if str(event_type) not in direct_events and not any(
                requested_task_type.startswith(prefix) for prefix in direct_prefixes if prefix
            ):
                return None

        batch_minutes = int(policy.get("batch_minutes", 0) or 0)
        if batch_minutes <= 0:
            task_id = f"{consumer}:event:{int(event_id)}"
            self.store.put_evolution_task(
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
        self.store.put_evolution_task(
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
