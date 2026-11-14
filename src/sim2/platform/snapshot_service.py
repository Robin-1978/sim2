from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class SnapshotService:
    """Read-only metrics, audit export and SQLite snapshot operations."""

    def __init__(self, store: Any):
        self.store = store

    def metrics(self) -> dict[str, Any]:
        active = self.store.connection.execute(
            "SELECT COUNT(*) AS n FROM agent_state "
            "WHERE json_extract(data_json,'$.initial_state.status') IN ('executing','paused')"
        ).fetchone()["n"]
        events = self.store.connection.execute("SELECT COUNT(*) AS n FROM event_log").fetchone()["n"]
        intents = self.store.connection.execute("SELECT COUNT(*) AS n FROM intent").fetchone()["n"]
        llm_calls = self.store.connection.execute("SELECT COUNT(*) AS n FROM llm_call").fetchone()["n"]
        return {
            "world_minute": int(self.store.get_meta("world_minute", 0)),
            "active_behaviors": int(active),
            "events": int(events),
            "intents": int(intents),
            "llm_calls": int(llm_calls),
            "llm_cache": self.store.repositories.llm.llm_cache_metrics(),
        }

    def export_audit(self) -> dict[str, Any]:
        def rows(query: str) -> list[dict[str, Any]]:
            result = []
            for row in self.store.connection.execute(query).fetchall():
                result.append(dict(row))
                if "data_json" in result[-1]:
                    result[-1]["data"] = json.loads(result[-1].pop("data_json"))
                if "result_json" in result[-1] and result[-1]["result_json"]:
                    result[-1]["result"] = json.loads(result[-1].pop("result_json"))
            return result

        return {
            "status": "ok",
            "world_minute": int(self.store.get_meta("world_minute", 0)),
            "events": rows("SELECT * FROM event_log ORDER BY event_id"),
            "intents": rows("SELECT * FROM intent ORDER BY created_minute,intent_id"),
            "llm_calls": rows("SELECT * FROM llm_call ORDER BY call_id"),
        }

    def snapshot(self, destination: Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.store.wait_for_checkpoint()
        backup = sqlite3.connect(destination)
        try:
            self.store.connection.backup(backup)
        finally:
            backup.close()
        return destination

    def restore(self, source: Path) -> None:
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(source)
        self.store.wait_for_checkpoint()
        backup = sqlite3.connect(source)
        try:
            backup.backup(self.store.connection)
            self.store.connection.commit()
        finally:
            backup.close()
