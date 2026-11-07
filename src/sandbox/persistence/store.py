from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
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
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
  agent_id UNINDEXED,
  content,
  memory_id UNINDEXED
);
CREATE TABLE IF NOT EXISTS knowledge_fact (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT NOT NULL,
  fact_key TEXT NOT NULL,
  data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS relationship (
  from_id TEXT NOT NULL,
  to_id TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  data_json TEXT NOT NULL,
  PRIMARY KEY (from_id, to_id, relation_type)
);
CREATE TABLE IF NOT EXISTS prompt_def (
  id TEXT PRIMARY KEY,
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
CREATE TABLE IF NOT EXISTS bt_instance (
  execution_id TEXT PRIMARY KEY,
  actor_id TEXT NOT NULL,
  behavior_id TEXT NOT NULL,
  status TEXT NOT NULL,
  current_node INTEGER NOT NULL,
  data_json TEXT NOT NULL,
  started_minute INTEGER NOT NULL,
  updated_minute INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS action_instance (
  action_instance_id TEXT PRIMARY KEY,
  execution_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  action_id TEXT NOT NULL,
  status TEXT NOT NULL,
  elapsed_minutes INTEGER NOT NULL,
  remaining_minutes INTEGER NOT NULL,
  data_json TEXT NOT NULL,
  started_minute INTEGER NOT NULL,
  updated_minute INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS exploration_result (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT NOT NULL,
  area_id TEXT NOT NULL,
  world_minute INTEGER NOT NULL,
  data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dialogue_exchange (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  speaker_id TEXT NOT NULL,
  listener_id TEXT,
  topic TEXT NOT NULL,
  world_minute INTEGER NOT NULL,
  data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evolution_task (
  task_id TEXT PRIMARY KEY,
  role TEXT NOT NULL,
  actor_id TEXT,
  task_type TEXT NOT NULL,
  due_minute INTEGER NOT NULL,
  cadence_minutes INTEGER NOT NULL,
  status TEXT NOT NULL,
  data_json TEXT NOT NULL
);
"""


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class WorldStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # The control adapter may dispatch read requests on worker threads;
        # Platform still serializes writes through ``transaction``.
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._write_lock = threading.RLock()
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
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
        self.put_entity(agent_id, "agent", name, data)
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

    def put_relationship(self, from_id: str, to_id: str, relation_type: str, data: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO relationship(from_id,to_id,relation_type,data_json) VALUES (?,?,?,?)",
            (from_id, to_id, relation_type, json_dumps(data)),
        )

    def get_relationships(self, from_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT to_id,relation_type,data_json FROM relationship WHERE from_id=? ORDER BY to_id LIMIT ?",
            (from_id, max(1, min(int(limit), 50))),
        ).fetchall()
        return [{"person": row["to_id"], "relation": row["relation_type"], **json.loads(row["data_json"])} for row in rows]

    def list_relationships(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT from_id,to_id,relation_type,data_json FROM relationship ORDER BY from_id,to_id").fetchall()
        return [{"from": row["from_id"], "to": row["to_id"], "type": row["relation_type"], **json.loads(row["data_json"])} for row in rows]

    def put_knowledge_fact(self, agent_id: str, fact_key: str, data: dict[str, Any]) -> int:
        cur = self.connection.execute(
            "INSERT INTO knowledge_fact(agent_id,fact_key,data_json) VALUES (?,?,?)",
            (agent_id, fact_key, json_dumps(data)),
        )
        return int(cur.lastrowid)

    def list_knowledge_facts(self, agent_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT id,fact_key,data_json FROM knowledge_fact WHERE agent_id=? ORDER BY id DESC LIMIT ?",
            (agent_id, max(1, min(int(limit), 50))),
        ).fetchall()
        return [{"id": row["id"], "fact_key": row["fact_key"], **json.loads(row["data_json"])} for row in rows]

    def put_prompt(self, prompt_id: str, data: dict[str, Any]) -> None:
        self.connection.execute("INSERT OR REPLACE INTO prompt_def(id,data_json) VALUES (?,?)", (prompt_id, json_dumps(data)))

    def put_bt_instance(self, execution_id: str, actor_id: str, behavior_id: str, status: str, current_node: int, data: dict[str, Any], started_minute: int, updated_minute: int) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO bt_instance(execution_id,actor_id,behavior_id,status,current_node,data_json,started_minute,updated_minute) VALUES (?,?,?,?,?,?,?,?)",
            (execution_id, actor_id, behavior_id, status, int(current_node), json_dumps(data), int(started_minute), int(updated_minute)),
        )

    def update_bt_instance(self, execution_id: str, *, status: str, current_node: int, data: dict[str, Any], updated_minute: int) -> None:
        self.connection.execute(
            "UPDATE bt_instance SET status=?,current_node=?,data_json=?,updated_minute=? WHERE execution_id=?",
            (status, int(current_node), json_dumps(data), int(updated_minute), execution_id),
        )

    def put_action_instance(self, action_instance_id: str, execution_id: str, actor_id: str, action_id: str, status: str, elapsed_minutes: int, remaining_minutes: int, data: dict[str, Any], started_minute: int, updated_minute: int) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO action_instance(action_instance_id,execution_id,actor_id,action_id,status,elapsed_minutes,remaining_minutes,data_json,started_minute,updated_minute) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (action_instance_id, execution_id, actor_id, action_id, status, int(elapsed_minutes), int(remaining_minutes), json_dumps(data), int(started_minute), int(updated_minute)),
        )

    def update_action_instance(self, action_instance_id: str, *, status: str, elapsed_minutes: int, remaining_minutes: int, data: dict[str, Any], updated_minute: int) -> None:
        self.connection.execute(
            "UPDATE action_instance SET status=?,elapsed_minutes=?,remaining_minutes=?,data_json=?,updated_minute=? WHERE action_instance_id=?",
            (status, int(elapsed_minutes), int(remaining_minutes), json_dumps(data), int(updated_minute), action_instance_id),
        )

    def put_exploration_result(self, agent_id: str, area_id: str, world_minute: int, data: dict[str, Any]) -> int:
        cur = self.connection.execute(
            "INSERT INTO exploration_result(agent_id,area_id,world_minute,data_json) VALUES (?,?,?,?)",
            (agent_id, area_id, int(world_minute), json_dumps(data)),
        )
        return int(cur.lastrowid)

    def put_dialogue_exchange(self, speaker_id: str, listener_id: str | None, topic: str, world_minute: int, data: dict[str, Any]) -> int:
        cur = self.connection.execute(
            "INSERT INTO dialogue_exchange(speaker_id,listener_id,topic,world_minute,data_json) VALUES (?,?,?,?,?)",
            (speaker_id, listener_id, topic, int(world_minute), json_dumps(data)),
        )
        return int(cur.lastrowid)

    def put_evolution_task(self, task_id: str, role: str, actor_id: str | None, task_type: str, due_minute: int, cadence_minutes: int, status: str = "scheduled", data: dict[str, Any] | None = None) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO evolution_task(task_id,role,actor_id,task_type,due_minute,cadence_minutes,status,data_json) VALUES (?,?,?,?,?,?,?,?)",
            (task_id, role, actor_id, task_type, int(due_minute), int(cadence_minutes), status, json_dumps(data or {})),
        )

    def due_evolution_tasks(self, world_minute: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT task_id,role,actor_id,task_type,due_minute,cadence_minutes,status,data_json FROM evolution_task WHERE due_minute<=? AND status='scheduled' ORDER BY due_minute,task_id",
            (int(world_minute),),
        ).fetchall()
        return [{"task_id": row["task_id"], "role": row["role"], "actor_id": row["actor_id"], "task_type": row["task_type"], "due_minute": row["due_minute"], "cadence_minutes": row["cadence_minutes"], "status": row["status"], **json.loads(row["data_json"])} for row in rows]

    def ready_evolution_tasks(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT task_id,role,actor_id,task_type,due_minute,cadence_minutes,status,data_json FROM evolution_task WHERE status='ready' ORDER BY due_minute,task_id",
        ).fetchall()
        return [{"task_id": row["task_id"], "role": row["role"], "actor_id": row["actor_id"], "task_type": row["task_type"], "due_minute": row["due_minute"], "cadence_minutes": row["cadence_minutes"], "status": row["status"], **json.loads(row["data_json"])} for row in rows]

    def mark_evolution_task_ready(self, task_id: str) -> None:
        self.connection.execute("UPDATE evolution_task SET status='ready' WHERE task_id=? AND status='scheduled'", (task_id,))

    def claim_evolution_task(self, task_id: str, world_minute: int) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT task_id,role,actor_id,task_type,due_minute,cadence_minutes,status,data_json FROM evolution_task WHERE task_id=?", (task_id,)).fetchone()
        if row is None or row["status"] not in {"scheduled", "ready"} or row["due_minute"] > world_minute:
            return None
        self.connection.execute("UPDATE evolution_task SET status='claimed' WHERE task_id=?", (task_id,))
        return {"task_id": row["task_id"], "role": row["role"], "actor_id": row["actor_id"], "task_type": row["task_type"], "due_minute": row["due_minute"], "cadence_minutes": row["cadence_minutes"], **json.loads(row["data_json"])}

    def reschedule_evolution_task(self, task_id: str, due_minute: int) -> None:
        self.connection.execute("UPDATE evolution_task SET due_minute=?,status='scheduled' WHERE task_id=?", (int(due_minute), task_id))

    def complete_evolution_task(self, task_id: str, completed_minute: int) -> None:
        row = self.connection.execute("SELECT cadence_minutes FROM evolution_task WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return
        self.connection.execute(
            "UPDATE evolution_task SET due_minute=?,status='scheduled' WHERE task_id=?",
            (int(completed_minute) + int(row["cadence_minutes"]), task_id),
        )

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT data_json FROM agent_state WHERE id=?", (agent_id,)).fetchone()
        if row is None:
            raise KeyError(f"agent not found: {agent_id}")
        return json.loads(row["data_json"])

    def list_agents(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT id,data_json FROM agent_state ORDER BY id").fetchall()
        return [{"id": row["id"], **json.loads(row["data_json"])} for row in rows]

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
        encoded = json_dumps(data)
        cur = self.connection.execute(
            "INSERT INTO memory_item(agent_id,memory_type,data_json) VALUES (?,?,?)",
            (agent_id, memory_type, encoded),
        )
        memory_id = int(cur.lastrowid)
        self.connection.execute("INSERT INTO memory_fts(agent_id,content,memory_id) VALUES (?,?,?)", (agent_id, encoded, str(memory_id)))
        return memory_id

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
        with self._write_lock:
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
        if query.strip():
            try:
                fts_rows = self.connection.execute(
                    "SELECT memory_id FROM memory_fts WHERE agent_id=? AND memory_fts MATCH ? LIMIT ?",
                    (agent_id, query.replace('"', ' '), limit),
                ).fetchall()
                ids = [int(row["memory_id"]) for row in fts_rows]
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    rows = self.connection.execute(
                        f"SELECT id,memory_type,data_json FROM memory_item WHERE agent_id=? AND id IN ({placeholders}) ORDER BY id DESC LIMIT ?",
                        (agent_id, *ids, limit),
                    ).fetchall()
                else:
                    rows = []
            except sqlite3.OperationalError:
                rows = self.connection.execute(
                    "SELECT id,memory_type,data_json FROM memory_item WHERE agent_id=? AND data_json LIKE ? ORDER BY id DESC LIMIT ?",
                    (agent_id, f"%{query}%", limit),
                ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT id,memory_type,data_json FROM memory_item WHERE agent_id=? ORDER BY id DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
        result = []
        for row in rows:
            value = json.loads(row["data_json"])
            result.append({"id": row["id"], "type": row["memory_type"], **value})
        return result

    def delete_memories(self, agent_id: str, memory_ids: list[int]) -> int:
        if not memory_ids:
            return 0
        placeholders = ",".join("?" for _ in memory_ids)
        self.connection.execute(f"DELETE FROM memory_fts WHERE agent_id=? AND memory_id IN ({','.join('?' for _ in memory_ids)})", (agent_id, *(str(item) for item in memory_ids)))
        cur = self.connection.execute(f"DELETE FROM memory_item WHERE agent_id=? AND id IN ({placeholders})", (agent_id, *memory_ids))
        return int(cur.rowcount)


def config_hash(config_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(config_dir).rglob("*.yaml")):
        digest.update(path.relative_to(config_dir).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
