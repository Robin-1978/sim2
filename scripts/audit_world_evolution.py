"""Audit a configured world's autonomous progression with real Agents.

The script keeps the ordinary Platform and SimulationRunner path intact.  It
records each round's Agent inputs and responses, world deltas, progression
views, resource state and LLM usage so a model run can be reviewed for causal
continuity rather than only for successful tool calls.

    bash -ic 'source ~/.bashrc && python scripts/audit_world_evolution.py \
      --config worlds/tidebound_commons --provider remote \
      --minutes 1440 --round-minutes 360 \
      --runtime-dir /tmp/sim2-tidebound-audit'
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

from sim2.bootstrap.service import create_instance
from sim2.llm.config import LLMConfig
from sim2.llm.gateway import LLMRouter
from sim2.platform.runtime import Platform


ROLES = ("agent_decision", "agent_reflection", "god", "story")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _turn_summary(turn: Any, actor_id: str) -> dict[str, Any]:
    transcript = []
    for item in list(turn.transcript or []):
        if not isinstance(item, dict):
            continue
        compact = {key: value for key, value in item.items() if key != "prompt"}
        transcript.append(compact)
    return {
        "actor_id": actor_id,
        "role": turn.role,
        "task_type": turn.task_type,
        "status": turn.result.get("status"),
        "failed_check": turn.result.get("failed_check"),
        "reason": turn.result.get("reason"),
        "iterations": turn.iterations,
        "result": turn.result,
        "response": turn.response,
        "transcript": transcript,
        "context": turn.initial_context,
        "prompt_chars": [len(item) for item in (turn.prompt_history or [])],
    }


def _new_events(platform: Platform, after_event_id: int) -> list[dict[str, Any]]:
    rows = platform.store.connection.execute(
        "SELECT event_id,event_type,actor_id,world_minute,data_json FROM event_log "
        "WHERE event_id>? AND event_type<>'world_minute.advanced' ORDER BY event_id",
        (max(0, int(after_event_id)),),
    ).fetchall()
    return [
        {
            "event_id": int(row["event_id"]),
            "event_type": str(row["event_type"]),
            "actor_id": row["actor_id"],
            "world_minute": int(row["world_minute"]),
            **json.loads(row["data_json"]),
        }
        for row in rows
    ]


def _usage(platform: Platform, after_call_id: int) -> dict[str, Any]:
    rows = platform.store.connection.execute(
        "SELECT role,COUNT(*) AS calls,COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,"
        "COALESCE(SUM(cached_tokens),0) AS cached_tokens,"
        "COALESCE(SUM(completion_tokens),0) AS completion_tokens,"
        "COALESCE(AVG(request_latency_ms),0) AS average_latency_ms,"
        "COALESCE(MAX(request_latency_ms),0) AS max_latency_ms "
        "FROM llm_call WHERE call_id>? GROUP BY role ORDER BY role",
        (int(after_call_id),),
    ).fetchall()
    return {
        str(row["role"]): {
            "calls": int(row["calls"]),
            "prompt_tokens": int(row["prompt_tokens"]),
            "cached_tokens": int(row["cached_tokens"]),
            "completion_tokens": int(row["completion_tokens"]),
            "average_latency_ms": round(float(row["average_latency_ms"]), 1),
            "max_latency_ms": round(float(row["max_latency_ms"]), 1),
        }
        for row in rows
    }


def _agent_states(platform: Platform) -> list[dict[str, Any]]:
    result = []
    for agent in platform.store.repositories.entities.list_agents():
        agent_id = str(agent["id"])
        view = platform.decision_view(agent_id)
        state = dict(agent.get("initial_state", {}) or {})
        result.append({
            "id": agent_id,
            "name": agent.get("name"),
            "location_id": state.get("location_id"),
            "status": state.get("status"),
            "needs": dict(agent.get("needs", {}) or {}),
            "inventory": dict(agent.get("inventory", {}) or {}),
            "progression": list(view.get("progression_goals") or []),
            "executable_behaviors": [
                item.get("label") or item.get("ref")
                for item in (view.get("executable_behaviors") or [])
            ],
        })
    return result


def _resource_states(platform: Platform) -> list[dict[str, Any]]:
    rules = platform.store.repositories.metadata.get("rules", {}) or {}
    types = rules.get("resource_entity_types", ["resource", "material"])
    result = []
    for entity_type in types:
        for row in platform.store.repositories.entities.entities_of_type(str(entity_type)):
            item = platform.store.repositories.entities.get_entity(str(row["id"]))
            state = dict(item.get("initial_state", {}) or {})
            result.append({
                "id": item.get("id"),
                "name": item.get("name"),
                "type": item.get("entity_type"),
                "location_id": state.get("location_id"),
                "quantity": item.get("amount", state.get("quantity", state.get("amount"))),
            })
    return result


def _final_summary(platform: Platform, rows: list[dict[str, Any]]) -> dict[str, Any]:
    events = platform.store.repositories.events.recent_events(limit=10000)
    event_counts = platform.store.repositories.events.event_type_counts()
    research = [
        item for item in events
        if str(item.get("event_type")) == "research.completed"
    ]
    research_keys = [
        (str(item.get("source_id") or ""), str(item.get("stage_id") or ""))
        for item in research
    ]
    return {
        "world_minute": int(platform.store.repositories.metadata.get("world_minute", 0)),
        "rounds": len(rows),
        "llm_cache": platform.store.repositories.llm.llm_cache_metrics()
        if hasattr(platform.store.repositories, "llm") else {},
        "event_counts": event_counts,
        "behavior_starts": platform.store.repositories.events.behavior_start_counts(),
        "research": {
            "completed": len(research),
            "distinct_sources_and_stages": len(set(research_keys)),
            "repeated_verified_stages": len(research_keys) - len(set(research_keys)),
        },
        "story_beats": event_counts.get("story.beat.activated", 0),
        "god_entities": event_counts.get("entity.created", 0),
        "god_definitions": event_counts.get("definition.registered", 0),
        "agents": _agent_states(platform),
        "resources": _resource_states(platform),
        "world_properties": platform.store.repositories.metadata.get("world_properties", {}),
        "round_empty": [row["round"] for row in rows if not row["events"] and not row["turns"]],
        "event_type_frequency": dict(Counter(item["event_type"] for row in rows for item in row["events"]).most_common()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--provider", default="remote")
    parser.add_argument("--model", default=None)
    parser.add_argument("--minutes", type=int, default=1440)
    parser.add_argument("--round-minutes", type=int, default=360)
    parser.add_argument("--runtime-dir", type=Path, default=None)
    parser.add_argument("--instance-id", default=None)
    parser.add_argument("--audit-output", type=Path, default=None)
    parser.add_argument("--resume", action="store_true", help="Continue an existing audited instance")
    args = parser.parse_args()

    temporary = tempfile.TemporaryDirectory(prefix="sim2-world-audit-") if args.runtime_dir is None else None
    runtime_root = args.runtime_dir or Path(temporary.name)
    runtime_root.mkdir(parents=True, exist_ok=True)
    instance_id = args.instance_id or f"world-audit-{uuid4().hex[:8]}"
    output_path = args.audit_output or (runtime_root / f"{instance_id}.evolution-audit.jsonl")
    summary_path = output_path.with_suffix(".summary.json")
    db_path = runtime_root / "instances" / instance_id / "world.sqlite3"
    if db_path.exists():
        if not args.resume:
            raise FileExistsError(f"world instance already exists: {instance_id}; pass --resume to continue")
    else:
        if args.resume:
            raise FileNotFoundError(f"world instance does not exist: {instance_id}")
        db_path = create_instance(args.config, instance_id, runtime_root)
    platform = Platform(db_path)

    router = LLMRouter(LLMConfig.load())
    providers = {
        role: router.new_provider_for_name(args.provider, args.model, role=role)
        for role in ROLES
    }
    turns: list[tuple[Any, str]] = []
    rows: list[dict[str, Any]] = []
    if args.resume and output_path.exists():
        rows = [
            json.loads(line)
            for line in output_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    try:
        remaining = max(1, int(args.minutes))
        round_no = max((int(row.get("round", 0)) for row in rows), default=0)
        while remaining > 0:
            round_no += 1
            minutes = min(max(1, int(args.round_minutes)), remaining)
            before_event_id_row = platform.store.connection.execute(
                "SELECT COALESCE(MAX(event_id),0) AS value FROM event_log"
            ).fetchone()
            before_call_id_row = platform.store.connection.execute(
                "SELECT COALESCE(MAX(call_id),0) AS value FROM llm_call"
            ).fetchone()
            before_event_id = int(before_event_id_row["value"])
            before_call_id = int(before_call_id_row["value"])
            turns.clear()
            report = platform.run(
                minutes,
                providers=providers,
                turn_observer=lambda turn, actor_id: turns.append((turn, actor_id)),
            )
            events = _new_events(platform, before_event_id)
            row = {
                "round": round_no,
                "requested_minutes": minutes,
                "world_minute": int(platform.store.repositories.metadata.get("world_minute", 0)),
                "report": report.as_dict(),
                "turns": [_turn_summary(turn, actor_id) for turn, actor_id in turns],
                "events": events,
                "usage": _usage(platform, before_call_id),
                "agents": _agent_states(platform),
                "resources": _resource_states(platform),
                "world_properties": platform.store.repositories.metadata.get("world_properties", {}),
            }
            rows.append(row)
            with output_path.open("a", encoding="utf-8") as stream:
                stream.write(_json(row) + "\n")
            print(_json({
                "round": round_no,
                "world_minute": row["world_minute"],
                "minutes_run": int(row["world_minute"]) - int(rows[round_no - 2]["world_minute"] if round_no > 1 else 0),
                "stop_reason": report.stop_reason,
                "turns": len(turns),
                "events": len(events),
                "usage": row["usage"],
                "progressed": [
                    {
                        "agent": agent["name"],
                        "ready": [item["label"] for item in agent["progression"] if item["status"] == "ready"],
                        "supporting": [item["label"] for item in agent["progression"] if item["status"] == "needs_support"],
                    }
                    for agent in row["agents"]
                    if any(item["status"] == "ready" for item in agent["progression"])
                ],
            }), flush=True)
            actual = max(0, int(row["world_minute"]) - (
                int(rows[round_no - 2]["world_minute"]) if round_no > 1 else 0
            ))
            remaining -= actual
            if report.stop_reason:
                break
        summary = _final_summary(platform, rows)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(_json({"summary": str(summary_path), **summary}), flush=True)
        return 0
    finally:
        platform.close()
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
