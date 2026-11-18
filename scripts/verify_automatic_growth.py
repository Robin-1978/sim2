"""Verify that a running world grows without harness-authored rounds.

The probe is the declarative Aster Relay world from
``audit_dynamic_evolution_loop`` with ``director.policy: llm`` and a generic
``god_structural`` contract.  Everything runs through ``Platform.run``: the
Director chooses a target, God proposes a growth unit, NPCs decide through the
ordinary Agent engine, and Story reacts to validated events.  The harness only
creates the world, advances time in chunks, and reads persisted state.

    python scripts/verify_automatic_growth.py --provider remote --minutes 360

Exit code 0 means at least two Director plans were opened, one plan completed,
two structural units were created, one new behavior was executed by an NPC, and
Story produced an accepted beat.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from sim2.agents.engine import AgentTurn
from sim2.bootstrap.service import create_instance_from_template
from sim2.llm.config import LLMConfig
from sim2.llm.gateway import LLMRouter
from sim2.platform.runtime import Platform
from sim2.world.growth import build_growth_ledger

PROBE_SCRIPT = Path(__file__).with_name("audit_dynamic_evolution_loop.py")


def _probe_template() -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location("sim2_growth_probe", PROBE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    template = module._probe_template(fallback=True, director=True)
    template["evolution"]["director"]["policy"] = "llm"
    return template


def _call_names(turn: AgentTurn) -> list[dict[str, Any]]:
    calls = []
    for item in list((turn.response or {}).get("tool_calls") or []):
        function = item.get("function") if isinstance(item, dict) else {}
        if not isinstance(function, dict):
            continue
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        interesting = {
            key: arguments.get(key)
            for key in ("name", "definition_type", "change_type", "entity_type", "assign_to", "target_cell")
            if arguments.get(key) is not None
        }
        calls.append({"name": function.get("name"), **interesting})
    return calls


def _observe(records: list[dict[str, Any]]):
    def observer(turn: AgentTurn, actor_id: str) -> None:
        records.append({
            "role": turn.role,
            "actor": actor_id,
            "task_type": turn.task_type,
            "status": turn.result.get("status"),
            "failed_check": turn.result.get("failed_check"),
            "iterations": turn.iterations,
            "calls": _call_names(turn),
            "prompt_chars": len(turn.prompt_history[0]) if turn.prompt_history else 0,
        })

    return observer


def _definitions(platform: Platform) -> dict[str, set[str]]:
    entities = platform.store.repositories.entities
    return {
        "action": {str(item.get("id")) for item in entities.get_definitions("action_def")},
        "behavior": {str(item.get("id")) for item in entities.get_definitions("bt_def")},
    }


def _state(platform: Platform, baseline: dict[str, Any]) -> dict[str, Any]:
    entities = platform.store.repositories.entities
    ledger = build_growth_ledger(platform.store)
    definitions = _definitions(platform)
    events = platform.store.repositories.events
    event_rows = platform.store.connection.execute(
        "SELECT event_type,actor_id,data_json FROM event_log "
        "WHERE event_type IN ('behavior.started','behavior.completed','story.beat.activated','entity.created','definition.registered')"
    ).fetchall()
    executed = set()
    for row in event_rows:
        if str(row["event_type"]) not in {"behavior.started", "behavior.completed"}:
            continue
        try:
            data = json.loads(row["data_json"] or "{}")
        except (TypeError, ValueError):
            data = {}
        behavior = str(data.get("behavior_id") or data.get("behavior_ref") or "").removeprefix("behavior:")
        if behavior:
            executed.add(behavior)
    new_actions = sorted(definitions["action"] - baseline["action"])
    new_behaviors = sorted(definitions["behavior"] - baseline["behavior"])
    new_behaviors_executed = sorted(set(new_behaviors) & executed)
    new_entities = []
    for row in platform.store.connection.execute(
        "SELECT id,entity_type,name FROM entity ORDER BY id"
    ).fetchall():
        if str(row["id"]) not in baseline["entities"]:
            new_entities.append({"id": row["id"], "type": row["entity_type"], "name": row["name"]})
    story_beats = sum(1 for row in event_rows if str(row["event_type"]) == "story.beat.activated")
    return {
        "world_minute": int(platform.store.repositories.metadata.get("world_minute", 0)),
        "season_plan": platform.store.repositories.metadata.get("season_plan", {}) or {},
        "last_completed_season_plan": platform.store.repositories.metadata.get("last_completed_season_plan", {}) or {},
        "plan_history": platform.store.repositories.metadata.get("season_plan_history", {}) or {},
        "ledger": {name: int(cell.get("count", 0) or 0) for name, cell in (ledger.get("cells") or {}).items()},
        "new_entities": new_entities,
        "new_actions": [item for item in entities.get_definitions("action_def") if str(item.get("id")) in new_actions],
        "new_behaviors": [item for item in entities.get_definitions("bt_def") if str(item.get("id")) in new_behaviors],
        "new_behaviors_executed": new_behaviors_executed,
        "story_beats": story_beats,
        "behavior_event_types": {
            key: int(value)
            for key, value in (events.event_type_counts() or {}).items()
            if key.startswith("behavior.") or key.startswith("story.")
        },
    }


def _baseline(platform: Platform) -> dict[str, Any]:
    return {
        "entities": {str(row["id"]) for row in platform.store.connection.execute("SELECT id FROM entity").fetchall()},
        **_definitions(platform),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="remote")
    parser.add_argument("--minutes", type=int, default=360)
    parser.add_argument("--chunk-minutes", type=int, default=120)
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--instance-id", default=None)
    args = parser.parse_args()

    router = LLMRouter(LLMConfig.load())
    providers = {
        role: router.new_provider_for_name(args.provider, role=role)
        for role in ("agent_decision", "agent_reflection", "director", "god", "story")
    }
    temporary = tempfile.TemporaryDirectory(prefix="sim2-auto-growth-") if args.runtime_dir is None else None
    runtime_root = Path(args.runtime_dir) if args.runtime_dir else Path(temporary.name)
    runtime_root.mkdir(parents=True, exist_ok=True)
    instance_id = args.instance_id or f"auto-growth-{uuid4().hex[:8]}"
    db_path = create_instance_from_template(_probe_template(), instance_id, runtime_root)
    platform = Platform(db_path)
    rows: list[dict[str, Any]] = []
    try:
        baseline = _baseline(platform)
        remaining = max(1, int(args.minutes))
        while remaining > 0:
            chunk = min(max(1, int(args.chunk_minutes)), remaining)
            records: list[dict[str, Any]] = []
            report = platform.run(chunk, providers=providers, turn_observer=_observe(records))
            state = _state(platform, baseline)
            director_calls = platform.store.connection.execute(
                "SELECT call_id,world_minute,response_json FROM llm_call WHERE role='director' ORDER BY call_id"
            ).fetchall()
            row = {
                "world_minute": state["world_minute"],
                "chunk_minutes": chunk,
                "stop_reason": report.stop_reason,
                "god_sessions": report.god_sessions,
                "story_sessions": report.story_sessions,
                "decisions": report.decisions,
                "director_plans": report.director_plans,
                "director_errors": report.director_errors,
                "director_calls": [
                    {"call_id": item["call_id"], "minute": item["world_minute"], "response": json.loads(item["response_json"] or "{}")}
                    for item in director_calls
                ],
                "season_plan": state["season_plan"],
                "ledger": state["ledger"],
                "new_entities": state["new_entities"],
                "new_actions": [{"id": item.get("id"), "name": item.get("name")} for item in state["new_actions"]],
                "new_behaviors": [{"id": item.get("id"), "label": item.get("agent_label")} for item in state["new_behaviors"]],
                "new_behaviors_executed": state["new_behaviors_executed"],
                "story_beats": state["story_beats"],
                "sessions": records,
            }
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            remaining -= max(0, int(state["world_minute"]) - (rows[-2]["world_minute"] if len(rows) > 1 else 0))
            if report.stop_reason:
                break
        final = _state(platform, baseline)
        history = {
            str(plan_id): plan
            for plan_id, plan in (final.get("plan_history") or {}).items()
            if isinstance(plan, dict)
        }
        if history:
            plan_ids = set(history)
            completed = [plan for plan in history.values() if plan.get("status") == "completed"]
        else:
            plan_ids = {
                str(row["season_plan"].get("plan_id"))
                for row in rows
                if row.get("season_plan") and row["season_plan"].get("plan_id")
            }
            completed = [
                item for item in [final.get("last_completed_season_plan")] + [row.get("last_completed_season_plan") for row in rows]
                if item and item.get("status") == "completed"
            ]
        director_total = len({item["call_id"] for row in rows for item in row["director_calls"]})
        flags: list[str] = []
        if director_total < 2:
            flags.append(f"director_calls:{director_total}<2")
        if not completed:
            flags.append("no_completed_season_plan")
        structural = len(final["new_entities"]) + len(final["new_actions"]) + len(final["new_behaviors"])
        if structural < 2:
            flags.append(f"structural_units:{structural}<2")
        if not final["new_behaviors_executed"]:
            flags.append("new_behavior_not_executed")
        if final["story_beats"] < 1:
            flags.append("no_story_beat")
        rejected = sum(1 for row in rows for item in row["sessions"] if item.get("status") == "rejected")
        summary = {
            "status": "passed" if not flags else "failed",
            "flags": flags,
            "world_minute": final["world_minute"],
            "director_calls": director_total,
            "plans_opened": len(plan_ids),
            "plans_completed": len(completed),
            "structural_units": structural,
            "rejected_sessions": rejected,
            "new_entities": final["new_entities"],
            "new_actions": [{"id": item.get("id"), "name": item.get("name")} for item in final["new_actions"]],
            "new_behaviors": [{"id": item.get("id"), "label": item.get("agent_label")} for item in final["new_behaviors"]],
            "new_behaviors_executed": final["new_behaviors_executed"],
            "story_beats": final["story_beats"],
            "ledger": final["ledger"],
            "season_plan": final["season_plan"],
            "last_completed_season_plan": final["last_completed_season_plan"],
        }
        output = runtime_root / f"{instance_id}.automatic-growth.json"
        output.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"summary": str(output), **summary}, ensure_ascii=False), flush=True)
        return 0 if not flags else 2
    finally:
        platform.close()
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
