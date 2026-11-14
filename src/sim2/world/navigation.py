from __future__ import annotations

from collections import deque
from typing import Any


def configured_location_tags(store: Any, group: str, default: set[str] | list[str] | tuple[str, ...]) -> set[str]:
    """Read semantic location tag groups from the instance package."""

    rules = store.get_meta("rules", {}) or {}
    configured = (rules.get("location_tag_groups", {}) or {}).get(group, default)
    values = configured if isinstance(configured, (list, tuple, set)) else [configured]
    return {str(item) for item in values}


def nearest_safe_location(store: Any, origin_id: str | None) -> str | None:
    """Return the nearest configured safe location reachable from origin."""

    if not origin_id:
        return None
    queue: deque[str] = deque([str(origin_id)])
    visited = {str(origin_id)}
    while queue:
        current_id = queue.popleft()
        try:
            location = store.repositories.entities.get_entity(current_id)
        except KeyError:
            continue
        safe_tags = configured_location_tags(store, "safe", {"safe"})
        public_safe = bool((store.get_meta("rules", {}) or {}).get("public_locations_are_safe", True))
        if bool(location.get("safe", False)) or (public_safe and location.get("public", False) is True) or set(location.get("tags", []) or []) & safe_tags:
            return current_id
        for neighbor_id in location.get("routes", []) or []:
            neighbor_id = str(neighbor_id)
            if neighbor_id in visited:
                continue
            try:
                neighbor = store.repositories.entities.get_entity(neighbor_id)
            except KeyError:
                continue
            if neighbor.get("entity_type") != "location":
                continue
            visited.add(neighbor_id)
            queue.append(neighbor_id)
    return None


def nearest_resource_node(store: Any, origin_id: str | None) -> dict[str, str] | None:
    """Find the nearest configured, non-empty resource or material node."""

    if not origin_id:
        return None
    rules = store.get_meta("rules", {}) or {}
    entity_types = {str(item) for item in (rules.get("resource_entity_types", ["resource", "material"]) or [])}
    protected_key = str(rules.get("resource_protected_state", "protected"))
    queue: deque[tuple[str, int]] = deque([(str(origin_id), 0)])
    visited = {str(origin_id)}
    while queue:
        current_id, _ = queue.popleft()
        for entity in store.repositories.entities.entities_at_location(current_id):
            if entity.get("entity_type") not in entity_types:
                continue
            state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
            amount = entity.get("amount")
            if amount is None:
                amount = state.get("quantity", state.get("amount", 0))
            if isinstance(amount, (int, float)) and float(amount) > 0 and not state.get(protected_key):
                return {"resource_tag": str(entity.get("id")), "location_id": current_id}
        try:
            location = store.repositories.entities.get_entity(current_id)
        except KeyError:
            continue
        for neighbor_id in location.get("routes", []) or []:
            neighbor_id = str(neighbor_id)
            if neighbor_id in visited:
                continue
            visited.add(neighbor_id)
            queue.append((neighbor_id, 0))
    return None


def nearest_location_with_tag(store: Any, origin_id: str | None, tags: set[str]) -> str | None:
    """Return the nearest location carrying one of the configured service tags."""

    if not origin_id:
        return None
    queue: deque[str] = deque([str(origin_id)])
    visited = {str(origin_id)}
    while queue:
        current_id = queue.popleft()
        try:
            location = store.repositories.entities.get_entity(current_id)
        except KeyError:
            continue
        if set(location.get("tags", []) or []) & set(tags):
            return current_id
        for neighbor_id in location.get("routes", []) or []:
            neighbor_id = str(neighbor_id)
            if neighbor_id in visited:
                continue
            visited.add(neighbor_id)
            queue.append(neighbor_id)
    return None


def reachable_locations(store: Any, origin_id: str | None) -> set[str]:
    """Return all locations reachable through configured routes."""

    if not origin_id:
        return set()
    queue: deque[str] = deque([str(origin_id)])
    visited = {str(origin_id)}
    while queue:
        current_id = queue.popleft()
        try:
            location = store.repositories.entities.get_entity(current_id)
        except KeyError:
            continue
        for neighbor_id in location.get("routes", []) or []:
            neighbor_id = str(neighbor_id)
            if neighbor_id in visited:
                continue
            visited.add(neighbor_id)
            queue.append(neighbor_id)
    return visited
