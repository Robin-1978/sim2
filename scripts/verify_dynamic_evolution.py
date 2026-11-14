"""Fast God -> Platform -> NPC -> Story evolution check.

This is intentionally much smaller than a long town run.  It seeds one
validated low-reserve event, lets a real God session create a new Action and
BT, executes that BT, then lets Story interpret the resulting world event.

    python scripts/verify_dynamic_evolution.py --provider remote
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from sim2.agents.engine import AgentEngine, AgentTurn
from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.llm.config import LLMConfig
from sim2.llm.gateway import LLMRouter
from sim2.platform.runtime import Platform


def _calls(turn: AgentTurn) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for item in list((turn.response or {}).get("tool_calls") or []):
        function = item.get("function") if isinstance(item, dict) else {}
        if not isinstance(function, dict):
            continue
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                pass
        calls.append({"name": function.get("name"), "arguments": arguments})
    return calls


def _summary(turn: AgentTurn) -> dict[str, Any]:
    return {
        "status": turn.result.get("status"),
        "failed_check": turn.result.get("failed_check"),
        "message": turn.result.get("message"),
        "iterations": turn.iterations,
        "calls": _calls(turn),
        "prompt_chars": len(turn.prompt_history[0]) if turn.prompt_history else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="remote")
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument(
        "--max-completion-tokens",
        type=int,
        default=None,
        help="Override the role limit for this check; omit to use roles.god/story from ~/.sim2/llm.json.",
    )
    args = parser.parse_args()

    world = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    router = LLMRouter(LLMConfig.load())
    god_provider = router.new_provider_for_name(args.provider, max_completion_tokens=args.max_completion_tokens, role="god")
    story_provider = router.new_provider_for_name(args.provider, max_completion_tokens=args.max_completion_tokens, role="story")
    temporary = tempfile.TemporaryDirectory(prefix="sim2-dynamic-evolution-") if args.runtime_dir is None else None
    runtime_root = Path(args.runtime_dir) if args.runtime_dir else Path(temporary.name)
    instance_id = f"dynamic-evolution-check-{uuid4().hex[:8]}"
    platform = Platform(create_instance(world, instance_id, runtime_root))
    try:
        agent = platform.store.get_agent("mei_lan")
        agent["inventory"] = {**(agent.get("inventory") or {}), "蓄水": 2}
        platform.store.update_agent("mei_lan", agent)
        pool = platform.store.get_entity("cistern_water")
        pool["initial_state"]["quantity"] = 0
        platform.store.put_entity(pool["id"], pool["entity_type"], pool["name"], {key: value for key, value in pool.items() if key not in {"id", "entity_type", "name"}})

        with platform.store.transaction():
            safety_event_id = platform.store.append_event(
                "resource.safety_low",
                None,
                1,
                {
                    "resource_id": "cistern_water",
                    "quantity": 0,
                    "minimum_reserve": 12,
                    "summary": "蓄水已耗尽，梅岚携带的蓄水可以补入公共池。",
                },
            )
        platform.wakes.configured(safety_event_id, "resource.safety_low", None, 1)
        god_task = next(item for item in platform.ready_evolution_tasks() if item.get("task_type") == "event_group:resource_safety")
        god_turn = AgentEngine(platform, god_provider).run(
            role="god",
            actor_id="god",
            task_type=god_task["task_type"],
            source_event_id=safety_event_id,
            source_events=[{"event_id": safety_event_id, "event_type": "resource.safety_low", "actor_id": None}],
        )
        if god_turn.result.get("status") not in {"accepted", "wait"}:
            raise RuntimeError(f"God evolution failed: {god_turn.result}")
        if god_turn.result.get("status") == "wait":
            raise RuntimeError("God waited instead of creating a valid carried-resource transfer path")
        platform.complete_evolution_task(god_task["task_id"])
        behavior_id = str(god_turn.result.get("bound_behavior_id") or "")
        if not behavior_id:
            behavior_id = next(
                str(item["id"])
                for item in platform.store.get_definitions("bt_def")
                if item.get("definition_source") == "god_proposal" and item.get("agent_label")
            )
        before_pool = float(platform.store.get_entity("cistern_water")["initial_state"]["quantity"])
        started = platform.submit_behavior(BehaviorIntent(
            "mei_lan",
            f"behavior:{behavior_id}",
            {},
            {},
            "Transfer carried water into the public cistern.",
            0.9,
            "dynamic-evolution-npc-execution",
        ))
        if started.get("status") != "accepted":
            raise RuntimeError(f"NPC could not execute God behavior: {started}")
        for _ in range(int(started.get("remaining_minutes", 1)) + 4):
            platform.step()
        after_pool = float(platform.store.get_entity("cistern_water")["initial_state"]["quantity"])
        remaining_water = float(platform.store.get_agent("mei_lan").get("inventory", {}).get("蓄水", 0))
        if after_pool <= before_pool or remaining_water >= 2:
            raise RuntimeError(f"resource transfer did not close the world loop: pool={after_pool}, water={remaining_water}")

        story_events = [
            event for event in platform.store.recent_events(limit=100)
            if str(event.get("actor_id") or "") == "mei_lan" and event.get("story_source")
        ]
        if not story_events:
            raise RuntimeError(
                "God-created behavior changed the resource but did not publish a Story-wakable outcome event"
            )
        story_event = max(story_events, key=lambda item: int(item.get("event_id", 0) or 0))
        story_event_id = int(story_event["event_id"])
        story_task = next(
            (
                item
                for item in platform.ready_evolution_tasks()
                if item.get("role") == "story" and int(item.get("source_event_id", 0) or 0) == story_event_id
            ),
            None,
        )
        if not story_task:
            raise RuntimeError(f"Story was not queued for the actual outcome event {story_event_id}")
        story_turn = AgentEngine(platform, story_provider).run(
            role="story",
            actor_id="story",
            task_type=story_task["task_type"],
            source_event_id=story_event_id,
            source_events=story_task.get("source_events") or [],
        )
        if story_turn.result.get("status") not in {"accepted", "wait"}:
            raise RuntimeError(f"Story continuation failed: {story_turn.result}")
        god_call = next((item for item in _calls(god_turn) if item.get("name") == "propose_definition"), None)
        god_followup = ((god_call or {}).get("arguments") or {}).get("followup_behavior") or {}
        god_outcome_events = list((god_followup.get("definition") or {}).get("outcome_events") or []) if isinstance(god_followup, dict) else []
        story_call = next((item for item in _calls(story_turn) if item.get("name") == "propose_story_beat"), None)
        story_arguments = (story_call or {}).get("arguments") or {}
        context_audit = {
            "god_trigger_present": any(
                str(item.get("event_type") or "") == "resource.safety_low"
                for item in (god_turn.initial_context.get("triggering_evidence") or [])
            ),
            "god_conservation_contract_present": "resource_transfer" in "\n".join(god_turn.prompt_history),
            "god_proposal_binds_action_and_behavior": bool(god_call and god_followup),
            "god_proposal_declares_story_wake": any(
                "story" in {str(role) for role in (event.get("wake") or [])}
                for event in god_outcome_events
                if isinstance(event, dict)
            ),
            "story_received_actual_outcome": any(
                int(item.get("event_id", 0) or 0) == story_event_id
                for item in [story_turn.initial_context.get("last_platform_result") or {}]
            ),
            "story_output_has_grounded_followup": bool(
                story_arguments.get("fact")
                and story_arguments.get("summary")
                and story_arguments.get("goal_hint")
                and story_arguments.get("audience")
            ),
        }
        if not all(context_audit.values()):
            raise RuntimeError(f"evolution context/output audit failed: {context_audit}")
        report = {
            "status": "passed",
            "instance_id": instance_id,
            "database": str(runtime_root / "instances" / instance_id / "world.sqlite3"),
            "world_minute": int(platform.store.get_meta("world_minute", 0)),
            "god": _summary(god_turn),
            "npc_behavior": started,
            "pool_before": before_pool,
            "pool_after": after_pool,
            "carried_water_after": remaining_water,
            "context_audit": context_audit,
            "story": _summary(story_turn),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        platform.close()
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
