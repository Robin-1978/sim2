"""Check NPC decisions against completed allocations in two world packs."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from sim2.agents.engine import AgentEngine
from sim2.agents.outcome_quality import evaluate_behavior_outcome
from sim2.agents.quality import evaluate_turn
from sim2.bootstrap.service import create_instance
from sim2.llm.config import LLMConfig
from sim2.llm.gateway import LLMRouter
from sim2.platform.runtime import Platform


WORLDS = Path(__file__).resolve().parents[1] / "worlds"
CASES = {
    "windward_skyfall": ("alan", 3, "emergency_copper_cache", "outpost_copper_cache", "supply_spare_plates"),
    "tidebound_commons": ("mara", 4, "emergency_water_cache", "relief_water_cache", "gate_supports"),
}
DEFENSE = "behavior:allocate_emergency_stock_defense"
RELIEF = "behavior:allocate_emergency_stock_relief"


def run_case(platform: Platform, world: str, provider: Any, *, relief_due: bool, through_window: bool = False) -> dict[str, Any]:
    actor, quantity, stock, destination, defense_metric = CASES[world]
    metadata = platform.store.repositories.metadata
    with platform.store.transaction():
        metadata.set("world_minute", 719)
        properties = metadata.get("world_properties")
        properties["supply_relief_due"] = int(relief_due)
        metadata.set("world_properties", properties)
    platform.step()  # The authored rule opens the consultation and delivers the batch.
    before = metadata.get("world_properties")
    minute = metadata.get("world_minute")
    deadline = minute + before["supply_minutes"]
    events = platform.store.repositories.events
    turns = []
    choices = []
    while True:
        start = metadata.get("world_minute")
        turn = AgentEngine(platform, provider).run(role="agent_decision", actor_id=actor)
        if not turns:
            visible = {item.get("ref") for item in turn.initial_context.get("executable_behaviors") or []}
            choices = sorted(visible & {DEFENSE, RELIEF})
        turns.append(turn)
        selected = turn.result.get("behavior_ref")
        deltas = {stock: -quantity}
        if selected == RELIEF:
            deltas[destination] = quantity
        while True:
            outcome = evaluate_behavior_outcome(
                turn, events, actor_id=actor, deadline_minute=deadline, resource_deltas=deltas, observed_minute=metadata.get("world_minute"),
            )
            if outcome["grade"] != "outcome_pending":
                break
            platform.step()
        if not through_window or (selected in choices and outcome["passed"]) or turn.result.get("failed_check") == "llm_response":
            break
        # An idle decision or a short action must consume time before a retry.
        # Bound retries by the configured deadline and a 30-minute interval.
        retry_at = min(deadline, start + 30)
        while metadata.get("world_minute") < retry_at:
            platform.step()
        if metadata.get("world_minute") >= deadline:
            break
    trace = evaluate_turn(turn, turn.initial_context)
    after = metadata.get("world_properties")
    checks = {
        "fixture_choices": choices == ([RELIEF] if relief_due else [DEFENSE, RELIEF]),
        "selected_allocation": selected in choices,
        "batch_consumed": before["emergency_stock"] == quantity and after["emergency_stock"] == 0,
        "world_consequence": (
            after["supply_relief_due"] == 0 and after["supply_consultation"] == 3 if selected == RELIEF else
            after["supply_relief_due"] == 1 and after["supply_consultation"] == 2 and after[defense_metric] > before[defense_metric]
        ),
    }
    usage = platform.store.repositories.llm.llm_cache_metrics()
    return {
        "world": world, "actor": actor, "relief_due": relief_due, "visible_choices": choices, "selected": selected,
        "trace_grade": trace["grade"], "failed_check": turn.result.get("failed_check"),
        "decisions": [item.result.get("behavior_ref") or item.result.get("status") for item in turns],
        "request_input_tokens_est": [item["request_input_tokens_est"] for review in turns for item in review.context_metrics],
        "provider_prompt_tokens": usage["prompt_tokens"] or None,
        "cache_measured_prompt_tokens": usage["provider_cache_prompt_tokens"],
        "cached_prompt_tokens": usage["cached_tokens"],
        "reused_snapshot_calls": sum(item.get("cache_snapshot_reused", False) for review in turns for item in review.context_metrics),
        "outcome": outcome, "checks": checks, "passed": outcome["passed"] and all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="remote", choices=["remote"])
    parser.add_argument("--runs", type=int, default=3, help="Repeated trials per world and condition")
    parser.add_argument("--through-window", action="store_true", help="Let the NPC decide again until allocation or the world deadline")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    config = LLMConfig.load()
    provider = config.providers.get(args.provider)
    if provider is None or (provider.api_key_env and not os.getenv(provider.api_key_env)):
        print(json.dumps({"status": "blocked", "provider": args.provider, "reason": "remote configuration or credentials unavailable"}))
        return 2
    router = LLMRouter(config)
    providers = {
        world: router.new_provider_for_name(args.provider, role="agent_decision") for world in CASES
    }
    results = []
    for trial in range(1, args.runs + 1):
        for world in CASES:
            for relief_due in (False, True):
                with tempfile.TemporaryDirectory(prefix="sim2-agent-outcome-") as runtime:
                    db = create_instance(WORLDS / world, "outcome-review", Path(runtime))
                    with closing(Platform(db)) as platform:
                        results.append({"trial": trial, **run_case(platform, world, providers[world], relief_due=relief_due, through_window=args.through_window)})
    passed = sum(result["passed"] for result in results)
    cached = sum(result["cached_prompt_tokens"] for result in results)
    measured = sum(result["cache_measured_prompt_tokens"] for result in results)
    print(json.dumps({
        "provider": args.provider, "passed": passed, "total": len(results),
        "cached_prompt_tokens": cached, "cache_measured_prompt_tokens": measured,
        "cache_hit_rate": round(cached / measured, 4) if measured else None, "trials": results,
    }, ensure_ascii=False, indent=2))
    return int(passed != len(results))


if __name__ == "__main__":
    raise SystemExit(main())
