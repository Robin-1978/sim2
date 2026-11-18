"""Persistence owner for access requests and scoped delegations."""

from __future__ import annotations

import json
from typing import Any

from ..store import json_dumps


class AuthorizationRepository:
    """Access-request and delegation lifecycle records.

    Delegations are a first-class row because a revoked delegation is an
    auditable historical fact.  The social ``relationship`` table remains the
    graph of durable social ties; it cannot preserve multiple delegation
    lifetimes for the same ``(source, target, scope)`` tuple.
    """

    def __init__(self, store: Any) -> None:
        self.store = store

    # -- access requests -------------------------------------------------
    def put_request(self, request: dict[str, Any]) -> dict[str, Any]:
        value = dict(request)
        self.store.connection.execute(
            "INSERT INTO access_request(request_id,requester_id,behavior_id,target_id,scope,status,based_on_event,created_minute,resolved_minute,resolved_by,data_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(value["request_id"]),
                str(value["requester"]),
                str(value["behavior_id"]),
                str(value["target"]),
                str(value["scope"]),
                str(value.get("status") or "pending"),
                int(value["based_on_event"]) if value.get("based_on_event") not in (None, "") else None,
                int(value.get("created_minute", 0) or 0),
                int(value["resolved_minute"]) if value.get("resolved_minute") is not None else None,
                str(value["resolved_by"]) if value.get("resolved_by") else None,
                json_dumps(value),
            ),
        )
        return value

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        row = self.store.connection.execute(
            "SELECT data_json FROM access_request WHERE request_id=?", (str(request_id),)
        ).fetchone()
        return json.loads(row["data_json"]) if row is not None else None

    def list_requests(self, *, status: str | None = None, requester_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if status:
            clauses.append("status=?")
            parameters.append(str(status))
        if requester_id:
            clauses.append("requester_id=?")
            parameters.append(str(requester_id))
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        rows = self.store.connection.execute(
            f"SELECT data_json FROM access_request {where}ORDER BY created_minute, request_id LIMIT ?",
            (*parameters, max(1, min(int(limit), 500))),
        ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def update_request(self, request: dict[str, Any]) -> dict[str, Any]:
        value = dict(request)
        self.store.connection.execute(
            "UPDATE access_request SET status=?,resolved_minute=?,resolved_by=?,data_json=? WHERE request_id=?",
            (
                str(value.get("status") or "pending"),
                int(value["resolved_minute"]) if value.get("resolved_minute") is not None else None,
                str(value["resolved_by"]) if value.get("resolved_by") else None,
                json_dumps(value),
                str(value["request_id"]),
            ),
        )
        return value

    # -- delegations -----------------------------------------------------
    def put_delegation(self, delegation: dict[str, Any]) -> dict[str, Any]:
        value = dict(delegation)
        self.store.connection.execute(
            "INSERT INTO delegation(delegation_id,source_id,target_id,scope,granted_by,status,granted_minute,expires_minute,revoked_minute,revoked_by,request_id,data_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(value["delegation_id"]),
                str(value["source"]),
                str(value["target"]),
                str(value["scope"]),
                str(value["granted_by"]),
                str(value.get("status") or "active"),
                int(value.get("granted_at", 0) or 0),
                int(value["expires_minute"]) if value.get("expires_minute") is not None else None,
                int(value["revoked_at"]) if value.get("revoked_at") is not None else None,
                str(value["revoked_by"]) if value.get("revoked_by") else None,
                str(value["request_id"]) if value.get("request_id") else None,
                json_dumps(value),
            ),
        )
        return value

    def get_delegation(self, delegation_id: str) -> dict[str, Any] | None:
        row = self.store.connection.execute(
            "SELECT data_json FROM delegation WHERE delegation_id=?", (str(delegation_id),)
        ).fetchone()
        return json.loads(row["data_json"]) if row is not None else None

    def list_delegations(
        self,
        *,
        status: str | None = None,
        source_id: str | None = None,
        target_id: str | None = None,
        scope: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        for column, value in (("status", status), ("source_id", source_id), ("target_id", target_id), ("scope", scope)):
            if value is not None:
                clauses.append(f"{column}=?")
                parameters.append(str(value))
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        rows = self.store.connection.execute(
            f"SELECT data_json FROM delegation {where}ORDER BY granted_minute, delegation_id LIMIT ?",
            (*parameters, max(1, min(int(limit), 500))),
        ).fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def active_delegation(self, source_id: str, target_id: str, scope: str) -> dict[str, Any] | None:
        rows = self.list_delegations(status="active", source_id=source_id, target_id=target_id, scope=scope, limit=1)
        return rows[0] if rows else None

    def update_delegation(self, delegation: dict[str, Any]) -> dict[str, Any]:
        value = dict(delegation)
        self.store.connection.execute(
            "UPDATE delegation SET status=?,revoked_minute=?,revoked_by=?,data_json=? WHERE delegation_id=?",
            (
                str(value.get("status") or "active"),
                int(value["revoked_at"]) if value.get("revoked_at") is not None else None,
                str(value["revoked_by"]) if value.get("revoked_by") else None,
                json_dumps(value),
                str(value["delegation_id"]),
            ),
        )
        return value

    def count_delegations(self) -> int:
        row = self.store.connection.execute("SELECT COUNT(*) AS count FROM delegation").fetchone()
        return int(row["count"] if row else 0)

    def count_requests(self) -> int:
        row = self.store.connection.execute("SELECT COUNT(*) AS count FROM access_request").fetchone()
        return int(row["count"] if row else 0)
