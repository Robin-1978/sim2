from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ModelProtocolError(ValueError):
    """The provider returned neither a usable tool call nor JSON content."""


@dataclass(frozen=True)
class ModelAdapter:
    name: str = "openai_compatible"
    native_tools: bool = True
    json_mode: bool = True

    def prepare_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload


@dataclass(frozen=True)
class DeepSeekAdapter(ModelAdapter):
    name: str = "deepseek"

    def prepare_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        prepared = dict(payload)
        # DeepSeek documents this field for non-thinking tool calls. It keeps
        # the reasoning channel from replacing the structured tool response.
        prepared.setdefault("thinking", {"type": "disabled"})
        # Tool calls already carry their own JSON Schema. DeepSeek's JSON mode
        # additionally requires the prompt to contain the word JSON, while
        # the documented tool-call flow omits response_format entirely.
        if prepared.get("tools"):
            messages = list(prepared.get("messages") or [])
            if messages and not any("json" in str(item.get("content", "")).casefold() for item in messages if isinstance(item, dict)):
                last = dict(messages[-1])
                last["content"] = f"{last.get('content', '')}\nIf no tool call is needed, return one valid JSON object for the requested intent."
                messages[-1] = last
                prepared["messages"] = messages
            prepared.pop("response_format", None)
        else:
            prepared["response_format"] = {"type": "json_object"}
            messages = list(prepared.get("messages") or [])
            if messages and not any("json" in str(item.get("content", "")).casefold() for item in messages if isinstance(item, dict)):
                last = dict(messages[-1])
                last["content"] = f"{last.get('content', '')}\nReturn one valid JSON object."
                messages[-1] = last
                prepared["messages"] = messages
        return prepared


def resolve_adapter(provider_type: str, base_url: str, model: str, hint: str | None = None) -> ModelAdapter:
    key = str(hint or "").casefold()
    if key in {"deepseek", "deepseek_chat"} or "deepseek" in str(model).casefold():
        return DeepSeekAdapter()
    return ModelAdapter()
