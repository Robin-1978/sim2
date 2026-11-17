"""Limits and de-duplication applied before a model tool request runs.

A rejection is an ordinary tool result, so the model reads the reason and
corrects its next request inside the same bounded ReAct session.
"""

from __future__ import annotations

import json
from typing import Any

from .session import ReactSession


def read_signature(tool_name: str, arguments: dict[str, Any]) -> str:
    """Normalize a read request so reworded repeats are visible to the guard."""

    name = str(tool_name)
    if name in {"observe_nearby", "listen"}:
        return name
    if name == "inspect_entity":
        return f"{name}:{str(arguments.get('entity') or '').casefold().strip()}"
    if name == "read_public_record":
        return f"{name}:{str(arguments.get('record') or '').casefold().strip()}"
    if name == "preview_behavior":
        return f"{name}:{str(arguments.get('choice') or '').casefold().strip()}"
    normalized = {
        str(key): " ".join(str(value).casefold().split())
        for key, value in sorted(arguments.items())
        if key != "question"
    }
    return f"{name}:{json.dumps(normalized, ensure_ascii=False, sort_keys=True)}"


def screen_tool_call(
    session: ReactSession,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    is_read_tool: bool,
    repeats: int,
) -> dict[str, Any] | None:
    """Return the rejection for a request that no longer fits the session, or ``None`` to run it.

    The checks run in a fixed order: tool budget, read budget, repeated read,
    repeated request, interaction tools, task tools, and the world-review rule
    against re-recording the triggering event.  ``repeats`` is how often this
    exact request has now been made (see ``ReactSession.register_call``).
    """

    if session.tool_calls_used > session.max_tool_calls:
        return {"status": "rejected", "tool": tool_name, "failed_check": "tool_budget", "message": "Tool-call budget exhausted; submit one final Intent now."}
    if is_read_tool and session.read_tool_calls > session.read_budget:
        return {"status": "rejected", "tool": tool_name, "failed_check": "read_budget", "message": "Read budget exhausted; use the facts already collected and submit one final Intent."}
    if is_read_tool and read_signature(tool_name, arguments) in session.read_signatures:
        return {"status": "rejected", "tool": tool_name, "failed_check": "repeated_read", "retryable": True, "message": "This scene, entity or behavior was already read in this session. Use the existing result and submit a behavior or wait."}
    if repeats > session.max_repeated_tool_calls:
        return {"status": "rejected", "tool": tool_name, "failed_check": "repeated_tool_call", "message": "The same tool request was repeated; use the previous result or choose the previous result."}
    if session.interaction_tools is not None and tool_name not in session.interaction_tools:
        return {
            "status": "rejected",
            "tool": tool_name,
            "failed_check": "interaction_tool",
            "retryable": True,
            "message": "This interaction exposes only its direct reply or trade decision tools; return wait or use one listed tool.",
        }
    if session.session_tools is not None and tool_name not in session.session_tools:
        return {
            "status": "rejected",
            "tool": tool_name,
            "failed_check": "task_tool",
            "retryable": True,
            "message": "This event task already includes its validated evidence. Use one listed submission tool or return wait.",
        }
    if (
        session.role == "god"
        and (str(session.task_type).startswith("trigger:") or str(session.task_type).startswith("event:"))
        and tool_name == "propose_world_change"
        and arguments.get("change_type") == "event"
        and not arguments.get("property")
    ):
        return {"status": "rejected", "tool": tool_name, "failed_check": "event_already_recorded", "retryable": True, "message": "The triggering event is already recorded. Do not add a narrative acknowledgement; return wait or submit one typed rule, progress, entity, relationship, or faction proposal grounded in the observed result."}
    return None
