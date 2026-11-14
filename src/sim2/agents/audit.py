from __future__ import annotations

import hashlib
import json
from typing import Any

from ..platform.runtime import Platform


class AgentAudit:
    """Persist bounded, redacted LLM call telemetry outside AgentEngine."""

    def __init__(self, platform: Platform) -> None:
        self.platform = platform

    def record(
        self,
        actor_id: str,
        role: str,
        task_type: str,
        iteration: int,
        context: str,
        response: dict[str, Any],
        status: str,
        usage: dict[str, Any] | None = None,
    ) -> None:
        try:
            digest = hashlib.sha256(context.encode("utf-8")).hexdigest()
            encoded = json.dumps(response, ensure_ascii=False)
            if len(encoded) > 16_000:
                response = {"truncated": True, "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}
            static_prefix = context.split("<world_state>", 1)[0]
            dynamic_text = context[len(static_prefix):]
            prefix_hash = hashlib.sha256(static_prefix.encode("utf-8")).hexdigest()
            self.platform.store.repositories.llm.append_llm_call(
                actor_id,
                role,
                task_type,
                iteration,
                digest,
                response,
                status,
                usage=usage or {},
                static_prefix_hash=prefix_hash,
                static_prefix_chars=len(static_prefix),
                dynamic_chars=len(dynamic_text),
                request_latency_ms=usage.get("request_latency_ms") if isinstance(usage, dict) else None,
            )
        except Exception:
            # Observability must never turn a valid Intent into a failed turn.
            return

