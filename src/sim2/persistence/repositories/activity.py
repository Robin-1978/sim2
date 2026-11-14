from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..store import json_dumps


class ActivityRepository:
    """Persistence owner for the configured activity domain."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def last_behavior_minute(self, actor_id: str, behavior_id: str) -> int | None:
        row = self.store.connection.execute(
            "SELECT MAX(world_minute) AS minute FROM event_log "
            "WHERE actor_id=? AND event_type='behavior.completed' "
            "AND json_extract(data_json,'$.behavior_id')=?",
            (actor_id, behavior_id),
        ).fetchone()
        return int(row["minute"]) if row and row["minute"] is not None else None

    def put_bt_instance(self, execution_id: str, actor_id: str, behavior_id: str, status: str, current_node: int, data: dict[str, Any], started_minute: int, updated_minute: int) -> None:
        self.store.connection.execute(
            "INSERT OR REPLACE INTO bt_instance(execution_id,actor_id,behavior_id,status,current_node,data_json,started_minute,updated_minute) VALUES (?,?,?,?,?,?,?,?)",
            (execution_id, actor_id, behavior_id, status, int(current_node), json_dumps(data), int(started_minute), int(updated_minute)),
        )

    def update_bt_instance(self, execution_id: str, *, status: str, current_node: int, data: dict[str, Any], updated_minute: int) -> None:
        self.store.connection.execute(
            "UPDATE bt_instance SET status=?,current_node=?,data_json=?,updated_minute=? WHERE execution_id=?",
            (status, int(current_node), json_dumps(data), int(updated_minute), execution_id),
        )

    def put_action_instance(self, action_instance_id: str, execution_id: str, actor_id: str, action_id: str, status: str, elapsed_minutes: int, remaining_minutes: int, data: dict[str, Any], started_minute: int, updated_minute: int) -> None:
        self.store.connection.execute(
            "INSERT OR REPLACE INTO action_instance(action_instance_id,execution_id,actor_id,action_id,status,elapsed_minutes,remaining_minutes,data_json,started_minute,updated_minute) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (action_instance_id, execution_id, actor_id, action_id, status, int(elapsed_minutes), int(remaining_minutes), json_dumps(data), int(started_minute), int(updated_minute)),
        )

    def update_action_instance(self, action_instance_id: str, *, status: str, elapsed_minutes: int, remaining_minutes: int, data: dict[str, Any], updated_minute: int) -> None:
        self.store.connection.execute(
            "UPDATE action_instance SET status=?,elapsed_minutes=?,remaining_minutes=?,data_json=?,updated_minute=? WHERE action_instance_id=?",
            (status, int(elapsed_minutes), int(remaining_minutes), json_dumps(data), int(updated_minute), action_instance_id),
        )

    def put_exploration_result(self, agent_id: str, area_id: str, world_minute: int, data: dict[str, Any]) -> int:
        cur = self.store.connection.execute(
            "INSERT INTO exploration_result(agent_id,area_id,world_minute,data_json) VALUES (?,?,?,?)",
            (agent_id, area_id, int(world_minute), json_dumps(data)),
        )
        return int(cur.lastrowid)

    def advance_progress(self, owner_id: str, progress_key: str, amount: float, required: float, world_minute: int, data: dict[str, Any] | None = None) -> dict[str, Any]:
        row = self.store.connection.execute(
            "SELECT current_value,required_value,data_json FROM progress_state WHERE owner_id=? AND progress_key=?",
            (owner_id, progress_key),
        ).fetchone()
        before = float(row["current_value"]) if row else 0.0
        configured_required = float(row["required_value"]) if row else max(1.0, float(required))
        current = min(configured_required, before + float(amount))
        merged = json.loads(row["data_json"]) if row else {}
        merged.update(data or {})
        self.store.connection.execute(
            "INSERT INTO progress_state(owner_id,progress_key,current_value,required_value,data_json,updated_minute) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(owner_id,progress_key) DO UPDATE SET current_value=excluded.current_value,required_value=excluded.required_value,data_json=excluded.data_json,updated_minute=excluded.updated_minute",
            (owner_id, progress_key, current, configured_required, json_dumps(merged), int(world_minute)),
        )
        return {"owner_id": owner_id, "progress_key": progress_key, "before": before, "after": current, "required": configured_required, "completed": current >= configured_required, **merged}

    def get_progress(self, owner_id: str, progress_key: str) -> dict[str, Any] | None:
        row = self.store.connection.execute(
            "SELECT current_value,required_value,data_json,updated_minute FROM progress_state WHERE owner_id=? AND progress_key=?",
            (owner_id, progress_key),
        ).fetchone()
        if row is None:
            return None
        return {"owner_id": owner_id, "progress_key": progress_key, "current": row["current_value"], "required": row["required_value"], "updated_minute": row["updated_minute"], **json.loads(row["data_json"])}

    def list_progress(self, owner_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            "SELECT progress_key,current_value,required_value,data_json,updated_minute FROM progress_state WHERE owner_id=? ORDER BY updated_minute DESC,progress_key LIMIT ?",
            (owner_id, max(1, min(int(limit), 200))),
        ).fetchall()
        return [{"owner_id": owner_id, "progress_key": row["progress_key"], "current": row["current_value"], "required": row["required_value"], "updated_minute": row["updated_minute"], **json.loads(row["data_json"])} for row in rows]

    def put_capability_progress(self, agent_id: str, capability: str, delta: float, source: str, world_minute: int, data: dict[str, Any] | None = None) -> int:
        cur = self.store.connection.execute(
            "INSERT INTO capability_progress(agent_id,capability,delta,source,world_minute,data_json) VALUES (?,?,?,?,?,?)",
            (agent_id, capability, float(delta), source, int(world_minute), json_dumps(data or {})),
        )
        return int(cur.lastrowid)

    def list_capability_progress(self, agent_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            "SELECT id,capability,delta,source,world_minute,data_json FROM capability_progress WHERE agent_id=? ORDER BY id DESC LIMIT ?",
            (agent_id, max(1, min(int(limit), 100))),
        ).fetchall()
        return [{"id": row["id"], "capability": row["capability"], "delta": row["delta"], "source": row["source"], "world_minute": row["world_minute"], **json.loads(row["data_json"])} for row in rows]

    def capability_evidence(self, agent_id: str, capability: str, *, after_minute: int = -1) -> list[dict[str, Any]]:
        """Return successful, capability-specific evidence after the last review."""

        rows = self.store.connection.execute(
            "SELECT event_id,world_minute,data_json FROM event_log "
            "WHERE actor_id=? AND event_type='capability.evidence' AND world_minute>? "
            "AND json_extract(data_json,'$.capability')=? ORDER BY event_id",
            (str(agent_id), int(after_minute), str(capability)),
        ).fetchall()
        return [
            {"event_id": int(row["event_id"]), "world_minute": int(row["world_minute"]), **json.loads(row["data_json"])}
            for row in rows
        ]

