"""Run Director -> God -> NPC -> Story on the dynamic social probe.

The Director uses its separately configured role to choose the first target.
God creates the full social pack through the contract's atomic stages.  The
probe then runs real NPC decisions, executes the queued Story session and
advances the Director to the next plan.

This is an observational integration probe, not a Runtime gate.

    python scripts/run_full_social_loop.py --provider remote
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any

from sim2.agents.engine import AgentEngine
from sim2.bootstrap.service import create_instance
from sim2.llm.config import LLMConfig
from sim2.llm.gateway import LLMRouter
from sim2.platform.runtime import Platform

ROOT = Path(__file__).parents[1]
WORLD = ROOT / "worlds" / "dynamic_social_probe"
SPEC = importlib.util.spec_from_file_location("verify_dynamic_social_content", ROOT / "scripts" / "verify_dynamic_social_content.py")
vdsc = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(vdsc)


def _advance_until_idle(platform: Platform, actor_id: str, limit: int = 20) -> None:
    for _ in range(limit):
        state = platform.store.repositories.entities.get_agent(actor_id).get("initial_state", {})
        if str(state.get("status") or "") not in {"executing", "paused"}:
            return
        platform.step()


def _npc_task(platform: Platform, actor_id: str) -> str:
    pending = platform.store.repositories.authorization.list_requests(status="pending", limit=10)
    violations = platform.store.repositories.contested.list_violations(statuses=["observed"], limit=10)
    if actor_id == "mira":
        if violations:
            return "An observed violation is in your view. Choose one behavior available to you now; review it or act otherwise."
        return "You steward the shared Water Reserve. Choose one behavior available to you now; fill it if empty or act otherwise."
    if pending:
        return "Your access request is pending. Choose one behavior available to you now."
    return "You need water from the shared reserve. Choose one behavior available to you now; request access, take water, or act otherwise."


def _run_npc_turns(platform: Platform, router: LLMRouter, provider: str, model: str | None, rounds: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for round_index in range(1, max(1, rounds) + 1):
        for actor in ("mira", "tomas"):
            turn = AgentEngine(
                platform,
                router.new_provider_for_name(provider, model=model, role="agent_decision"),
            ).run(role="agent_decision", actor_id=actor, task_type=_npc_task(platform, actor))
            calls = [
                {
                    "name": step.get("name"),
                    "arguments": step.get("arguments") or {},
                    "status": (step.get("result") or {}).get("status"),
                    "failed_check": (step.get("result") or {}).get("failed_check"),
                }
                for step in turn.transcript
                if step.get("step") == "tool"
            ]
            _advance_until_idle(platform, actor)
            records.append({"round": round_index, "actor": actor, "result": turn.result, "tool_calls": calls})
    return records


STORY_EVENT_TYPES = {
    "violation.observed",
    "resource.taken",
    "access.requested",
    "access.approved",
    "access.rejected",
    "resource.changed",
    "dialogue.completed",
    "resource.withdrawal.completed",
}


def _queue_story_wakes(platform: Platform) -> None:
    """Publish configured Story wakes for events that used the low-level append path."""

    events = platform.store.repositories.events.timeline_events(after_event_id=0, limit=1000, include_ticks=False)
    for event in events:
        event_type = str(event.get("event_type") or "")
        if event_type not in STORY_EVENT_TYPES:
            continue
        platform.wakes.configured(
            int(event.get("event_id") or 0),
            event_type,
            str(event.get("actor_id") or "") or None,
            int(event.get("world_minute") or 0),
        )


def _run_story_tasks(platform: Platform, router: LLMRouter, provider: str, model: str | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    tasks = [item for item in platform.ready_evolution_tasks() if item.get("role") == "story"]
    for task in tasks:
        source_events = [item for item in (task.get("source_events") or []) if isinstance(item, dict)]
        provider_obj = router.new_provider_for_name(provider, model=model, role="story")
        turn = AgentEngine(platform, provider_obj).run(
            role="story",
            actor_id="story",
            task_type=str(task.get("task_type") or "story"),
            source_event_id=int(task["source_event_id"]) if task.get("source_event_id") is not None else None,
            source_events=source_events,
        )
        if turn.result.get("status") in {"accepted", "wait"}:
            platform.complete_evolution_task(str(task["task_id"]))
        records.append({
            "task_id": task.get("task_id"),
            "source_event_type": task.get("source_event_type"),
            "result": turn.result,
            "response": turn.response,
        })
    return records


def run(platform: Platform, router: LLMRouter, provider: str, model: str | None, *, rounds: int = 2) -> dict[str, Any]:
    report: dict[str, Any] = {"director": {}, "god": {}, "npc": [], "story": [], "next_director": {}}
    with platform.store.transaction():
        report["director"]["plan"] = platform._services["director"].plan_with_provider(
            router.new_provider_for_name(provider, model=model, role="director"),
            5,
        )
    tasks = [item for item in platform.ready_evolution_tasks() if item.get("task_type") == "god_structural"]
    report["director"]["god_task"] = tasks[-1] if tasks else None
    if not tasks:
        report["god"]["status"] = "no_task"
        return report
    task = tasks[-1]
    turn = vdsc._run_turn(
        platform,
        router,
        provider,
        model,
        str(task["task_type"]),
        task.get("source_events") or [],
        vdsc.BROAD_INSTRUCTION,
        max_iterations=24,
        max_tool_calls=40,
        max_read_tool_calls=2,
    )
    report["god"] = {
        "status": turn.result.get("status"),
        "iterations": turn.iterations,
        "tool_calls": vdsc._tool_calls(turn),
        "content": {
            "resources": [item["id"] for item in platform.store.repositories.entities.entities_of_type("resource")],
            "actions": [item.get("id") for item in platform.store.repositories.entities.get_definitions("action_def")],
            "behaviors": [item.get("id") for item in platform.store.repositories.entities.get_definitions("bt_def")],
        },
    }
    if turn.result.get("status") in {"accepted", "wait"}:
        platform.complete_evolution_task(str(task["task_id"]))
    report["npc"] = _run_npc_turns(platform, router, provider, model, rounds)
    _queue_story_wakes(platform)
    report["story"] = _run_story_tasks(platform, router, provider, model)
    for _ in range(20):
        platform.step()
        if platform.store.repositories.metadata.get("last_completed_season_plan"):
            break
    report["next_director"] = {
        "last_completed": platform.store.repositories.metadata.get("last_completed_season_plan"),
        "season_plan": platform.store.repositories.metadata.get("season_plan"),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="remote")
    parser.add_argument("--model", default=None)
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--rounds", type=int, default=2)
    args = parser.parse_args()
    config = LLMConfig.load()
    provider = config.providers.get(args.provider)
    if provider is None:
        print(json.dumps({"status": "invalid_probe", "reason": f"unknown provider: {args.provider}"}, indent=2))
        return 2
    router = LLMRouter(config)
    temporary = tempfile.TemporaryDirectory(prefix="sim2-full-social-") if args.runtime_dir is None else None
    runtime = Path(args.runtime_dir) if args.runtime_dir else Path(temporary.name)
    platform = Platform(create_instance(WORLD, "full-social-loop", runtime))
    try:
        report = run(platform, router, args.provider, args.model, rounds=args.rounds)
        print(json.dumps(report, ensure_ascii=False, default=str, indent=2))
        return 0
    finally:
        platform.close()
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
