from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import json
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .tool_executor import AgentToolExecutor

# State every character has regardless of the world's attribute schema.
STRUCTURAL_STATE_KEYS = frozenset({"status", "location_id", "active_behavior", "active_action", "remaining_minutes"})


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
    context_metrics: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class TaskContext:
    """The AgentView and tool allow-lists compiled for one task, before its session starts."""

    context: dict[str, Any]
    state_attributes: list[str]
    event_ids: list[int]
    trigger_contract: Any
    interaction_tools: list[str] | None
    session_tools: list[str] | None


def call_key(tool_name: str, arguments: dict[str, Any]) -> str:
    """Identify one tool request by name and exact arguments."""

    return f"{tool_name}:{repr(sorted(arguments.items()))}"


@dataclass
class ReactSession:
    """The state one bounded ReAct session mutates; ``AgentEngine.run`` creates exactly one.

    The limits are copied from the engine when the session starts, so the tool
    guards and counters read this object instead of a dozen loop locals.
    """

    role: str
    actor_id: str
    task_type: str
    task: str
    system_policy: str
    context: dict[str, Any]
    initial_context: dict[str, Any]
    executor: AgentToolExecutor
    event_ids: list[int]
    state_attributes: list[str]
    interaction_tools: list[str] | None
    session_tools: list[str] | None
    messages: list[dict[str, Any]]
    max_tool_calls: int
    max_repeated_tool_calls: int
    read_budget: int
    last_response: dict[str, Any] = field(default_factory=dict)
    last_result: dict[str, Any] = field(default_factory=lambda: {"status": "no_result"})
    stage_index: int = 0
    transcript: list[dict[str, Any]] = field(default_factory=list)
    prompt_history: list[str] = field(default_factory=list)
    context_metrics: list[dict[str, Any]] = field(default_factory=list)
    repeated: dict[str, int] = field(default_factory=dict)
    read_signatures: set[str] = field(default_factory=set)
    tool_calls_used: int = 0
    read_tool_calls: int = 0
    accepted_proposals: int = 0

    def register_call(self, tool_name: str, arguments: dict[str, Any], *, is_read_tool: bool) -> int:
        """Count one requested tool call and return how often this exact request was made."""

        self.tool_calls_used += 1
        if is_read_tool:
            self.read_tool_calls += 1
        key = call_key(tool_name, arguments)
        self.repeated[key] = self.repeated.get(key, 0) + 1
        return self.repeated[key]

    def finish(self, result: dict[str, Any], *, iterations: int, response: dict[str, Any] | None = None) -> AgentTurn:
        """Close the session with ``result``; the response defaults to the last readable one."""

        return AgentTurn(
            self.role, self.task_type, self.last_response if response is None else response,
            result, iterations, self.transcript, self.initial_context, self.prompt_history,
            self.context_metrics,
        )


def model_tool_result(tool_name: str, result: dict[str, Any], *, state_attributes: Iterable[str] = ()) -> dict[str, Any]:
    """Return the bounded tool projection sent back through the session.

    An entity's ``state`` keeps the structural fields every character has plus
    the numeric attributes the world declares (``state_attributes``); it never
    forwards other state, which can be large or private.
    """

    state_keys = STRUCTURAL_STATE_KEYS | {str(item) for item in state_attributes}

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
