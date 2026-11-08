from __future__ import annotations

from collections import deque
from typing import Any


def nearest_safe_location(store: Any, origin_id: str | None) -> str | None:
    """Return the nearest configured safe location reachable from origin."""

    if not origin_id:
        return None
    queue: deque[str] = deque([str(origin_id)])
    visited = {str(origin_id)}
    while queue:
        current_id = queue.popleft()
        try:
            location = store.get_entity(current_id)
        except KeyError:
            continue
        if bool(location.get("safe", False)):
            return current_id
        for neighbor_id in location.get("routes", []) or []:
            neighbor_id = str(neighbor_id)
            if neighbor_id in visited:
                continue
            try:
                neighbor = store.get_entity(neighbor_id)
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
    queue: deque[tuple[str, int]] = deque([(str(origin_id), 0)])
    visited = {str(origin_id)}
    while queue:
        current_id, _ = queue.popleft()
        for entity in store.entities_at_location(current_id):
            if entity.get("entity_type") not in {"resource", "material"}:
                continue
            state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
            amount = entity.get("amount")
            if amount is None:
                amount = state.get("quantity", state.get("amount", 0))
            if isinstance(amount, (int, float)) and float(amount) > 0 and not state.get("protected"):
                return {"resource_tag": str(entity.get("id")), "location_id": current_id}
        try:
            location = store.get_entity(current_id)
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
            location = store.get_entity(current_id)
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
            location = store.get_entity(current_id)
        except KeyError:
            continue
        for neighbor_id in location.get("routes", []) or []:
            neighbor_id = str(neighbor_id)
            if neighbor_id in visited:
                continue
            visited.add(neighbor_id)
            queue.append(neighbor_id)
    return visited
