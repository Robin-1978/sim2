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
CREATE TABLE IF NOT EXISTS world_proposal (
  proposal_id TEXT PRIMARY KEY,
  intent_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  change_type TEXT NOT NULL,
  status TEXT NOT NULL,
  created_minute INTEGER NOT NULL,
  evaluation_due_minute INTEGER NOT NULL,
  expected_json TEXT NOT NULL,
  observed_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  updated_minute INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS world_proposal_status_idx ON world_proposal(status,evaluation_due_minute);
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
  dynamic_chars INTEGER NOT NULL DEFAULT 0,
  request_latency_ms REAL
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
CREATE TABLE IF NOT EXISTS knowledge_gap (
  gap_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  topic_key TEXT NOT NULL,
  question TEXT NOT NULL,
  current_source_id TEXT,
  created_minute INTEGER NOT NULL,
  updated_minute INTEGER NOT NULL,
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
CREATE TABLE IF NOT EXISTS institution_definition (
  institution_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  data_json TEXT NOT NULL,
  created_minute INTEGER NOT NULL,
  activated_minute INTEGER
);
CREATE TABLE IF NOT EXISTS institution_ledger_entry (
  entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
  institution_id TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  period_key TEXT NOT NULL,
  category TEXT NOT NULL,
  amount REAL NOT NULL,
  source_event_id INTEGER NOT NULL,
  status TEXT NOT NULL,
  data_json TEXT NOT NULL,
  created_minute INTEGER NOT NULL,
  UNIQUE(institution_id, subject_id, category, source_event_id)
);
CREATE INDEX IF NOT EXISTS institution_ledger_lookup
  ON institution_ledger_entry(institution_id, period_key, subject_id, category);
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
        # WAL + NORMAL preserves atomic database consistency across process or
        # OS failure while avoiding a full device sync for every simulated
        # minute. A host power loss may drop the most recent WAL commit, while
        # the database remains consistent; snapshots provide explicit recovery
        # points for long-running worlds.
        self.connection.execute("PRAGMA synchronous = NORMAL")
        # Large worlds rewrite many compact state rows per minute. SQLite's
        # default 1,000-page auto-checkpoint can therefore run on almost every
        # tick. Keep commits short and checkpoint at a bounded store cadence.
        self.connection.execute("PRAGMA wal_autocheckpoint = 0")
        self._commits_since_checkpoint = 0
        self._checkpoint_interval = 64
        self._checkpoint_thread: threading.Thread | None = None
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
        llm_columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(llm_call)").fetchall()}
        if "request_latency_ms" not in llm_columns:
            self.connection.execute("ALTER TABLE llm_call ADD COLUMN request_latency_ms REAL")
        self.connection.commit()
        from .repositories.entities import EntitiesRepository
        from .repositories.memory import MemoryRepository
        from .repositories.activity import ActivityRepository
        from .repositories.social import SocialRepository
        from .repositories.events import EventsRepository
        from .repositories.evolution import EvolutionRepository
        from .repositories.llm import LlmRepository
        self._repositories = [
            EntitiesRepository(self),
            MemoryRepository(self),
            ActivityRepository(self),
            SocialRepository(self),
            EventsRepository(self),
            EvolutionRepository(self),
            LlmRepository(self),
        ]

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            # Keep one revision for one logical commit.  A nested caller is
            # already inside the same commit and must not advance the world a
            # second time.  The revision is reserved before any event is
            # written so every event in the transaction carries the revision
            # that will become visible after commit.
            if self.connection.in_transaction:
                yield self.connection
                return
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                next_revision = int(self.get_meta("world_revision", 0) or 0) + 1
                self.set_meta("world_revision", next_revision)
                yield self.connection
                self.connection.commit()
                self._commits_since_checkpoint += 1
                if self._commits_since_checkpoint >= self._checkpoint_interval:
                    if self._checkpoint_thread is None or not self._checkpoint_thread.is_alive():
                        self._checkpoint_thread = threading.Thread(
                            target=self._checkpoint_passive,
                            name=f"sim2-checkpoint-{self.db_path.stem}",
                            daemon=True,
                        )
                        self._checkpoint_thread.start()
                        self._commits_since_checkpoint = 0
            except Exception:
                self.connection.rollback()
                raise

    def close(self) -> None:
        self.wait_for_checkpoint()
        if not self.connection.in_transaction:
            self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.connection.close()

    def wait_for_checkpoint(self) -> None:
        """Join the optional maintenance checkpoint before backup or replace."""

        if self._checkpoint_thread is not None:
            self._checkpoint_thread.join()

    def _checkpoint_passive(self) -> None:
        """Copy committed WAL pages without blocking the simulation writer."""

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.Error:
            # A later cadence or the final TRUNCATE checkpoint retries it. The
            # committed WAL remains the source of truth in the meantime.
            pass
        finally:
            connection.close()

    def set_meta(self, key: str, value: Any) -> None:
        self.connection.execute(
            "INSERT INTO instance_meta(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json_dumps(value)),
        )

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.connection.execute("SELECT value FROM instance_meta WHERE key=?", (key,)).fetchone()
        return default if row is None else json.loads(row["value"])

    def world_snapshot(self, *, include_agent_revisions: bool = True) -> dict[str, Any]:
        """Return the read-only boundary used by a decision or tick.

        The snapshot is deliberately a projection rather than a copy of every
        table.  LLM work may keep this value while SQLite continues to run;
        commit code compares the revision again before accepting a write.
        """

        snapshot = {
            "world_id": self.get_meta("world_id", self.get_meta("instance_id", "")),
            "world_minute": int(self.get_meta("world_minute", 0) or 0),
            "world_revision": int(self.get_meta("world_revision", 0) or 0),
        }
        if include_agent_revisions:
            snapshot["agent_revisions"] = {
                str(agent["id"]): int((agent.get("agent_revision", 0) or 0))
                for agent in self.list_agents()
            }
        return snapshot

    def __getattr__(self, name: str) -> Any:
        """Resolve domain persistence methods through explicit repositories."""

        repositories = self.__dict__.get("_repositories", ())
        for repository in repositories:
            try:
                return object.__getattribute__(repository, name)
            except AttributeError:
                continue
        raise AttributeError(name)

    def __dir__(self) -> list[str]:
        names = set(object.__dir__(self))
        for repository in self.__dict__.get("_repositories", ()):
            names.update(name for name in dir(repository) if not name.startswith("__"))
        return sorted(names)



def config_hash(config_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(config_dir).rglob("*.yaml")):
        digest.update(path.relative_to(config_dir).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
