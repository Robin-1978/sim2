"""The LLM Director runs inside the ordinary simulation loop.

The deterministic Director still chooses targets by default.  A world that
declares ``director.policy: llm`` hands the same one-call decision to the
``roles.director`` provider before the tick, and any provider failure or
invalid target falls back to the deterministic policy.
"""

from __future__ import annotations

import json
from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.evolution.config import normalize_evolution_config
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform
from sim2.simulation.runner import SimulationRunner

WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"


class _Director:
    def __init__(self, target: str = "place", *, error: bool = False) -> None:
        self.target = target
        self.error = error
        self.calls = 0
        self.contexts: list[str] = []

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.calls += 1
        self.contexts.append(context)
        if self.error:
            raise RuntimeError("director provider unavailable")
        return {"target_cell": self.target, "reason": "The model chose this cell."}


def _platform(tmp_path: Path, name: str, policy: str = "llm") -> Platform:
    platform = Platform(create_instance(WORLD, name, tmp_path / "runtime"))
    with platform.store.transaction():
        platform.store.repositories.metadata.set("evolution", normalize_evolution_config({
            "director": {"cadence": "1m", "window": "3h", "targets": ["place", "rule"], "policy": policy},
        }))
    return platform


def test_runner_asks_the_llm_director_before_the_tick(tmp_path: Path) -> None:
    platform = _platform(tmp_path, "auto-director")
    provider = _Director("rule")
    try:
        report = SimulationRunner(platform, {"director": provider}).run(2)
        assert provider.calls == 1
        assert report.director_plans == 1
        plan = platform.store.repositories.metadata.get("season_plan")
        assert plan["status"] == "active"
        assert plan["target_cell"] == "rule"
        assert plan["reason"] == "The model chose this cell."
        tasks = [item for item in platform.ready_evolution_tasks() if item["task_type"] == "god_structural"]
        assert tasks and tasks[-1]["season_plan"]["target_cell"] == "rule"
        # The Director receives targets and counts, not world content.
        assert "place=" in provider.contexts[0] and "rule=" in provider.contexts[0]
        assert "Lantern" not in provider.contexts[0]
    finally:
        platform.close()


def test_runner_records_the_director_call_for_audit(tmp_path: Path) -> None:
    platform = _platform(tmp_path, "auto-director-audit")
    provider = _Director("rule")
    try:
        SimulationRunner(platform, {"director": provider}).run(2)
        rows = platform.store.connection.execute(
            "SELECT role,task_type,response_json FROM llm_call WHERE role='director'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["task_type"] == "director_plan"
        payload = json.loads(rows[0]["response_json"])
        assert payload["target_cell"] == "rule"
        assert payload["reason"] == "The model chose this cell."
    finally:
        platform.close()


def test_runner_falls_back_to_the_deterministic_plan_on_provider_error(tmp_path: Path) -> None:
    platform = _platform(tmp_path, "auto-director-error")
    provider = _Director(error=True)
    try:
        report = SimulationRunner(platform, {"director": provider}).run(2)
        assert provider.calls == 1
        assert report.director_errors == 1
        plan = platform.store.repositories.metadata.get("season_plan")
        assert plan["status"] == "active"
        assert plan["target_cell"] in {"place", "rule"}
        assert plan["reason"].startswith("place=")
    finally:
        platform.close()


def test_budget_exhausted_structural_retry_fits_the_plan_window(tmp_path: Path) -> None:
    platform = _platform(tmp_path, "auto-director-retry")
    try:
        with platform.store.transaction():
            platform.store.repositories.metadata.set("world_minute", 5)
        runner = SimulationRunner(platform, {})
        plan = {
            "plan_id": "plan:test:place",
            "target_cell": "place",
            "baseline": 0,
            "created_minute": 0,
            "expires_minute": 15,
            "status": "active",
        }
        task = {"task_type": "god_structural", "data": {"season_plan": plan}}
        assert runner._evolution_retry_delay(task, {"failed_check": "budget_exhausted"}) == 1
        assert runner._evolution_retry_delay({"task_type": "god_structural"}, {"failed_check": "budget_exhausted"}) == 1440

        tight = {**plan, "expires_minute": 7}
        capped = runner._evolution_retry_delay(
            {"task_type": "god_structural", "retry_count": 3, "data": {"season_plan": tight}},
            {"failed_check": "budget_exhausted"},
        )
        assert capped == 1
        semantic = runner._evolution_retry_delay(task, {"failed_check": "tool_arguments"})
        assert semantic == 5
    finally:
        platform.close()


def test_runner_keeps_the_deterministic_policy_without_opt_in(tmp_path: Path) -> None:
    platform = _platform(tmp_path, "auto-director-off", policy="deterministic")
    provider = _Director("rule")
    try:
        report = SimulationRunner(platform, {"director": provider}).run(2)
        assert provider.calls == 0
        assert report.director_plans == 0
        plan = platform.store.repositories.metadata.get("season_plan")
        assert plan["status"] == "active"
        assert plan["reason"].startswith("place=")
    finally:
        platform.close()


def test_llm_director_advances_after_the_cell_grows(tmp_path: Path) -> None:
    platform = _platform(tmp_path, "auto-director-advance")
    provider = _Director("rule")
    try:
        SimulationRunner(platform, {"director": provider}).run(2)
        first = platform.store.repositories.metadata.get("season_plan")
        assert first["target_cell"] == "rule" and first["baseline"] == 0

        created = platform.submit_world_change("god", {
            "change_type": "rule",
            "summary": "The new inspection policy needs a completed cistern review.",
            "evaluation_window_minutes": 80,
            "rule": {
                "id": "auto_director_rule",
                "condition": "actor.alive",
                "effect": "policy.auto_director",
                "behavior_ids": ["inspect_cistern"],
                "wake": ["story"],
            },
        })
        assert created["status"] == "accepted", created

        runner = SimulationRunner(platform, {"director": provider})
        runner.run(1)
        assert provider.calls == 1
        accepted = platform.submit_behavior(BehaviorIntent(
            actor_id="mei_lan", behavior_ref="behavior:inspect_cistern",
            target={}, parameters={}, reason="Check the cistern under the new policy.",
            confidence=1.0, intent_id="director-proof",
        ))
        assert accepted["status"] == "accepted", accepted
        report = runner.run(81)
        assert platform.store.repositories.metadata.get("last_completed_season_plan")["outcome_event_id"]
        second = platform.store.repositories.metadata.get("season_plan")
        assert report.director_plans == 1
        assert provider.calls == 2
        assert "Last completed: rule" in provider.contexts[-1]
        assert second["plan_id"] != first["plan_id"]
        assert second["baseline"] == first["baseline"] + 1
        assert second["target_cell"] == "rule"
    finally:
        platform.close()
