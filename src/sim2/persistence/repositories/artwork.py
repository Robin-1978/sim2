"""Binary artwork belonging to one world save."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..store import WorldStore


class ArtworkRepository:
    def __init__(self, store: WorldStore):
        self.store = store

    def replace(self, artwork: dict[str, bytes]) -> None:
        conn = self.store.connection
        conn.execute("DELETE FROM world_artwork")
        conn.executemany("INSERT INTO world_artwork(digest, image) VALUES (?, ?)", artwork.items())

    def get(self, digest: str) -> bytes | None:
        row = self.store.connection.execute("SELECT image FROM world_artwork WHERE digest = ?", (digest,)).fetchone()
        return row["image"] if row else None
