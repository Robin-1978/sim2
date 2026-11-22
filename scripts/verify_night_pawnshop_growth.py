"""Probe the living-world loop: Director plan -> God case -> Story beat -> player action.

This is not a fixed-script test.  The remote Director chooses a growth cell, the
remote God must grow one playable pawnshop case from that plan, and the remote
Story role reacts to the verified result.  The probe then checks that the player
view contains a new executable action and a readable item label.

    python scripts/verify_night_pawnshop_growth.py --provider remote
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from sim2.agents.engine import AgentEngine, AgentTurn
from sim2.api.service import ControlService
from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.llm.config import LLMConfig
from sim2.llm.gateway import LLMRouter
from sim2.platform.runtime import Platform

WORLD = Path(__file__).parents[1] / "worlds" / "night_pawnshop"


def _call_names(turn: AgentTurn | None) -> list[str]:
    if turn is None:
        return []
    response = turn.response or {}
    names = []
    for item in response.get("tool_calls") or []:
        function = item.get("function") if isinstance(item, dict) else {}
        if isinstance(function, dict) and function.get("name"):
            names.append(str(function["name"]))
    return names


def _dynamic_state(platform: Platform) -> dict[str, Any]:
    items = platform.store.repositories.entities.entities_of_type("item")
    agents = platform.store.repositories.entities.list_agents()
    behaviors = [
        item for item in platform.store.repositories.entities.get_definitions("bt_def")
        if any(str(node.get("action_id") or "").startswith("dynamic_") for node in item.get("nodes") or [])
    ]
    return {
        "items": [{"id": item["id"], "name": item["name"]} for item in items],
        "agents": [{"id": item["id"], "name": item.get("name")} for item in agents],
        "behaviors": [
            {
                "id": item.get("id"),
                "label": item.get("agent_label"),
                "actions": [node.get("action_id") for node in item.get("nodes") or []],
            }
            for item in behaviors
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="remote")
    parser.add_argument("--max-completion-tokens", type=int, default=None)
    parser.add_argument("--runtime-dir", default=None)
    args = parser.parse_args()

    router = LLMRouter(LLMConfig.load())
    director = router.new_provider_for_name(args.provider, max_completion_tokens=args.max_completion_tokens, role="director")
    god = router.new_provider_for_name(args.provider, max_completion_tokens=args.max_completion_tokens, role="god")
    story = router.new_provider_for_name(args.provider, max_completion_tokens=args.max_completion_tokens, role="story")

    temporary = tempfile.TemporaryDirectory(prefix="sim2-night-growth-") if not args.runtime_dir else None
    root = Path(args.runtime_dir) if args.runtime_dir else Path(temporary.name)
    root.mkdir(parents=True, exist_ok=True)
    db = create_instance(WORLD, f"night-growth-{uuid4().hex[:8]}", root)
    platform = Platform(db)
    service = ControlService(db)
    result: dict[str, Any] = {"status": "failed", "provider": args.provider}
    try:
        assert platform.enter_player("hero", "old_hand", name="林晚")["status"] == "accepted"
        # Start with a fast plan boundary; this is a probe, not the shipped cadence.
        with platform.store.transaction():
            evolution = dict(platform.store.repositories.metadata.get("evolution") or {})
            root_evolution = evolution.get("evolution", evolution)
            director_config = dict(root_evolution.get("director") or {})
            director_config["cadence_minutes"] = 1
            director_config["window_minutes"] = 60
            director_config["targets"] = ["social", "verb"]
            root_evolution["director"] = director_config
            for wake in root_evolution.get("wakes") or []:
                if wake.get("id") == "story_after_case":
                    wake["count"] = 1
            platform.store.repositories.metadata.set("evolution", evolution)

        plan = platform.plan_structure_with_provider(director, 1)
        result["plan"] = plan
        tasks = [item for item in platform.ready_evolution_tasks() if item.get("task_type") == "god_structural"]
        if not tasks:
            result["message"] = "Director did not queue a god_structural task."
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 2
        task = tasks[-1]
        before = _dynamic_state(platform)
        god_turn = AgentEngine(platform, god, max_iterations=24, max_tool_calls=30).run(
            role="god",
            actor_id="god",
            task_type=str(task["task_type"]),
            source_events=task.get("source_events") or [],
        )
        result["god"] = {
            "status": god_turn.result.get("status"),
            "failed_check": god_turn.result.get("failed_check"),
            "reason": god_turn.result.get("reason"),
            "calls": _call_names(god_turn),
            "iterations": god_turn.iterations,
            "transcript": [
                {key: (str(value)[:900] if isinstance(value, (dict, list)) else value) for key, value in entry.items()}
                for entry in (god_turn.transcript or [])[-12:]
            ],
        }
        if god_turn.result.get("status") in {"accepted", "wait"}:
            platform.complete_evolution_task(str(task["task_id"]))
        after = _dynamic_state(platform)
        result["before"] = before
        result["after"] = after

        story_turns = []
        for story_task in [item for item in platform.ready_evolution_tasks() if item.get("role") == "story"][:2]:
            turn = AgentEngine(platform, story).run(
                role="story",
                actor_id="story",
                task_type=str(story_task["task_type"]),
                source_events=story_task.get("source_events") or [],
            )
            if turn.result.get("status") in {"accepted", "wait"}:
                platform.complete_evolution_task(str(story_task["task_id"]))
            story_turns.append({"status": turn.result.get("status"), "calls": _call_names(turn)})
        result["story"] = story_turns

        view = service.player_view("hero")
        dynamic_refs = {f"behavior:{item['id']}" for item in after["behaviors"]}
        dynamic_actions = [
            {"ref": action.get("ref"), "name": action.get("name")}
            for action in view.get("actions", [])
            if str(action.get("ref") or "") in dynamic_refs
        ]
        result["player_actions"] = dynamic_actions
        result["dynamic_item_labels"] = {
            item["id"]: (view.get("item_labels") or {}).get(item["id"])
            for item in after["items"]
            if item["id"] not in {entry["id"] for entry in before["items"]}
        }
        pawn_action = next((action for action in dynamic_actions if "收下" in str(action.get("name") or "")), None)
        living_pawn = None
        living_sell = None
        living_story = []
        if pawn_action:
            started = platform.submit_behavior(BehaviorIntent(
                actor_id="hero",
                behavior_ref=str(pawn_action["ref"]),
                target={},
                parameters={},
                reason="The living case is waiting at the counter.",
                confidence=0.9,
                intent_id="probe-pawn",
            ))
            if started.get("status") == "accepted":
                agent = platform.store.repositories.entities.get_agent("hero")
                for _ in range(max(1, int((agent.get("initial_state") or {}).get("remaining_minutes", 0) or 0))):
                    platform.step()
                living_pawn = {
                    "status": "accepted",
                    "inventory": platform.store.repositories.entities.get_agent("hero").get("inventory"),
                    "properties": {
                        key: (platform.store.repositories.metadata.get("world_properties") or {}).get(key)
                        for key in ("dyn_case_1", "night_actions")
                    },
                }
                view = service.player_view("hero")
                sell_action = next(
                    (
                        action for action in view.get("actions", [])
                        if any(
                            str(node.get("action_id") or "") == "dynamic_sell"
                            for behavior in platform.store.repositories.entities.get_definitions("bt_def")
                            if str(behavior.get("id") or "") == str(action.get("ref") or "").removeprefix("behavior:")
                            for node in behavior.get("nodes") or []
                        )
                    ),
                    None,
                )
                if sell_action:
                    sold = platform.submit_behavior(BehaviorIntent(
                        actor_id="hero",
                        behavior_ref=str(sell_action["ref"]),
                        target={},
                        parameters={},
                        reason="The buyer for the pawned item is waiting.",
                        confidence=0.9,
                        intent_id="probe-sell",
                    ))
                    if sold.get("status") == "accepted":
                        agent = platform.store.repositories.entities.get_agent("hero")
                        for _ in range(max(1, int((agent.get("initial_state") or {}).get("remaining_minutes", 0) or 0))):
                            platform.step()
                        living_sell = {
                            "status": "accepted",
                            "inventory": platform.store.repositories.entities.get_agent("hero").get("inventory"),
                            "properties": {
                                key: (platform.store.repositories.metadata.get("world_properties") or {}).get(key)
                                for key in ("dyn_case_1", "chapel_rep", "watch_rep", "collector_rep", "night_actions")
                            },
                        }
                for story_task in [item for item in platform.ready_evolution_tasks() if item.get("role") == "story"][:1]:
                    turn = AgentEngine(platform, story).run(
                        role="story",
                        actor_id="story",
                        task_type=str(story_task["task_type"]),
                        source_events=story_task.get("source_events") or [],
                    )
                    if turn.result.get("status") in {"accepted", "wait"}:
                        platform.complete_evolution_task(str(story_task["task_id"]))
                    living_story.append({"status": turn.result.get("status"), "calls": _call_names(turn)})
        result["living_pawn"] = living_pawn
        result["living_sell"] = living_sell
        result["living_story"] = living_story

        created_items = len(after["items"]) > len(before["items"])
        created_agents = len(after["agents"]) > len(before["agents"])
        created_behaviors = len(after["behaviors"]) > len(before["behaviors"])
        passed = (
            god_turn.result.get("status") == "accepted"
            and created_items
            and created_agents
            and created_behaviors
            and bool(dynamic_actions)
            and living_pawn is not None
            and living_sell is not None
        )
        result["status"] = "passed" if passed else "failed"
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0 if passed else 2
    finally:
        service.close()
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
