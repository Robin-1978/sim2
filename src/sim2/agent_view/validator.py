from __future__ import annotations

from typing import Any


REQUIRED_VIEW_FIELDS = {
    "role",
    "world_time",
    "self",
    "identity",
    "needs",
    "drives",
    "executable_behaviors",
    "tools",
}


def validate_agent_view(view: dict[str, Any], *, role: str | None = None) -> list[str]:
    """Validate the compact world-to-Agent contract before an LLM call."""

    errors: list[str] = []
    missing = sorted(REQUIRED_VIEW_FIELDS - set(view))
    errors.extend(f"missing context field: {field}" for field in missing)
    if role and view.get("role") != role:
        errors.append("context role does not match the session role")
    if not isinstance(view.get("world_time"), dict):
        errors.append("world_time must be an object")
    if not isinstance(view.get("self"), dict):
        errors.append("self must be an object")
    behaviors = view.get("executable_behaviors")
    if not isinstance(behaviors, list):
        errors.append("executable_behaviors must be a list")
    else:
        refs: set[str] = set()
        for behavior in behaviors:
            if not isinstance(behavior, dict):
                errors.append("behavior entry must be an object")
                continue
            ref = behavior.get("ref")
            if not isinstance(ref, str) or not ref:
                errors.append("behavior entry is missing ref")
            elif ref in refs:
                errors.append(f"duplicate behavior ref: {ref}")
            refs.add(ref)
            if not isinstance(behavior.get("choice"), str) or not behavior.get("choice", "").strip():
                errors.append(f"behavior {ref or '<unknown>'} is missing choice")
            for field in ("purpose", "when", "requires", "time", "cost", "impact", "success", "failure"):
                if not behavior.get(field):
                    errors.append(f"behavior {ref or '<unknown>'} is missing {field}")
    tools = view.get("tools")
    if not isinstance(tools, list):
        errors.append("tools must be a list")
    else:
        for tool in tools:
            if not isinstance(tool, dict) or not tool.get("name") or not tool.get("purpose") or not isinstance(tool.get("parameters"), dict):
                errors.append("tool entry must include name, purpose and parameters")
    return errors
