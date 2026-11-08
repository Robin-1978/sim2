"""Runtime LLM configuration.

The real file lives at ~/.sim2/llm.json. It contains provider metadata only;
secrets are referenced by environment variable name and never persisted here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
class LLMConfig:
    default_provider: str
    providers: dict[str, ProviderConfig]
    role_providers: dict[str, str]

    @classmethod
    def load(cls, path: Path | None = None) -> "LLMConfig":
        config_path = path or (Path.home() / ".sim2" / "llm.json")
        if not config_path.exists():
            raise FileNotFoundError(
                f"LLM config not found at {config_path}. Copy examples/llm-config.example.json "
                "to ~/.sim2/llm.json and edit provider settings."
            )
        raw: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
        providers: dict[str, ProviderConfig] = {}
        for name, value in raw.get("providers", {}).items():
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
        role_providers = {str(k): str(v) for k, v in raw.get("role_providers", {}).items()}
        for role, provider in role_providers.items():
            if provider not in providers:
                raise ValueError(f"Role {role!r} maps to unknown provider {provider!r}")
        return cls(default_provider=default, providers=providers, role_providers=role_providers)

    def provider_name_for(self, role: str) -> str:
        return self.role_providers.get(role, self.default_provider)

    def provider_for(self, role: str) -> ProviderConfig:
        return self.providers[self.provider_name_for(role)]
