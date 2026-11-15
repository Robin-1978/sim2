"""Verify direct food recovery and ingredient preparation with a real model.

Example:
    bash -ic 'SIM2_BGE_MODEL=disabled python scripts/verify_food_semantics.py --provider remote'

The output excludes credentials and raw hidden reasoning. It records the
rendered behavior catalog, selected choice, reason and observed state change.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sim2.agents.engine import AgentEngine
from sim2.agents.session import AgentTurn
from sim2.bootstrap.service import create_instance
from sim2.llm.config import LLMConfig
from sim2.llm.gateway import LLMRouter
from sim2.platform.runtime import Platform


WORLD = Path(__file__).parents[1] / "worlds" / "tidebound_commons"


def _catalog(view: dict[str, Any]) -> list[dict[str, Any]]:
    keys = ("name", "purpose", "impact", "drive_tags", "supports_drive_tags")
    return [
        {key: item.get(key) for key in keys if item.get(key) not in (None, "", [])}
        for item in view.get("executable_behaviors", [])
    ]


def _turn_record(turn: AgentTurn, view: dict[str, Any]) -> dict[str, Any]:
    calls = [
        step.get("response", {}).get("tool_calls") or []
        for step in turn.transcript
        if step.get("step") == "llm"
    ]
    return {
        "status": turn.result.get("status"),
        "failed_check": turn.result.get("failed_check"),
        "iterations": turn.iterations,
        "needs": view.get("needs"),
        "inventory": view.get("inventory"),
        "catalog": _catalog(view),
        "tool_calls": calls,
        "result": turn.result,
    }


def _finish(platform: Platform, actor_id: str) -> int:
    agent = platform.store.repositories.entities.get_agent(actor_id)
    state = agent.get("initial_state", {}) or {}
    minutes = int(state.get("remaining_minutes", 0) or 0)
    for _ in range(minutes + 3):
        platform.step()
    return minutes


def _prepare(platform: Platform, *, ration: int, shellfish: int = 2, fresh_water: int = 1) -> None:
    mara = platform.store.repositories.entities.get_agent("mara")
    mara["needs"]["hydration"] = 60
    mara["needs"]["satiety"] = 20
    mara["initial_state"]["location_id"] = "workshop"
    mara["inventory"].update({"shellfish": shellfish, "fresh_water": fresh_water, "food_ration": ration})
    platform.store.repositories.entities.update_agent("mara", mara)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="remote")
    parser.add_argument("--model", default=None)
    parser.add_argument("--runtime-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--max-completion-tokens", type=int, default=768)
    args = parser.parse_args()
    temporary = tempfile.TemporaryDirectory(prefix="sim2-food-semantics-") if args.runtime_dir is None else None
    runtime_root = args.runtime_dir or Path(temporary.name)
    runtime_root.mkdir(parents=True, exist_ok=True)
    output = args.output or Path("artifacts") / f"food-semantics-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    router = LLMRouter(LLMConfig.load())
    provider = router.new_provider_for_name(
        args.provider,
        args.model,
        max_completion_tokens=args.max_completion_tokens,
        role="agent_decision",
    )
    records: list[dict[str, Any]] = []
    scenarios = (
        ("direct_recovery", 1, 2, 1),
        ("prepare_then_eat", 0, 2, 1),
        ("forage_then_eat", 0, 0, 1),
    )
    for scenario, ration, shellfish, fresh_water in scenarios:
        instance_id = f"food-{scenario}-{datetime.now(timezone.utc):%H%M%S%f}"
        platform = Platform(create_instance(WORLD, instance_id, runtime_root))
        try:
            _prepare(platform, ration=ration, shellfish=shellfish, fresh_water=fresh_water)
            first_view = platform.decision_view("mara")
            first = AgentEngine(platform, provider).run(
                role="agent_decision", actor_id="mara", task_type="agent_plan_selection"
            )
            turn_records = [_turn_record(first, first_view)]
            selected = []
            changes: list[dict[str, Any]] = []
            if first.result.get("status") == "accepted":
                selected.append(first.result.get("behavior_ref") or first.result.get("choice"))
                changes.append({"minutes": _finish(platform, "mara")})
                agent = platform.store.repositories.entities.get_agent("mara")
                changes[-1].update({"needs": agent.get("needs"), "inventory": agent.get("inventory")})
                followups = 2 if scenario == "forage_then_eat" else (1 if scenario == "prepare_then_eat" else 0)
                for _ in range(followups):
                    next_view = platform.decision_view("mara")
                    next_turn = AgentEngine(platform, provider).run(
                        role="agent_decision", actor_id="mara", task_type="agent_plan_selection"
                    )
                    if next_turn.result.get("status") == "accepted":
                        selected.append(next_turn.result.get("behavior_ref") or next_turn.result.get("choice"))
                        changes.append({"minutes": _finish(platform, "mara")})
                        agent = platform.store.repositories.entities.get_agent("mara")
                        changes[-1].update({"needs": agent.get("needs"), "inventory": agent.get("inventory")})
                    turn_records.append(_turn_record(next_turn, next_view))
            records.append({"scenario": scenario, "turns": turn_records, "selected": selected, "changes": changes})
        finally:
            platform.close()

    result = {
        "provider": args.provider,
        "model": args.model or router.config.providers[args.provider].model,
        "records": records,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"output": str(output), "records": records}, ensure_ascii=False, default=str))
    passed = (
        all(turn["status"] == "accepted" for item in records for turn in item["turns"])
        and records[0]["selected"] == ["Eat Preserved Ration"]
        and records[1]["selected"] == ["Preserve Marsh Rations", "Eat Preserved Ration"]
        and records[2]["selected"] == ["Gather Shellfish", "Preserve Marsh Rations", "Eat Preserved Ration"]
        and all(item["changes"][-1]["needs"]["satiety"] > 45 for item in records)
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
