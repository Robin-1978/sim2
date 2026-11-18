from __future__ import annotations

from sim2.agents.protocol import ProviderProtocol, normalize_provider_intent, readable_model_response
from sim2.agents.tool_executor import _message_sentences


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


def test_message_sentence_split_does_not_reject_decimal_or_chinese_two_sentence_reply() -> None:
    assert len(_message_sentences("Inspection shows village food at 319.0. Ration changes await your review.")) == 2
    assert len(_message_sentences("库存是319.0。配给调整等你确认。")) == 2


def test_native_intent_batch_preserves_provider_order() -> None:
    from pathlib import Path
    import tempfile

    from sim2.agents.engine import AgentEngine
    from sim2.bootstrap.service import create_instance
    from sim2.platform.runtime import Platform

    class BatchProvider:
        def __init__(self, response):
            self.response = response

        def complete_json(self, *, system, context, tools=None, messages=None, role=None):
            return self.response

    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    with tempfile.TemporaryDirectory(prefix="intent-batch-") as root:
        platform = Platform(create_instance(config, "intent-batch", Path(root)))
        research_event = platform.store.repositories.events.append_event(
            "research.completed", "luo_shan", 10,
            {"source_id": "boundary_stone", "stage_id": "stage_1", "fact": "A verified boundary mark was recorded.", "story_source": True},
        )
        provider = BatchProvider({
            "tool_calls": [
                {"id": "progress", "type": "function", "function": {"name": "record_world_progress", "arguments": {"progress_key": "boundary_knowledge", "evidence": "The completed research records a verified boundary fact."}}},
                {"id": "story", "type": "function", "function": {"name": "propose_story_beat", "arguments": {"beat": "记录界碑研究结果并决定下一步核验"}}},
            ],
        })
        try:
            turn = AgentEngine(platform, provider, max_iterations=1).run(
                role="story", actor_id="story", task_type="integration_verification", source_event_id=research_event
            )
            assert turn.result["status"] == "accepted"
            assert turn.result["submission_count"] == 2
            assert [item.get("tool") for item in turn.result["submission_results"]] == ["record_world_progress", "propose_story_beat"]
        finally:
            platform.close()


def test_fresh_context_replaces_the_first_user_snapshot_without_touching_tool_tail():
    from sim2.agents.engine import AgentEngine

    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "<world_state>stale</world_state>"},
        {"role": "assistant", "content": '{"tool":"wait"}'},
        {"role": "user", "content": "tool result"},
    ]
    updated = AgentEngine._messages_for_prompt(messages, "<world_state>fresh</world_state>")

    assert updated[1]["content"] == "<world_state>fresh</world_state>"
    assert updated[3]["content"] == "tool result"
    assert messages[1]["content"] == "<world_state>stale</world_state>"
