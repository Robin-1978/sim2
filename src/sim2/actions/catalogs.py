"""Declarative action catalogs: one verb, many configured entries.

A catalog is a bounded list of data entries (trade offers, gather nodes,
consumables) that a generic Action can select by a visible label.  The catalog
owns no runtime state; Platform validates costs, location and requirements and
executes the entry's ordinary effects through the existing effect pipeline.
"""

from __future__ import annotations

from typing import Any


def configured_catalogs(store: Any) -> dict[str, list[dict[str, Any]]]:
    rules = store.repositories.metadata.get("rules", {}) or {}
    raw = rules.get("action_catalogs") if isinstance(rules, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {
        str(name): [dict(item) for item in entries if isinstance(item, dict)]
        for name, entries in raw.items()
        if isinstance(entries, list)
    }


def catalog_entries(store: Any, catalog: str) -> list[dict[str, Any]]:
    return list(configured_catalogs(store).get(str(catalog), []))


def _fold(value: Any) -> str:
    return "".join(char for char in str(value or "").casefold() if char.isalnum())


def resolve_catalog_entry(store: Any, catalog: str, value: Any) -> dict[str, Any] | None:
    """Resolve one visible entry label or machine id inside a catalog."""

    wanted = _fold(value)
    if not wanted:
        return None
    for entry in catalog_entries(store, catalog):
        if _fold(entry.get("id")) == wanted or _fold(entry.get("label")) == wanted:
            return entry
    return None


def entry_location(entry: dict[str, Any]) -> str:
    return str(entry.get("location") or "").strip()


def entry_cost_available(store: Any, entry: dict[str, Any], agent: dict[str, Any], *, entity_quantity: Any) -> bool:
    """Check a catalog entry's static reserve costs without mutating state."""

    reserve = ((entry.get("costs") or {}).get("reserve") or {}) if isinstance(entry.get("costs"), dict) else {}
    inventory = agent.get("inventory") or {}
    for key, value in reserve.items():
        if key in {"recipe_inputs", "item_parameter", "resource_parameter", "resource_amount", "item_quantity_parameter"}:
            continue
        if not isinstance(value, (int, float)):
            continue
        amount = float(value)
        if float(inventory.get(str(key), 0) or 0) >= amount:
            continue
        try:
            entity = store.repositories.entities.get_entity(str(key))
        except KeyError:
            return False
        if float(entity_quantity(entity) or 0) < amount:
            return False
    return True
