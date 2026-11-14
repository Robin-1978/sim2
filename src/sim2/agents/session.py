from __future__ import annotations

from dataclasses import dataclass, field
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
