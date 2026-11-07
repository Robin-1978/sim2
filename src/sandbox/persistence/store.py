from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS instance_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entity (
  id TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL,
  name TEXT NOT NULL,
  data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_state (
  id TEXT PRIMARY KEY REFERENCES entity(id),
  data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS action_def (
  id TEXT PRIMARY KEY,
  data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bt_def (
  id TEXT PRIMARY KEY,
  data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recipe_def (
  id TEXT PRIMARY KEY,
  data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS capability_def (
  id TEXT PRIMARY KEY,
  data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intent (
  intent_id TEXT PRIMARY KEY,
  actor_id TEXT NOT NULL,
  intent_type TEXT NOT NULL,
  status TEXT NOT NULL,
  data_json TEXT NOT NULL,
  created_minute INTEGER NOT NULL,
  result_json TEXT
);
CREATE TABLE IF NOT EXISTS event_log (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  actor_id TEXT,
  world_minute INTEGER NOT NULL,
  data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_item (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT NOT NULL,
  memory_type TEXT NOT NULL,
  data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_call (
  call_id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_id TEXT NOT NULL,
  role TEXT NOT NULL,
  task_type TEXT NOT NULL,
  iteration INTEGER NOT NULL,
  context_hash TEXT NOT NULL,
  response_json TEXT NOT NULL,
  status TEXT NOT NULL,
  world_minute INTEGER NOT NULL
);
"""


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class WorldStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def close(self) -> None:
        self.connection.close()

    def set_meta(self, key: str, value: Any) -> None:
        self.connection.execute(
            "INSERT INTO instance_meta(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json_dumps(value)),
        )

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.connection.execute("SELECT value FROM instance_meta WHERE key=?", (key,)).fetchone()
        return default if row is None else json.loads(row["value"])

    def put_entity(self, entity_id: str, entity_type: str, name: str, data: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO entity(id,entity_type,name,data_json) VALUES (?,?,?,?)",
            (entity_id, entity_type, name, json_dumps(data)),
        )

    def put_agent(self, agent_id: str, data: dict[str, Any], name: str) -> None:
        self.put_entity(agent_id, "npc", name, data)
        self.connection.execute(
            "INSERT OR REPLACE INTO agent_state(id,data_json) VALUES (?,?)",
            (agent_id, json_dumps(data)),
        )

    def put_definition(self, table: str, definition_id: str, data: dict[str, Any]) -> None:
        if table not in {"action_def", "bt_def", "recipe_def", "capability_def"}:
            raise ValueError(f"unsupported definition table: {table}")
        self.connection.execute(
            f"INSERT OR REPLACE INTO {table}(id,data_json) VALUES (?,?)",
            (definition_id, json_dumps(data)),
        )

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT data_json FROM agent_state WHERE id=?", (agent_id,)).fetchone()
        if row is None:
            raise KeyError(f"agent not found: {agent_id}")
        return json.loads(row["data_json"])

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT entity_type,name,data_json FROM entity WHERE id=?", (entity_id,)).fetchone()
        if row is None:
            raise KeyError(f"entity not found: {entity_id}")
        data = json.loads(row["data_json"])
        data.update({"id": entity_id, "entity_type": row["entity_type"], "name": row["name"]})
        return data

    def entities_at_location(self, location_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT id,entity_type,name,data_json FROM entity").fetchall()
        result = []
        for row in rows:
            data = json.loads(row["data_json"])
            state = data.get("initial_state", {})
            if state.get("location_id") == location_id:
                result.append({"id": row["id"], "type": row["entity_type"], "name": row["name"], "summary": data.get("role", data.get("type", row["entity_type"]))})
        return result

    def entities_of_type(self, entity_type: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT id,entity_type,name,data_json FROM entity WHERE entity_type=? ORDER BY id",
            (entity_type,),
        ).fetchall()
        result = []
        for row in rows:
            data = json.loads(row["data_json"])
            result.append({"id": row["id"], "entity_type": row["entity_type"], "name": row["name"], "summary": data.get("description", data.get("role", entity_type))})
        return result

    def get_definitions(self, table: str) -> list[dict[str, Any]]:
        if table not in {"action_def", "bt_def", "recipe_def", "capability_def"}:
            raise ValueError(f"unsupported definition table: {table}")
        rows = self.connection.execute(f"SELECT data_json FROM {table} ORDER BY id").fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def append_event(self, event_type: str, actor_id: str | None, world_minute: int, data: dict[str, Any]) -> int:
        cur = self.connection.execute(
            "INSERT INTO event_log(event_type,actor_id,world_minute,data_json) VALUES (?,?,?,?)",
            (event_type, actor_id, world_minute, json_dumps(data)),
        )
        return int(cur.lastrowid)

    def create_intent(self, intent_id: str, actor_id: str, intent_type: str, data: dict[str, Any], world_minute: int) -> None:
        self.connection.execute(
            "INSERT INTO intent(intent_id,actor_id,intent_type,status,data_json,created_minute) VALUES (?,?,?,?,?,?)",
            (intent_id, actor_id, intent_type, "received", json_dumps(data), world_minute),
        )

    def finish_intent(self, intent_id: str, status: str, result: dict[str, Any]) -> None:
        self.connection.execute(
            "UPDATE intent SET status=?, result_json=? WHERE intent_id=?",
            (status, json_dumps(result), intent_id),
        )

    def get_intent(self, intent_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
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

    def update_agent(self, agent_id: str, data: dict[str, Any]) -> None:
        self.connection.execute("UPDATE agent_state SET data_json=? WHERE id=?", (json_dumps(data), agent_id))
        self.connection.execute("UPDATE entity SET data_json=? WHERE id=?", (json_dumps(data), agent_id))

    def put_memory(self, agent_id: str, memory_type: str, data: dict[str, Any]) -> int:
        cur = self.connection.execute(
            "INSERT INTO memory_item(agent_id,memory_type,data_json) VALUES (?,?,?)",
            (agent_id, memory_type, json_dumps(data)),
        )
        return int(cur.lastrowid)

    def append_llm_call(
        self,
        actor_id: str,
        role: str,
        task_type: str,
        iteration: int,
        context_hash: str,
        response: dict[str, Any],
        status: str,
    ) -> int:
        cur = self.connection.execute(
            "INSERT INTO llm_call(actor_id,role,task_type,iteration,context_hash,response_json,status,world_minute) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                actor_id,
                role,
                task_type,
                int(iteration),
                context_hash,
                json_dumps(response),
                status,
                int(self.get_meta("world_minute", 0)),
            ),
        )
        self.connection.commit()
        return int(cur.lastrowid)

    def list_memories(self, agent_id: str, *, limit: int = 8, query: str = "") -> list[dict[str, Any]]:
        """Return the agent's newest memories, optionally matching plain text.

        Memory retrieval is intentionally small and role-scoped.  The full
        event log and other agents' memories never enter an AgentView.
        """

        limit = max(1, min(int(limit), 50))
        rows = self.connection.execute(
            "SELECT id,memory_type,data_json FROM memory_item "
            "WHERE agent_id=? AND (?='' OR data_json LIKE ?) "
            "ORDER BY id DESC LIMIT ?",
            (agent_id, query, f"%{query}%", limit),
        ).fetchall()
        result = []
        for row in rows:
            value = json.loads(row["data_json"])
            result.append({"id": row["id"], "type": row["memory_type"], **value})
        return result


def config_hash(config_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(config_dir).rglob("*.yaml")):
        digest.update(path.relative_to(config_dir).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
