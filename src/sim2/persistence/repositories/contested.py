"""Persistence owner for contested-resource violations and access restrictions."""

from __future__ import annotations

import json
from typing import Any

from ..store import json_dumps


class ContestedRepository:
    """Violation lifecycle and reversible access restrictions.

    A violation is a historical fact, so status changes update the same row
    instead of deleting it.  An access restriction is a consequence of one
    violation; the partial unique index keeps at most one active restriction
    for one ``(actor, target, scope)`` tuple.
    """

    def __init__(self, store: Any) -> None:
        self.store = store

    # -- violations ------------------------------------------------------
    def put_violation(self, violation: dict[str, Any]) -> dict[str, Any]:
        value = dict(violation)
        self.store.connection.execute(
            "INSERT INTO violation(violation_id,intent_id,actor_id,target_id,scope,amount,mode,legitimacy,status,witnesses_json,created_minute,resolved_minute,resolved_by,data_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(value["violation_id"]),
                str(value["intent_id"]),
                str(value["actor"]),
                str(value["target"]),
                str(value["scope"]),
                float(value.get("amount", 0) or 0),
                str(value.get("mode") or "covert"),
                str(value.get("legitimacy") or "unauthorized"),
                str(value.get("status") or "undetected"),
                json_dumps(list(value.get("witnesses") or [])),
                int(value.get("created_minute", 0) or 0),
                int(value["resolved_minute"]) if value.get("resolved_minute") is not None else None,
                str(value["resolved_by"]) if value.get("resolved_by") else None,
                json_dumps(value),
            ),
        )
        return value

    def get_violation(self, violation_id: str) -> dict[str, Any] | None:
        row = self.store.connection.execute(
            "SELECT data_json FROM violation WHERE violation_id=?", (str(violation_id),)
        ).fetchone()
        return json.loads(row["data_json"]) if row is not None else None

    def list_violations(
        self,
        *,
        actor_id: str | None = None,
        witness_id: str | None = None,
        target_id: str | None = None,
        statuses: tuple[str, ...] | list[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if actor_id:
            clauses.append("actor_id=?")
            parameters.append(str(actor_id))
        if target_id:
            clauses.append("target_id=?")
            parameters.append(str(target_id))
        wanted = tuple(str(item) for item in (statuses or ()) if str(item))
        if wanted:
            clauses.append(f"status IN ({','.join('?' for _ in wanted)})")
            parameters.extend(wanted)
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        rows = self.store.connection.execute(
            f"SELECT data_json FROM violation {where}ORDER BY created_minute, violation_id LIMIT ?",
            (*parameters, max(1, min(int(limit), 500))),
        ).fetchall()
        values = [json.loads(row["data_json"]) for row in rows]
        if witness_id:
            values = [
                value for value in values
                if str(witness_id) in {str(item) for item in (value.get("witnesses") or [])}
                and str(value.get("actor") or "") != str(witness_id)
            ]
        return values

    def update_violation(self, violation: dict[str, Any]) -> dict[str, Any]:
        value = dict(violation)
        self.store.connection.execute(
            "UPDATE violation SET status=?,resolved_minute=?,resolved_by=?,data_json=? WHERE violation_id=?",
            (
                str(value.get("status") or "undetected"),
                int(value["resolved_minute"]) if value.get("resolved_minute") is not None else None,
                str(value["resolved_by"]) if value.get("resolved_by") else None,
                json_dumps(value),
                str(value["violation_id"]),
            ),
        )
        return value

    def count_violations(self) -> int:
        return int(self.store.connection.execute("SELECT COUNT(*) FROM violation").fetchone()[0])

    # -- access restrictions ---------------------------------------------
    def put_restriction(self, restriction: dict[str, Any]) -> dict[str, Any]:
        value = dict(restriction)
        self.store.connection.execute(
            "INSERT INTO access_restriction(restriction_id,actor_id,target_id,scope,status,violation_id,created_by,created_minute,revoked_by,revoked_minute,data_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(value["restriction_id"]),
                str(value["actor"]),
                str(value["target"]),
                str(value["scope"]),
                str(value.get("status") or "active"),
                str(value["violation_id"]) if value.get("violation_id") else None,
                str(value.get("created_by") or ""),
                int(value.get("created_minute", 0) or 0),
                str(value["revoked_by"]) if value.get("revoked_by") else None,
                int(value["revoked_minute"]) if value.get("revoked_minute") is not None else None,
                json_dumps(value),
            ),
        )
        return value

    def get_restriction(self, restriction_id: str) -> dict[str, Any] | None:
        row = self.store.connection.execute(
            "SELECT data_json FROM access_restriction WHERE restriction_id=?", (str(restriction_id),)
        ).fetchone()
        return json.loads(row["data_json"]) if row is not None else None

    def active_restriction(self, actor_id: str, target_id: str, scope: str) -> dict[str, Any] | None:
        row = self.store.connection.execute(
            "SELECT data_json FROM access_restriction WHERE actor_id=? AND target_id=? AND scope=? AND status='active' LIMIT 1",
            (str(actor_id), str(target_id), str(scope)),
        ).fetchone()
        return json.loads(row["data_json"]) if row is not None else None

    def list_restrictions(
        self,
        *,
        actor_id: str | None = None,
        target_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if actor_id:
            clauses.append("actor_id=?")
            parameters.append(str(actor_id))
        if target_id:
            clauses.append("target_id=?")
            parameters.append(str(target_id))
        if status:
            clauses.append("status=?")
            parameters.append(str(status))
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        rows = self.store.connection.execute(
            f"SELECT data_json FROM access_restriction {where}ORDER BY created_minute, restriction_id LIMIT ?",
            (*parameters, max(1, min(int(limit), 500))),
        ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def update_restriction(self, restriction: dict[str, Any]) -> dict[str, Any]:
        value = dict(restriction)
        self.store.connection.execute(
            "UPDATE access_restriction SET status=?,revoked_by=?,revoked_minute=?,data_json=? WHERE restriction_id=?",
            (
                str(value.get("status") or "active"),
                str(value["revoked_by"]) if value.get("revoked_by") else None,
                int(value["revoked_minute"]) if value.get("revoked_minute") is not None else None,
                json_dumps(value),
                str(value["restriction_id"]),
            ),
        )
        return value

    def count_restrictions(self) -> int:
        return int(self.store.connection.execute("SELECT COUNT(*) FROM access_restriction").fetchone()[0])
