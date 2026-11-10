from __future__ import annotations

from collections import deque
from typing import Any


def build_map_view(store: Any, *, viewer_id: str | None = None, observer: bool = False) -> dict[str, Any]:
    """Build a transport-neutral topology projection from SQLite world data.

    Locations and routes remain content data.  The client receives visible
    nodes, undirected edges and current occupants; it never reads package YAML
    or infers world-specific location names.
    """

    locations: list[dict[str, Any]] = []
    for row in store.entities_of_type("location"):
        location = store.get_entity(str(row["id"]))
        visibility = location.get("public", True)
        if visibility == "hidden":
            continue
        if not observer and viewer_id and not _player_can_see_location(store, viewer_id, location):
            continue
        position = location.get("map_position") or location.get("position")
        locations.append({
            "id": str(location["id"]),
            "name": str(location.get("name") or location["id"]),
            "tags": [str(item) for item in (location.get("tags") or [])],
            "visibility": visibility,
            "safe": bool(location.get("safe", False)),
            "position": dict(position) if isinstance(position, dict) else None,
            "routes": [str(item) for item in (location.get("routes") or [])],
            "occupants": [],
        })

    visible_ids = {item["id"] for item in locations}
    nodes = {item["id"]: item for item in locations}
    for agent in store.list_agents():
        state = agent.get("initial_state") if isinstance(agent.get("initial_state"), dict) else {}
        location_id = str(state.get("location_id") or "")
        if location_id in nodes:
            nodes[location_id]["occupants"].append({
                "id": str(agent["id"]),
                "name": str(agent.get("name") or agent["id"]),
                "status": str(state.get("status") or "idle"),
                "active_behavior": state.get("active_behavior_id"),
            })

    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for node in locations:
        for destination in node["routes"]:
            if destination not in visible_ids or destination == node["id"]:
                continue
            pair = tuple(sorted((node["id"], destination)))
            if pair in seen:
                continue
            seen.add(pair)
            edges.append({"from": pair[0], "to": pair[1]})

    return {
        "status": "ok",
        "world_id": store.get_meta("world_id"),
        "world_minute": int(store.get_meta("world_minute", 0)),
        "nodes": sorted(locations, key=lambda item: item["id"]),
        "edges": sorted(edges, key=lambda item: (item["from"], item["to"])),
        "viewer": "observer" if observer else viewer_id,
    }


def _player_can_see_location(store: Any, viewer_id: str, location: dict[str, Any]) -> bool:
    """Keep the player map within public and personally known geography."""

    location_id = str(location.get("id") or "")
    try:
        agent = store.get_agent(str(viewer_id))
    except KeyError:
        return False
    state = agent.get("initial_state") if isinstance(agent.get("initial_state"), dict) else {}
    if str(state.get("location_id") or "") == location_id:
        return True
    if location.get("public") is True:
        return True
    for fact in store.list_knowledge_facts(str(viewer_id), limit=1000):
        key = str(fact.get("fact_key") or "")
        if location_id and location_id in key.split(":"):
            return True
        text = str(fact.get("fact") or "")
        if location_id and location_id in text:
            return True
    return False


def render_topology_map(view: dict[str, Any]) -> str:
    """Render a compact spanning-tree map plus explicit cross routes.

    This representation remains readable in narrow terminals and for worlds
    without authored coordinates.  Clients that want a canvas can use the
    same nodes/edges JSON and optional node positions.
    """

    nodes = {str(item["id"]): item for item in (view.get("nodes") or []) if isinstance(item, dict) and item.get("id")}
    if not nodes:
        return "No visible locations."
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for edge in view.get("edges") or []:
        left, right = str(edge.get("from") or ""), str(edge.get("to") or "")
        if left in adjacency and right in adjacency:
            adjacency[left].add(right)
            adjacency[right].add(left)

    root = max(nodes, key=lambda node_id: (len(adjacency[node_id]), nodes[node_id].get("safe", False), node_id))
    parent: dict[str, str | None] = {root: None}
    children: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    queue: deque[str] = deque([root])
    while queue:
        current = queue.popleft()
        for neighbor in sorted(adjacency[current], key=lambda value: str(nodes[value].get("name") or value)):
            if neighbor in parent:
                continue
            parent[neighbor] = current
            children[current].append(neighbor)
            queue.append(neighbor)
    # Disconnected regions are legitimate.  Show each as another root.
    roots = [root]
    for node_id in sorted(nodes):
        if node_id not in parent:
            parent[node_id] = None
            roots.append(node_id)

    lines: list[str] = []

    def label(node_id: str) -> str:
        node = nodes[node_id]
        occupants = ", ".join(str(item.get("name") or item.get("id")) for item in node.get("occupants") or [])
        flags = []
        if node.get("safe"):
            flags.append("safe")
        if occupants:
            flags.append(occupants)
        suffix = f"  [{'; '.join(flags)}]" if flags else ""
        return f"{node.get('name', node_id)}{suffix}"

    def visit(node_id: str, prefix: str, last: bool, is_root: bool = False) -> None:
        connector = "" if is_root else ("└─ " if last else "├─ ")
        lines.append(f"{prefix}{connector}● {label(node_id)}")
        next_prefix = prefix + ("   " if last and not is_root else "│  " if not is_root else "")
        values = children[node_id]
        for index, child in enumerate(values):
            visit(child, next_prefix, index == len(values) - 1)

    for index, root_id in enumerate(roots):
        if index:
            lines.append("")
        visit(root_id, "", True, True)

    tree_edges = {tuple(sorted((node_id, parent_id))) for node_id, parent_id in parent.items() if parent_id}
    cross = []
    for edge in view.get("edges") or []:
        pair = tuple(sorted((str(edge.get("from")), str(edge.get("to")))))
        if pair not in tree_edges:
            cross.append(f"{nodes[pair[0]].get('name', pair[0])} ↔ {nodes[pair[1]].get('name', pair[1])}")
    if cross:
        lines.extend(["", "Other routes:", *[f"  • {item}" for item in sorted(set(cross))]])
    return "\n".join(lines)
