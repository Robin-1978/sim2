"""Resolve effect targets through a behavior tree's bindings.

A capability is generic; a behavior may bind it.  The binding can come from:

* an Agent parameter (``context.resource_id`` in a node, supplied by the
  submitted intent),
* a literal value in an Action node (``resource_id: stored_resource``), or
* a literal or parameterized binding on a nested Behavior node.

Admission checks (authorization and contested feasibility) run before the
activity starts, so they must see the same resolved target that execution
will use.  These helpers are deliberately independent of world content: they
only follow configured nodes and resolve ``context.*`` references.
"""

from __future__ import annotations

from typing import Any, Iterator

CONTEXT_PREFIX = "context."


def resolve_binding(value: Any, parameters: dict[str, Any], _seen: set[str] | None = None) -> Any:
    """Resolve every ``context.<name>`` reference in a configured value.

    A node parameter may itself point at another context name (for example a
    Travel node resolves ``context.resource_location_id`` while the effect
    receives that node parameter as ``context.location_id``).  Resolution is
    therefore recursive, with a guard against a self-referential binding.
    """

    if isinstance(value, str) and value.startswith(CONTEXT_PREFIX):
        name = value.removeprefix(CONTEXT_PREFIX)
        seen = set(_seen or ())
        if name in seen:
            return parameters.get(name)
        seen.add(name)
        resolved = parameters.get(name)
        if isinstance(resolved, str) and resolved.startswith(CONTEXT_PREFIX):
            return resolve_binding(resolved, parameters, seen)
        return resolved
    if isinstance(value, dict):
        return {key: resolve_binding(item, parameters, _seen) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_binding(item, parameters, _seen) for item in value]
    return value


def _merge_node_bindings(
    inherited: dict[str, Any],
    node_parameters: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge one node's bindings without letting a caller override a literal.

    A node parameter written as ``context.x`` is a request to use the current
    binding for ``x``.  A literal node parameter is a fixed binding and wins
    over an Agent-supplied value with the same name.
    """

    merged = dict(inherited)
    for key, value in (node_parameters or {}).items():
        if isinstance(value, str) and value.startswith(CONTEXT_PREFIX):
            name = value.removeprefix(CONTEXT_PREFIX)
            supplied = inherited.get(name)
            if supplied is not None:
                merged[key] = supplied
            else:
                merged[key] = value
        else:
            merged[key] = value
    return merged


def _with_agent_parameters(
    bindings: dict[str, Any],
    parameters: dict[str, Any] | None,
) -> dict[str, Any]:
    """Fill unresolved names from the submitted intent; never replace a binding."""

    merged = dict(bindings)
    for key, value in (parameters or {}).items():
        current = merged.get(str(key))
        if current is None or (isinstance(current, str) and current.startswith(CONTEXT_PREFIX)):
            merged[str(key)] = value
    return merged


def iter_behavior_actions(
    behavior: dict[str, Any],
    action_defs: dict[str, dict[str, Any]],
    behavior_defs: dict[str, dict[str, Any]] | None = None,
    *,
    bindings: dict[str, Any] | None = None,
    seen: set[str] | None = None,
) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Yield ``(action, bindings)`` for every Action reachable from a behavior."""

    if not isinstance(behavior, dict):
        return
    owner_id = str(behavior.get("id") or "")
    visited = set(seen or ())
    if owner_id:
        visited.add(owner_id)
    yield from _visit_nodes(
        list(behavior.get("nodes") or []),
        action_defs,
        behavior_defs or {},
        dict(bindings or {}),
        visited,
    )


def _visit_nodes(
    nodes: list[Any],
    action_defs: dict[str, dict[str, Any]],
    behavior_defs: dict[str, dict[str, Any]],
    bindings: dict[str, Any],
    seen: set[str],
) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    for raw in nodes:
        node = raw if isinstance(raw, dict) else {"type": "behavior", "behavior_id": raw}
        node_type = str(node.get("type") or "")
        if node_type == "action":
            action = action_defs.get(str(node.get("action_id") or ""))
            if isinstance(action, dict):
                yield action, _merge_node_bindings(bindings, node.get("parameters"))
            continue
        if node_type in {"sequence", "parallel", "root", "selector", "condition"}:
            yield from _visit_nodes(
                list(node.get("children") or []),
                action_defs,
                behavior_defs,
                bindings,
                seen,
            )
            continue
        if node_type in {"behavior", "subtree"} and node.get("behavior_id"):
            nested_id = str(node["behavior_id"])
            if nested_id in seen:
                continue
            nested = behavior_defs.get(nested_id)
            if not isinstance(nested, dict):
                continue
            child_bindings = _merge_node_bindings(bindings, node.get("parameters"))
            yield from _visit_nodes(
                list(nested.get("nodes") or []),
                action_defs,
                behavior_defs,
                child_bindings,
                seen | {nested_id},
            )


def iter_behavior_effects(
    behavior: dict[str, Any],
    action_defs: dict[str, dict[str, Any]],
    behavior_defs: dict[str, dict[str, Any]] | None = None,
    parameters: dict[str, Any] | None = None,
) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Yield ``(action, resolved_effect)`` in execution order.

    Node literals win over Agent parameters, while ``context.*`` references
    are resolved from the submitted parameters.  Callers that need to know
    whether an earlier step moves the actor to a target location can inspect
    the yielded effects in order.
    """

    for action, bindings in iter_behavior_actions(behavior, action_defs, behavior_defs):
        merged = _with_agent_parameters(bindings, parameters)
        for effect in action.get("effects", []) or []:
            if not isinstance(effect, dict):
                continue
            yield action, {key: resolve_binding(value, merged) for key, value in effect.items()}


def resolve_behavior_effect(
    behavior: dict[str, Any],
    effect_type: str,
    action_defs: dict[str, dict[str, Any]],
    behavior_defs: dict[str, dict[str, Any]] | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the first matching effect with node and Agent bindings applied."""

    wanted = str(effect_type or "").casefold()
    if not wanted:
        return None
    for _action, effect in iter_behavior_effects(behavior, action_defs, behavior_defs, parameters):
        if str(effect.get("type") or effect.get("op") or "").casefold() == wanted:
            return effect
    return None
