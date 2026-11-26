"""Provider wire protocol owned by the Agent runtime.

The provider adapter may expose several OpenAI-compatible method signatures.
This module normalizes that transport boundary without changing an Intent's
meaning. It never persists model-supplied tool IDs as world identifiers and
never mutates the provider's response object in place.
"""

from __future__ import annotations

import copy
import inspect
from dataclasses import dataclass
from typing import Any

from ..llm.budget import DEFAULT_CONTEXT_WINDOW_TOKENS, request_metrics
from ..llm.config import DEFAULT_MAX_COMPLETION_TOKENS


@dataclass(frozen=True)
class ProviderReply:
    payload: dict[str, Any]
    usage: dict[str, Any]


class ProviderProtocol:
    """Call one configured provider and normalize its response envelope."""

    def __init__(self, provider: Any) -> None:
        self.provider = provider

    def prepare(
        self,
        *,
        role: str,
        system: str,
        context: str,
        messages: list[dict[str, Any]] | None = None,
        allowed_tools: list[str] | set[str] | None = None,
    ) -> dict[str, Any]:
        method = self.provider.complete_json
        kwargs: dict[str, Any] = {"system": system, "context": context}
        try:
            parameters = inspect.signature(method).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "tools" in parameters:
            # Imported lazily to keep this low-level module independent from
            # tool execution and to avoid a registry/protocol import cycle.
            from ..agent_view.tool_registry import tool_schemas_for_role

            kwargs["tools"] = tool_schemas_for_role(role, allowed_tools)
        if "role" in parameters:
            kwargs["role"] = role
        if "messages" in parameters:
            kwargs["messages"] = messages
        return kwargs

    def budget(self, request: dict[str, Any]) -> dict[str, int]:
        measure = getattr(self.provider, "completion_budget", None)
        if callable(measure):
            return measure(**{key: request[key] for key in ("system", "context", "tools", "messages") if key in request})
        return request_metrics(
            request.get("messages") or [
                {"role": "system", "content": request["system"]},
                {"role": "user", "content": request["context"]},
            ], request.get("tools"),
            window=int(getattr(self.provider, "context_window_tokens", DEFAULT_CONTEXT_WINDOW_TOKENS)),
            completion=int(getattr(self.provider, "max_completion_tokens", DEFAULT_MAX_COMPLETION_TOKENS)),
        )

    def complete(self, **kwargs: Any) -> ProviderReply:
        return self.complete_prepared(self.prepare(**kwargs))

    def complete_prepared(self, request: dict[str, Any]) -> ProviderReply:
        response = self.provider.complete_json(**request)
        if not isinstance(response, dict):
            raise ValueError("LLM response must be a JSON object")
        wire = copy.deepcopy(response)
        usage = wire.pop("_usage", {})
        return ProviderReply(wire, dict(usage) if isinstance(usage, dict) else {})


def readable_model_response(response: dict[str, Any]) -> dict[str, Any]:
    """Decode nested native-tool argument JSON for operator-facing copies."""

    readable = copy.deepcopy(response)
    candidates: list[Any] = []
    for key in ("tool_calls", "tool_uses"):
        value = readable.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    for key in ("tool_call", "function_call"):
        value = readable.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    for call in candidates:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else call
        arguments = function.get("arguments") if isinstance(function, dict) else None
        if not isinstance(arguments, str):
            continue
        try:
            import json

            parsed = json.loads(arguments)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            function["arguments"] = parsed
    return readable


def normalize_provider_intent(role: str, data: dict[str, Any]) -> dict[str, Any]:
    """Normalize harmless provider wrappers without changing intent meaning."""

    normalized = dict(data)
    if role != "agent_decision":
        return normalized
    nested = normalized.get("parameters")
    nested_choice = nested.get("choice") if isinstance(nested, dict) else None
    choice_detail = normalized.get("choice_detail") if isinstance(normalized.get("choice_detail"), dict) else {}
    choice = normalized.get("choice")
    intent_names = {"request_behavior", "select_behavior"}
    if isinstance(choice, str) and choice.casefold() in intent_names:
        candidate = (
            nested_choice
            or choice_detail.get("behavior_name")
            or choice_detail.get("choice")
            or normalized.get("behavior")
            or normalized.get("behavior_name")
        )
        if isinstance(candidate, str) and candidate.strip():
            normalized["choice"] = candidate
            if isinstance(nested, dict):
                for key in ("target", "reason", "confidence"):
                    if normalized.get(key) in (None, "") and nested.get(key) not in (None, ""):
                        normalized[key] = nested.get(key)
                normalized["parameters"] = {
                    key: value for key, value in nested.items()
                    if key not in {"choice", "target", "reason", "confidence"}
                }
    elif not isinstance(choice, str) or not choice.strip():
        candidate = normalized.get("behavior") or normalized.get("behavior_name")
        if isinstance(candidate, str) and candidate.strip():
            normalized["choice"] = candidate
    target = normalized.get("target")
    if isinstance(target, str) and target.strip():
        normalized["target"] = {"name": target.strip()}
    elif isinstance(target, dict):
        target_copy = dict(target)
        if not target_copy.get("name"):
            for key, value in target_copy.items():
                if str(key).casefold() in {"participant", "entity", "target"} and isinstance(value, str) and value.strip():
                    target_copy["name"] = value.strip()
                    break
        normalized["target"] = target_copy
    return normalized

