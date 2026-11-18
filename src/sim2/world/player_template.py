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


def _opening(value: Any, field: str) -> dict[str, str]:
    """Parse one origin's optional first-person opening beat.

    A world may give every origin its own arrival event so the first minute of
    play differs by identity, not only by starting stats.  The event is emitted
    once when the player is created; an empty opening means "no extra event".
    """

    if value is None or value == {}:
        return {}
    if not isinstance(value, dict):
        raise PlayerTemplateError(f"{field} must be an object with summary and optional event_type")
    summary = str(value.get("summary") or "").strip()
    event_type = str(value.get("event_type") or "origin.opening").strip()
    if not summary:
        raise PlayerTemplateError(f"{field}.summary is required")
    if not event_type:
        raise PlayerTemplateError(f"{field}.event_type must not be empty")
    return {"event_type": event_type, "summary": summary}


def _number_map(value: Any, field: str, *, maximum: float | None = None) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PlayerTemplateError(f"{field} must be an object of name: number")
    result: dict[str, float] = {}
    for key, raw in value.items():
        name = str(key or "").strip()
        if not name:
            raise PlayerTemplateError(f"{field} keys must be non-empty names")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise PlayerTemplateError(f"{field}.{name} must be a number")
        number = float(raw)
        if number < 0:
            raise PlayerTemplateError(f"{field}.{name} must not be negative")
        if maximum is not None and number > maximum:
            raise PlayerTemplateError(f"{field}.{name} must be at most {maximum:g}")
        result[name] = number
    return result


def parse_player_template(raw: Any, *, require_entry: bool = False) -> dict[str, Any]:
    """Parse the canonical top-level player entry template.

    The supported format is::

        entry_location_id: village_square
        capabilities: {gathering: 0.5, conversation: 0.5}
        inventory: {food_ration: 2, fresh_water: 2}
        initial_bt_ids: [gather_water, communicate]
        origins:
          - id: traveler
            traits: {mobility: 0.6}
            capabilities: {exploration: 0.5}
            inventory: {food_ration: 1}
            starting_goal: Find the missing caravan.
            knowledge_seed: ["The caravan left before the storm."]
            opening:
              event_type: origin.traveler
              summary: You arrive with the caravan's last waybill.
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
            "name": str(item.get("name") or origin_id),
            "description": str(item.get("description") or ""),
            "traits": dict(traits),
            "starting_goal": starting_goal,
            "capabilities": _number_map(item.get("capabilities"), f"origins[{index}].capabilities", maximum=1.0),
            "inventory": _number_map(item.get("inventory"), f"origins[{index}].inventory"),
            "knowledge_seed": _string_list(item.get("knowledge_seed"), f"origins[{index}].knowledge_seed"),
            "opening": _opening(item.get("opening"), f"origins[{index}].opening"),
        })

    if origins and not entry_location_id:
        raise PlayerTemplateError("entry_location_id is required when origins are configured")
    if require_entry and (not entry_location_id or not origins):
        raise PlayerTemplateError("player entry requires both entry_location_id and at least one origin")

    initial_faction_ids = _string_list(source.get("initial_faction_ids"), "initial_faction_ids")
    initial_reputation = source.get("initial_reputation", 0)
    if isinstance(initial_reputation, bool) or not isinstance(initial_reputation, (int, float)):
        raise PlayerTemplateError("initial_reputation must be a number")
    controller = str(source.get("controller") or "manual").strip().lower()
    if controller not in {"manual", "llm"}:
        raise PlayerTemplateError("controller must be 'manual' or 'llm'")

    return {
        "id_prefix": str(source.get("id_prefix") or "player_"),
        "entry_location_id": entry_location_id,
        "origins": origins,
        "knowledge_seed": _string_list(source.get("knowledge_seed"), "knowledge_seed"),
        "initial_bt_ids": _string_list(source.get("initial_bt_ids"), "initial_bt_ids"),
        "initial_faction_ids": initial_faction_ids,
        "initial_reputation": float(initial_reputation),
        "capabilities": _number_map(source.get("capabilities"), "capabilities", maximum=1.0),
        "inventory": _number_map(source.get("inventory"), "inventory"),
        "controller": controller,
    }
