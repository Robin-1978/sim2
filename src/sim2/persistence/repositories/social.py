from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..store import json_dumps


class SocialRepository:
    """Persistence owner for the configured social domain."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def put_dialogue_exchange(self, speaker_id: str, listener_id: str | None, topic: str, world_minute: int, data: dict[str, Any]) -> int:
        cur = self.store.connection.execute(
            "INSERT INTO dialogue_exchange(speaker_id,listener_id,topic,world_minute,data_json) VALUES (?,?,?,?,?)",
            (speaker_id, listener_id, topic, int(world_minute), json_dumps(data)),
        )
        return int(cur.lastrowid)

    def get_dialogue_exchange(self, exchange_id: int | str) -> dict[str, Any] | None:
        """Return one recorded exchange with its human-readable payload.

        The interaction scheduler uses this small projection to decide whether
        a reply adds new information.  It deliberately stays separate from
        memories: a transcript is audit data, while memories are the facts an
        agent may later retrieve.
        """

        row = self.store.connection.execute(
            "SELECT id,speaker_id,listener_id,topic,world_minute,data_json "
            "FROM dialogue_exchange WHERE id=?",
            (int(exchange_id),),
        ).fetchone()
        if row is None:
            return None
        return {
            "dialogue_id": int(row["id"]),
            "speaker_id": row["speaker_id"],
            "listener_id": row["listener_id"],
            "topic": row["topic"],
            "world_minute": int(row["world_minute"]),
            **json.loads(row["data_json"] or "{}"),
        }

    def put_trade_offer(self, offer_id: str, offerer_id: str, recipient_id: str, give: dict[str, Any], want: dict[str, Any], world_minute: int, *, parent_offer_id: str | None = None, round_number: int = 1) -> None:
        self.store.connection.execute(
            "INSERT INTO trade_offer(offer_id,parent_offer_id,round,offerer_id,recipient_id,give_json,want_json,status,created_minute) VALUES (?,?,?,?,?,?,?,?,?)",
            (offer_id, parent_offer_id, int(round_number), offerer_id, recipient_id, json_dumps(give), json_dumps(want), "open", int(world_minute)),
        )

    def get_trade_offer(self, offer_id: str) -> dict[str, Any] | None:
        row = self.store.connection.execute(
            "SELECT offer_id,parent_offer_id,round,offerer_id,recipient_id,give_json,want_json,status,created_minute,resolved_minute,result_json FROM trade_offer WHERE offer_id=?",
            (offer_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "offer_id": row["offer_id"], "parent_offer_id": row["parent_offer_id"], "round": row["round"], "offerer_id": row["offerer_id"], "recipient_id": row["recipient_id"],
            "give": json.loads(row["give_json"]), "want": json.loads(row["want_json"]), "status": row["status"],
            "created_minute": row["created_minute"], "resolved_minute": row["resolved_minute"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
        }

    def finish_trade_offer(self, offer_id: str, status: str, world_minute: int, result: dict[str, Any]) -> None:
        self.store.connection.execute(
            "UPDATE trade_offer SET status=?,resolved_minute=?,result_json=? WHERE offer_id=?",
            (status, int(world_minute), json_dumps(result), offer_id),
        )

    def list_trade_offers(self, agent_id: str, *, include_closed: bool = False, limit: int = 20) -> list[dict[str, Any]]:
        query = "SELECT offer_id FROM trade_offer WHERE (offerer_id=? OR recipient_id=?)"
        params: list[Any] = [agent_id, agent_id]
        if not include_closed:
            query += " AND status='open'"
        query += " ORDER BY created_minute DESC, offer_id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 100)))
        ids = [row["offer_id"] for row in self.store.connection.execute(query, params).fetchall()]
        offers = []
        for offer_id in ids:
            offer = self.get_trade_offer(offer_id)
            if offer is not None:
                offers.append(offer)
        return offers

    def put_institution(self, institution_id: str, status: str, data: dict[str, Any], created_minute: int, activated_minute: int | None = None) -> None:
        self.store.connection.execute(
            "INSERT INTO institution_definition(institution_id,status,data_json,created_minute,activated_minute) VALUES (?,?,?,?,?) "
            "ON CONFLICT(institution_id) DO UPDATE SET status=excluded.status,data_json=excluded.data_json,activated_minute=excluded.activated_minute",
            (str(institution_id), str(status), json_dumps(data), int(created_minute), int(activated_minute) if activated_minute is not None else None),
        )

    def get_institution(self, institution_id: str) -> dict[str, Any] | None:
        row = self.store.connection.execute(
            "SELECT institution_id,status,data_json,created_minute,activated_minute FROM institution_definition WHERE institution_id=?",
            (str(institution_id),),
        ).fetchone()
        if row is None:
            return None
        return {
            "institution_id": row["institution_id"],
            "status": row["status"],
            "created_minute": row["created_minute"],
            "activated_minute": row["activated_minute"],
            **json.loads(row["data_json"] or "{}"),
        }

    def list_institutions(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT institution_id,status,data_json,created_minute,activated_minute FROM institution_definition"
        args: list[Any] = []
        if status:
            query += " WHERE status=?"
            args.append(str(status))
        query += " ORDER BY created_minute DESC,institution_id LIMIT ?"
        args.append(max(1, min(int(limit), 200)))
        rows = self.store.connection.execute(query, tuple(args)).fetchall()
        return [
            {
                "institution_id": row["institution_id"],
                "status": row["status"],
                "created_minute": row["created_minute"],
                "activated_minute": row["activated_minute"],
                **json.loads(row["data_json"] or "{}"),
            }
            for row in rows
        ]

    def append_institution_ledger_entry(
        self,
        institution_id: str,
        subject_id: str,
        period_key: str,
        category: str,
        amount: float,
        source_event_id: int,
        status: str,
        data: dict[str, Any],
        created_minute: int,
    ) -> int | None:
        cur = self.store.connection.execute(
            "INSERT OR IGNORE INTO institution_ledger_entry(institution_id,subject_id,period_key,category,amount,source_event_id,status,data_json,created_minute) VALUES (?,?,?,?,?,?,?,?,?)",
            (str(institution_id), str(subject_id), str(period_key), str(category), float(amount), int(source_event_id), str(status), json_dumps(data), int(created_minute)),
        )
        return int(cur.lastrowid) if cur.rowcount else None

    def institution_ledger_totals(self, institution_id: str, period_key: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT subject_id,category,period_key,SUM(amount) AS total,COUNT(*) AS entries FROM institution_ledger_entry WHERE institution_id=? AND status='verified'"
        args: list[Any] = [str(institution_id)]
        if period_key is not None:
            query += " AND period_key=?"
            args.append(str(period_key))
        query += " GROUP BY subject_id,category,period_key ORDER BY subject_id,category,period_key"
        return [dict(row) for row in self.store.connection.execute(query, tuple(args)).fetchall()]

