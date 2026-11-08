from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

import httpx

from .config import LLMConfig, ProviderConfig


class LLMProvider(Protocol):
    def complete_json(self, *, system: str, context: str, tools: list[dict[str, Any]] | None = None, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]: ...


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
    session_id: str = ""

    def _post(self, url: str, *, headers: dict[str, str], payload: dict[str, Any]) -> httpx.Response:
        """Perform a bounded retry for transport and transient upstream errors."""

        retry_statuses = {408, 425, 429, 500, 502, 503, 504}
        attempts = max(0, int(self.config.max_retries))
        for attempt in range(attempts + 1):
            try:
                response = httpx.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self.config.timeout_seconds,
                    proxy=self.config.proxy,
                    trust_env=False,
                )
            except httpx.RequestError:
                if attempt >= attempts:
                    raise
                delay = min(8.0, self.config.retry_backoff_seconds * (2 ** attempt))
                if delay:
                    time.sleep(delay)
                continue
            if response.status_code not in retry_statuses or attempt >= attempts:
                return response
            retry_after = response.headers.get("retry-after", "")
            try:
                server_delay = max(0.0, float(retry_after))
            except ValueError:
                server_delay = 0.0
            delay = min(30.0, max(server_delay, self.config.retry_backoff_seconds * (2 ** attempt)))
            if delay:
                time.sleep(delay)
        raise RuntimeError("LLM request retry loop ended unexpectedly")

    def complete_json(self, *, system: str, context: str, tools: list[dict[str, Any]] | None = None, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        headers = {"content-type": "application/json"}
        if not self.session_id:
            self.session_id = str(uuid4())
        session_header = self.config.session_header or ("x-opencode-session" if "opencode.ai" in self.config.base_url else None)
        if session_header:
            headers[session_header] = self.session_id
        api_key = None
        if self.config.api_key_env:
            # The value is read only at request time and is never logged or persisted.
            api_key = os.environ.get(self.config.api_key_env)
            if not api_key:
                raise RuntimeError(f"Required API key environment variable is not set: {self.config.api_key_env}")
            headers["authorization"] = f"Bearer {api_key}"
        payload = {
            "model": self.config.model,
            "messages": messages or [
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
        response = self._post(url, headers=headers, payload=payload)
        # Some local OpenAI-compatible servers accept JSON mode but reject the
        # optional native tools fields.  Retry the same read-only completion
        # without those hints; the prompt-level tool envelope remains usable.
        if response.status_code in {400, 422} and tools:
            fallback = dict(payload)
            fallback.pop("tools", None)
            fallback.pop("tool_choice", None)
            response = self._post(url, headers=headers, payload=fallback)
        if response.status_code in {400, 422} and "response_format" in payload:
            fallback = dict(payload)
            fallback.pop("response_format", None)
            fallback.pop("tools", None)
            fallback.pop("tool_choice", None)
            response = self._post(url, headers=headers, payload=fallback)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text[:800].replace("authorization", "auth")
            raise RuntimeError(f"LLM HTTP {response.status_code}: {detail}") from exc
        data = response.json()
        message = data["choices"][0]["message"]
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        # Native tool calls are normalized to the same compact envelope used
        # by local llama.cpp models that return a JSON object in content.
        if message.get("tool_calls"):
            result = {"tool_calls": message["tool_calls"]}
        elif message.get("function_call"):
            result = {"function_call": message["function_call"]}
        else:
            content = message.get("content") or ""
            result = _json_from_text(content)
        if usage:
            result["_usage"] = usage
        return result


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

    def provider_for_name(self, name: str) -> LLMProvider:
        """Return the configured provider instance for an explicit override."""

        try:
            return self.providers[name]
        except KeyError as exc:
            raise ValueError(f"Unknown LLM provider: {name}") from exc

    def new_provider_for_name(self, name: str) -> LLMProvider:
        """Create an isolated provider session for a shared role override."""

        try:
            return OpenAICompatibleProvider(self.config.providers[name])
        except KeyError as exc:
            raise ValueError(f"Unknown LLM provider: {name}") from exc

    def complete_json(self, *, role: str, system: str, context: str, tools: list[dict[str, Any]] | None = None, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return self.provider_for_role(role).complete_json(system=system, context=context, tools=tools, messages=messages)
