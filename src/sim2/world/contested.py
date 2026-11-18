"""Pure helpers for generic contested-resource behavior definitions."""

from __future__ import annotations

from typing import Any


def resource_take_effect(
    behavior: dict[str, Any],
    action_defs: dict[str, dict[str, Any]],
    behavior_defs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return the first configured ``resource_take`` effect in one behavior.

    Nested behavior nodes are followed only when the caller supplies the full
    behavior catalog.  Platform effect handlers stay independent of any world
    resource name.
    """

    def visit(nodes: list[Any], owner_id: str, seen: set[str]) -> dict[str, Any] | None:
        for raw in nodes or []:
            node = raw if isinstance(raw, dict) else {"type": "behavior", "behavior_id": raw}
            node_type = str(node.get("type") or "")
            if node_type == "action":
                action = action_defs.get(str(node.get("action_id"))) or {}
                for effect in action.get("effects", []) or []:
                    if isinstance(effect, dict) and str(effect.get("type") or effect.get("op") or "") == "resource_take":
                        return dict(effect)
            elif node_type in {"sequence", "parallel", "root", "selector"}:
                found = visit(list(node.get("children") or []), owner_id, seen)
                if found:
                    return found
            elif node_type in {"behavior", "subtree"} and node.get("behavior_id") and behavior_defs:
                nested_id = str(node["behavior_id"])
                if nested_id not in seen:
                    nested = behavior_defs.get(nested_id) or {}
                    found = visit(list(nested.get("nodes") or []), nested_id, seen | {nested_id})
                    if found:
                        return found
        return None

    return visit(list(behavior.get("nodes") or []), str(behavior.get("id") or ""), {str(behavior.get("id") or "")})
