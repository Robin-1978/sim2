"""Minimal Director-driven growth test: social, then rule, no research escape.

The Director selects the least-covered cell and queues one ``god_structural``
task.  God must fill that exact cell.  The check advances the plan after the
cell grows, then requires the next cell.  No tidebound world is used.

    python scripts/verify_director_growth.py --provider remote --cycles 2
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from sim2.agents.engine import AgentEngine
from sim2.bootstrap.service import create_instance_from_template
from sim2.llm.config import LLMConfig
from sim2.llm.gateway import LLMRouter
from sim2.platform.runtime import Platform
from sim2.world.growth import build_growth_ledger

PROBE_SCRIPT = Path(__file__).with_name("audit_dynamic_evolution_loop.py")


def _probe_module():
    spec = importlib.util.spec_from_file_location("sim2_director_probe", PROBE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _template() -> dict[str, Any]:
    template = _probe_module()._probe_template(fallback=True)
    template["actions"] = [item for item in template["actions"] if item["id"] != "research_source"]
    template["behavior_trees"] = [item for item in template["behavior_trees"] if item["id"] != "study_source"]
    for obj in template["objects"]:
        for key in ("research_required", "research_discoveries", "research_focus", "research_assignees"):
            obj.pop(key, None)
    template["capabilities"] = [item for item in template["capabilities"] if item["id"] != "research"]
    mira = template["agents"][0]
    mira["capabilities"].pop("research", None)
    mira["initial_bt_ids"] = ["inspect_archive"]
    mira.pop("research_targets", None)
    template["rules"]["fallback_behavior_ids"] = ["inspect_archive"]
    template["rules"].pop("capability_progression", None)
    template["rules"]["world_rules"] = []
    template["resources"][0]["initial_state"]["quantity"] = 6
    template["evolution"]["stagnation"] = {"window": "1h", "max_interval": "2h", "growth": "structural"}
    template["evolution"]["director"] = {
        "cadence": "10m",
        "window": "30m",
        "targets": ["social", "rule"],
    }
    template["evolution"]["limits"] = {"max_ready_god_tasks": 2}
    template["evolution"]["contracts"] = {
        "god_structural": {
            "tools": ["query_history", "propose_entity", "propose_definition", "propose_world_change", "wait"],
            "purpose": "Fill exactly the Director's target growth cell.",
            "result": (
                "Read <season_plan>. Submit exactly one growth unit for the target cell, and no "
                "substitute. For social: create a distinct faction or a relationship that changes "
                "a decision. For rule: create one rule that changes an outcome. For place: create a "
                "reachable location. For verb: create one non-research Action and its bound behavior. "
                "Do not create a research source, archive, ledger, table, register or worksheet. "
                "Assign any new behavior to Mira. If the evidence cannot support the target cell, wait."
            ),
        },
    }
    template["evolution"]["wakes"] = [
        {
            "id": "story_after_growth",
            "on": ["entity.created", "world.change.rule", "world.change.faction", "world.change.relationship"],
            "for": "story",
            "external": True,
            "every": "10m",
            "tools": ["query_history", "propose_story_beat", "wait"],
            "purpose": "Narrate the structural growth the Director asked for.",
        },
    ]
    return template


def _call_names(turn: Any) -> list[str]:
    names = []
    for item in (turn.response or {}).get("tool_calls") or []:
        function = item.get("function") if isinstance(item, dict) else {}
        if isinstance(function, dict):
            names.append(str(function.get("name")))
    return names


def _research_sources(platform: Platform) -> list[str]:
    names = []
    for row in platform.store.connection.execute("SELECT name,data_json FROM entity").fetchall():
        try:
            data = json.loads(row["data_json"] or "{}")
        except json.JSONDecodeError:
            data = {}
        state = data.get("initial_state") if isinstance(data.get("initial_state"), dict) else {}
        if state.get("research_discoveries") or state.get("research_required"):
            names.append(str(row["name"]))
    return names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="remote")
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--max-completion-tokens", type=int, default=None)
    args = parser.parse_args()

    router = LLMRouter(LLMConfig.load())
    god = router.new_provider_for_name("remote" if args.provider == "remote" else args.provider, max_completion_tokens=args.max_completion_tokens, role="god")
    story = router.new_provider_for_name("remote" if args.provider == "remote" else args.provider, max_completion_tokens=args.max_completion_tokens, role="story")
    temporary = tempfile.TemporaryDirectory(prefix="sim2-director-growth-") if not args.runtime_dir else None
    root = Path(args.runtime_dir) if args.runtime_dir else Path(temporary.name)
    root.mkdir(parents=True, exist_ok=True)
    db = create_instance_from_template(_template(), f"director-growth-{uuid4().hex[:8]}", root)
    platform = Platform(db)
    rows: list[dict[str, Any]] = []
    try:
        for cycle in range(1, max(1, args.cycles) + 1):
            # The Director runs at cadence boundaries inside the world tick.
            for _ in range(60):
                if any(item.get("task_type") == "god_structural" for item in platform.ready_evolution_tasks()):
                    break
                platform.step()
            plan = platform.store.repositories.metadata.get("season_plan", {}) or {}
            tasks = [item for item in platform.ready_evolution_tasks() if item.get("task_type") == "god_structural"]
            before_ledger = build_growth_ledger(platform.store)
            target = str(plan.get("target_cell") or "")
            before = int((before_ledger.get("cells", {}).get(target, {}) or {}).get("count", 0))
            god_turn = None
            if tasks:
                task = tasks[-1]
                god_turn = AgentEngine(platform, god, max_iterations=10).run(
                    role="god",
                    actor_id="god",
                    task_type=str(task["task_type"]),
                    source_events=task.get("source_events") or [],
                )
                if god_turn.result.get("status") in {"accepted", "wait"}:
                    platform.complete_evolution_task(str(task["task_id"]))
            after_ledger = build_growth_ledger(platform.store)
            after = int((after_ledger.get("cells", {}).get(target, {}) or {}).get("count", 0))
            story_status = []
            for task in [item for item in platform.ready_evolution_tasks() if item.get("role") == "story"][:2]:
                turn = AgentEngine(platform, story).run(
                    role="story",
                    actor_id="story",
                    task_type=str(task["task_type"]),
                    source_events=task.get("source_events") or [],
                )
                if turn.result.get("status") in {"accepted", "wait"}:
                    platform.complete_evolution_task(str(task["task_id"]))
                story_status.append(turn.result.get("status"))
            row = {
                "cycle": cycle,
                "minute": int(platform.store.repositories.metadata.get("world_minute", 0) or 0),
                "plan_id": plan.get("plan_id"),
                "target_cell": target,
                "before": before,
                "after": after,
                "god_status": god_turn.result.get("status") if god_turn else "no_task",
                "god_calls": _call_names(god_turn) if god_turn else [],
                "god_check": god_turn.result.get("failed_check") if god_turn else None,
                "story": story_status,
                "research_sources": _research_sources(platform),
                "dead_content": after_ledger.get("dead_content") or [],
            }
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if not god_turn or god_turn.result.get("status") != "accepted" or after <= before:
                break
            # Advance to the next Director cadence and let it complete the plan.
            for _ in range(12):
                platform.step()

        passed = bool(rows) and all(
            row.get("god_status") == "accepted"
            and row.get("after", 0) > row.get("before", 0)
            and not row.get("research_sources")
            for row in rows
        )
        print(json.dumps({"director_growth": {"status": "passed" if passed else "failed", "rows": rows}}, ensure_ascii=False), flush=True)
        return 0 if passed else 2
    finally:
        platform.close()
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
