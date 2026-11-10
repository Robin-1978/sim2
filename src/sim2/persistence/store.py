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
  memory_tier TEXT NOT NULL DEFAULT 'long_term',
  data_json TEXT NOT NULL,
  importance REAL NOT NULL DEFAULT 0.5,
  salience REAL NOT NULL DEFAULT 0.5,
  access_count INTEGER NOT NULL DEFAULT 0,
  last_access_minute INTEGER
);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
  agent_id UNINDEXED,
  content,
  memory_id UNINDEXED
);
CREATE TABLE IF NOT EXISTS semantic_embedding (
  model_key TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  content TEXT NOT NULL,
  dimension INTEGER NOT NULL,
  vector_blob BLOB NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (model_key, content_hash)
);
CREATE INDEX IF NOT EXISTS semantic_embedding_model_idx ON semantic_embedding(model_key);
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
  world_minute INTEGER NOT NULL,
  prompt_tokens INTEGER,
  cached_tokens INTEGER,
  completion_tokens INTEGER,
  static_prefix_hash TEXT,
  static_prefix_chars INTEGER NOT NULL DEFAULT 0,
  dynamic_chars INTEGER NOT NULL DEFAULT 0
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
CREATE TABLE IF NOT EXISTS progress_state (
  owner_id TEXT NOT NULL,
  progress_key TEXT NOT NULL,
  current_value REAL NOT NULL DEFAULT 0,
  required_value REAL NOT NULL DEFAULT 1,
  data_json TEXT NOT NULL,
  updated_minute INTEGER NOT NULL,
  PRIMARY KEY (owner_id, progress_key)
);
CREATE TABLE IF NOT EXISTS dialogue_exchange (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  speaker_id TEXT NOT NULL,
  listener_id TEXT,
  topic TEXT NOT NULL,
  world_minute INTEGER NOT NULL,
  data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trade_offer (
  offer_id TEXT PRIMARY KEY,
  parent_offer_id TEXT,
  round INTEGER NOT NULL DEFAULT 1,
  offerer_id TEXT NOT NULL,
  recipient_id TEXT NOT NULL,
  give_json TEXT NOT NULL,
  want_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_minute INTEGER NOT NULL,
  resolved_minute INTEGER,
  result_json TEXT
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
CREATE TABLE IF NOT EXISTS capability_progress (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  delta REAL NOT NULL,
  source TEXT NOT NULL,
  world_minute INTEGER NOT NULL,
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
        # The current schema is used for new instances.  Keep an additive
        # compatibility check so a previously opened instance gains the
        # explicit short/long memory tier without rewriting its records.
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(memory_item)").fetchall()}
        if "memory_tier" not in columns:
            self.connection.execute("ALTER TABLE memory_item ADD COLUMN memory_tier TEXT NOT NULL DEFAULT 'long_term'")
            self.connection.execute(
                "UPDATE memory_item SET memory_tier=CASE WHEN memory_type IN ('experience','summary','belief','goal','knowledge','capability') THEN 'long_term' ELSE 'short_term' END"
            )
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

    def sync_world_property_from_resource(self, entity_id: str, amount: Any) -> dict[str, Any] | None:
        """Keep a shared resource and its existing world-property label equal."""

        if not isinstance(amount, (int, float)) or isinstance(amount, bool):
            return None
        properties = dict(self.get_meta("world_properties", {}) or {})
        key = str(entity_id)
        if key not in properties or properties.get(key) == amount:
            return None
        before = properties.get(key)
        properties[key] = amount
        self.set_meta("world_properties", properties)
        return {"world_property": key, "before": before, "after": amount}

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

    def get_related_relationships(self, entity_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT from_id,to_id,relation_type,data_json FROM relationship WHERE from_id=? OR to_id=? ORDER BY from_id,to_id LIMIT ?",
            (entity_id, entity_id, max(1, min(int(limit), 50))),
        ).fetchall()
        result = []
        for row in rows:
            other = row["to_id"] if row["from_id"] == entity_id else row["from_id"]
            relation = json.loads(row["data_json"])
            result.append({"person": other, "relation": row["relation_type"], "direction": "outgoing" if row["from_id"] == entity_id else "incoming", **relation})
        return result

    def list_relationships(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT from_id,to_id,relation_type,data_json FROM relationship ORDER BY from_id,to_id").fetchall()
        return [{"from": row["from_id"], "to": row["to_id"], "type": row["relation_type"], **json.loads(row["data_json"])} for row in rows]

    def _memory_tags(self, data: dict[str, Any]) -> dict[str, Any]:
        """Build the small, stable retrieval envelope shared by facts and memories.

        The prose remains free-form, but retrieval should not have to infer
        every useful dimension from a transcript.  These dimensions are
        deliberately declarative and optional so a world package can add
        fields without changing the schema: location, participants,
        behavior, object, topic and goal.
        """

        raw = data.get("tags")
        tags = dict(raw) if isinstance(raw, dict) else {}
        sources = {
            "location": ("location", "location_id"),
            "participants": ("participants", "target_id", "speaker_id", "listener_id", "recipient_id", "counterparty_id"),
            "behavior": ("behavior", "behavior_ref", "event_ref", "source_action"),
            "object": ("object", "object_id"),
            "topic": ("topic",),
            "goal": ("goal", "goal_tags"),
        }
        for tag, fields in sources.items():
            if tag in tags and tags[tag] not in (None, "", []):
                continue
            value = next((data.get(field) for field in fields if data.get(field) not in (None, "", [])), None)
            if value not in (None, "", []):
                tags[tag] = value
        return tags

    def put_knowledge_fact(self, agent_id: str, fact_key: str, data: dict[str, Any]) -> int:
        data = dict(data)
        tags = self._memory_tags(data)
        if tags:
            data["tags"] = tags
        cur = self.connection.execute(
            "INSERT INTO knowledge_fact(agent_id,fact_key,data_json) VALUES (?,?,?)",
            (agent_id, fact_key, json_dumps(data)),
        )
        return int(cur.lastrowid)

    def merge_knowledge_fact(self, agent_id: str, fact_key: str, data: dict[str, Any]) -> int:
        data = dict(data)
        tags = self._memory_tags(data)
        if tags:
            data["tags"] = tags
        confidence = float(data.get("confidence", 0.5))
        row = self.connection.execute("SELECT id,data_json FROM knowledge_fact WHERE agent_id=? AND fact_key=? ORDER BY id DESC LIMIT 1", (agent_id, fact_key)).fetchone()
        if row is None:
            return self.put_knowledge_fact(agent_id, fact_key, data)
        current = json.loads(row["data_json"])
        old_confidence = float(current.get("confidence", 0.5))
        merged = {**current, **data, "confidence": round((old_confidence + confidence) / 2, 4), "evidence_count": int(current.get("evidence_count", 1)) + 1}
        self.connection.execute("UPDATE knowledge_fact SET data_json=? WHERE id=?", (json_dumps(merged), row["id"]))
        return int(row["id"])

    def list_knowledge_facts(self, agent_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT id,fact_key,data_json FROM knowledge_fact WHERE agent_id=? ORDER BY id DESC LIMIT ?",
            (agent_id, max(1, min(int(limit), 1000))),
        ).fetchall()
        return [{"id": row["id"], "fact_key": row["fact_key"], **json.loads(row["data_json"])} for row in rows]

    def get_semantic_embeddings(self, model_key: str, texts: list[str]) -> dict[str, tuple[int, bytes]]:
        """Load persisted vectors for exact content in the current instance."""

        values = [str(text) for text in texts if str(text)]
        if not values:
            return {}
        hashes = [hashlib.sha256(value.encode("utf-8")).hexdigest() for value in values]
        placeholders = ",".join("?" for _ in hashes)
        rows = self.connection.execute(
            f"SELECT content,dimension,vector_blob FROM semantic_embedding WHERE model_key=? AND content_hash IN ({placeholders})",
            (str(model_key), *hashes),
        ).fetchall()
        return {str(row["content"]): (int(row["dimension"]), bytes(row["vector_blob"])) for row in rows}

    def put_semantic_embeddings(self, model_key: str, vectors: dict[str, tuple[int, bytes]]) -> None:
        """Persist candidate vectors without coupling them to a memory row."""

        if not vectors:
            return
        rows = []
        for text, (dimension, blob) in vectors.items():
            text = str(text)
            rows.append((str(model_key), hashlib.sha256(text.encode("utf-8")).hexdigest(), text, int(dimension), sqlite3.Binary(blob)))
        with self._write_lock:
            was_in_transaction = self.connection.in_transaction
            self.connection.executemany(
                "INSERT INTO semantic_embedding(model_key,content_hash,content,dimension,vector_blob) VALUES (?,?,?,?,?) "
                "ON CONFLICT(model_key,content_hash) DO UPDATE SET content=excluded.content,dimension=excluded.dimension,vector_blob=excluded.vector_blob,updated_at=CURRENT_TIMESTAMP",
                rows,
            )
            if not was_in_transaction:
                self.connection.commit()

    def semantic_embedding_count(self, model_key: str | None = None) -> int:
        if model_key:
            row = self.connection.execute("SELECT COUNT(*) AS n FROM semantic_embedding WHERE model_key=?", (str(model_key),)).fetchone()
        else:
            row = self.connection.execute("SELECT COUNT(*) AS n FROM semantic_embedding").fetchone()
        return int(row["n"] if row else 0)

    def explored_areas(self, agent_id: str) -> set[str]:
        rows = self.connection.execute(
            "SELECT DISTINCT area_id FROM exploration_result WHERE agent_id=?",
            (agent_id,),
        ).fetchall()
        return {str(row["area_id"]) for row in rows if row["area_id"]}

    def researched_sources(self, agent_id: str) -> set[str]:
        rows = self.connection.execute(
            "SELECT fact_key FROM knowledge_fact WHERE agent_id=? AND fact_key LIKE 'researched:%'",
            (agent_id,),
        ).fetchall()
        return {str(row["fact_key"]).removeprefix("researched:").split(":", 1)[0] for row in rows}

    def last_behavior_minute(self, actor_id: str, behavior_id: str) -> int | None:
        row = self.connection.execute(
            "SELECT MAX(world_minute) AS minute FROM event_log "
            "WHERE actor_id=? AND event_type='behavior.completed' "
            "AND json_extract(data_json,'$.behavior_id')=?",
            (actor_id, behavior_id),
        ).fetchone()
        return int(row["minute"]) if row and row["minute"] is not None else None

    def put_prompt(self, prompt_id: str, data: dict[str, Any]) -> None:
        self.connection.execute("INSERT OR REPLACE INTO prompt_def(id,data_json) VALUES (?,?)", (prompt_id, json_dumps(data)))

    def get_prompt(self, prompt_id: str, default: Any = None) -> Any:
        row = self.connection.execute("SELECT data_json FROM prompt_def WHERE id=?", (prompt_id,)).fetchone()
        return default if row is None else json.loads(row["data_json"])

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

    def advance_progress(self, owner_id: str, progress_key: str, amount: float, required: float, world_minute: int, data: dict[str, Any] | None = None) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT current_value,required_value,data_json FROM progress_state WHERE owner_id=? AND progress_key=?",
            (owner_id, progress_key),
        ).fetchone()
        before = float(row["current_value"]) if row else 0.0
        configured_required = float(row["required_value"]) if row else max(1.0, float(required))
        current = min(configured_required, before + float(amount))
        merged = json.loads(row["data_json"]) if row else {}
        merged.update(data or {})
        self.connection.execute(
            "INSERT INTO progress_state(owner_id,progress_key,current_value,required_value,data_json,updated_minute) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(owner_id,progress_key) DO UPDATE SET current_value=excluded.current_value,required_value=excluded.required_value,data_json=excluded.data_json,updated_minute=excluded.updated_minute",
            (owner_id, progress_key, current, configured_required, json_dumps(merged), int(world_minute)),
        )
        return {"owner_id": owner_id, "progress_key": progress_key, "before": before, "after": current, "required": configured_required, "completed": current >= configured_required, **merged}

    def get_progress(self, owner_id: str, progress_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT current_value,required_value,data_json,updated_minute FROM progress_state WHERE owner_id=? AND progress_key=?",
            (owner_id, progress_key),
        ).fetchone()
        if row is None:
            return None
        return {"owner_id": owner_id, "progress_key": progress_key, "current": row["current_value"], "required": row["required_value"], "updated_minute": row["updated_minute"], **json.loads(row["data_json"])}

    def list_progress(self, owner_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT progress_key,current_value,required_value,data_json,updated_minute FROM progress_state WHERE owner_id=? ORDER BY updated_minute DESC,progress_key LIMIT ?",
            (owner_id, max(1, min(int(limit), 200))),
        ).fetchall()
        return [{"owner_id": owner_id, "progress_key": row["progress_key"], "current": row["current_value"], "required": row["required_value"], "updated_minute": row["updated_minute"], **json.loads(row["data_json"])} for row in rows]

    def put_dialogue_exchange(self, speaker_id: str, listener_id: str | None, topic: str, world_minute: int, data: dict[str, Any]) -> int:
        cur = self.connection.execute(
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

        row = self.connection.execute(
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
        self.connection.execute(
            "INSERT INTO trade_offer(offer_id,parent_offer_id,round,offerer_id,recipient_id,give_json,want_json,status,created_minute) VALUES (?,?,?,?,?,?,?,?,?)",
            (offer_id, parent_offer_id, int(round_number), offerer_id, recipient_id, json_dumps(give), json_dumps(want), "open", int(world_minute)),
        )

    def get_trade_offer(self, offer_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
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
        self.connection.execute(
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
        ids = [row["offer_id"] for row in self.connection.execute(query, params).fetchall()]
        offers = []
        for offer_id in ids:
            offer = self.get_trade_offer(offer_id)
            if offer is not None:
                offers.append(offer)
        return offers

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
        if int(row["cadence_minutes"]) <= 0:
            self.connection.execute("UPDATE evolution_task SET status='completed' WHERE task_id=?", (task_id,))
            return
        self.connection.execute(
            "UPDATE evolution_task SET due_minute=?,status='scheduled' WHERE task_id=?",
            (int(completed_minute) + int(row["cadence_minutes"]), task_id),
        )

    def put_capability_progress(self, agent_id: str, capability: str, delta: float, source: str, world_minute: int, data: dict[str, Any] | None = None) -> int:
        cur = self.connection.execute(
            "INSERT INTO capability_progress(agent_id,capability,delta,source,world_minute,data_json) VALUES (?,?,?,?,?,?)",
            (agent_id, capability, float(delta), source, int(world_minute), json_dumps(data or {})),
        )
        return int(cur.lastrowid)

    def list_capability_progress(self, agent_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT id,capability,delta,source,world_minute,data_json FROM capability_progress WHERE agent_id=? ORDER BY id DESC LIMIT ?",
            (agent_id, max(1, min(int(limit), 100))),
        ).fetchall()
        return [{"id": row["id"], "capability": row["capability"], "delta": row["delta"], "source": row["source"], "world_minute": row["world_minute"], **json.loads(row["data_json"])} for row in rows]

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
                result.append({
                    "id": row["id"],
                    "type": row["entity_type"],
                    "entity_type": row["entity_type"],
                    "name": row["name"],
                    "summary": data.get("role", data.get("type", row["entity_type"])),
                    "initial_state": state,
                    "amount": data.get("amount"),
                })
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

    def recent_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT event_id,event_type,actor_id,world_minute,data_json FROM event_log ORDER BY event_id DESC LIMIT ?",
            (max(1, min(int(limit), 10000)),),
        ).fetchall()
        return [{"event_id": row["event_id"], "event_type": row["event_type"], "actor_id": row["actor_id"], "world_minute": row["world_minute"], **json.loads(row["data_json"])} for row in rows]

    def timeline_events(self, *, after_event_id: int = 0, limit: int = 100, include_ticks: bool = False) -> list[dict[str, Any]]:
        event_filter = "" if include_ticks else " AND event_type <> 'world_minute.advanced'"
        rows = self.connection.execute(
            "SELECT event_id,event_type,actor_id,world_minute,data_json FROM event_log "
            f"WHERE event_id>?{event_filter} ORDER BY event_id LIMIT ?",
            (max(0, int(after_event_id)), max(1, min(int(limit), 500))),
        ).fetchall()
        return [{"event_id": row["event_id"], "event_type": row["event_type"], "actor_id": row["actor_id"], "world_minute": row["world_minute"], **json.loads(row["data_json"])} for row in rows]

    def event_type_counts(self) -> dict[str, int]:
        rows = self.connection.execute("SELECT event_type,COUNT(*) AS count FROM event_log GROUP BY event_type").fetchall()
        return {str(row["event_type"]): int(row["count"]) for row in rows}

    def behavior_start_counts(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT json_extract(data_json,'$.behavior_id') AS behavior_id,COUNT(*) AS count "
            "FROM event_log WHERE event_type='behavior.started' "
            "AND json_extract(data_json,'$.behavior_id') IS NOT NULL GROUP BY behavior_id"
        ).fetchall()
        return {str(row["behavior_id"]): int(row["count"]) for row in rows}

    def recent_events_for_actor(self, actor_id: str, *, limit: int = 2) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT event_id,event_type,world_minute,data_json FROM event_log WHERE actor_id=? ORDER BY event_id DESC LIMIT ?",
            (actor_id, max(1, min(int(limit), 10))),
        ).fetchall()
        return [{"event_id": row["event_id"], "event_type": row["event_type"], "world_minute": row["world_minute"], **json.loads(row["data_json"])} for row in rows]

    def recent_outcomes_for_actor(self, actor_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """Return behavior outcomes even when later action events are numerous."""

        rows = self.connection.execute(
            "SELECT event_id,event_type,world_minute,data_json FROM event_log "
            "WHERE actor_id=? AND event_type IN ('behavior.completed','behavior.failed','behavior.interrupted') "
            "ORDER BY event_id DESC LIMIT ?",
            (actor_id, max(1, min(int(limit), 20))),
        ).fetchall()
        return [{"event_id": row["event_id"], "event_type": row["event_type"], "world_minute": row["world_minute"], **json.loads(row["data_json"])} for row in rows]

    def recent_world_change_events(self, *, limit: int = 8) -> list[dict[str, Any]]:
        """Return durable world proposal results for the God view."""

        rows = self.connection.execute(
            "SELECT event_id,event_type,actor_id,world_minute,data_json FROM event_log "
            "WHERE event_type LIKE 'world.change.%' OR event_type IN ('entity.created','world.rule.followed') "
            "ORDER BY event_id DESC LIMIT ?",
            (max(1, min(int(limit), 30)),),
        ).fetchall()
        return [{"event_id": row["event_id"], "event_type": row["event_type"], "actor_id": row["actor_id"], "world_minute": row["world_minute"], **json.loads(row["data_json"])} for row in rows]

    def recent_behavior_outcomes(self, *, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.connection.execute(
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

        end = int(self.get_meta("world_minute", 0) if current_minute is None else current_minute)
        baseline = self.get_meta("resource_baseline", {}) or {}
        resources: list[dict[str, Any]] = []
        resource_labels: dict[str, str] = {}
        for item in self.entities_of_type("resource"):
            try:
                entity = self.get_entity(str(item["id"]))
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
            rows = self.connection.execute(
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
            current_relationships = self.list_relationships()
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

        calendar = (self.get_meta("setting", {}) or {}).get("calendar", {}) if isinstance(self.get_meta("setting", {}), dict) else {}
        week_minutes = max(1, int(calendar.get("minutes_per_week", calendar.get("minutes_per_day", 1440) * 7) or 10080))
        month_minutes = max(week_minutes, int(calendar.get("minutes_per_month", calendar.get("minutes_per_day", 1440) * 30) or 43200))
        agents = self.list_agents()
        active_goals = [str(agent.get("ultimate_goal", "")).strip() for agent in agents if str(agent.get("ultimate_goal", "")).strip()]
        return {
            "as_of_minute": end,
            "current_resources": sorted(resources, key=lambda item: str(item.get("name", ""))),
            "resource_labels": resource_labels,
            "active_goal_count": len(active_goals),
            "weekly": aggregate(week_minutes),
            "monthly": aggregate(month_minutes),
        }

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

    def update_agents_bulk(self, agents: list[dict[str, Any]]) -> None:
        rows = [(json_dumps(agent), agent["id"]) for agent in agents]
        self.connection.executemany("UPDATE agent_state SET data_json=? WHERE id=?", rows)
        self.connection.executemany("UPDATE entity SET data_json=? WHERE id=?", rows)

    def put_memory(self, agent_id: str, memory_type: str, data: dict[str, Any], *, tier: str | None = None) -> int:
        memory_type = str(memory_type)
        tier = str(tier or data.get("memory_tier") or ("long_term" if memory_type in {"experience", "summary", "belief", "goal", "knowledge", "capability"} else "short_term"))
        if tier not in {"short_term", "long_term"}:
            raise ValueError("memory tier must be short_term or long_term")
        # The tier has its own indexed column; do not duplicate it inside the
        # model-facing JSON payload.
        data = {key: value for key, value in data.items() if key != "memory_tier"}
        # Keep a small fixed retrieval envelope beside free-form prose. The
        # tags are storage metadata, not additional prompt text; hybrid FTS
        # and BGE ranking can use location, participants, behavior, object,
        # topic and goal without parsing a transcript.
        tags = self._memory_tags(data)
        # IDs are useful for filtering but visible names make the envelope
        # readable in diagnostics and prompt previews.
        for tag in ("location", "participants", "object"):
            value = tags.get(tag)
            if value in (None, "", []):
                continue
            values = value if isinstance(value, list) else [value]
            visible_values = []
            for candidate in values:
                try:
                    visible_values.append(self.get_entity(str(candidate)).get("name", candidate))
                except KeyError:
                    visible_values.append(candidate)
            tags[tag] = visible_values if isinstance(value, list) else visible_values[0]
        if tags:
            data["tags"] = tags
        encoded = json_dumps(data)
        importance = max(0.0, min(1.0, float(data.get("importance", 0.5))))
        salience = max(0.0, min(1.0, float(data.get("salience", 0.5))))
        world_minute = int(self.get_meta("world_minute", 0))
        cur = self.connection.execute(
            "INSERT INTO memory_item(agent_id,memory_type,memory_tier,data_json,importance,salience,access_count,last_access_minute) VALUES (?,?,?,?,?,?,?,?)",
            (agent_id, memory_type, tier, encoded, importance, salience, 1, world_minute),
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
        *,
        usage: dict[str, Any] | None = None,
        static_prefix_hash: str | None = None,
        static_prefix_chars: int = 0,
        dynamic_chars: int = 0,
    ) -> int:
        usage = usage or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
        cached_tokens = usage.get("cached_tokens", usage.get("prompt_cache_hit_tokens", details.get("cached_tokens")))
        with self._write_lock:
            cur = self.connection.execute(
                "INSERT INTO llm_call(actor_id,role,task_type,iteration,context_hash,response_json,status,world_minute,prompt_tokens,cached_tokens,completion_tokens,static_prefix_hash,static_prefix_chars,dynamic_chars) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    actor_id,
                    role,
                    task_type,
                    int(iteration),
                    context_hash,
                    json_dumps(response),
                    status,
                    int(self.get_meta("world_minute", 0)),
                    int(prompt_tokens) if isinstance(prompt_tokens, (int, float)) else None,
                    int(cached_tokens) if isinstance(cached_tokens, (int, float)) else None,
                    int(completion_tokens) if isinstance(completion_tokens, (int, float)) else None,
                    static_prefix_hash,
                    int(static_prefix_chars),
                    int(dynamic_chars),
                ),
            )
            self.connection.commit()
        return int(cur.lastrowid)

    def llm_cache_metrics(self) -> dict[str, Any]:
        """Report provider cache evidence and prompt-prefix reuse separately."""

        row = self.connection.execute(
            "SELECT COUNT(*) AS calls, SUM(CASE WHEN cached_tokens IS NOT NULL THEN 1 ELSE 0 END) AS measured, "
            "COALESCE(SUM(prompt_tokens),0) AS prompt_tokens, COALESCE(SUM(cached_tokens),0) AS cached_tokens, "
            "COALESCE(SUM(completion_tokens),0) AS completion_tokens FROM llm_call"
        ).fetchone()
        prefixes = self.connection.execute(
            "SELECT COUNT(*) AS total, COUNT(DISTINCT static_prefix_hash) AS distinct_prefixes, "
            "COALESCE(AVG(static_prefix_chars),0) AS avg_static_chars, COALESCE(AVG(dynamic_chars),0) AS avg_dynamic_chars FROM llm_call"
        ).fetchone()
        calls = int(row["calls"] or 0)
        measured = int(row["measured"] or 0)
        prompt_tokens = int(row["prompt_tokens"] or 0)
        cached_tokens = int(row["cached_tokens"] or 0)
        return {
            "calls": calls,
            "provider_usage_calls": measured,
            "provider_cache_hit_rate": round(cached_tokens / prompt_tokens, 4) if prompt_tokens else None,
            "cached_tokens": cached_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": int(row["completion_tokens"] or 0),
            "static_prefix_distinct": int(prefixes["distinct_prefixes"] or 0),
            "static_prefix_reuse_rate": round(1 - int(prefixes["distinct_prefixes"] or 0) / int(prefixes["total"] or 1), 4) if calls else 0.0,
            "average_static_prefix_chars": round(float(prefixes["avg_static_chars"] or 0), 1),
            "average_dynamic_chars": round(float(prefixes["avg_dynamic_chars"] or 0), 1),
        }

    def list_memories(self, agent_id: str, *, limit: int = 8, query: str = "", tier: str | None = None, touch: bool = False) -> list[dict[str, Any]]:
        """Return the agent's newest memories, optionally matching plain text.

        Memory retrieval is intentionally small and role-scoped.  The full
        event log and other agents' memories never enter an AgentView.
        """

        limit = max(1, min(int(limit), 1000))
        tier_clause = " AND memory_tier=?" if tier in {"short_term", "long_term"} else ""
        tier_args: tuple[Any, ...] = (tier,) if tier in {"short_term", "long_term"} else ()
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
                        f"SELECT id,memory_type,memory_tier,data_json,importance,salience,access_count,last_access_minute FROM memory_item WHERE agent_id=?{tier_clause} AND id IN ({placeholders}) ORDER BY (importance + salience + MIN(access_count, 100) * 0.01) DESC, id DESC LIMIT ?",
                        (agent_id, *tier_args, *ids, limit),
                    ).fetchall()
                else:
                    rows = []
            except sqlite3.OperationalError:
                rows = self.connection.execute(
                    f"SELECT id,memory_type,memory_tier,data_json,importance,salience,access_count,last_access_minute FROM memory_item WHERE agent_id=?{tier_clause} AND data_json LIKE ? ORDER BY (importance + salience + MIN(access_count, 100) * 0.01) DESC, id DESC LIMIT ?",
                    (agent_id, *tier_args, f"%{query}%", limit),
                ).fetchall()
        else:
            rows = self.connection.execute(
                f"SELECT id,memory_type,memory_tier,data_json,importance,salience,access_count,last_access_minute FROM memory_item WHERE agent_id=?{tier_clause} ORDER BY (importance + salience + MIN(access_count, 100) * 0.01) DESC, id DESC LIMIT ?",
                (agent_id, *tier_args, limit),
            ).fetchall()
        result = []
        for row in rows:
            value = json.loads(row["data_json"])
            result.append({"id": row["id"], "type": row["memory_type"], "tier": row["memory_tier"], "importance": row["importance"], "salience": row["salience"], "uses": row["access_count"], "last_access_minute": row["last_access_minute"], **value})
        if result and touch:
            was_in_transaction = self.connection.in_transaction
            self.connection.executemany(
                "UPDATE memory_item SET access_count=access_count+1,last_access_minute=? WHERE id=?",
                [(int(self.get_meta("world_minute", 0)), int(item["id"])) for item in result],
            )
            if not was_in_transaction:
                self.connection.commit()
        return result

    def delete_memories(self, agent_id: str, memory_ids: list[int]) -> int:
        if not memory_ids:
            return 0
        placeholders = ",".join("?" for _ in memory_ids)
        self.connection.execute(f"DELETE FROM memory_fts WHERE agent_id=? AND memory_id IN ({','.join('?' for _ in memory_ids)})", (agent_id, *(str(item) for item in memory_ids)))
        cur = self.connection.execute(f"DELETE FROM memory_item WHERE agent_id=? AND id IN ({placeholders})", (agent_id, *memory_ids))
        return int(cur.rowcount)

    def touch_memories(self, memory_ids: list[int]) -> None:
        """Record retrieval for exactly the rows exposed to an Agent."""

        ids = [int(item) for item in memory_ids if item]
        if not ids:
            return
        was_in_transaction = self.connection.in_transaction
        self.connection.executemany(
            "UPDATE memory_item SET access_count=access_count+1,last_access_minute=? WHERE id=?",
            [(int(self.get_meta("world_minute", 0)), item) for item in ids],
        )
        if not was_in_transaction:
            self.connection.commit()


def config_hash(config_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(config_dir).rglob("*.yaml")):
        digest.update(path.relative_to(config_dir).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
