"""Run a real-model NPC/Story/God closed-loop audit through the formal runner.

Each audit round advances the world with ``SimulationRunner.run`` (the same
scheduler used by ``sim2 simulate``), so world time, behavior progress,
evolution tasks and NPC decisions all follow production code. A
``turn_observer`` captures every real Agent session (prompt in, model output
and Platform result out) for review. Creation-time setup contains only world
structure; no evidence, institution or God-authored behavior is injected.

Each completed round is printed as one JSON line for live review.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from sim2.agents.engine import AgentTurn
from sim2.bootstrap.service import create_instance
from sim2.llm.config import LLMConfig
from sim2.llm.gateway import LLMRouter
from sim2.platform.runtime import Platform
from sim2.simulation.runner import SimulationRunner


def _session(turn: AgentTurn, actor_id: str, *, full: bool = False) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    for item in list((turn.response or {}).get("tool_calls") or []):
        function = item.get("function") if isinstance(item, dict) else {}
        if isinstance(function, dict):
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except (TypeError, json.JSONDecodeError):
                arguments = function.get("arguments")
            calls.append({"name": function.get("name"), "arguments": arguments})
    session = {
        "role": turn.role,
        "actor": actor_id,
        "task_type": turn.task_type,
        "status": turn.result.get("status"),
        "failed_check": turn.result.get("failed_check"),
        "message": turn.result.get("message"),
        "reason": turn.result.get("reason"),
        "iterations": turn.iterations,
        "model_calls": calls,
        "result": {k: v for k, v in turn.result.items() if k in {
            "choice", "beat", "summary", "fact", "goal_hint", "name", "purpose",
            "definition_type", "assigned_to", "evidence", "status", "event_id",
            "entity_id", "institution", "allocations", "consumed", "remaining",
        } and v is not None},
        "prompt_chars": len(turn.prompt_history[0]) if turn.prompt_history else 0,
        "prompt_head": (turn.prompt_history[0][:1200] if turn.prompt_history else ""),
    }
    if full:
        # Keep the complete causal exchange for audit/review. This is opt-in
        # because a ten-round run can contain hundreds of prompts and tool
        # results; no secrets are present in Platform prompts or normalized
        # provider responses.
        session.update({
            "prompt_history": list(turn.prompt_history),
            "model_response": turn.response,
            "initial_context": turn.initial_context,
            "transcript": turn.transcript,
        })
    return session


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default=None, help="Override every role with one configured provider (e.g. remote)")
    parser.add_argument("--provider-max-completion-tokens", type=int, default=None, help="Override the per-role completion cap for an explicit provider override")
    parser.add_argument("--world", default="three_lanterns_town", help="Content package under worlds/")
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--minutes-per-round", type=int, default=120)
    parser.add_argument(
        "--full-audit",
        action="store_true",
        help="Persist complete prompts, model responses, tool results and transcripts for every session.",
    )
    parser.add_argument(
        "--runtime-dir",
        default=None,
        help="Keep the SQLite instance under this directory for post-run inspection; temporary storage is used by default.",
    )
    args = parser.parse_args()

    config_path = Path(__file__).parents[1] / "worlds" / args.world
    router = LLMRouter(LLMConfig.load())

    def provider(role: str) -> Any:
        return router.new_provider_for_name(args.provider, max_completion_tokens=args.provider_max_completion_tokens, role=role) if args.provider else router.provider_for_role(role)

    providers = {role: provider(role) for role in ("agent_decision", "agent_reflection", "god", "story")}
    sessions: list[dict[str, Any]] = []

    def observe(turn: AgentTurn, actor_id: str) -> None:
        sessions.append(_session(turn, actor_id, full=args.full_audit))

    temporary_runtime = tempfile.TemporaryDirectory(prefix="sim2-real-audit-") if args.runtime_dir is None else None
    runtime_root = Path(args.runtime_dir) if args.runtime_dir is not None else Path(temporary_runtime.name)
    try:
        platform = Platform(create_instance(config_path, "real-audit", runtime_root))
        try:
            runner = SimulationRunner(platform, providers, turn_observer=observe)
            for round_no in range(1, max(1, args.rounds) + 1):
                sessions.clear()
                before = float(platform.store.get_entity("cistern_water")["initial_state"]["quantity"])
                event_cursor = int(platform.store.connection.execute("SELECT COALESCE(MAX(event_id), 0) FROM event_log").fetchone()[0])
                report = runner.run(args.minutes_per_round)
                after = float(platform.store.get_entity("cistern_water")["initial_state"]["quantity"])
                events = [
                    event for event in platform.store.recent_events(limit=2000)
                    if int(event.get("event_id", 0) or 0) > event_cursor
                ]
                row = {
                    "round": round_no,
                    "world_minute": int(platform.store.get_meta("world_minute", 0)),
                    "stop_reason": report.stop_reason,
                    "decisions": report.decisions,
                    "waits": report.waits,
                    "llm_errors": report.llm_errors,
                    "event_cursor": event_cursor,
                    "event_count": len(events),
                    "sessions": list(sessions),
                    "reserve_before": before,
                    "reserve_after": after,
                    "behavior_completed": [
                        {"minute": e["world_minute"], "behavior": e.get("behavior_id"), "result": e.get("result")}
                        for e in events if e["event_type"] == "behavior.completed"
                    ],
                    "ledger_entries": len([e for e in events if e["event_type"] == "institution.ledger_entry"]),
                    "allocations": len([e for e in events if e["event_type"] == "institution.allocation_completed"]),
                    "institutions": [
                        {"name": i.get("name"), "status": i.get("status")}
                        for i in platform.store.list_institutions(limit=10)
                    ],
                    "registered_god_behaviors": [
                        {"id": d.get("id"), "label": d.get("agent_label")}
                        for d in platform.store.get_definitions("bt_def")
                        if d.get("definition_source") == "god_proposal"
                    ],
                    "world_properties": platform.store.get_meta("world_properties", {}),
                }
                print(json.dumps(row, ensure_ascii=False), flush=True)
        finally:
            platform.close()
    finally:
        if temporary_runtime is not None:
            temporary_runtime.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
