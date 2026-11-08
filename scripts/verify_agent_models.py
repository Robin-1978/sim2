"""Run real provider checks for the three Agent roles.

The script never prints API keys or raw model reasoning.  It reports only
AgentView validation, session steps, final Intent status and Platform checks.

    python scripts/verify_agent_models.py
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

import httpx

from sim2.agents.engine import AgentEngine
from sim2.agents.quality import evaluate_turn
from sim2.bootstrap.service import create_instance
from sim2.llm.config import LLMConfig, ProviderConfig
from sim2.llm.gateway import OpenAICompatibleProvider
from sim2.platform.runtime import Platform


def discover_local_model(base_url: str) -> str | None:
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/models", timeout=10, trust_env=False)
        response.raise_for_status()
        models = response.json().get("data") or response.json().get("models") or []
        if models:
            return str(models[0].get("id") or models[0].get("model") or models[0].get("name"))
    except Exception:
        return None
    return None


def run_session(platform: Platform, provider: OpenAICompatibleProvider, role: str, actor_id: str) -> dict[str, Any]:
    engine = AgentEngine(platform, provider, max_iterations=6)
    view = platform.decision_view(actor_id, role=role)
    turn = engine.run(role=role, actor_id=actor_id, task_type="integration_verification")
    quality = evaluate_turn(turn, view)
    result = {
        "role": role,
        "iterations": turn.iterations,
        "steps": [step.get("step") for step in turn.transcript],
        "errors": [str(step.get("error", ""))[:300] for step in turn.transcript if step.get("step") == "llm_error"],
        "tool_calls": [
            {"name": step.get("name"), "arguments": step.get("arguments"), "status": step.get("result", {}).get("status"), "failed_check": step.get("result", {}).get("failed_check")}
            for step in turn.transcript if step.get("step") == "tool"
        ],
        "final_response": turn.response,
        "status": turn.result.get("status"),
        "failed_check": turn.result.get("failed_check"),
        "behavior_ref": turn.result.get("behavior_ref"),
        "entity_id": turn.result.get("entity_id"),
    }
    result["quality"] = quality["passed"]
    result["quality_grade"] = quality["grade"]
    result["quality_checks"] = quality["checks"]
    result["provider_status"] = _provider_status(turn.result)
    if result["provider_status"]:
        result["quality_grade"] = f"provider_{result['provider_status']}"
    result["context_cache"] = platform.metrics().get("llm_cache", {})
    return result


def _provider_status(result: dict[str, Any]) -> str | None:
    if result.get("failed_check") != "llm_response":
        return None
    message = str(result.get("message", "")).lower()
    if "429" in message or "rate_limit" in message:
        return "rate_limited"
    if "cooldown" in message:
        return "cooldown"
    if "http 5" in message or "disconnect" in message or "timeout" in message:
        return "upstream_unavailable"
    return "provider_error"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-remote", action="store_true")
    parser.add_argument("--skip-local", action="store_true")
    args = parser.parse_args()
    config = LLMConfig.load()
    world_config = Path(__file__).parents[1] / "worlds" / "starter_village"
    reports: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="sim2-model-check-") as runtime:
        db = create_instance(world_config, "model-check", Path(runtime))
        platform = Platform(db)
        try:
            local = config.providers.get("local")
            if local and not args.skip_local:
                model = discover_local_model(local.base_url) or local.model
                local_provider = OpenAICompatibleProvider(ProviderConfig(**{**local.__dict__, "model": model, "proxy": None}))
                reports.append({"provider": "local", "model": model, **run_session(platform, local_provider, "agent_decision", "lu_xingzhou")})
                reports.append({"provider": "local", "model": model, **run_session(platform, local_provider, "agent_reflection", "lu_xingzhou")})
            remote = config.providers.get("remote")
            if remote and not args.skip_remote:
                if remote.api_key_env and not __import__("os").environ.get(remote.api_key_env):
                    reports.append({"provider": "remote", "model": remote.model, "status": "blocked", "reason": "configured API key environment variable is not available to this process", "quality": False})
                else:
                    remote_provider = OpenAICompatibleProvider(remote)
                    reports.append({"provider": "remote", "model": remote.model, **run_session(platform, remote_provider, "god", "god")})
        finally:
            platform.close()
    print(json.dumps({
        "checks": reports,
        "provider_blocked": any(item.get("provider_status") in {"rate_limited", "cooldown", "upstream_unavailable"} for item in reports),
        "all_quality_checks_passed": bool(reports) and all(item.get("quality") and not item.get("provider_status") for item in reports),
    }, ensure_ascii=False, indent=2))
    return 0 if reports and all(item.get("quality") for item in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
