"""Canonical player entry template parsing and validation."""

from __future__ import annotations

from typing import Any


DEFAULT_PLAYER_GOAL = "Find a meaningful place in this world."
_LEGACY_KEYS = ("player", "entry_location", "origin_choices")


class PlayerTemplateError(ValueError):
    """Raised when a world package uses a player template the runtime no longer supports."""


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PlayerTemplateError(f"{field} must be a list of strings")
    return list(value)


def parse_player_template(raw: Any, *, require_entry: bool = False) -> dict[str, Any]:
    """Parse the canonical top-level player entry template.

    The supported format is::

        entry_location_id: village_square
        origins:
          - id: traveler
            traits: {mobility: 0.6}
            starting_goal: Find the missing caravan.
    """

    source = raw if isinstance(raw, dict) else {}
    legacy = [key for key in _LEGACY_KEYS if key in source]
    if legacy:
        raise PlayerTemplateError(
            "legacy player template keys are not supported "
            f"({', '.join(legacy)}); use top-level entry_location_id and origins"
        )

    entry_value = source.get("entry_location_id", "")
    if entry_value is None:
        entry_value = ""
    if not isinstance(entry_value, str):
        raise PlayerTemplateError("entry_location_id must be a string")
    entry_location_id = entry_value.strip()

    raw_origins = source.get("origins", [])
    if not isinstance(raw_origins, list):
        raise PlayerTemplateError("origins must be a list of objects")
    origins: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_origins):
        if not isinstance(item, dict):
            raise PlayerTemplateError(f"origins[{index}] must be an object with an id")
        origin_id = str(item.get("id") or "").strip()
        if not origin_id:
            raise PlayerTemplateError(f"origins[{index}].id is required")
        if origin_id in seen:
            raise PlayerTemplateError(f"duplicate origin id: {origin_id}")
        seen.add(origin_id)
        traits = item.get("traits", {})
        if not isinstance(traits, dict):
            raise PlayerTemplateError(f"origins[{index}].traits must be an object")
        starting_goal = item.get("starting_goal") or DEFAULT_PLAYER_GOAL
        if not isinstance(starting_goal, str):
            raise PlayerTemplateError(f"origins[{index}].starting_goal must be a string")
        origins.append({
            "id": origin_id,
            "traits": dict(traits),
            "starting_goal": starting_goal,
        })

    if origins and not entry_location_id:
        raise PlayerTemplateError("entry_location_id is required when origins are configured")
    if require_entry and (not entry_location_id or not origins):
        raise PlayerTemplateError("player entry requires both entry_location_id and at least one origin")

    initial_faction_ids = _string_list(source.get("initial_faction_ids"), "initial_faction_ids")
    initial_reputation = source.get("initial_reputation", 0)
    if isinstance(initial_reputation, bool) or not isinstance(initial_reputation, (int, float)):
        raise PlayerTemplateError("initial_reputation must be a number")

    return {
        "id_prefix": str(source.get("id_prefix") or "player_"),
        "entry_location_id": entry_location_id,
        "origins": origins,
        "knowledge_seed": _string_list(source.get("knowledge_seed"), "knowledge_seed"),
        "initial_bt_ids": _string_list(source.get("initial_bt_ids"), "initial_bt_ids"),
        "initial_faction_ids": initial_faction_ids,
        "initial_reputation": float(initial_reputation),
    }
