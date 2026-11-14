from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any


@dataclass(frozen=True)
class AgentTurn:
    role: str
    task_type: str
    response: dict[str, Any]
    result: dict[str, Any]
    iterations: int
    transcript: list[dict[str, Any]] = field(default_factory=list)
    initial_context: dict[str, Any] = field(default_factory=dict)
    prompt_history: list[str] = field(default_factory=list)


def model_tool_result(tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Return the bounded tool projection sent back through the session."""

    def compact(value: Any, *, key: str = "", depth: int = 0) -> Any:
        if depth > 4:
            return "[details omitted]"
        if isinstance(value, float):
            return round(value, 4)
        if isinstance(value, (str, int, bool)) or value is None:
            return value[:497] + "..." if isinstance(value, str) and len(value) > 500 else value
        if isinstance(value, list):
            return [compact(item, depth=depth + 1) for item in value[:8]]
        if not isinstance(value, dict):
            return str(value)
        if key in {"last_outcome", "outcome_history", "transcript", "prompt_history", "data_json"}:
            return "[details omitted]"
        drop = {"id", "intent_id", "execution_id", "event_id", "entity_id", "action_instance_id", "active_bt_instance", "active_action_instance"}
        output: dict[str, Any] = {}
        for child_key, child_value in value.items():
            normalized = str(child_key)
            if normalized in drop or normalized.endswith("_id"):
                continue
            if normalized in {"initial_state", "state"} and isinstance(child_value, dict):
                state_keys = {"status", "location_id", "active_behavior", "active_action", "remaining_minutes", "energy", "attention", "health", "exposure", "threat_level", "reputation"}
                child_value = {k: v for k, v in child_value.items() if k in state_keys}
            output[normalized] = compact(child_value, key=normalized, depth=depth + 1)
        return output

    compacted = compact(result)
    if not isinstance(compacted, dict):
        compacted = {"status": "ok", "result": compacted}
    compacted.setdefault("tool", str(tool_name))
    if len(json.dumps(compacted, ensure_ascii=False, separators=(",", ":"))) > 6000:
        compacted = {key: compacted[key] for key in ("status", "message", "failed_check", "errors", "invalid_choices", "available_choices", "choice", "summary", "remaining_minutes", "tool") if key in compacted}
        compacted["message"] = str(compacted.get("message") or "Detailed tool output omitted; use the visible facts already collected.")
    return compacted


def trim_conversation_messages(messages: list[dict[str, Any]], *, max_tail: int = 6) -> list[dict[str, Any]]:
    """Keep a bounded valid native-tool conversation tail."""

    if len(messages) <= 2 + max(1, int(max_tail)):
        return list(messages)
    prefix = list(messages[:2])
    tail = list(messages[2:])
    limit = max(1, int(max_tail))
    while len(tail) > limit:
        removed = tail.pop(0)
        if removed.get("role") == "assistant" and removed.get("tool_calls"):
            while tail and tail[0].get("role") == "tool":
                tail.pop(0)
        while tail and tail[0].get("role") == "tool":
            tail.pop(0)
    # Never leave a tool result without its assistant declaration.
    if tail and tail[0].get("role") == "tool":
        while tail and tail[0].get("role") == "tool":
            tail.pop(0)
    return prefix + tail
