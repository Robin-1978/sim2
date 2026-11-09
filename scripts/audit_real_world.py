"""Run and audit a real-provider world session.

The audit keeps the complete initial AgentView, every rendered ReAct prompt,
model response, Platform tool result, and final world projection.  It is an
operator-facing verification command rather than a production scheduler.

Examples::

    bash -ic 'python scripts/audit_real_world.py --minutes 1440'
    SIM2_BGE_MODEL=disabled bash -ic 'python scripts/audit_real_world.py --minutes 1440'

The provider key is read by the normal LLM configuration layer and is never
printed or written by this script.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from sim2.agents.quality import evaluate_turn
from sim2.bootstrap.service import create_instance
from sim2.llm.config import LLMConfig
from sim2.platform.runtime import Platform


def _safe_result(turn: Any, context: dict[str, Any], actor_id: str) -> dict[str, Any]:
    quality = evaluate_turn(turn, context)
    return {
        "role": turn.role,
        "actor_id": actor_id,
        "task_type": turn.task_type,
        "world_time": context.get("world_time", {}),
        "initial_context": turn.initial_context,
        "prompt_history": turn.prompt_history,
        "response": turn.response,
        "result": turn.result,
        "iterations": turn.iterations,
        "transcript": turn.transcript,
        "quality": quality,
    }


def _causality(platform: Platform, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    intents = platform.store.connection.execute(
        "SELECT intent_id, actor_id, intent_type, status, data_json, result_json, created_minute FROM intent ORDER BY created_minute, intent_id"
    ).fetchall()
    events = platform.store.connection.execute(
        "SELECT event_id, event_type, actor_id, world_minute, data_json FROM event_log ORDER BY event_id"
    ).fetchall()
    accepted = [dict(row) for row in intents if row["status"] in {"accepted", "completed", "wait"}]
    behavior_intents = [item for item in accepted if item["intent_type"] in {"select_behavior", "request_behavior"}]
    behavior_events = [dict(row) for row in events if str(row["event_type"]).startswith("behavior.")]
    behavior_outcome_ids = {
        str(json.loads(row["data_json"] or "{}").get("execution_id"))
        for row in behavior_events
        if row["event_type"] in {"behavior.completed", "behavior.failed", "behavior.interrupted"}
    }
    bt_status = {
        str(row["execution_id"]): str(row["status"])
        for row in platform.store.connection.execute("SELECT execution_id, status FROM bt_instance")
    }
    session_roles = {role: sum(1 for item in sessions if item["role"] == role) for role in ("agent_decision", "agent_reflection", "story", "god")}
    return {
        "accepted_intents": len(accepted),
        "accepted_behavior_intents": len(behavior_intents),
        "behavior_outcome_events": len(behavior_events),
        "session_roles": session_roles,
        "orphan_behavior_intents": [
            {"intent_id": item["intent_id"], "actor_id": item["actor_id"], "created_minute": item["created_minute"]}
            for item in behavior_intents
            if str(json.loads(item["result_json"] or "{}").get("execution_id", "")) not in behavior_outcome_ids
            and bt_status.get(str(json.loads(item["result_json"] or "{}").get("execution_id", ""))) not in {"executing", "paused"}
        ],
        "active_behavior_intents": [
            {"intent_id": item["intent_id"], "actor_id": item["actor_id"], "execution_id": json.loads(item["result_json"] or "{}").get("execution_id")}
            for item in behavior_intents
            if bt_status.get(str(json.loads(item["result_json"] or "{}").get("execution_id", ""))) in {"executing", "paused"}
        ],
        "event_types": platform.store.event_type_counts(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=1440)
    parser.add_argument("--provider", default="remote", help="Configured provider name; remote is the default real-model path.")
    parser.add_argument("--output", type=Path, default=Path("artifacts/real-agent-audit.json"))
    args = parser.parse_args()
    if args.minutes < 1:
        raise SystemExit("--minutes must be at least 1")
    config = LLMConfig.load()
    if args.provider not in config.providers:
        raise SystemExit(f"Configured provider not found: {args.provider}")
    world_config = Path(__file__).parents[1] / "worlds" / "starter_village"
    runtime_root = Path("artifacts") / f"audit-runtime-{int(time.time())}"
    instance_id = "real-agent-audit"
    db_path = create_instance(world_config, instance_id, runtime_root)
    platform = Platform(db_path)
    sessions: list[dict[str, Any]] = []

    def observe(turn: Any, actor_id: str) -> None:
        context = turn.initial_context or {}
        sessions.append(_safe_result(turn, context, actor_id))

    try:
        report = platform.run(args.minutes, provider_name=args.provider, turn_observer=observe)
        output = {
            "provider": args.provider,
            "model": config.providers[args.provider].model,
            "minutes_requested": args.minutes,
            "db_path": str(db_path),
            "sessions": sessions,
            "world": {
                "status": platform.status(),
                "health": platform.health(),
                "report": report.as_dict(),
                "metrics": platform.metrics(),
                "observer": platform.observer_view(event_limit=100),
                "agents": platform.store.list_agents(),
                "progress": platform.store.list_progress("world", limit=100),
                "causality": _causality(platform, sessions),
            },
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        failed = sum(1 for item in sessions if not item["quality"].get("passed"))
        print(json.dumps({
            "audit_file": str(args.output),
            "db_path": str(db_path),
            "model": config.providers[args.provider].model,
            "world_minute": output["world"]["status"]["world_minute"],
            "sessions": len(sessions),
            "quality_failed": failed,
            "quality_by_role": {
                role: {
                    "sessions": sum(1 for item in sessions if item["role"] == role),
                    "failed": sum(1 for item in sessions if item["role"] == role and not item["quality"].get("passed")),
                }
                for role in ("agent_decision", "agent_reflection", "story", "god")
            },
            "report": report.as_dict(),
            "causality": output["world"]["causality"],
        }, ensure_ascii=False, indent=2))
        return 0 if output["world"]["status"]["status"] == "running" and not failed else 1
    finally:
        platform.close()


if __name__ == "__main__":
    raise SystemExit(main())
