from __future__ import annotations

import json
from typing import Any

def parse_tool_call(response: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """Normalize JSON and OpenAI tool-call shapes into one call."""

    candidate: Any = response.get("tool_call")
    if candidate is None:
        candidate = response.get("function_call")
    if candidate is None and response.get("tool_calls"):
        candidate = response["tool_calls"][0]
    # Several OpenAI-compatible free models use the Anthropic/SDK-style
    # ``tool_uses`` envelope in JSON mode even when native tools were not
    # negotiated.  Treat it as a prompt-level call; the Engine will append
    # the result as ordinary ReAct context and will not fabricate a native
    # provider correlation id.
    if candidate is None and response.get("tool_uses"):
        candidate = response["tool_uses"][0]
    if candidate is None and response.get("type") == "tool_call":
        candidate = response
    # Longcat and a few other compatible endpoints flatten a call as
    # ``{"tool": "name", "parameters": {...}}``.  This is still a tool
    # request, not a world Intent; normalize it before the role and Platform
    # validation layers see it.
    if candidate is None and isinstance(response.get("tool"), str):
        candidate = response
    # Some OpenAI-compatible gateways flatten one function call to
    # ``{"name": ..., "arguments": {...}}``.  Accept that wire shape at the
    # protocol boundary; the role and schema checks still happen below.
    if candidate is None and isinstance(response.get("name"), str) and "arguments" in response:
        candidate = response
    if isinstance(candidate, list):
        candidate = candidate[0] if candidate else None
    if not isinstance(candidate, dict):
        return None
    function = candidate.get("function") if isinstance(candidate.get("function"), dict) else candidate
    name = (
        function.get("name")
        or candidate.get("name")
        or candidate.get("tool")
        or (candidate.get("function") if isinstance(candidate.get("function"), str) else None)
    )
    arguments = function.get(
        "arguments",
        function.get(
            "args",
            candidate.get(
                "arguments",
                candidate.get("parameters", candidate.get("params", candidate.get("input", {}))),
            ),
        ),
    )
    # Some JSON-mode adapters put the arguments beside the tool name rather
    # than under ``arguments``/``params``.  Keep only fields that are not
    # protocol envelope metadata; the normal tool-schema validator still
    # rejects anything unknown for the selected tool.
    if not arguments and isinstance(candidate, dict):
        envelope_fields = {
            "tool", "name", "type", "id", "function", "arguments", "args",
            "parameters", "params", "input",
        }
        arguments = {key: value for key, value in candidate.items() if key not in envelope_fields}
    if not isinstance(name, str) or not name:
        return None
    if isinstance(arguments, str):
        arguments = json.loads(arguments or "{}")
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    return name, arguments
