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
import os
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


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _quality_counts(sessions: list[dict[str, Any]]) -> dict[str, int]:
    def provider_error(item: dict[str, Any]) -> bool:
        return (
            str((item.get("quality") or {}).get("grade") or "").startswith("provider_")
            or (item.get("result") or {}).get("failed_check") == "llm_response"
        )

    provider_errors = sum(
        1 for item in sessions if provider_error(item)
    )
    return {
        "passed": sum(1 for item in sessions if item["quality"].get("passed")),
        "pending": sum(1 for item in sessions if item["quality"].get("pending")),
        "provider_errors": provider_errors,
        "failed": sum(
            1 for item in sessions
            if not item["quality"].get("passed")
            and not item["quality"].get("pending")
            and not provider_error(item)
        ),
    }


def _unresolved_provider_errors(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return provider failures that did not recover at the same world boundary.

    A cooldown retry is causally safe only when the same role, actor and task
    succeeds before world time advances.  Later success cannot repair a
    decision that the simulation already skipped.
    """

    unresolved: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for item in sessions:
        key = (
            str(item.get("role") or ""),
            str(item.get("actor_id") or ""),
            str(item.get("task_type") or ""),
            int((item.get("world_time") or {}).get("minute", -1) or -1),
        )
        provider_error = (
            str((item.get("quality") or {}).get("grade") or "").startswith("provider_")
            or (item.get("result") or {}).get("failed_check") == "llm_response"
        )
        if provider_error:
            unresolved[key] = item
        elif (item.get("result") or {}).get("status") in {"accepted", "wait", "skipped"}:
            unresolved.pop(key, None)
    return list(unresolved.values())


def _merge_reports(reports: list[Any], *, target_minute: int) -> dict[str, Any]:
    if not reports:
        return {"start_minute": target_minute, "end_minute": target_minute, "stop_reason": "no_run"}
    numeric = (
        "decisions", "reflections", "god_sessions", "story_sessions", "waits",
        "llm_errors", "rejected_turns", "interaction_replies_suppressed",
        "interaction_threads_closed",
    )
    latest = reports[-1].as_dict()
    merged = {
        "start_minute": reports[0].start_minute,
        "end_minute": reports[-1].end_minute,
        **{key: sum(int(getattr(report, key, 0) or 0) for report in reports) for key in numeric},
        "event_types": latest.get("event_types", {}),
        "selected_behaviors": latest.get("selected_behaviors", {}),
        "degeneration_flags": sorted({flag for report in reports for flag in (report.degeneration_flags or [])}),
        "diversity": latest.get("diversity", {}),
        "stop_reason": None if reports[-1].end_minute >= target_minute else reports[-1].stop_reason,
        "segments": len(reports),
    }
    return merged


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
    rule_changes: list[dict[str, Any]] = []
    rule_followups: list[dict[str, Any]] = []
    for row in events:
        data = json.loads(row["data_json"] or "{}")
        if row["event_type"] == "world.change.rule":
            added = ((data.get("effect") or {}).get("rule_added") or {})
            names = [str(item) for item in (added.get("id"), added.get("name"), added.get("label")) if str(item or "").strip()]
            behavior_names = [str(item) for item in (added.get("behavior_names") or added.get("behavior_ids") or added.get("action_names") or [])]
            rule_changes.append({
                "event_id": row["event_id"],
                "world_minute": row["world_minute"],
                "rule_id": str(added.get("id") or added.get("name") or ""),
                "rule_names": names,
                "behavior_names": behavior_names,
                "followup_count": 0,
                "first_followup_event_id": None,
                "status": "pending",
            })
        elif row["event_type"] == "world.rule.followed":
            rule_followups.append({"event_id": row["event_id"], "world_minute": row["world_minute"], **data})
    def norm(value: Any) -> str:
        return "".join(char for char in str(value or "").casefold() if char.isalnum())
    for change in rule_changes:
        names = {norm(item) for item in change["rule_names"]}
        expected_behaviors = {norm(item) for item in change["behavior_names"]}
        matches = [item for item in rule_followups if item["event_id"] > change["event_id"] and norm(item.get("rule")) in names and (not expected_behaviors or norm(item.get("behavior_id")) in expected_behaviors or any(norm(item.get("behavior_id")) == norm(known.get("id")) for known in platform.store.get_definitions("bt_def") if norm(known.get("agent_label")) in expected_behaviors))]
        change["followup_count"] = len(matches)
        if matches:
            change["status"] = "verified"
            change["first_followup_event_id"] = matches[0]["event_id"]
    god_direct_property_changes = [
        {"event_id": row["event_id"], "world_minute": row["world_minute"], "event_type": row["event_type"]}
        for row in events
        if row["actor_id"] == "god" and row["event_type"] in {"world.change.world_property"}
    ]
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
        "world_rule_changes": rule_changes,
        "world_rule_followups": rule_followups,
        "god_direct_property_changes": god_direct_property_changes,
        "event_types": platform.store.event_type_counts(),
    }


def _finalize_quality(sessions: list[dict[str, Any]], causality: dict[str, Any]) -> None:
    """Reconcile deferred proposal checks against the completed SQLite run.

    A God rule cannot be verified in the same session that registers it.  The
    live evaluator therefore marks it pending.  Once the run ends, this pass
    uses the persisted rule-followup event and changes only that deferred check;
    it does not turn a rejected proposal into a success.
    """

    by_rule = {str(item.get("rule_id")): item for item in causality.get("world_rule_changes", [])}
    for session in sessions:
        if session.get("role") != "god" or session.get("result", {}).get("status") != "accepted":
            continue
        payload = session.get("response") or {}
        if not isinstance(payload, dict) or not payload.get("change_type"):
            for step in reversed(session.get("transcript") or []):
                if step.get("step") == "tool" and step.get("name") == "propose_world_change":
                    payload = dict(step.get("arguments") or {})
                    break
                if step.get("step") == "intent" and isinstance(step.get("response"), dict):
                    candidate = dict(step.get("response") or {})
                    if candidate.get("change_type"):
                        payload = candidate
                        break
        if payload.get("change_type") != "rule":
            continue
        rule = payload.get("rule") if isinstance(payload.get("rule"), dict) else {}
        rule_id = str(rule.get("id") or rule.get("name") or "")
        change = by_rule.get(rule_id)
        if change is None and session.get("result", {}).get("event_id") is not None:
            change = next((item for item in causality.get("world_rule_changes", []) if item.get("event_id") == session["result"].get("event_id")), None)
        if not change:
            continue
        quality = session.setdefault("quality", {})
        check = next((item for item in quality.get("checks", []) if item.get("name") == "god_rule_has_behavior_followup"), None)
        if not check:
            continue
        if change.get("status") == "verified":
            check["passed"] = True
            check["detail"] = f"{change['followup_count']} later behavior outcome(s) verified the rule"
            quality.pop("pending", None)
            quality["passed"] = all(item.get("passed") for item in quality.get("checks", []))
            if quality["passed"]:
                quality["grade"] = "grounded"
        else:
            quality["pending"] = ["god_rule_has_behavior_followup"]
            quality["grade"] = "accepted_pending_world_followup"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=1440)
    parser.add_argument("--provider", default="remote", help="Configured provider name; remote is the default real-model path.")
    parser.add_argument("--model", default=None, help="Override the configured model for this provider.")
    parser.add_argument("--max-completion-tokens", type=int, default=None, help="Override the provider completion budget for this audit (use a larger value for reasoning-heavy free models).")
    parser.add_argument("--output", type=Path, default=Path("artifacts/real-agent-audit.json"))
    parser.add_argument("--resume-db", type=Path, help="continue the same SQLite world instead of creating a new instance")
    parser.add_argument("--target-minute", type=int, help="run until this absolute world minute; recommended with --resume-db")
    parser.add_argument("--checkpoint", type=Path, help="per-turn checkpoint path (default: OUTPUT.checkpoint.json)")
    parser.add_argument("--auto-resume", action="store_true", help="wait on provider cooldown and continue the same SQLite world")
    parser.add_argument("--cooldown-seconds", type=float, default=60.0, help="wall-clock wait between automatic provider retries")
    parser.add_argument("--max-provider-errors", type=int, default=20, help="stop automatic retries after this many provider-error sessions")
    args = parser.parse_args()
    if args.minutes < 1:
        raise SystemExit("--minutes must be at least 1")
    config = LLMConfig.load()
    if args.provider not in config.providers:
        raise SystemExit(f"Configured provider not found: {args.provider}")
    provider_config = config.providers[args.provider]
    if provider_config.api_key_env and not os.environ.get(provider_config.api_key_env):
        raise SystemExit(
            f"Required API key environment variable is not set: {provider_config.api_key_env}. "
            "Start this audit from a shell that loads the configured environment before creating a world instance."
        )
    if args.resume_db:
        db_path = args.resume_db.resolve()
        if not db_path.is_file():
            raise SystemExit(f"--resume-db does not exist: {db_path}")
    else:
        world_config = Path(__file__).parents[1] / "worlds" / "starter_village"
        runtime_root = Path("artifacts") / f"audit-runtime-{int(time.time())}"
        instance_id = "real-agent-audit"
        db_path = create_instance(world_config, instance_id, runtime_root)
    platform = Platform(db_path)
    checkpoint_path = args.checkpoint or args.output.with_name(args.output.stem + ".checkpoint.json")
    sessions: list[dict[str, Any]] = []
    resume_after_provider_cooldown = False
    for prior_path in (checkpoint_path, args.output):
        if args.resume_db and prior_path.is_file():
            try:
                prior = json.loads(prior_path.read_text(encoding="utf-8"))
                if not sessions:
                    sessions = list(prior.get("sessions") or [])
                stop_reason = str(
                    prior.get("stop_reason")
                    or ((prior.get("world") or {}).get("report") or {}).get("stop_reason")
                    or ""
                )
                resume_after_provider_cooldown = resume_after_provider_cooldown or stop_reason.startswith("provider_cooldown")
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                pass
    if args.resume_db and resume_after_provider_cooldown:
        scheduler_state = dict(platform.store.get_meta("runtime_scheduler_state", {}) or {})
        scheduler_state["provider_retry_pending"] = True
        with platform.store.transaction():
            platform.store.set_meta("runtime_scheduler_state", scheduler_state)

    def observe(turn: Any, actor_id: str) -> None:
        context = turn.initial_context or {}
        sessions.append(_safe_result(turn, context, actor_id))
        _write_json_atomic(checkpoint_path, {
            "status": "running",
            "provider": args.provider,
            "model": args.model or config.providers[args.provider].model,
            "db_path": str(db_path),
            "world_minute": platform.status()["world_minute"],
            "sessions": sessions,
        })

    try:
        start_minute = int(platform.status()["world_minute"])
        target_minute = int(args.target_minute) if args.target_minute is not None else start_minute + int(args.minutes)
        if target_minute <= start_minute:
            raise SystemExit(f"target minute {target_minute} must be greater than current minute {start_minute}")
        reports = []
        while int(platform.status()["world_minute"]) < target_minute:
            segment = platform.run(
                target_minute - int(platform.status()["world_minute"]),
                provider_name=args.provider,
                provider_model=args.model,
                provider_max_completion_tokens=args.max_completion_tokens,
                turn_observer=observe,
            )
            reports.append(segment)
            if int(platform.status()["world_minute"]) >= target_minute:
                break
            if not str(segment.stop_reason or "").startswith("provider_cooldown") or not args.auto_resume:
                break
            counts = _quality_counts(sessions)
            if counts["provider_errors"] >= max(1, int(args.max_provider_errors)):
                break
            _write_json_atomic(checkpoint_path, {
                "status": "provider_cooldown",
                "provider": args.provider,
                "model": args.model or config.providers[args.provider].model,
                "db_path": str(db_path),
                "world_minute": platform.status()["world_minute"],
                "target_minute": target_minute,
                "stop_reason": segment.stop_reason,
                "quality_counts": counts,
                "sessions": sessions,
            })
            time.sleep(max(1.0, float(args.cooldown_seconds)))
        report = _merge_reports(reports, target_minute=target_minute)
        causality = _causality(platform, sessions)
        _finalize_quality(sessions, causality)
        quality_counts = _quality_counts(sessions)
        unresolved_provider_errors = _unresolved_provider_errors(sessions)
        output = {
            "provider": args.provider,
            "model": args.model or config.providers[args.provider].model,
            "minutes_requested": args.minutes,
            "start_minute": start_minute,
            "target_minute": target_minute,
            "stop_reason": report.get("stop_reason"),
            "db_path": str(db_path),
            "sessions": sessions,
            "world": {
                "status": platform.status(),
                "health": platform.health(),
                "report": report,
                "metrics": platform.metrics(),
                "observer": platform.observer_view(event_limit=100),
                "agents": platform.store.list_agents(),
                "progress": platform.store.list_progress("world", limit=100),
                "causality": causality,
                "quality_counts": quality_counts,
                "unresolved_provider_errors": len(unresolved_provider_errors),
            },
        }
        _write_json_atomic(args.output, output)
        reached_target = int(output["world"]["status"]["world_minute"]) >= target_minute
        audit_complete = (
            reached_target
            and not quality_counts["failed"]
            and not quality_counts["pending"]
            and not unresolved_provider_errors
        )
        _write_json_atomic(checkpoint_path, {
            "status": "complete" if audit_complete else "stopped",
            "provider": args.provider,
            "model": args.model or config.providers[args.provider].model,
            "db_path": str(db_path),
            "world_minute": output["world"]["status"]["world_minute"],
            "target_minute": target_minute,
            "stop_reason": report.get("stop_reason"),
            "quality_counts": quality_counts,
            "unresolved_provider_errors": len(unresolved_provider_errors),
            "sessions": sessions,
        })
        failed = quality_counts["failed"]
        print(json.dumps({
            "audit_file": str(args.output),
            "db_path": str(db_path),
            "model": args.model or config.providers[args.provider].model,
            "world_minute": output["world"]["status"]["world_minute"],
            "sessions": len(sessions),
            "quality_failed": failed,
            "quality_pending": quality_counts["pending"],
            "unresolved_provider_errors": len(unresolved_provider_errors),
            "quality_by_role": {
                role: {
                    "sessions": sum(1 for item in sessions if item["role"] == role),
                    "passed": sum(1 for item in sessions if item["role"] == role and item["quality"].get("passed")),
                    "pending": sum(1 for item in sessions if item["role"] == role and item["quality"].get("pending")),
                    "provider_errors": sum(1 for item in sessions if item["role"] == role and ((item.get("result") or {}).get("failed_check") == "llm_response" or str(item["quality"].get("grade") or "").startswith("provider_"))),
                    "failed": sum(
                        1 for item in sessions
                        if item["role"] == role
                        and not item["quality"].get("passed")
                        and not item["quality"].get("pending")
                        and (item.get("result") or {}).get("failed_check") != "llm_response"
                        and not str(item["quality"].get("grade") or "").startswith("provider_")
                    ),
                }
                for role in ("agent_decision", "agent_reflection", "story", "god")
            },
            "report": report,
            "causality": output["world"]["causality"],
        }, ensure_ascii=False, indent=2))
        return 0 if output["world"]["status"]["status"] == "running" and audit_complete else 1
    finally:
        platform.close()


if __name__ == "__main__":
    raise SystemExit(main())
