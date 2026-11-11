from __future__ import annotations

import json
from pathlib import Path

import pytest

from sim2.llm.config import DEFAULT_MAX_AGENT_ITERATIONS, DEFAULT_MAX_COMPLETION_TOKENS, LLMConfig
from sim2.llm.gateway import LLMRouter


def _write_config(tmp_path: Path, *, provider: str = "fast") -> Path:
    path = tmp_path / "llm.json"
    path.write_text(json.dumps({
        "default_provider": "fast",
        "roles": {
            "agent_decision": {
                "provider": provider,
                "max_completion_tokens": 512,
                "max_agent_iterations": 3,
            },
            "agent_reflection": {"provider": provider},
        },
        "providers": {
            "fast": {
                "base_url": "http://127.0.0.1:8192/v1",
                "model": "fast-model",
            },
            "deep": {
                "base_url": "http://127.0.0.1:8193/v1",
                "model": "deep-model",
            },
        },
    }), encoding="utf-8")
    return path


def test_role_config_combines_provider_and_limits(tmp_path: Path):
    config = LLMConfig.load(_write_config(tmp_path))

    assert config.roles["agent_decision"].provider == "fast"
    assert config.roles["agent_decision"].max_completion_tokens == 512
    assert config.roles["agent_decision"].max_agent_iterations == 3
    assert config.provider_name_for("unknown_role") == "fast"

    router = LLMRouter(config)
    decision = router.provider_for_role("agent_decision")
    assert decision.config.model == "fast-model"
    assert decision.max_completion_tokens == 512
    assert decision.max_agent_iterations == 3
    assert router.provider_for_role("agent_decision") is decision

    explicit = LLMRouter(config).new_provider_for_name("deep", role="agent_decision")
    assert explicit.config.model == "deep-model"
    assert explicit.max_completion_tokens == 512
    assert explicit.max_agent_iterations == 3


def test_role_without_limits_uses_agent_defaults(tmp_path: Path):
    config = LLMConfig.load(_write_config(tmp_path))
    reflection = LLMRouter(config).provider_for_role("agent_reflection")

    assert reflection.max_completion_tokens == DEFAULT_MAX_COMPLETION_TOKENS
    assert reflection.max_agent_iterations == DEFAULT_MAX_AGENT_ITERATIONS


def test_role_rejects_unknown_provider(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown provider 'missing'"):
        LLMConfig.load(_write_config(tmp_path, provider="missing"))


def test_provider_rejects_agent_limits(tmp_path: Path):
    path = _write_config(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["providers"]["fast"]["max_agent_iterations"] = 3
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="configure it under roles.<role>"):
        LLMConfig.load(path)


@pytest.mark.parametrize("legacy_key", ["role_providers", "agent_limits"])
def test_split_role_tables_are_rejected(tmp_path: Path, legacy_key: str):
    path = _write_config(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw[legacy_key] = {}
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="was replaced by 'roles'"):
        LLMConfig.load(path)
