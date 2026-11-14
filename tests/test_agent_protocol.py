from __future__ import annotations

from sim2.agents.protocol import ProviderProtocol, normalize_provider_intent, readable_model_response


class Provider:
    max_agent_iterations = 2

    def __init__(self) -> None:
        self.calls = []
        self.response = {
            "tool_calls": [{
                "id": "model-call-id",
                "type": "function",
                "function": {"name": "request_behavior", "arguments": '{"choice":"Rest"}'},
            }],
            "_usage": {"prompt_tokens": 12, "completion_tokens": 3},
        }

    def complete_json(self, *, system, context, tools=None, messages=None, role=None):
        self.calls.append({"system": system, "context": context, "tools": tools, "messages": messages, "role": role})
        return self.response


def test_provider_protocol_copies_wire_response_and_extracts_usage() -> None:
    provider = Provider()
    reply = ProviderProtocol(provider).complete(
        role="agent_decision",
        system="policy",
        context="snapshot",
        messages=[{"role": "user", "content": "snapshot"}],
        allowed_tools=["request_behavior"],
    )
    assert reply.payload["tool_calls"][0]["id"] == "model-call-id"
    assert reply.usage == {"prompt_tokens": 12, "completion_tokens": 3}
    assert "_usage" not in reply.payload
    assert "_usage" in provider.response
    assert provider.calls[0]["role"] == "agent_decision"


def test_readable_response_decodes_arguments_without_mutating_wire_response() -> None:
    raw = {"tool_call": {"function": {"name": "wait", "arguments": '{"reason":"保持原样"}'}}}
    readable = readable_model_response(raw)
    assert readable["tool_call"]["function"]["arguments"] == {"reason": "保持原样"}
    assert raw["tool_call"]["function"]["arguments"] == '{"reason":"保持原样"}'


def test_provider_intent_normalization_keeps_visible_choice() -> None:
    result = normalize_provider_intent(
        "agent_decision",
        {"choice": "request_behavior", "parameters": {"choice": "去安全区域休息", "confidence": 0.8}},
    )
    assert result["choice"] == "去安全区域休息"
    assert result["confidence"] == 0.8
