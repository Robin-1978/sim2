from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..persistence.store import WorldStore


class TargetResolver:
    """Resolve configured target parameters into a safe public projection."""

    TARGET_KEYS = ("target_id", "patient_id", "protected_target_id", "counterparty_id", "listener_id")

    def __init__(self, store: "WorldStore"):
        self.store = store

    @staticmethod
    def _key(value: Any) -> str:
        return "".join(str(value).casefold().split())

    def canonical_id(self, value: Any) -> str | None:
        """Resolve an id or an exact name without fuzzy guessing."""

        text = str(value or "").strip()
        if not text:
            return None
        try:
            return str(self.store.get_entity(text)["id"])
        except KeyError:
            pass
        rows = self.store.connection.execute("SELECT id,name FROM entity WHERE name IS NOT NULL").fetchall()
        matches = [str(row["id"]) for row in rows if self._key(row["name"]) == self._key(text)]
        return matches[0] if len(matches) == 1 else None

    def resolve(self, agent: dict[str, Any], parameters: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        target_id = next((parameters.get(key) for key in self.TARGET_KEYS if parameters.get(key)), None)
        if not target_id:
            return None, None
        canonical_id = self.canonical_id(target_id)
        if canonical_id is None:
            return None, f"target does not exist or is ambiguous: {target_id}"
        try:
            entity = self.store.get_entity(canonical_id)
        except KeyError:
            return None, f"target does not exist: {target_id}"
        state = agent.get("initial_state", {})
        target_state = entity.get("initial_state", {}) if isinstance(entity, dict) else {}
        visible = True
        target_location = target_state.get("location_id")
        agent_location = state.get("location_id")
        if target_location and agent_location and target_location != agent_location:
            visible = False
        return {
            "id": entity.get("id", target_id),
            "name": entity.get("name", target_id),
            "entity_type": entity.get("entity_type", "entity"),
            "location_id": target_location,
            "visible": visible,
            "available": visible,
            "health": target_state.get("health"),
            "defense": entity.get("combat_stats", {}).get("defense", target_state.get("defense", 0)),
        }, None
