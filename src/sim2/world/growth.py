"""Shared, content-neutral growth ledger for the Director, God and audits.

The ledger is derived only from persisted state: entities, definitions, rules
and events.  It knows the generic cells (place, verb, social, rule, resource)
but no world names.  A research source is deliberately not a structural cell,
so adding another archive cannot masquerade as world growth.
"""

from __future__ import annotations

import json
from typing import Any

def _canonical_id(store: Any, value: Any) -> str:
    """Resolve a visible name or id without importing an upper-layer package."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    row = store.connection.execute(
        "SELECT id FROM entity WHERE id=? OR name=? LIMIT 1", (raw, raw)
    ).fetchone()
    return str(row["id"]) if row else raw


def _count(store: Any, sql: str) -> int:
    row = store.connection.execute(sql).fetchone()
    return int(row["count"] if row else 0)


def build_growth_ledger(store: Any) -> dict[str, Any]:
    """Return the current structural surface plus dead or empty content."""

    entities = store.repositories.entities
    metadata = store.repositories.metadata
    rules = metadata.get("rules", {}) or {}
    resource_types = [str(item) for item in (rules.get("resource_entity_types", ["resource", "material"]) or [])]
    locations = entities.entities_of_type("location")
    factions = entities.entities_of_type("faction")
    resources = [item for kind in resource_types for item in entities.entities_of_type(kind)]
    behaviors = entities.get_definitions("bt_def")
    action_defs = {str(item.get("id")): item for item in entities.get_definitions("action_def")}
    world_rules = rules.get("world_rules", []) or []

    targeted_locations: set[str] = set()
    referenced_resources: set[str] = set()
    for behavior in behaviors:
        for node in behavior.get("nodes") or []:
            if not isinstance(node, dict) or str(node.get("type") or "") != "action":
                continue
            action = action_defs.get(str(node.get("action_id") or ""))
            if not action:
                continue
            parameters = node.get("parameters") if isinstance(node.get("parameters"), dict) else {}
            if any(isinstance(effect, dict) and str(effect.get("type") or "") == "location_set" for effect in (action.get("effects") or [])):
                raw = parameters.get("location_id")
                if raw and not str(raw).startswith("context."):
                    targeted_locations.add(_canonical_id(store, raw))
    for action in action_defs.values():
        for field in ("effects", "progress_effects"):
            for effect in action.get(field) or []:
                if not isinstance(effect, dict):
                    continue
                if str(effect.get("type") or effect.get("op") or "") not in {"world_resource_delta", "resource_transfer"}:
                    continue
                raw = effect.get("resource_id") or effect.get("resource")
                if raw:
                    referenced_resources.add(_canonical_id(store, raw))

    dynamic_location_ids: set[str] = set()
    dynamic_resource_ids: set[str] = set()
    for row in store.connection.execute(
        "SELECT data_json FROM event_log WHERE event_type='entity.created' ORDER BY event_id"
    ).fetchall():
        try:
            payload = json.loads(row["data_json"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        entity_id = str(payload.get("entity_id") or "")
        entity_type = str(payload.get("entity_type") or "")
        if not entity_id:
            continue
        if entity_type == "location":
            dynamic_location_ids.add(entity_id)
        elif entity_type in resource_types:
            dynamic_resource_ids.add(entity_id)

    dead: list[str] = []
    for location in locations:
        location_id = str(location.get("id") or "")
        if location_id in dynamic_location_ids and location_id not in targeted_locations:
            dead.append(f"{location.get('name') or location_id} (place: no behavior)")
    for resource in resources:
        resource_id = str(resource.get("id") or "")
        if resource_id in dynamic_resource_ids and resource_id not in referenced_resources:
            dead.append(f"{resource.get('name') or resource_id} (resource: no behavior)")

    cells = {
        "place": {"count": len(locations)},
        "verb": {"count": len(behaviors)},
        "social": {"count": len(factions) + _count(store, "SELECT COUNT(*) AS count FROM relationship") + _count(store, "SELECT COUNT(*) AS count FROM institution_definition WHERE status='active'")},
        "rule": {"count": len(world_rules)},
        "resource": {"count": len(resources)},
    }
    empty_cells = [name for name, cell in cells.items() if int(cell.get("count", 0) or 0) <= 0]
    last = store.connection.execute(
        "SELECT MAX(world_minute) AS minute FROM event_log WHERE event_type IN "
        "('definition.registered','entity.created','world.change.rule','world.change.faction','world.change.relationship','institution.created','institution.revised')"
    ).fetchone()
    evolution = metadata.get("evolution", {}) or {}
    evolution = evolution.get("evolution", evolution) if isinstance(evolution, dict) else {}
    stagnation = evolution.get("stagnation") if isinstance(evolution, dict) else {}
    return {
        "cells": cells,
        "empty_cells": empty_cells,
        "dead_content": dead,
        "last_structural_change": int(last["minute"]) if last and last["minute"] is not None else None,
        "growth_mode": str((stagnation or {}).get("growth") or "option"),
    }
