from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..store import json_dumps


class EventsRepository:
    """Persistence owner for the configured events domain."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def append_event(self, event_type: str, actor_id: str | None, world_minute: int, data: dict[str, Any]) -> int:
        data = dict(data)
        data.setdefault("world_instance_id", self.store.get_meta("world_id", self.store.get_meta("instance_id", "")))
        data.setdefault("world_revision", int(self.store.get_meta("world_revision", 0) or 0))
        if data.get("intent_id") and not data.get("causation_id"):
            data["causation_id"] = str(data["intent_id"])
        if data.get("execution_id") and not data.get("correlation_id"):
            data["correlation_id"] = str(data["execution_id"])
        elif data.get("intent_id") and not data.get("correlation_id"):
            data["correlation_id"] = str(data["intent_id"])
        cur = self.store.connection.execute(
            "INSERT INTO event_log(event_type,actor_id,world_minute,data_json) VALUES (?,?,?,?)",
            (event_type, actor_id, world_minute, json_dumps(data)),
        )
        return int(cur.lastrowid)

    def recent_events(self, *, limit: int = 20, exclude_types: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        """Return the newest events, optionally skipping bookkeeping types such as the minute tick."""

        event_filter = f"WHERE event_type NOT IN ({','.join('?' for _ in exclude_types)}) " if exclude_types else ""
        rows = self.store.connection.execute(
            f"SELECT event_id,event_type,actor_id,world_minute,data_json FROM event_log {event_filter}ORDER BY event_id DESC LIMIT ?",
            (*exclude_types, max(1, min(int(limit), 10000))),
        ).fetchall()
        return [{"event_id": row["event_id"], "event_type": row["event_type"], "actor_id": row["actor_id"], "world_minute": row["world_minute"], **json.loads(row["data_json"])} for row in rows]

    def events_by_ids(self, event_ids: list[int]) -> list[dict[str, Any]]:
        values = list(dict.fromkeys(int(value) for value in event_ids if int(value) > 0))
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        rows = self.store.connection.execute(
            "SELECT event_id,event_type,actor_id,world_minute,data_json FROM event_log "
            f"WHERE event_id IN ({placeholders}) ORDER BY event_id",
            tuple(values),
        ).fetchall()
        return [
            {"event_id": row["event_id"], "event_type": row["event_type"], "actor_id": row["actor_id"], "world_minute": row["world_minute"], **json.loads(row["data_json"])}
            for row in rows
        ]

    def timeline_events(self, *, after_event_id: int = 0, limit: int = 100, include_ticks: bool = False) -> list[dict[str, Any]]:
        event_filter = "" if include_ticks else " AND event_type <> 'world_minute.advanced'"
        rows = self.store.connection.execute(
            "SELECT event_id,event_type,actor_id,world_minute,data_json FROM event_log "
            f"WHERE event_id>?{event_filter} ORDER BY event_id LIMIT ?",
            (max(0, int(after_event_id)), max(1, min(int(limit), 500))),
        ).fetchall()
        return [{"event_id": row["event_id"], "event_type": row["event_type"], "actor_id": row["actor_id"], "world_minute": row["world_minute"], **json.loads(row["data_json"])} for row in rows]

    def event_type_counts(self) -> dict[str, int]:
        rows = self.store.connection.execute("SELECT event_type,COUNT(*) AS count FROM event_log GROUP BY event_type").fetchall()
        return {str(row["event_type"]): int(row["count"]) for row in rows}

    def behavior_start_counts(self) -> dict[str, int]:
        rows = self.store.connection.execute(
            "SELECT json_extract(data_json,'$.behavior_id') AS behavior_id,COUNT(*) AS count "
            "FROM event_log WHERE event_type='behavior.started' "
            "AND json_extract(data_json,'$.behavior_id') IS NOT NULL GROUP BY behavior_id"
        ).fetchall()
        return {str(row["behavior_id"]): int(row["count"]) for row in rows}

    def recent_events_for_actor(self, actor_id: str, *, limit: int = 2) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            "SELECT event_id,event_type,world_minute,data_json FROM event_log WHERE actor_id=? ORDER BY event_id DESC LIMIT ?",
            (actor_id, max(1, min(int(limit), 10))),
        ).fetchall()
        return [{"event_id": row["event_id"], "event_type": row["event_type"], "world_minute": row["world_minute"], **json.loads(row["data_json"])} for row in rows]

    def recent_outcomes_for_actor(self, actor_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """Return behavior outcomes even when later action events are numerous."""

        rows = self.store.connection.execute(
            "SELECT event_id,event_type,world_minute,data_json FROM event_log "
            "WHERE actor_id=? AND event_type IN ('behavior.completed','behavior.failed','behavior.interrupted') "
            "ORDER BY event_id DESC LIMIT ?",
            (actor_id, max(1, min(int(limit), 20))),
        ).fetchall()
        return [{"event_id": row["event_id"], "event_type": row["event_type"], "world_minute": row["world_minute"], **json.loads(row["data_json"])} for row in rows]

    def recent_world_change_events(self, *, limit: int = 8) -> list[dict[str, Any]]:
        """Return durable world proposal results for the God view."""

        rows = self.store.connection.execute(
            "SELECT event_id,event_type,actor_id,world_minute,data_json FROM event_log "
            "WHERE event_type LIKE 'world.change.%' OR event_type IN ('entity.created','world.rule.followed') "
            "ORDER BY event_id DESC LIMIT ?",
            (max(1, min(int(limit), 30)),),
        ).fetchall()
        return [{"event_id": row["event_id"], "event_type": row["event_type"], "actor_id": row["actor_id"], "world_minute": row["world_minute"], **json.loads(row["data_json"])} for row in rows]

    def recent_behavior_outcomes(self, *, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            "SELECT event_id,event_type,actor_id,world_minute,data_json FROM event_log "
            "WHERE event_type IN ('behavior.completed','behavior.failed','behavior.interrupted') "
            "ORDER BY event_id DESC LIMIT ?",
            (max(1, min(int(limit), 30)),),
        ).fetchall()
        return [{"event_id": row["event_id"], "event_type": row["event_type"], "actor_id": row["actor_id"], "world_minute": row["world_minute"], **json.loads(row["data_json"])} for row in rows]

    @staticmethod
    def _resource_amount(entity: dict[str, Any]) -> float | None:
        state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
        value = entity.get("amount", state.get("quantity", state.get("amount")))
        return float(value) if isinstance(value, (int, float)) else None

    def world_trends(self, *, current_minute: int | None = None) -> dict[str, Any]:
        """Build compact weekly/monthly aggregates for the world-level Agent.

        The God view needs evidence of change, not another unbounded event
        dump.  Aggregates are derived from the event log and current SQLite
        projections; no time-decay score is applied to character memory.
        """

        end = int(self.store.get_meta("world_minute", 0) if current_minute is None else current_minute)
        baseline = self.store.get_meta("resource_baseline", {}) or {}
        resources: list[dict[str, Any]] = []
        resource_labels: dict[str, str] = {}
        for item in self.store.repositories.entities.entities_of_type("resource"):
            try:
                entity = self.store.repositories.entities.get_entity(str(item["id"]))
            except KeyError:
                continue
            amount = self._resource_amount(entity)
            if amount is None:
                continue
            resource_labels[str(item["id"])] = str(entity.get("name", item["id"])).replace("_", " ")
            initial = baseline.get(str(item["id"]))
            row = {"name": str(entity.get("name", item["id"])).replace("_", " "), "current": round(amount, 3)}
            if isinstance(initial, (int, float)):
                row["change_from_start"] = round(amount - float(initial), 3)
            resources.append(row)

        def aggregate(window: int) -> dict[str, Any]:
            start = max(0, end - int(window))
            rows = self.store.connection.execute(
                "SELECT event_type,data_json FROM event_log WHERE world_minute>? AND world_minute<=?",
                (start, end),
            ).fetchall()
            resource_regenerated: dict[str, float] = {}
            resource_delta: dict[str, float] = {}
            relationship_delta: dict[str, float] = {}
            relationship_changes: dict[str, int] = {}
            goal_updates = 0
            goal_progress: dict[str, int] = {}
            behavior_completed = behavior_failed = 0
            signal_events: dict[str, int] = {}
            world_property_deltas: dict[str, float] = {}
            for row in rows:
                event_type = str(row["event_type"])
                data = json.loads(row["data_json"])
                if event_type == "resource.regenerated":
                    key = str(data.get("resource_id", "resource"))
                    resource_regenerated[key] = resource_regenerated.get(key, 0.0) + float(data.get("amount", 0) or 0)
                elif event_type == "resource.changed":
                    key = str(data.get("resource_id", "resource"))
                    resource_delta[key] = resource_delta.get(key, 0.0) + float(data.get("delta", 0) or 0)
                elif event_type == "relationship.changed":
                    relation = str(data.get("relation", "related"))
                    relationship_delta[relation] = relationship_delta.get(relation, 0.0) + float(data.get("delta", 0) or 0)
                    relationship_changes[relation] = relationship_changes.get(relation, 0) + 1
                elif event_type == "goal.updated":
                    goal_updates += 1
                elif event_type == "behavior.completed":
                    behavior_completed += 1
                    expected = ((data.get("outcome") or {}).get("expected") or {})
                    for tag in expected.get("goal_tags", []) or []:
                        goal_progress[str(tag)] = goal_progress.get(str(tag), 0) + 1
                elif event_type in {"behavior.failed", "behavior.interrupted"}:
                    behavior_failed += 1
                if data.get("trend") is True:
                    signal_events[event_type] = signal_events.get(event_type, 0) + 1
                if event_type == "world.property_changed":
                    property_id = str(data.get("property", "property"))
                    world_property_deltas[property_id] = world_property_deltas.get(property_id, 0.0) + float(data.get("delta", 0) or 0)
            current_relationships = self.store.repositories.entities.list_relationships()
            relation_current: dict[str, dict[str, float]] = {}
            for item in current_relationships:
                relation = str(item.get("type", "related"))
                value = float(item.get("relation_strength", item.get("trust", 0)) or 0)
                bucket = relation_current.setdefault(relation, {"total": 0.0, "count": 0.0})
                bucket["total"] += value
                bucket["count"] += 1
            relationships = []
            for relation, values in sorted(relation_current.items()):
                current_average = values["total"] / max(1.0, values["count"])
                delta = relationship_delta.get(relation, 0.0)
                changes = relationship_changes.get(relation, 0)
                if not changes and abs(delta) < 1e-9 and abs(current_average) < 1e-9:
                    continue
                relationships.append({
                    "relation": relation,
                    "current_average": round(current_average, 4),
                    "delta": round(delta, 4),
                    "changes": changes,
                })
            return {
                "window_minutes": int(window),
                "from_minute": start,
                "to_minute": end,
                "resources_regenerated": {key: round(value, 3) for key, value in sorted(resource_regenerated.items())},
                "resource_deltas": {key: round(value, 3) for key, value in sorted(resource_delta.items())},
                "relationship_trends": relationships,
                "goal_trends": {
                    "goal_updates": goal_updates,
                    "completed_behaviors": behavior_completed,
                    "failed_or_interrupted_behaviors": behavior_failed,
                    "progress_by_tag": dict(sorted(goal_progress.items())),
                },
                "signal_events": dict(sorted(signal_events.items())),
                "world_property_deltas": {key: round(value, 4) for key, value in sorted(world_property_deltas.items())},
            }

        calendar = (self.store.get_meta("setting", {}) or {}).get("calendar", {}) if isinstance(self.store.get_meta("setting", {}), dict) else {}
        week_minutes = max(1, int(calendar.get("minutes_per_week", calendar.get("minutes_per_day", 1440) * 7) or 10080))
        month_minutes = max(week_minutes, int(calendar.get("minutes_per_month", calendar.get("minutes_per_day", 1440) * 30) or 43200))
        agents = self.store.repositories.entities.list_agents()
        active_goals = [str(agent.get("ultimate_goal", "")).strip() for agent in agents if str(agent.get("ultimate_goal", "")).strip()]
        weekly = aggregate(week_minutes)
        monthly = aggregate(month_minutes)
        pending_proposals = len(self.store.repositories.evolution.list_world_proposals(status="pending_evaluation", limit=100))
        active_story_threads = sum(
            1 for key, value in (self.store.get_meta("story_state", {}) or {}).items()
            if key != "emergent_beats" and isinstance(value, dict) and value.get("status") == "active"
        )
        completed = float(weekly["goal_trends"]["completed_behaviors"] or 0)
        failed = float(weekly["goal_trends"]["failed_or_interrupted_behaviors"] or 0)
        total = completed + failed
        failure_rate = failed / max(1.0, total)
        resource_volatility = sum(abs(float(value)) for value in weekly.get("resource_deltas", {}).values())
        baseline_total = sum(abs(float(value)) for value in (baseline or {}).values()) or 1.0
        uncertainty = min(1.0, 0.45 * min(1.0, pending_proposals / 6.0) + 0.35 * min(1.0, active_story_threads / 6.0) + 0.20 * failure_rate)
        disorder = min(1.0, 0.55 * failure_rate + 0.25 * min(1.0, resource_volatility / baseline_total) + 0.20 * min(1.0, pending_proposals / 6.0))
        coherence = max(0.0, min(1.0, (completed / max(1.0, total)) * 0.7 + (1.0 - failure_rate) * 0.3))
        novelty = min(1.0, (len(weekly.get("signal_events", {})) + len(weekly.get("resource_deltas", {})) + len(weekly.get("world_property_deltas", {}))) / 12.0)
        return {
            "as_of_minute": end,
            "current_resources": sorted(resources, key=lambda item: str(item.get("name", ""))),
            "resource_labels": resource_labels,
            "active_goal_count": len(active_goals),
            "weekly": weekly,
            "monthly": monthly,
            "world_signals": {
                "uncertainty": round(uncertainty, 4),
                "disorder": round(disorder, 4),
                "coherence": round(coherence, 4),
                "novelty": round(novelty, 4),
                "basis": "derived observation only; signals do not mutate world state",
            },
        }

