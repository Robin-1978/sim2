"""Estimate the full input and reserve room for model output."""

from __future__ import annotations

import json
from typing import Any

from ..prompting.compaction import estimate_tokens


DEFAULT_CONTEXT_WINDOW_TOKENS = 32768


def request_metrics(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
    *, window: int, completion: int, response_schema: Any = None,
) -> dict[str, int]:
    def size(value: Any) -> int:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        # The renderer's estimate is for prose. JSON includes short keys and
        # punctuation, and unusual Unicode must leave room for byte tokens.
        return max(estimate_tokens(encoded), (len(encoded.encode("utf-8")) + 2) // 3)

    message_tokens = size(messages) + 4 * len(messages)
    tool_tokens = size(tools) if tools else 0
    schema_tokens = size(response_schema) if response_schema else 0
    input_tokens = message_tokens + tool_tokens + schema_tokens
    headroom = max(128, (input_tokens + 9) // 10)
    return {
        "context_window_tokens": window,
        "request_message_tokens_est": message_tokens,
        "request_tool_tokens_est": tool_tokens,
        "request_response_schema_tokens_est": schema_tokens,
        "request_input_tokens_est": input_tokens,
        "completion_reserved_tokens": completion,
        "request_headroom_tokens": headroom,
        "request_total_tokens_est": input_tokens + completion + headroom,
    }


class ContextBudgetExceeded(ValueError):
    """A request would exceed the configured context window before transport."""
