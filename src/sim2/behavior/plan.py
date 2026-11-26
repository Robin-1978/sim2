"""Compile a behavior into forward-only steps for the persisted action cursor."""

from __future__ import annotations

from typing import Any


MAX_EXPANDED_NODES = 4096
MAX_REFERENCE_DEPTH = 64


def wait_minutes(node: dict[str, Any]) -> int:
    minutes = node.get("minutes")
    if type(minutes) is not int or minutes < 1:
        raise ValueError("wait requires a positive integer minutes")
    return minutes


def compile_behavior_plan(
    behavior_id: str, behaviors: dict[str, dict[str, Any]], actions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    expanded_nodes = 0
    behavior = behaviors.get(behavior_id) or {}

    def visit(raw: Any, ancestors: set[str], group: str | None = None) -> None:
        nonlocal expanded_nodes
        expanded_nodes += 1
        if expanded_nodes > MAX_EXPANDED_NODES or len(ancestors) > MAX_REFERENCE_DEPTH:
            raise ValueError("expanded behavior exceeds execution limits")
        node = raw if isinstance(raw, dict) else {"type": "behavior", "behavior_id": raw}
        kind = node.get("type")
        if behavior.get("authorization_operation") and kind in {"condition", "selector", "wait"}:
            raise ValueError("authorization operations use entry_conditions instead of runtime control nodes")
        if group is not None and kind in {"condition", "selector"}:
            raise ValueError(f"{kind} must be outside a parallel group")
        if kind in {"action", "wait"}:
            action_id = node.get("action_id") if kind == "action" else "wait"
            action = actions.get(action_id, {}) if kind == "action" else {
                "id": "wait", "duration": {"minutes": wait_minutes(node)},
                "success": {"result": "wait_completed"},
            }
            steps.append({
                "action_id": action_id,
                "action_version": int(action.get("definition_version", action.get("version", 1)) or 1),
                "definition_snapshot": action,
                "parameters": dict(node.get("parameters") or {}) if kind == "action" else {},
                "parallel_group": group,
                **({"type": "wait"} if kind == "wait" else {}),
            })
        elif kind in {"behavior", "subtree"}:
            nested_id = str(node.get("behavior_id"))
            if nested_id in ancestors:
                raise ValueError(f"cyclic behavior reference: {nested_id}")
            for child in (behaviors.get(nested_id) or {}).get("nodes", []) or []:
                visit(child, ancestors | {nested_id}, group)
        elif kind in {"sequence", "parallel", "root"}:
            child_group = group
            if kind == "parallel" and child_group is None:
                child_group = f"parallel:{len(steps)}"
            for child in node.get("children", []) or []:
                visit(child, ancestors, child_group)
        elif kind == "condition":
            steps.append({"type": "condition", "expression": node.get("expression")})
        elif kind == "selector":
            children = node.get("children", []) or []
            if not children:
                raise ValueError("selector requires children")
            jumps = []
            for child in children[:-1]:
                start = len(steps)
                visit(child, ancestors)
                jump = {"type": "jump"}
                jumps.append(jump)
                next_branch = len(steps) + 1
                for step in steps[start:]:
                    if step.get("type") != "jump":
                        step.setdefault("on_failure", next_branch)
                steps.append(jump)
            visit(children[-1], ancestors)
            for jump in jumps:
                jump["to"] = len(steps)
        else:
            raise ValueError(f"unsupported behavior node type: {kind}")

    for node in behavior.get("nodes", []) or []:
        visit(node, {behavior_id})
    return steps
