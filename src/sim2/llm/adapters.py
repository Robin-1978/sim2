from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ModelProtocolError(ValueError):
    """The provider returned neither a usable tool call nor JSON content."""

    def __init__(self, message: str, *, usage: dict[str, Any] | None = None):
        super().__init__(message)
        self.usage = dict(usage or {})


@dataclass(frozen=True)
class ModelAdapter:
    name: str = "openai_compatible"
    native_tools: bool = True
    json_mode: bool = True

    def prepare_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload

    def normalize_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Normalize provider-specific response envelopes at the adapter boundary."""

        return response


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


@dataclass(frozen=True)
class IcemanAdapter(ModelAdapter):
    """Iceman accepts native tools but rejects OpenAI JSON response_format."""

    name: str = "iceman"

    def prepare_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        prepared = dict(payload)
        prepared.pop("response_format", None)
        if prepared.get("tools"):
            # Iceman occasionally emits plain prose when tool use is optional.
            # A required call is safe because every role exposes the side
            # effect-free wait tool and keeps the protocol bounded.
            prepared["tool_choice"] = "required"
        return prepared


@dataclass(frozen=True)
class LongCatAdapter(ModelAdapter):
    """Normalize LongCat's bare ``{function_name: arguments}`` envelope.

    LongCat may return a function-shaped JSON object without the OpenAI
    ``tool_calls`` wrapper. This is transport normalization, not Agent logic:
    after this method the Agent sees the same intent JSON as every provider.
    Read-only tools are deliberately excluded; only final intent names are
    unwrapped here.
    """

    name: str = "longcat"

    def normalize_response(self, response: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(response, dict) or response.get("intent_type"):
            return response
        aliases = {
            "request_behavior": "select_behavior",
            "select_behavior": "select_behavior",
            "wait": "wait",
            "send_message": "send_message",
            "create_trade_offer": "create_trade_offer",
            "accept_trade_offer": "accept_trade_offer",
            "counter_trade_offer": "counter_trade_offer",
            "reject_trade_offer": "reject_trade_offer",
            "record_experience": "record_experience",
            "update_capability": "update_capability",
            "update_belief": "update_belief",
            "update_goal": "update_goal",
            "propose_behavior": "propose_behavior",
            "propose_institution": "propose_institution",
            "record_world_progress": "record_world_progress",
            "propose_entity": "propose_entity",
            "propose_definition": "propose_definition",
            "propose_world_change": "propose_world_change",
            "approve_institution": "approve_institution",
            "revise_institution": "revise_institution",
            "propose_story_beat": "propose_story_beat",
            "submit_world_template": "submit_world_template",
        }
        for wrapper, intent_type in aliases.items():
            inner = response.get(wrapper)
            if isinstance(inner, dict):
                normalized = dict(inner)
                normalized.setdefault("intent_type", intent_type)
                return normalized
        return response


@dataclass(frozen=True)
class BeamAdapter(ModelAdapter):
    """Normalize Beam's occasional whitespace-padded JSON object keys."""

    name: str = "beam"

    @classmethod
    def _clean(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key).strip(): cls._clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._clean(item) for item in value]
        return value

    def normalize_response(self, response: dict[str, Any]) -> dict[str, Any]:
        return self._clean(response)


def resolve_adapter(provider_type: str, base_url: str, model: str, hint: str | None = None) -> ModelAdapter:
    key = str(hint or "").casefold()
    if key in {"deepseek", "deepseek_chat"} or "deepseek" in str(model).casefold():
        return DeepSeekAdapter()
    if key in {"iceman", "iceman_chat"} or "iceman" in str(model).casefold():
        return IcemanAdapter()
    if key in {"longcat", "longcat_chat"} or "longcat" in str(model).casefold():
        return LongCatAdapter()
    if key in {"beam", "beam_chat"} or "beam" in str(model).casefold():
        return BeamAdapter()
    return ModelAdapter()
