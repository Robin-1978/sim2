"""Content-neutral, bounded indexes for a growing world.

A structural prompt must describe the current target without rendering every
entity, Action, behavior and rule.  These functions build the compact index the
prompt receives and answer the paged details a model requests through the
``query_world_index`` and ``inspect_definition`` read tools.
"""

from __future__ import annotations

import json
from typing import Any

CELL_TYPES: dict[str, tuple[str, ...]] = {
    "place": ("location",),
    "social": ("agent", "faction", "institution"),
    "resource": ("resource", "material", "object"),
}
CELL_ORDER = ("place", "verb", "social", "rule", "resource")


def display_name(item: dict[str, Any]) -> str:
    """Return the model-facing label of one content definition."""

    for key in ("agent_label", "name", "summary"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return str(item.get("id") or "content").replace("_", " ").title()


def _summary(item: dict[str, Any]) -> str:
    for key in ("agent_summary", "description", "purpose", "summary"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)[:240]
    return "configured content"


def _entity_label(store: Any, value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    row = store.connection.execute(
        "SELECT name FROM entity WHERE id=? OR name=? LIMIT 1", (raw, raw)
    ).fetchone()
    return str(row["name"] or raw) if row else raw


def _effect_targets(store: Any, item: dict[str, Any]) -> list[str]:
    targets: list[str] = []
    for field in ("effects", "progress_effects"):
        for effect in item.get(field) or []:
            if not isinstance(effect, dict):
                continue
            for key in ("resource_id", "resource", "entity_id", "target", "location_id"):
                label = _entity_label(store, effect.get(key))
                if label and label not in targets:
                    targets.append(label)
    for node in item.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        for key in ("action_id", "location_id"):
            label = _entity_label(store, node.get(key))
            if label and label not in targets:
                targets.append(label)
    return targets[:12]


def _effect_types(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for field in ("effects", "progress_effects"):
        for effect in item.get(field) or []:
            if not isinstance(effect, dict):
                continue
            value = str(effect.get("type") or effect.get("op") or "")
            if value and value not in values:
                values.append(value)
    return values


def _cell_of_entity(entity_type: str) -> str:
    for cell, types in CELL_TYPES.items():
        if entity_type in types:
            return cell
    return "resource"


def _entity_items(store: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    rows = store.connection.execute(
        "SELECT id,entity_type,name,data_json FROM entity ORDER BY entity_type,name"
    ).fetchall()
    for row in rows:
        entity_type = str(row["entity_type"] or "")
        if not entity_type:
            continue
        try:
            data = json.loads(row["data_json"] or "{}")
        except (TypeError, ValueError):
            data = {}
        state = data.get("initial_state") if isinstance(data.get("initial_state"), dict) else {}
        location_id = state.get("location_id") or data.get("location_id")
        items.append({
            "kind": "entity",
            "cell": _cell_of_entity(entity_type),
            "entity_type": entity_type,
            "name": str(row["name"] or row["id"]),
            "summary": str(data.get("description") or data.get("role") or entity_type)[:240],
            "location": _entity_label(store, location_id) if location_id else "",
            "ref": str(row["id"]),
        })
    return items


def _action_items(store: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for action in store.repositories.entities.get_definitions("action_def"):
        items.append({
            "kind": "action",
            "cell": "verb",
            "name": display_name(action),
            "summary": _summary(action),
            "effects": _effect_types(action)[:8],
            "targets": _effect_targets(store, action),
            "parameters": [str(key) for key in (action.get("parameters") or {}).keys()][:12],
            "ref": str(action.get("id") or ""),
        })
    return items


def _behavior_items(store: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    actions = {
        str(action.get("id") or ""): action
        for action in store.repositories.entities.get_definitions("action_def")
    }
    for behavior in store.repositories.entities.get_definitions("bt_def"):
        operations = []
        for field in ("authorization_operation", "authorization_decision"):
            value = behavior.get(field)
            if value not in (None, ""):
                operations.append(str(value))
        effects = _effect_types(behavior)
        targets = _effect_targets(store, behavior)
        for node in behavior.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            action = actions.get(str(node.get("action_id") or ""))
            if not action:
                continue
            for effect in _effect_types(action):
                if effect not in effects:
                    effects.append(effect)
            for target in _effect_targets(store, action):
                if target not in targets:
                    targets.append(target)
        items.append({
            "kind": "behavior",
            "cell": "verb",
            "name": display_name(behavior),
            "summary": _summary(behavior),
            "operations": operations,
            "effects": effects[:8],
            "targets": targets[:12],
            "ref": str(behavior.get("id") or ""),
        })
    return items


def _rule_items(store: Any) -> list[dict[str, Any]]:
    rules = (store.repositories.metadata.get("rules", {}) or {}).get("world_rules", []) or []
    items: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        effect = rule.get("effect") if isinstance(rule.get("effect"), dict) else {}
        items.append({
            "kind": "rule",
            "cell": "rule",
            "name": display_name(rule),
            "summary": _summary(rule),
            "mode": str(effect.get("mode") or effect.get("type") or ""),
            "ref": str(rule.get("id") or rule.get("name") or ""),
        })
    return items


def _recent_changes(store: Any, limit: int = 4) -> list[dict[str, Any]]:
    event_types = (
        "definition.registered", "entity.created", "world.change.rule",
        "world.change.relationship", "world.change.faction", "institution.created",
        "institution.revised",
    )
    placeholders = ",".join("?" for _ in event_types)
    rows = store.connection.execute(
        f"SELECT event_type,world_minute,data_json FROM event_log WHERE event_type IN ({placeholders}) "
        "ORDER BY event_id DESC LIMIT ?",
        (*event_types, max(1, int(limit))),
    ).fetchall()
    changes: list[dict[str, Any]] = []
    for row in rows:
        try:
            data = json.loads(row["data_json"] or "{}")
        except (TypeError, ValueError):
            data = {}
        label = data.get("name") or data.get("summary") or data.get("definition_id") or data.get("entity_type") or ""
        changes.append({
            "event_type": str(row["event_type"]),
            "minute": int(row["world_minute"] or 0),
            "label": str(label)[:160],
        })
    return changes


def build_world_index(store: Any, *, target_cell: str = "", limit: int = 8) -> dict[str, Any]:
    """Build the bounded structural index rendered into the God prompt."""

    cap = max(1, min(int(limit), 12))
    items = _entity_items(store) + _action_items(store) + _behavior_items(store) + _rule_items(store)
    counts = {cell: 0 for cell in CELL_ORDER}
    for item in items:
        cell = str(item.get("cell") or "")
        if cell in counts:
            counts[cell] += 1
    target = str(target_cell or "").strip().casefold()
    target_items = [item for item in items if not target or item.get("cell") == target]
    target_names = {
        str(item.get("name") or "").casefold()
        for item in target_items
        if item.get("kind") == "entity"
    }

    def relevant(item: dict[str, Any]) -> bool:
        if not target:
            return True
        if item.get("cell") == target:
            return True
        haystack = json.dumps(item, ensure_ascii=False).casefold()
        return any(name and name in haystack for name in target_names)

    def pick(kind: str) -> list[dict[str, Any]]:
        candidates = [item for item in items if item.get("kind") == kind and relevant(item)]
        candidates.sort(key=lambda item: (item.get("cell") != target, str(item.get("name") or "")))
        return candidates[:cap]

    return {
        "target_cell": target,
        "counts": counts,
        "totals": {
            "entities": sum(1 for item in items if item.get("kind") == "entity"),
            "actions": sum(1 for item in items if item.get("kind") == "action"),
            "behaviors": sum(1 for item in items if item.get("kind") == "behavior"),
            "rules": sum(1 for item in items if item.get("kind") == "rule"),
        },
        "entities": pick("entity"),
        "actions": pick("action"),
        "behaviors": pick("behavior"),
        "rules": pick("rule"),
        "recent_changes": _recent_changes(store),
        "limit": cap,
    }


def query_world_index(
    store: Any,
    *,
    cell: str = "",
    query: str = "",
    limit: int = 12,
    offset: int = 0,
) -> dict[str, Any]:
    """Return one bounded page of the world's structural index."""

    requested = str(cell or "").strip().casefold()
    if requested and requested not in CELL_ORDER:
        return {
            "status": "rejected",
            "failed_check": "world_index_cell",
            "retryable": True,
            "message": "cell must be place, verb, social, rule, resource, or omitted.",
        }
    needle = str(query or "").strip().casefold()
    items = _entity_items(store) + _action_items(store) + _behavior_items(store) + _rule_items(store)
    if requested:
        items = [item for item in items if item.get("cell") == requested]
    if needle:
        items = [
            item for item in items
            if needle in json.dumps(item, ensure_ascii=False).casefold()
        ]
    start = max(0, int(offset))
    page = items[start:start + max(1, min(int(limit), 20))]
    return {
        "status": "ok",
        "cell": requested or "all",
        "query": query,
        "offset": start,
        "limit": max(1, min(int(limit), 20)),
        "total": len(items),
        "items": page,
        "next_offset": start + len(page) if start + len(page) < len(items) else None,
    }


def _compact(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "[details omitted]"
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, list):
        return [_compact(item, depth=depth + 1) for item in value[:12]]
    if isinstance(value, dict):
        # ``action_id`` and similar machine fields are part of a definition's
        # executable shape.  Keep them; only the row id and revision
        # bookkeeping are audit-only.
        return {
            str(key): _compact(item, depth=depth + 1)
            for key, item in value.items()
            if str(key) not in {"id", "created_revision", "definition_version"}
        }
    return value


def inspect_definition(store: Any, *, name: str, kind: str = "any") -> dict[str, Any]:
    """Read one Action, behavior tree or world rule by its visible name."""

    needle = str(name or "").strip()
    requested = str(kind or "any").strip().casefold()
    if not needle:
        return {"status": "rejected", "failed_check": "definition_name", "retryable": True, "message": "name is required."}
    if requested not in {"any", "action", "behavior", "rule"}:
        return {"status": "rejected", "failed_check": "definition_kind", "retryable": True, "message": "kind must be any, action, behavior or rule."}

    candidates: list[tuple[str, dict[str, Any]]] = []
    if requested in {"any", "action"}:
        candidates.extend(("action", item) for item in store.repositories.entities.get_definitions("action_def"))
    if requested in {"any", "behavior"}:
        candidates.extend(("behavior", item) for item in store.repositories.entities.get_definitions("bt_def"))
    if requested in {"any", "rule"}:
        for rule in (store.repositories.metadata.get("rules", {}) or {}).get("world_rules", []) or []:
            if isinstance(rule, dict):
                candidates.append(("rule", rule))

    key = needle.casefold()
    exact = [
        (candidate_kind, item) for candidate_kind, item in candidates
        if key in {
            str(item.get("id") or "").casefold(),
            str(item.get("name") or "").casefold(),
            str(item.get("agent_label") or "").casefold(),
        }
    ]
    matches = exact or [
        (candidate_kind, item) for candidate_kind, item in candidates
        if key in json.dumps(item, ensure_ascii=False).casefold()
    ]
    if not matches:
        return {"status": "ok", "found": False, "name": needle, "candidates": []}
    if len(matches) > 1 and requested == "any":
        # Ambiguous shorthand: list the exact labels instead of returning the
        # wrong definition or an unbounded dump.
        return {
            "status": "ok",
            "found": True,
            "ambiguous": True,
            "candidates": [
                {"kind": candidate_kind, "name": display_name(item)}
                for candidate_kind, item in matches[:8]
            ],
        }
    candidate_kind, item = matches[0]
    detail = {**_compact(item), "kind": candidate_kind, "name": display_name(item)}
    return {"status": "ok", "found": True, "definition": detail}
