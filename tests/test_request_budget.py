from contextlib import closing
from pathlib import Path

import pytest

from sim2.agents.engine import AgentEngine
from sim2.agents.quality import evaluate_turn
from sim2.agents.session import trim_conversation_messages
from sim2.agents.tool_executor import AgentToolExecutor
from sim2.bootstrap.service import create_instance
from sim2.llm.budget import ContextBudgetExceeded, request_metrics
from sim2.llm.config import ProviderConfig
from sim2.llm.gateway import OpenAICompatibleProvider
from sim2.platform.runtime import Platform


class Provider:
    max_completion_tokens = 512
    context_window_tokens = 32768

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.requests.append((messages, tools))
        return next(self.responses)


@pytest.fixture
def platform(tmp_path):
    world = Path(__file__).parents[1] / "worlds" / "starter_village"
    with closing(Platform(create_instance(world, "request-budget", tmp_path))) as instance:
        yield instance


def test_full_request_budget_compacts_native_history_and_keeps_feedback(platform, monkeypatch):
    wait = {"intent_type": "wait", "reason": "No world change is needed."}
    responses = [
        {"tool_calls": [{"id": "read-1", "type": "function", "function": {"name": "observe_nearby", "arguments": "{}"}}]},
        {"tool_calls": [{"id": "read-2", "type": "function", "function": {"name": "inspect_entity", "arguments": '{"entity":"village_food"}'}}]},
        wait,
    ]
    monkeypatch.setattr(AgentToolExecutor, "execute", lambda self, tool, arguments: {
        "status": "ok", "message": "Water reserve was checked.", "events": [{"summary": "风暴" * 140}] * 6,
    })
    baseline = AgentEngine(platform, Provider(responses)).run(role="agent_decision", actor_id="su_wu")
    first_request = baseline.context_metrics[0]
    assert first_request["request_tool_tokens_est"] > 0
    assert first_request["request_message_tokens_est"] > first_request["estimated_tokens"]
    provider = Provider(responses)
    provider.context_window_tokens = baseline.context_metrics[1]["request_total_tokens_est"] + 400
    turn = AgentEngine(platform, provider).run(role="agent_decision", actor_id="su_wu")
    assert turn.result["status"] == "wait", turn.result
    assert len(provider.requests) == len(turn.context_metrics) == 3
    for (messages, tools), metrics in zip(provider.requests, turn.context_metrics):
        actual = request_metrics(messages, tools, window=provider.context_window_tokens, completion=512)
        assert metrics["request_total_tokens_est"] == actual["request_total_tokens_est"] <= provider.context_window_tokens
        assert not metrics["omitted_required"]
    assert turn.context_metrics[-1]["history_messages_omitted"] == 2
    assert "latest_platform_result" in turn.prompt_history[-1]
    assert "<decision>" in turn.prompt_history[-1]
    assert [message["tool_call_id"] for message in provider.requests[-1][0] if message["role"] == "tool"] == ["read-2"]


def test_impossible_request_reports_budget_and_never_calls_provider(platform):
    provider = Provider([])
    provider.context_window_tokens = 900
    turn = AgentEngine(platform, provider).run(role="agent_decision", actor_id="su_wu")
    assert turn.result["failed_check"] == "context_budget"
    assert turn.result["retryable"] is True
    assert evaluate_turn(turn, turn.initial_context)["grade"] == "context_budget"
    assert turn.iterations == 0 and not provider.requests
    assert turn.context_metrics[0]["omitted_required"]
    audit = platform.store.connection.execute("SELECT status FROM llm_call ORDER BY call_id DESC").fetchone()
    assert audit["status"] == "context_budget"


def test_request_budget_compacts_snapshot_after_accounting_for_tools(platform):
    wait = {"intent_type": "wait", "reason": "No world change is needed."}
    baseline = AgentEngine(platform, Provider([wait])).run(role="agent_decision", actor_id="su_wu")
    original = baseline.context_metrics[0]
    provider = Provider([wait])
    provider.context_window_tokens = original["request_total_tokens_est"] - 350
    turn = AgentEngine(platform, provider).run(role="agent_decision", actor_id="su_wu")
    assert turn.result["status"] == "wait", turn.result
    fitted = turn.context_metrics[0]
    assert fitted["request_total_tokens_est"] <= provider.context_window_tokens
    assert fitted["chars"] < original["chars"]
    assert not fitted["omitted_required"]


def test_direct_gateway_reserves_provider_output_override_before_transport(monkeypatch):
    config = ProviderConfig(
        "test", "openai_compatible", "https://example.invalid", "fake", context_window_tokens=1000,
        extra_body={"max_completion_tokens": 800, "response_format": {"type": "json_schema", "json_schema": {"name": "intent", "schema": {"type": "object"}}}},
    )
    provider = OpenAICompatibleProvider(config)
    monkeypatch.setattr(provider, "_post", lambda *_args, **_kwargs: pytest.fail("over-budget request reached transport"))
    call = {"system": "Policy", "context": "台风" * 200}
    budget = provider.completion_budget(**call)
    assert budget["completion_reserved_tokens"] == 800
    assert budget["request_response_schema_tokens_est"] > 0
    with pytest.raises(ContextBudgetExceeded):
        provider.complete_json(**call)


def test_count_limit_keeps_the_latest_parallel_tool_batch_together():
    prefix = [{"role": "system", "content": "policy"}, {"role": "user", "content": "snapshot"}]
    older = [{"role": "assistant", "content": "old"}, {"role": "user", "content": "old feedback"}]
    calls = [{"id": f"tool-{index}"} for index in range(7)]
    latest = [{"role": "assistant", "tool_calls": calls}] + [
        {"role": "tool", "tool_call_id": call["id"], "content": "{}"} for call in calls
    ]
    assert trim_conversation_messages(prefix + older + latest, max_tail=4) == prefix + latest


def test_react_extends_the_previous_request_prefix_and_keeps_live_feedback(platform):
    provider = Provider([
        {"tool_calls": [{"id": "look", "type": "function", "function": {"name": "observe_nearby", "arguments": "{}"}}]},
        {"intent_type": "wait", "reason": "No world change is needed."},
    ])
    turn = AgentEngine(platform, provider).run(role="agent_decision", actor_id="su_wu")
    first, tools = provider.requests[0]
    second, second_tools = provider.requests[1]
    assert second_tools == tools
    assert second[:len(first)] == first
    assert second[-2]["tool_call_id"] == "look"
    assert "latest_platform_result" in second[-1]["content"]
    assert "<decision>" in second[-1]["content"]
    assert [item["cache_snapshot_reused"] for item in turn.context_metrics] == [False, True]


def test_cache_rate_uses_only_prompt_tokens_with_a_reported_hit_count(platform):
    llm = platform.store.repositories.llm
    for usage in (
        {"prompt_tokens": 200},
        {"prompt_tokens": 100, "prompt_cache_hit_tokens": 50, "cached_tokens": 0},
        {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 0}},
        {"prompt_cache_hit_tokens": 40, "prompt_cache_miss_tokens": 60},
    ):
        llm.append_llm_call("su_wu", "agent_decision", "decision", 1, "digest", {}, "response", usage=usage)
    stats = llm.llm_cache_metrics()
    assert stats["provider_usage_calls"] == 4
    assert stats["provider_cache_measured_calls"] == 3
    assert stats["prompt_tokens"] == 500
    assert stats["provider_cache_prompt_tokens"] == 300
    assert stats["cached_tokens"] == 90
    assert stats["provider_cache_hit_rate"] == 0.3
