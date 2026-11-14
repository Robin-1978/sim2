from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..store import json_dumps


class LlmRepository:
    """Persistence owner for the configured llm domain."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def append_llm_call(
        self,
        actor_id: str,
        role: str,
        task_type: str,
        iteration: int,
        context_hash: str,
        response: dict[str, Any],
        status: str,
        *,
        usage: dict[str, Any] | None = None,
        static_prefix_hash: str | None = None,
        static_prefix_chars: int = 0,
        dynamic_chars: int = 0,
        request_latency_ms: float | None = None,
    ) -> int:
        usage = usage or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
        cached_tokens = usage.get("cached_tokens", usage.get("prompt_cache_hit_tokens", details.get("cached_tokens")))
        with self.store._write_lock:
            cur = self.store.connection.execute(
                "INSERT INTO llm_call(actor_id,role,task_type,iteration,context_hash,response_json,status,world_minute,prompt_tokens,cached_tokens,completion_tokens,static_prefix_hash,static_prefix_chars,dynamic_chars,request_latency_ms) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    actor_id,
                    role,
                    task_type,
                    int(iteration),
                    context_hash,
                    json_dumps(response),
                    status,
                    int(self.store.get_meta("world_minute", 0)),
                    int(prompt_tokens) if isinstance(prompt_tokens, (int, float)) else None,
                    int(cached_tokens) if isinstance(cached_tokens, (int, float)) else None,
                    int(completion_tokens) if isinstance(completion_tokens, (int, float)) else None,
                    static_prefix_hash,
                    int(static_prefix_chars),
                    int(dynamic_chars),
                    float(request_latency_ms) if isinstance(request_latency_ms, (int, float)) else None,
                ),
            )
            self.store.connection.commit()
        return int(cur.lastrowid)

    def llm_cache_metrics(self) -> dict[str, Any]:
        """Report provider cache evidence and prompt-prefix reuse separately."""

        row = self.store.connection.execute(
            "SELECT COUNT(*) AS calls, SUM(CASE WHEN cached_tokens IS NOT NULL THEN 1 ELSE 0 END) AS measured, "
            "COALESCE(SUM(prompt_tokens),0) AS prompt_tokens, COALESCE(SUM(cached_tokens),0) AS cached_tokens, "
            "COALESCE(SUM(completion_tokens),0) AS completion_tokens, COALESCE(AVG(request_latency_ms),0) AS average_latency_ms, COALESCE(MAX(request_latency_ms),0) AS max_latency_ms FROM llm_call"
        ).fetchone()
        prefixes = self.store.connection.execute(
            "SELECT COUNT(*) AS total, COUNT(DISTINCT static_prefix_hash) AS distinct_prefixes, "
            "COALESCE(AVG(static_prefix_chars),0) AS avg_static_chars, COALESCE(AVG(dynamic_chars),0) AS avg_dynamic_chars FROM llm_call"
        ).fetchone()
        calls = int(row["calls"] or 0)
        measured = int(row["measured"] or 0)
        prompt_tokens = int(row["prompt_tokens"] or 0)
        cached_tokens = int(row["cached_tokens"] or 0)
        return {
            "calls": calls,
            "provider_usage_calls": measured,
            "provider_cache_hit_rate": round(cached_tokens / prompt_tokens, 4) if prompt_tokens else None,
            "cached_tokens": cached_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": int(row["completion_tokens"] or 0),
            "average_request_latency_ms": round(float(row["average_latency_ms"] or 0), 1),
            "max_request_latency_ms": round(float(row["max_latency_ms"] or 0), 1),
            "static_prefix_distinct": int(prefixes["distinct_prefixes"] or 0),
            "static_prefix_reuse_rate": round(1 - int(prefixes["distinct_prefixes"] or 0) / int(prefixes["total"] or 1), 4) if calls else 0.0,
            "average_static_prefix_chars": round(float(prefixes["avg_static_chars"] or 0), 1),
            "average_dynamic_chars": round(float(prefixes["avg_dynamic_chars"] or 0), 1),
        }

