"""Binary artwork belonging to one world save."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..store import WorldStore


class ArtworkRepository:
    def __init__(self, store: WorldStore):
        self.store = store

    def replace(self, artwork: dict[str, bytes]) -> None:
        conn = self.store.connection
        conn.execute("DELETE FROM world_artwork WHERE digest NOT IN (SELECT digest FROM generated_artwork WHERE digest IS NOT NULL)")
        conn.executemany("INSERT OR IGNORE INTO world_artwork(digest, image) VALUES (?, ?)", artwork.items())

    def get(self, digest: str) -> bytes | None:
        row = self.store.connection.execute("SELECT image FROM world_artwork WHERE digest = ?", (digest,)).fetchone()
        return row["image"] if row else None

    def generated_for(self, subject_id: str) -> str | None:
        row = self.store.connection.execute("SELECT digest FROM generated_artwork WHERE subject_id=?", (subject_id,)).fetchone()
        return row["digest"] if row else None

    def candidate(self, now: float, max_attempts: int, entity_types: tuple[str, ...]) -> sqlite3.Row | None:
        placeholders = ", ".join("?" for _ in entity_types)
        return self.store.connection.execute(f"""
            WITH occupied_places AS (
                SELECT json_extract(data_json, '$.initial_state.location_id') AS id
                FROM agent_state WHERE json_extract(data_json, '$.role') = 'player'
            )
            SELECT subject.id, subject.entity_type, subject.name, event.event_id,
                   site.name AS place_name, json_extract(site.data_json, '$.description') AS place_description,
                   json_extract(resident.data_json, '$.role') AS resident_role
            FROM event_log AS event
            JOIN entity AS subject ON subject.id = json_extract(event.data_json, '$.entity_id')
            LEFT JOIN agent_state AS resident ON resident.id = subject.id
            JOIN entity AS site ON site.id = CASE WHEN subject.entity_type = 'location' THEN subject.id
                ELSE json_extract(resident.data_json, '$.initial_state.location_id') END
            LEFT JOIN generated_artwork AS job ON job.subject_id = subject.id
            WHERE event.event_type = 'entity.created' AND event.actor_id = 'god'
                AND subject.entity_type IN ({placeholders}) AND site.entity_type = 'location'
                AND json_type(site.data_json, '$.public') = 'true' AND site.id IN (SELECT id FROM occupied_places)
                AND COALESCE(json_extract(resident.data_json, '$.role'), '') <> 'player'
                AND job.digest IS NULL AND COALESCE(job.available_at, 0) <= ? AND COALESCE(job.attempts, 0) < ?
            ORDER BY event.event_id LIMIT 1
        """, (*entity_types, now, max_attempts)).fetchone()

    def claim(self, subject_id: str, source_event_id: int, token: str, now: float, lease_until: float, max_attempts: int) -> sqlite3.Row | None:
        conn = self.store.connection
        updated = conn.execute("""
            INSERT INTO generated_artwork(subject_id, source_event_id, claim_token, available_at, attempts)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(subject_id) DO UPDATE SET claim_token=excluded.claim_token,
                available_at=excluded.available_at, attempts=generated_artwork.attempts + 1
            WHERE generated_artwork.digest IS NULL AND generated_artwork.available_at <= ? AND generated_artwork.attempts < ?
        """, (subject_id, source_event_id, token, lease_until, now, max_attempts))
        if not updated.rowcount:
            return None
        return conn.execute("SELECT prompt, attempts FROM generated_artwork WHERE subject_id=?", (subject_id,)).fetchone()

    def release(self, subject_id: str, token: str, prompt: str, next_attempt: float) -> None:
        self.store.connection.execute("""
            UPDATE generated_artwork SET claim_token=NULL, prompt=?, available_at=?
            WHERE subject_id=? AND claim_token=?
        """, (prompt, next_attempt, subject_id, token))

    def finish(self, subject_id: str, token: str, prompt: str, digest: str, image: bytes) -> bool:
        conn = self.store.connection
        if not conn.execute("SELECT 1 FROM generated_artwork WHERE subject_id=? AND claim_token=?", (subject_id, token)).fetchone():
            return False
        conn.execute("INSERT OR IGNORE INTO world_artwork(digest, image) VALUES (?, ?)", (digest, image))
        conn.execute("UPDATE generated_artwork SET digest=?, prompt=?, claim_token=NULL WHERE subject_id=?", (digest, prompt, subject_id))
        return True
