"""Runtime LLM configuration.

The real file lives at ~/.sim2/llm.json. It contains provider metadata and
per-role routing/spend policy; secrets are references to environment variables
and are never persisted here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_MAX_COMPLETION_TOKENS = 2048
DEFAULT_MAX_AGENT_ITERATIONS = 4
DEFAULT_MAX_READ_TOOL_CALLS = 8


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    provider_type: str
    base_url: str
    model: str
    api_key_env: str | None = None
    proxy: str | None = None
    session_header: str | None = None
    timeout_seconds: float = 120.0
    max_retries: int = 2
    retry_backoff_seconds: float = 1.0
    adapter: str | None = None
    extra_body: dict[str, Any] | None = None


@dataclass(frozen=True)
class RoleConfig:
    """Route one Agent role and define its model-spend budget."""

    provider: str | None = None
    max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS
    max_agent_iterations: int = DEFAULT_MAX_AGENT_ITERATIONS
    max_read_tool_calls: int = DEFAULT_MAX_READ_TOOL_CALLS


@dataclass(frozen=True)
class LLMConfig:
    default_provider: str
    providers: dict[str, ProviderConfig]
    roles: dict[str, RoleConfig]

    @classmethod
    def load(cls, path: Path | None = None) -> "LLMConfig":
        config_path = path or (Path.home() / ".sim2" / "llm.json")
        if not config_path.exists():
            raise FileNotFoundError(
                f"LLM config not found at {config_path}. Copy examples/llm-config.example.json "
                "to ~/.sim2/llm.json and edit provider settings."
                )
        raw: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
        for legacy_key in ("role_providers", "agent_limits"):
            if legacy_key in raw:
                raise ValueError(
                    f"LLM config field {legacy_key!r} was replaced by 'roles'; "
                    "merge the provider and Agent limits into roles.<role>"
                )
        providers: dict[str, ProviderConfig] = {}
        for name, value in raw.get("providers", {}).items():
            for limit_key in ("max_completion_tokens", "max_agent_iterations"):
                if limit_key in value:
                    raise ValueError(
                        f"Provider {name!r} must not define {limit_key!r}; "
                        f"configure it under roles.<role>.{limit_key}"
                    )
            providers[name] = ProviderConfig(
                name=name,
                provider_type=value.get("type", "openai_compatible"),
                base_url=str(value["base_url"]).rstrip("/"),
                model=str(value["model"]),
                api_key_env=value.get("api_key_env"),
                proxy=value.get("proxy"),
                session_header=value.get("session_header"),
                timeout_seconds=float(value.get("timeout_seconds", 120)),
                max_retries=max(0, int(value.get("max_retries", 2))),
                retry_backoff_seconds=max(0.0, float(value.get("retry_backoff_seconds", 1.0))),
                adapter=value.get("adapter"),
                extra_body=dict(value.get("extra_body") or {}),
            )
        if not providers:
            raise ValueError(f"No LLM providers configured in {config_path}")
        default = str(raw.get("default_provider", next(iter(providers))))
        if default not in providers:
            raise ValueError(f"Default provider {default!r} is not configured")
        roles: dict[str, RoleConfig] = {}
        configured_roles = raw.get("roles", {}) or {}
        if not isinstance(configured_roles, dict):
            raise ValueError("LLM config 'roles' must be an object")
        for role, value in configured_roles.items():
            if not isinstance(value, dict):
                raise ValueError(f"Role {role!r} configuration must be an object")
            provider = value.get("provider")
            if provider is not None:
                provider = str(provider)
                if provider not in providers:
                    raise ValueError(f"Role {role!r} maps to unknown provider {provider!r}")
            completion = value.get("max_completion_tokens")
            iterations = value.get("max_agent_iterations")
            read_calls = value.get("max_read_tool_calls")
            roles[str(role)] = RoleConfig(
                provider=provider,
                max_completion_tokens=(
                    max(128, min(8192, int(completion)))
                    if completion is not None else DEFAULT_MAX_COMPLETION_TOKENS
                ),
                max_agent_iterations=(
                    max(1, min(12, int(iterations)))
                    if iterations is not None else DEFAULT_MAX_AGENT_ITERATIONS
                ),
                max_read_tool_calls=(
                    max(0, min(12, int(read_calls)))
                    if read_calls is not None else DEFAULT_MAX_READ_TOOL_CALLS
                ),
            )
        return cls(default_provider=default, providers=providers, roles=roles)

    def provider_name_for(self, role: str) -> str:
        configured = self.roles.get(str(role))
        return configured.provider if configured and configured.provider else self.default_provider

    def role_for(self, role: str) -> RoleConfig:
        return self.roles.get(str(role), RoleConfig())

    def provider_for(self, role: str) -> ProviderConfig:
        return self.providers[self.provider_name_for(role)]
