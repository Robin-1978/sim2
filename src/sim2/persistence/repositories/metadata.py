from __future__ import annotations

import json
from typing import Any

from ..store import json_dumps


class MetadataRepository:
    """Own the instance metadata table and its typed runtime projections.

    Metadata is configuration and runtime bookkeeping, not a general-purpose
    service API.  Callers that need a known projection should use the typed
    accessors below; ``get``/``set`` remain available for declarative keys that
    are introduced by a world package or a registered runtime definition.
    The caller owns the surrounding transaction for writes.
    """

    def __init__(self, store: Any) -> None:
        self.store = store

    def get(self, key: str, default: Any = None) -> Any:
        with self.store._write_lock:
            row = self.store.connection.execute(
                "SELECT value FROM instance_meta WHERE key=?", (str(key),)
            ).fetchone()
            return default if row is None else json.loads(row["value"])

    def set(self, key: str, value: Any) -> None:
        self.store.connection.execute(
            "INSERT INTO instance_meta(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(key), json_dumps(value)),
        )

    def delete(self, key: str) -> None:
        self.store.connection.execute("DELETE FROM instance_meta WHERE key=?", (str(key),))

    def world_id(self, default: str = "") -> str:
        return str(self.get("world_id", self.get("instance_id", default)) or default)

    def instance_id(self, default: str = "") -> str:
        return str(self.get("instance_id", default) or default)

    def world_minute(self, default: int = 0) -> int:
        return int(self.get("world_minute", default) or default)

    def world_revision(self, default: int = 0) -> int:
        return int(self.get("world_revision", default) or default)

    def world_properties(self) -> dict[str, Any]:
        value = self.get("world_properties", {})
        return dict(value) if isinstance(value, dict) else {}

    def rules(self) -> dict[str, Any]:
        value = self.get("rules", {})
        return dict(value) if isinstance(value, dict) else {}

    def setting(self) -> dict[str, Any]:
        value = self.get("setting", {})
        return dict(value) if isinstance(value, dict) else {}

    def attributes(self) -> list[dict[str, Any]]:
        value = self.get("attributes", [])
        return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []

