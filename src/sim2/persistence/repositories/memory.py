from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..store import json_dumps


class MemoryRepository:
    """Persistence owner for the configured memory domain."""

    def __init__(self, store: Any) -> None:
        self.store = store

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
        cur = self.store.connection.execute(
            "INSERT INTO knowledge_fact(agent_id,fact_key,data_json) VALUES (?,?,?)",
            (agent_id, fact_key, json_dumps(data)),
        )
        return int(cur.lastrowid)

    def merge_knowledge_fact(self, agent_id: str, fact_key: str, data: dict[str, Any]) -> int:
        data = dict(data)
        tags = self._memory_tags(data)
        if tags:
            data["tags"] = tags
        # A later verified result may revise an earlier claim.  Keep both
        # records for audit/replay, but mark the older claim so retrieval and
        # Agent prompts can distinguish the current result from history.
        supersedes = data.get("supersedes", [])
        if isinstance(supersedes, str):
            supersedes = [supersedes]
        supersedes = {str(item) for item in (supersedes or []) if str(item).strip()}
        if supersedes:
            rows = self.store.connection.execute(
                "SELECT id,fact_key,data_json FROM knowledge_fact WHERE agent_id=?",
                (agent_id,),
            ).fetchall()
            for old_row in rows:
                if str(old_row["fact_key"]) not in supersedes:
                    continue
                try:
                    old_data = json.loads(old_row["data_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    old_data = {}
                if str(old_data.get("status") or "active") in {"superseded", "obsolete"}:
                    continue
                old_data.update({"status": "superseded", "superseded_by": str(fact_key)})
                self.store.connection.execute(
                    "UPDATE knowledge_fact SET data_json=? WHERE id=?",
                    (json_dumps(old_data), int(old_row["id"])),
                )
        confidence = float(data.get("confidence", 0.5))
        row = self.store.connection.execute("SELECT id,data_json FROM knowledge_fact WHERE agent_id=? AND fact_key=? ORDER BY id DESC LIMIT 1", (agent_id, fact_key)).fetchone()
        if row is None:
            return self.put_knowledge_fact(agent_id, fact_key, data)
        current = json.loads(row["data_json"])
        old_confidence = float(current.get("confidence", 0.5))
        merged = {**current, **data, "confidence": round((old_confidence + confidence) / 2, 4), "evidence_count": int(current.get("evidence_count", 1)) + 1}
        self.store.connection.execute("UPDATE knowledge_fact SET data_json=? WHERE id=?", (json_dumps(merged), row["id"]))
        return int(row["id"])

    def research_discoveries(self, source_id: str) -> list[dict[str, Any]]:
        """Return the declarative research stages exposed by one source.

        A source may publish one legacy ``research_discovery`` object or an
        open-ended ``research_discoveries`` list.  The latter is intentionally
        stored as ordinary entity data so God can add a new source or a new
        stage through the normal declarative entity proposal path.
        """

        try:
            source = self.store.repositories.entities.get_entity(str(source_id))
        except KeyError:
            return []
        state = source.get("initial_state") if isinstance(source.get("initial_state"), dict) else {}
        raw = source.get("research_discoveries")
        if raw is None:
            raw = state.get("research_discoveries")
        if raw is None:
            raw = source.get("research_discovery")
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list):
            return []
        discoveries: list[dict[str, Any]] = []
        for index, item in enumerate(raw):
            if isinstance(item, str):
                item = {"fact": item}
            if not isinstance(item, dict):
                continue
            value = dict(item)
            value["id"] = str(
                value.get("id")
                or value.get("fact_key")
                or f"researched:{source_id}:stage_{index + 1}"
            )
            value.setdefault("status", "active")
            value.setdefault("source_id", str(source_id))
            discoveries.append(value)
        return discoveries

    def research_source_catalog(self) -> list[dict[str, Any]]:
        """Return every configured entity that exposes research stages.

        The catalog is derived from entity data rather than a world-specific
        registry.  A later Story/God entity proposal therefore becomes
        researchable through the existing generic research behavior without
        registering another BT.
        """

        rows = self.store.connection.execute("SELECT id,entity_type,name,data_json FROM entity ORDER BY id").fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                data = json.loads(row["data_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            state = data.get("initial_state") if isinstance(data.get("initial_state"), dict) else {}
            raw = data.get("research_discoveries")
            if raw is None:
                raw = state.get("research_discoveries")
            if raw is None:
                raw = data.get("research_discovery")
            if raw is None:
                continue
            result.append({
                "id": str(row["id"]),
                "entity_type": str(row["entity_type"]),
                "name": str(row["name"]),
                "location_id": state.get("location_id") or data.get("location_id"),
                "research_required": data.get("research_required", state.get("research_required", state.get("required_research"))),
                "discoveries": self.research_discoveries(str(row["id"])),
            })
        return result

    def research_source_facts(self) -> list[dict[str, Any]]:
        """Return one compact manifest row for each persisted research source.

        The manifest lets Platform reject a new source that merely rephrases a
        completed one. It contains only stored source metadata and the latest
        verified discovery, not model reasoning or hidden prompts.
        """

        result: list[dict[str, Any]] = []
        for source in self.research_source_catalog():
            progress_prefix = f"research:{source['id']}"
            progress = self.store.connection.execute(
                "SELECT progress_key,current_value,required_value,data_json,updated_minute FROM progress_state "
                "WHERE owner_id=? AND (progress_key=? OR progress_key LIKE ?) ORDER BY progress_key",
                (str(self.store.get_meta("world_id", "world")), progress_prefix, progress_prefix + ":%"),
            ).fetchone()
            completed = bool(progress and float(progress["current_value"] or 0) >= float(progress["required_value"] or 1))
            try:
                entity = self.store.repositories.entities.get_entity(str(source["id"]))
            except KeyError:
                entity = {}
            state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
            discovery = next((item.get("fact") or item.get("question") for item in source.get("discoveries", []) if isinstance(item, dict) and (item.get("fact") or item.get("question"))), "")
            result.append({
                "id": source["id"],
                "name": source["name"],
                "focus": state.get("research_focus") or discovery,
                "open_question": discovery,
                "completed": completed,
            })
        return result

    def list_knowledge_facts(self, agent_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
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
        rows = self.store.connection.execute(
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
        with self.store._write_lock:
            was_in_transaction = self.store.connection.in_transaction
            self.store.connection.executemany(
                "INSERT INTO semantic_embedding(model_key,content_hash,content,dimension,vector_blob) VALUES (?,?,?,?,?) "
                "ON CONFLICT(model_key,content_hash) DO UPDATE SET content=excluded.content,dimension=excluded.dimension,vector_blob=excluded.vector_blob,updated_at=CURRENT_TIMESTAMP",
                rows,
            )
            if not was_in_transaction:
                self.store.connection.commit()

    def semantic_embedding_count(self, model_key: str | None = None) -> int:
        if model_key:
            row = self.store.connection.execute("SELECT COUNT(*) AS n FROM semantic_embedding WHERE model_key=?", (str(model_key),)).fetchone()
        else:
            row = self.store.connection.execute("SELECT COUNT(*) AS n FROM semantic_embedding").fetchone()
        return int(row["n"] if row else 0)

    def explored_areas(self, agent_id: str) -> set[str]:
        rows = self.store.connection.execute(
            "SELECT DISTINCT area_id FROM exploration_result WHERE agent_id=?",
            (agent_id,),
        ).fetchall()
        return {str(row["area_id"]) for row in rows if row["area_id"]}

    def researched_sources(self, agent_id: str) -> set[str]:
        rows = self.store.connection.execute(
            "SELECT fact_key FROM knowledge_fact WHERE agent_id=? AND fact_key LIKE 'researched:%'",
            (agent_id,),
        ).fetchall()
        return {str(row["fact_key"]).removeprefix("researched:").split(":", 1)[0] for row in rows}

    def put_prompt(self, prompt_id: str, data: dict[str, Any]) -> None:
        self.store.connection.execute("INSERT OR REPLACE INTO prompt_def(id,data_json) VALUES (?,?)", (prompt_id, json_dumps(data)))

    def get_prompt(self, prompt_id: str, default: Any = None) -> Any:
        row = self.store.connection.execute("SELECT data_json FROM prompt_def WHERE id=?", (prompt_id,)).fetchone()
        return default if row is None else json.loads(row["data_json"])

    def upsert_knowledge_gap(self, gap_id: str, status: str, topic_key: str, question: str, *, source_id: str | None = None, data: dict[str, Any] | None = None, world_minute: int | None = None) -> dict[str, Any]:
        minute = int(self.store.get_meta("world_minute", 0) if world_minute is None else world_minute)
        current = self.store.connection.execute("SELECT created_minute FROM knowledge_gap WHERE gap_id=?", (str(gap_id),)).fetchone()
        created = int(current["created_minute"]) if current else minute
        payload = dict(data or {})
        self.store.connection.execute(
            "INSERT INTO knowledge_gap(gap_id,status,topic_key,question,current_source_id,created_minute,updated_minute,data_json) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(gap_id) DO UPDATE SET status=excluded.status,topic_key=excluded.topic_key,question=excluded.question,current_source_id=excluded.current_source_id,updated_minute=excluded.updated_minute,data_json=excluded.data_json",
            (str(gap_id), str(status), str(topic_key), str(question), str(source_id or "") or None, created, minute, json_dumps(payload)),
        )
        return {"gap_id": str(gap_id), "status": str(status), "topic_key": str(topic_key), "question": str(question), "current_source_id": source_id, "created_minute": created, "updated_minute": minute, **payload}

    def list_knowledge_gaps(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT gap_id,status,topic_key,question,current_source_id,created_minute,updated_minute,data_json FROM knowledge_gap"
        args: list[Any] = []
        if status:
            query += " WHERE status=?"
            args.append(str(status))
        query += " ORDER BY updated_minute DESC,gap_id LIMIT ?"
        args.append(max(1, min(int(limit), 200)))
        return [
            {"gap_id": row["gap_id"], "status": row["status"], "topic_key": row["topic_key"], "question": row["question"], "current_source_id": row["current_source_id"], "created_minute": row["created_minute"], "updated_minute": row["updated_minute"], **json.loads(row["data_json"] or "{}")}
            for row in self.store.connection.execute(query, tuple(args)).fetchall()
        ]

    def update_research_gap(
        self,
        source_id: str,
        *,
        stage_id: str | None = None,
        completed: bool = False,
        progress: dict[str, Any] | None = None,
        fact: str = "",
        event_id: int | None = None,
        world_minute: int | None = None,
    ) -> list[dict[str, Any]]:
        """Project a research result onto the source's persisted knowledge gap.

        ``event_log`` remains the historical source of truth.  This table is a
        small current-state projection so world agents can see which questions
        are still open without replaying every event.  A multi-stage source is
        resolved only after every active configured stage has a completed
        ``research.completed`` event; a one-stage/legacy source resolves on its
        first completed result.
        """

        source_key = str(source_id or "").strip()
        if not source_key:
            return []
        minute = int(self.store.get_meta("world_minute", 0) if world_minute is None else world_minute)
        discoveries = [
            item for item in self.research_discoveries(source_key)
            if str(item.get("status", "active")).casefold() not in {"obsolete", "disabled"}
        ]
        expected = {str(item.get("id")) for item in discoveries if item.get("id")}
        completed_stages: set[str] = set()
        rows = self.store.connection.execute(
            "SELECT event_id,data_json FROM event_log WHERE event_type='research.completed' ORDER BY event_id"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["data_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if str(payload.get("source_id") or "") != source_key:
                continue
            value = str(payload.get("stage_id") or "")
            if value:
                completed_stages.add(value)
            elif len(expected) <= 1:
                completed_stages.update(expected)
        if completed and stage_id:
            completed_stages.add(str(stage_id))
        resolved = bool(completed and (not expected or expected <= completed_stages))
        payload_progress = dict(progress or {})
        updated: list[dict[str, Any]] = []
        gap_rows = self.store.connection.execute(
            "SELECT gap_id,status,topic_key,question,current_source_id,created_minute,updated_minute,data_json "
            "FROM knowledge_gap WHERE current_source_id=? ORDER BY gap_id",
            (source_key,),
        ).fetchall()
        for row in gap_rows:
            try:
                prior = json.loads(row["data_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                prior = {}
            merged = {
                **prior,
                "completed_stages": sorted(completed_stages),
                "total_stages": len(expected),
                "last_stage_id": str(stage_id or prior.get("last_stage_id") or "") or None,
            }
            if payload_progress:
                merged["last_progress"] = payload_progress
            if fact:
                merged["last_fact"] = str(fact)
            if event_id is not None:
                merged["last_event_id"] = int(event_id)
            status = "resolved" if resolved else "active"
            self.store.connection.execute(
                "UPDATE knowledge_gap SET status=?,updated_minute=?,data_json=? WHERE gap_id=?",
                (status, minute, json_dumps(merged), str(row["gap_id"])),
            )
            updated.append({
                "gap_id": str(row["gap_id"]),
                "status": status,
                "topic_key": str(row["topic_key"]),
                "question": str(row["question"]),
                "current_source_id": source_key,
                "created_minute": int(row["created_minute"]),
                "updated_minute": minute,
                **merged,
            })
        return updated

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
                    visible_values.append(self.store.repositories.entities.get_entity(str(candidate)).get("name", candidate))
                except KeyError:
                    visible_values.append(candidate)
            tags[tag] = visible_values if isinstance(value, list) else visible_values[0]
        if tags:
            data["tags"] = tags
        encoded = json_dumps(data)
        importance = max(0.0, min(1.0, float(data.get("importance", 0.5))))
        salience = max(0.0, min(1.0, float(data.get("salience", 0.5))))
        world_minute = int(self.store.get_meta("world_minute", 0))
        cur = self.store.connection.execute(
            "INSERT INTO memory_item(agent_id,memory_type,memory_tier,data_json,importance,salience,access_count,last_access_minute) VALUES (?,?,?,?,?,?,?,?)",
            (agent_id, memory_type, tier, encoded, importance, salience, 1, world_minute),
        )
        memory_id = int(cur.lastrowid)
        self.store.connection.execute("INSERT INTO memory_fts(agent_id,content,memory_id) VALUES (?,?,?)", (agent_id, encoded, str(memory_id)))
        return memory_id

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
                fts_rows = self.store.connection.execute(
                    "SELECT memory_id FROM memory_fts WHERE agent_id=? AND memory_fts MATCH ? LIMIT ?",
                    (agent_id, query.replace('"', ' '), limit),
                ).fetchall()
                ids = [int(row["memory_id"]) for row in fts_rows]
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    rows = self.store.connection.execute(
                        f"SELECT id,memory_type,memory_tier,data_json,importance,salience,access_count,last_access_minute FROM memory_item WHERE agent_id=?{tier_clause} AND id IN ({placeholders}) ORDER BY (importance + salience + MIN(access_count, 100) * 0.01) DESC, id DESC LIMIT ?",
                        (agent_id, *tier_args, *ids, limit),
                    ).fetchall()
                else:
                    rows = []
            except sqlite3.OperationalError:
                rows = self.store.connection.execute(
                    f"SELECT id,memory_type,memory_tier,data_json,importance,salience,access_count,last_access_minute FROM memory_item WHERE agent_id=?{tier_clause} AND data_json LIKE ? ORDER BY (importance + salience + MIN(access_count, 100) * 0.01) DESC, id DESC LIMIT ?",
                    (agent_id, *tier_args, f"%{query}%", limit),
                ).fetchall()
        else:
            rows = self.store.connection.execute(
                f"SELECT id,memory_type,memory_tier,data_json,importance,salience,access_count,last_access_minute FROM memory_item WHERE agent_id=?{tier_clause} ORDER BY (importance + salience + MIN(access_count, 100) * 0.01) DESC, id DESC LIMIT ?",
                (agent_id, *tier_args, limit),
            ).fetchall()
        result = []
        for row in rows:
            value = json.loads(row["data_json"])
            result.append({"id": row["id"], "type": row["memory_type"], "tier": row["memory_tier"], "importance": row["importance"], "salience": row["salience"], "uses": row["access_count"], "last_access_minute": row["last_access_minute"], **value})
        if result and touch:
            was_in_transaction = self.store.connection.in_transaction
            self.store.connection.executemany(
                "UPDATE memory_item SET access_count=access_count+1,last_access_minute=? WHERE id=?",
                [(int(self.store.get_meta("world_minute", 0)), int(item["id"])) for item in result],
            )
            if not was_in_transaction:
                self.store.connection.commit()
        return result

    def delete_memories(self, agent_id: str, memory_ids: list[int]) -> int:
        if not memory_ids:
            return 0
        placeholders = ",".join("?" for _ in memory_ids)
        self.store.connection.execute(f"DELETE FROM memory_fts WHERE agent_id=? AND memory_id IN ({','.join('?' for _ in memory_ids)})", (agent_id, *(str(item) for item in memory_ids)))
        cur = self.store.connection.execute(f"DELETE FROM memory_item WHERE agent_id=? AND id IN ({placeholders})", (agent_id, *memory_ids))
        return int(cur.rowcount)

    def touch_memories(self, memory_ids: list[int]) -> None:
        """Record retrieval for exactly the rows exposed to an Agent."""

        ids = [int(item) for item in memory_ids if item]
        if not ids:
            return
        was_in_transaction = self.store.connection.in_transaction
        self.store.connection.executemany(
            "UPDATE memory_item SET access_count=access_count+1,last_access_minute=? WHERE id=?",
            [(int(self.store.get_meta("world_minute", 0)), item) for item in ids],
        )
        if not was_in_transaction:
            self.store.connection.commit()

