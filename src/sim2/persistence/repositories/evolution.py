from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..store import json_dumps


class EvolutionRepository:
    """Persistence owner for the configured evolution domain."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def put_evolution_task(self, task_id: str, role: str, actor_id: str | None, task_type: str, due_minute: int, cadence_minutes: int, status: str = "scheduled", data: dict[str, Any] | None = None) -> None:
        self.store.connection.execute(
            "INSERT OR REPLACE INTO evolution_task(task_id,role,actor_id,task_type,due_minute,cadence_minutes,status,data_json) VALUES (?,?,?,?,?,?,?,?)",
            (task_id, role, actor_id, task_type, int(due_minute), int(cadence_minutes), status, json_dumps(data or {})),
        )

    def due_evolution_tasks(self, world_minute: int) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            "SELECT task_id,role,actor_id,task_type,due_minute,cadence_minutes,status,data_json FROM evolution_task WHERE due_minute<=? AND status='scheduled' ORDER BY due_minute,task_id",
            (int(world_minute),),
        ).fetchall()
        return [{"task_id": row["task_id"], "role": row["role"], "actor_id": row["actor_id"], "task_type": row["task_type"], "due_minute": row["due_minute"], "cadence_minutes": row["cadence_minutes"], "status": row["status"], **json.loads(row["data_json"])} for row in rows]

    def ready_evolution_tasks(self) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            "SELECT task_id,role,actor_id,task_type,due_minute,cadence_minutes,status,data_json FROM evolution_task WHERE status='ready' ORDER BY due_minute,task_id",
        ).fetchall()
        return [{"task_id": row["task_id"], "role": row["role"], "actor_id": row["actor_id"], "task_type": row["task_type"], "due_minute": row["due_minute"], "cadence_minutes": row["cadence_minutes"], "status": row["status"], **json.loads(row["data_json"])} for row in rows]

    def mark_evolution_task_ready(self, task_id: str) -> None:
        self.store.connection.execute("UPDATE evolution_task SET status='ready' WHERE task_id=? AND status='scheduled'", (task_id,))

    def claim_evolution_task(self, task_id: str, world_minute: int) -> dict[str, Any] | None:
        row = self.store.connection.execute("SELECT task_id,role,actor_id,task_type,due_minute,cadence_minutes,status,data_json FROM evolution_task WHERE task_id=?", (task_id,)).fetchone()
        if row is None or row["status"] not in {"scheduled", "ready"} or row["due_minute"] > world_minute:
            return None
        self.store.connection.execute("UPDATE evolution_task SET status='claimed' WHERE task_id=?", (task_id,))
        return {"task_id": row["task_id"], "role": row["role"], "actor_id": row["actor_id"], "task_type": row["task_type"], "due_minute": row["due_minute"], "cadence_minutes": row["cadence_minutes"], **json.loads(row["data_json"])}

    def recover_claimed_evolution_tasks(self, world_minute: int) -> list[str]:
        """Return tasks left claimed by a process that stopped unexpectedly.

        ``claimed`` is a transient hand-off state between the scheduler and an
        Agent session.  A provider failure is explicitly rescheduled by the
        runner, but a process crash can occur after the claim and before that
        cleanup.  On the next process start, due claimed tasks are safe to
        put back into the ready queue because this runtime has one logical
        evolution worker and does not execute a claimed task concurrently.
        """

        rows = self.store.connection.execute(
            "SELECT task_id FROM evolution_task "
            "WHERE status='claimed' AND due_minute<=? ORDER BY due_minute,task_id",
            (int(world_minute),),
        ).fetchall()
        task_ids = [str(row["task_id"]) for row in rows]
        if task_ids:
            self.store.connection.execute(
                "UPDATE evolution_task SET status='ready' "
                "WHERE status='claimed' AND due_minute<=?",
                (int(world_minute),),
            )
        return task_ids

    def reschedule_evolution_task(self, task_id: str, due_minute: int, data: dict[str, Any] | None = None) -> None:
        if data is None:
            self.store.connection.execute(
                "UPDATE evolution_task SET due_minute=?,status='scheduled' WHERE task_id=?",
                (int(due_minute), task_id),
            )
            return
        self.store.connection.execute(
            "UPDATE evolution_task SET due_minute=?,status='scheduled',data_json=? WHERE task_id=?",
            (int(due_minute), json_dumps(data), task_id),
        )

    def complete_evolution_task(self, task_id: str, completed_minute: int) -> None:
        row = self.store.connection.execute("SELECT cadence_minutes,data_json FROM evolution_task WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return
        try:
            data = json.loads(row["data_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.pop("retry_count", None)
        data.pop("last_retry_reason", None)
        if int(row["cadence_minutes"]) <= 0:
            self.store.connection.execute(
                "UPDATE evolution_task SET status='completed',data_json=? WHERE task_id=?",
                (json_dumps(data), task_id),
            )
            return
        self.store.connection.execute(
            "UPDATE evolution_task SET due_minute=?,status='scheduled',data_json=? WHERE task_id=?",
            (int(completed_minute) + int(row["cadence_minutes"]), json_dumps(data), task_id),
        )

    def create_intent(self, intent_id: str, actor_id: str, intent_type: str, data: dict[str, Any], world_minute: int) -> None:
        self.store.connection.execute(
            "INSERT INTO intent(intent_id,actor_id,intent_type,status,data_json,created_minute) VALUES (?,?,?,?,?,?)",
            (intent_id, actor_id, intent_type, "received", json_dumps(data), world_minute),
        )

    def finish_intent(self, intent_id: str, status: str, result: dict[str, Any]) -> None:
        self.store.connection.execute(
            "UPDATE intent SET status=?, result_json=? WHERE intent_id=?",
            (status, json_dumps(result), intent_id),
        )

    def get_intent(self, intent_id: str) -> dict[str, Any] | None:
        row = self.store.connection.execute(
            "SELECT intent_id,actor_id,intent_type,status,data_json,result_json FROM intent WHERE intent_id=?",
            (intent_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "intent_id": row["intent_id"],
            "actor_id": row["actor_id"],
            "intent_type": row["intent_type"],
            "status": row["status"],
            "data": json.loads(row["data_json"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
        }

    def put_world_proposal(
        self,
        proposal_id: str,
        intent_id: str,
        actor_id: str,
        change_type: str,
        status: str,
        created_minute: int,
        evaluation_due_minute: int,
        expected: dict[str, Any] | list[Any] | None,
        result: dict[str, Any] | None = None,
    ) -> None:
        self.store.connection.execute(
            "INSERT OR REPLACE INTO world_proposal(proposal_id,intent_id,actor_id,change_type,status,created_minute,evaluation_due_minute,expected_json,observed_json,result_json,updated_minute) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(proposal_id), str(intent_id), str(actor_id), str(change_type), str(status), int(created_minute), int(evaluation_due_minute),
                json_dumps(expected or {}), json_dumps({}), json_dumps(result or {}), int(created_minute),
            ),
        )

    def list_world_proposals(self, *, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        query = "SELECT proposal_id,intent_id,actor_id,change_type,status,created_minute,evaluation_due_minute,expected_json,observed_json,result_json,updated_minute FROM world_proposal"
        args: list[Any] = []
        if status:
            query += " WHERE status=?"
            args.append(str(status))
        query += " ORDER BY created_minute DESC,proposal_id LIMIT ?"
        args.append(max(1, min(int(limit), 100)))
        rows = self.store.connection.execute(query, tuple(args)).fetchall()
        return [
            {
                "proposal_id": row["proposal_id"], "intent_id": row["intent_id"], "actor_id": row["actor_id"],
                "change_type": row["change_type"], "status": row["status"], "created_minute": row["created_minute"],
                "evaluation_due_minute": row["evaluation_due_minute"], "expected": json.loads(row["expected_json"] or "{}"),
                "observed": json.loads(row["observed_json"] or "{}"), "result": json.loads(row["result_json"] or "{}"),
                "updated_minute": row["updated_minute"],
            }
            for row in rows
        ]

    def update_world_proposal(self, proposal_id: str, status: str, observed: dict[str, Any], result: dict[str, Any], updated_minute: int) -> None:
        self.store.connection.execute(
            "UPDATE world_proposal SET status=?,observed_json=?,result_json=?,updated_minute=? WHERE proposal_id=?",
            (str(status), json_dumps(observed or {}), json_dumps(result or {}), int(updated_minute), str(proposal_id)),
        )

