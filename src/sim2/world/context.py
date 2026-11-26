"""Resolve world-defined condition and Action context values.

The Runtime must not know that a particular package has food, oxygen, charge,
or another domain concept.  A package may expose literal context values and a
small set of generic resolvers; Actions and BT predicates then reference those
values through ``context.*``.
"""

from __future__ import annotations

from typing import Any, Callable

from .navigation import reachable_locations, resource_is_at_location
from .time import world_time_view


def calendar_context(store: Any, minute: int | None = None) -> dict[str, Any]:
    clock = world_time_view(store, minute)
    return {"day_phase": clock["day_phase"], "calendar_day": clock["day"], "minute_of_day": clock["minute_of_day"]}


def action_condition_context(
    store: Any, agent: dict[str, Any], parameters: dict[str, Any],
    target: dict[str, Any] | None, *, canonical_id: Callable[[str], str | None],
    recent_event_types: list[str], minute: int | None = None,
    location: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project the same declared operands for action preview, start and settlement."""

    entities, memory = store.repositories.entities, store.repositories.memory

    def entity(identifier: Any) -> dict[str, Any]:
        try:
            return entities.get_entity(str(identifier)) if identifier else {}
        except KeyError:
            return {}

    state = agent.get("initial_state") or {}
    actor_id = str(agent.get("id") or "")
    location_id = state.get("location_id")
    current_location = entity(location_id) if location is None else location
    source_id = parameters.get("source_id")
    resolved_target = target or {}
    context = {
        "location_id": location_id,
        "destination_id": parameters.get("location_id") or parameters.get("destination_id"),
        "area_id": parameters.get("area_id"),
        "source_id": source_id,
        "item_id": parameters.get("item_id"),
        "object_id": parameters.get("object_id"),
        "inventory": agent.get("inventory") or {},
        "current_location": current_location,
        "location": current_location,
        "station": current_location,
        "station_tags": [],
        "current_object": entity(parameters.get("object_id")),
        "source": entity(source_id),
        "research_discoveries": memory.research_discoveries(str(source_id)) if source_id else [],
        "research_stage_status": memory.research_stage_status(actor_id, str(source_id)) if source_id else [],
        "mapped_areas": memory.explored_areas(actor_id),
        "researched_sources": memory.researched_sources(actor_id),
        "known_fact_keys": [item["fact_key"] for item in memory.list_knowledge_facts(actor_id, limit=1000) if item.get("fact_key")],
        "target": resolved_target,
        "target.visible": bool(resolved_target.get("visible")),
        "target.available": bool(resolved_target.get("available")),
        "danger": {"detected": "danger" in (state.get("conditions") or [])},
        "hostile_contact": "hostile_contact" in (state.get("conditions") or []),
        "target.refused": "target_refused" in (state.get("conditions") or []),
        "known_route_destinations": reachable_locations(store, location_id),
        "recent_event_types": recent_event_types,
    }
    resource_ref = next((parameters[key] for key in ("resource_id", "target_resource", "resource", "resource_tag") if parameters.get(key)), None)
    if resource_ref:
        resource_id = str(canonical_id(str(resource_ref)) or resource_ref)
        resource = entity(resource_id)
        resource_state = resource.get("initial_state") if isinstance(resource.get("initial_state"), dict) else {}
        amount = float(resource_state.get("quantity", resource_state.get("amount", resource.get("amount", 0))) or 0)
        context.update({
            "resource_id": resource_id,
            "resource.amount": amount,
            "resource.available": amount > 0,
            "resource.here": resource_is_at_location(store, resource_id, location_id),
        })
    context.update(configured_context_values(store.repositories.metadata.get("rules", {}) or {}, agent))
    context.update(calendar_context(store, minute))
    for key, value in parameters.items():
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            context.setdefault(str(key), value)
    return context


def configured_context_values(
    rules: dict[str, Any] | None,
    agent: dict[str, Any] | None = None,
    *,
    include_resolvers: bool = True,
) -> dict[str, Any]:
    rules = rules or {}
    agent = agent or {}
    configured = rules.get("context_values", {}) or {}
    # Values are read-only within a condition context. A shallow copy keeps
    # callers from mutating the stored mapping without paying a deep-copy cost
    # once per person and minute.
    result = dict(configured) if isinstance(configured, dict) else {}
    groups = rules.get("item_groups", {}) or {}
    inventory = agent.get("inventory", {}) or {}

    if not include_resolvers:
        return result
    resolvers = rules.get("context_resolvers", {}) or {}
    for name, raw in resolvers.items():
        if not isinstance(raw, dict):
            continue
        resolver = str(raw.get("type") or raw.get("resolver") or "")
        if resolver == "first_inventory_item":
            group = str(raw.get("group") or "")
            candidates = groups.get(group, []) if isinstance(groups, dict) else []
            result[str(name)] = next(
                (
                    str(item)
                    for item in (candidates or [])
                    if isinstance(inventory.get(str(item)), (int, float))
                    and float(inventory.get(str(item), 0) or 0) > 0
                ),
                None,
            )
    return result
