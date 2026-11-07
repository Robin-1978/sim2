from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .config import LLMConfig, ProviderConfig


class LLMProvider(Protocol):
    def complete_json(self, *, system: str, context: str, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]: ...


def _json_from_text(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("LLM response must be a JSON object")
    return value


@dataclass
class OpenAICompatibleProvider:
    config: ProviderConfig

    def complete_json(self, *, system: str, context: str, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        headers = {"content-type": "application/json"}
        api_key = None
        if self.config.api_key_env:
            # The value is read only at request time and is never logged or persisted.
            api_key = os.environ.get(self.config.api_key_env)
            if not api_key:
                raise RuntimeError(f"Required API key environment variable is not set: {self.config.api_key_env}")
            headers["authorization"] = f"Bearer {api_key}"
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": context},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        url = f"{self.config.base_url}/chat/completions"
        response = httpx.post(url, headers=headers, json=payload, timeout=self.config.timeout_seconds)
        # Some local OpenAI-compatible servers accept JSON mode but reject the
        # optional native tools fields.  Retry the same read-only completion
        # without those hints; the prompt-level tool envelope remains usable.
        if response.status_code in {400, 422} and tools:
            fallback = dict(payload)
            fallback.pop("tools", None)
            fallback.pop("tool_choice", None)
            response = httpx.post(url, headers=headers, json=fallback, timeout=self.config.timeout_seconds)
        if response.status_code in {400, 422} and "response_format" in payload:
            fallback = dict(payload)
            fallback.pop("response_format", None)
            fallback.pop("tools", None)
            fallback.pop("tool_choice", None)
            response = httpx.post(url, headers=headers, json=fallback, timeout=self.config.timeout_seconds)
        response.raise_for_status()
        data = response.json()
        message = data["choices"][0]["message"]
        # Native tool calls are normalized to the same compact envelope used
        # by local llama.cpp models that return a JSON object in content.
        if message.get("tool_calls"):
            return {"tool_calls": message["tool_calls"]}
        if message.get("function_call"):
            return {"function_call": message["function_call"]}
        content = message.get("content") or ""
        return _json_from_text(content)


class LLMRouter:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.providers = {
            name: OpenAICompatibleProvider(provider_config)
            for name, provider_config in config.providers.items()
        }

    @classmethod
    def from_default_config(cls) -> "LLMRouter":
        return cls(LLMConfig.load())

    def provider_for_role(self, role: str) -> LLMProvider:
        return self.providers[self.config.provider_name_for(role)]

    def complete_json(self, *, role: str, system: str, context: str, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return self.provider_for_role(role).complete_json(system=system, context=context, tools=tools)
