from __future__ import annotations

from collections import deque
import heapq
import math
from typing import Any


def configured_location_tags(store: Any, group: str, default: set[str] | list[str] | tuple[str, ...]) -> set[str]:
    """Read semantic location tag groups from the instance package."""

    rules = store.repositories.metadata.get("rules", {}) or {}
    configured = (rules.get("location_tag_groups", {}) or {}).get(group, default)
    values = configured if isinstance(configured, (list, tuple, set)) else [configured]
    return {str(item) for item in values}


def _route_edge_distance(location: dict[str, Any], neighbor_id: str) -> float:
    """Return one configured edge's distance, falling back to one unit.

    ``routes`` remains the topology and ``route_distances`` is optional edge
    data.  Keeping the two fields separate lets existing content use a simple
    list while new worlds can give each connection a different length.
    Invalid or missing values intentionally fall back to one unit; package
    validation reports malformed authored values before an instance starts.
    """

    distances = location.get("route_distances")
    if isinstance(distances, dict):
        value = distances.get(str(neighbor_id))
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0:
            return float(value)
    return 1.0


def route_edge_distance(store: Any, origin_id: str, destination_id: str) -> float:
    """Read one authored edge distance without traversing alternate routes."""

    try:
        location = store.repositories.entities.get_entity(str(origin_id))
    except KeyError:
        return 1.0
    return _route_edge_distance(location, str(destination_id))


def route_distance(store: Any, origin_id: str | None, destination_id: str | None) -> float:
    """Find the shortest weighted route distance in configured distance units."""

    if not origin_id or not destination_id or str(origin_id) == str(destination_id):
        return 0.0
    origin, destination = str(origin_id), str(destination_id)
    queue: list[tuple[float, str]] = [(0.0, origin)]
    distances: dict[str, float] = {origin: 0.0}
    while queue:
        current_distance, current = heapq.heappop(queue)
        if current_distance > distances.get(current, float("inf")):
            continue
        if current == destination:
            return current_distance
        try:
            location = store.repositories.entities.get_entity(current)
        except KeyError:
            continue
        for neighbor in location.get("routes", []) or []:
            neighbor_id = str(neighbor)
            edge_distance = _route_edge_distance(location, neighbor_id)
            candidate = current_distance + edge_distance
            if candidate < distances.get(neighbor_id, float("inf")):
                distances[neighbor_id] = candidate
                heapq.heappush(queue, (candidate, neighbor_id))
    # Preserve the old behavior for an unreachable destination: the caller
    # will reject the route through the Action preconditions, while duration
    # projection stays finite and useful for diagnostics.
    return 1.0


def _configured_travel_rules(store: Any) -> dict[str, Any]:
    rules = store.repositories.metadata.get("rules", {}) or {}
    travel = rules.get("travel", {})
    return dict(travel) if isinstance(travel, dict) else {}


def movement_speed(store: Any, agent: dict[str, Any] | None = None, parameters: dict[str, Any] | None = None) -> float:
    """Resolve an actor's configured movement speed.

    Speed is content data.  A package may declare an agent state attribute and
    a transport parameter/state key in ``rules.travel``.  A transport entity
    can expose the configured ``transport_speed_field`` in its top-level data
    or ``initial_state``.  If neither exists, one speed unit is used, so route
    distance still deterministically converts to time.
    """

    travel = _configured_travel_rules(store)
    default_speed = travel.get("default_speed", 1.0)
    try:
        speed = float(default_speed)
    except (TypeError, ValueError):
        speed = 1.0
    speed = speed if math.isfinite(speed) and speed > 0 else 1.0
    agent = agent if isinstance(agent, dict) else {}
    state = agent.get("initial_state") if isinstance(agent.get("initial_state"), dict) else {}
    parameters = parameters if isinstance(parameters, dict) else {}

    transport_key = str(travel.get("transport_parameter") or "")
    state_transport_key = str(travel.get("transport_state_key") or "")
    transport_id = (parameters.get(transport_key) if transport_key else None) or (state.get(state_transport_key) if state_transport_key else None)
    speed_field = str(travel.get("transport_speed_field") or "")
    actor_speed: float | None = None
    if agent_speed_key := travel.get("agent_speed_attribute"):
        candidate = state.get(str(agent_speed_key))
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool) and math.isfinite(float(candidate)) and float(candidate) > 0:
            actor_speed = float(candidate)
            speed = actor_speed

    transport_speed: float | None = None
    if transport_id:
        try:
            transport = store.repositories.entities.get_entity(str(transport_id))
        except KeyError:
            transport = {}
        transport_state = transport.get("initial_state") if isinstance(transport.get("initial_state"), dict) else {}
        if speed_field:
            candidate = transport.get(speed_field, transport_state.get(speed_field))
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool) and math.isfinite(float(candidate)) and float(candidate) > 0:
                transport_speed = float(candidate)
    if transport_speed is not None:
        if str(travel.get("transport_speed_mode") or "absolute") == "multiplier":
            speed = (actor_speed or speed) * transport_speed
        else:
            # A selected vehicle's speed is an absolute movement speed by
            # default. Worlds that model vehicles as bonuses can opt into the
            # declarative multiplier mode above.
            speed = transport_speed
    return speed


def route_minutes(store: Any, origin_id: str | None, destination_id: str | None, *, agent: dict[str, Any] | None = None, parameters: dict[str, Any] | None = None) -> int:
    """Convert weighted route distance to actor-specific simulated minutes."""

    distance = route_distance(store, origin_id, destination_id)
    if distance <= 0:
        return 0
    rules = store.repositories.metadata.get("rules", {}) or {}
    travel = _configured_travel_rules(store)
    per_distance = travel.get("minutes_per_distance", rules.get("route_minutes_per_hop", 1))
    try:
        per_distance_value = float(per_distance)
    except (TypeError, ValueError):
        per_distance_value = 1.0
    if not math.isfinite(per_distance_value) or per_distance_value <= 0:
        per_distance_value = 1.0
    return max(1, int(round(distance * per_distance_value / movement_speed(store, agent, parameters))))


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
        public_safe = bool((store.repositories.metadata.get("rules", {}) or {}).get("public_locations_are_safe", True))
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
    rules = store.repositories.metadata.get("rules", {}) or {}
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
