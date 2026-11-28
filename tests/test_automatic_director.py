"""The LLM Director runs inside the ordinary simulation loop.

The deterministic Director still chooses targets by default.  A world that
declares ``director.policy: llm`` hands the same one-call decision to the
``roles.director`` provider before the tick, and any provider failure or
invalid target falls back to the deterministic policy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sim2.bootstrap.service import create_instance
from sim2.evolution.config import normalize_evolution_config
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform
from sim2.prompting.sections_god import _season_plan_lines
from sim2.prompting.sections_story import story_lines
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


@pytest.mark.parametrize(("source_is_visible", "choice", "recorded_choice"), [
    (True, "Unload food now or wait for the crew", "Unload food now or wait for the crew"),
    (True, {"option_a": "Unload food", "option_b": "Hold the berth", "tradeoff": "Food or crew access"},
     "Unload food / Hold the berth / Food or crew access"),
    (True, {"option_a": "Unload food", "option_b": "Hold the berth"}, None),
    (False, "Unload food now or wait for the crew", None),
])
def test_director_wakes_story_only_from_a_grounded_mainline(
    tmp_path: Path, source_is_visible: bool, choice: str | dict, recorded_choice: str | None,
) -> None:
    platform = _platform(tmp_path, "mainline-director")
    try:
        store = platform.store
        with store.transaction():
            store.repositories.metadata.set("evolution", normalize_evolution_config({
                "director": {"cadence": 1, "policy": "llm", "targets": ["place"],
                             "context_events": ["port.opened"], "narrative_direction": True},
                "wakes": [{"id": "director_story", "on": "director.plan.started", "for": "story",
                           "tools": ["query_history", "propose_story_beat", "wait"], "external": True}],
            }))
            fact_id = store.repositories.events.append_event("port.opened", "captain", 0, {"summary": "East dock opened"})
        mainline = {"source_event_id": fact_id if source_is_visible else fact_id + 1,
                    "objective": "Decide who can use the new dock",
                    "player_choice": choice}

        class Provider:
            def complete_json(self, *, system, context):
                assert "East dock opened" in context and "player_choice" in context
                return {"target_cell": "place", "reason": "Give the dock a purpose", **mainline}

        plan = platform.plan_structure_with_provider(Provider(), 1)
        if recorded_choice is None:
            assert "mainline" not in plan
            assert not store.repositories.events.recent_events_by_types(["director.plan.started"])
            return
        mainline["player_choice"] = recorded_choice
        assert plan["mainline"] == mainline
        assert store.repositories.metadata.get("season_plan_history")[plan["plan_id"]]["mainline"] == mainline
        assert any(task["task_type"] == "event_group:director_story" for task in platform.ready_evolution_tasks())
        (event,) = store.repositories.events.recent_events_by_types(["director.plan.started"])
        assert event["source_event_id"] == fact_id
        for prompt in (story_lines({"season_plan": plan}), _season_plan_lines({"season_plan": plan})):
            assert any(mainline["player_choice"] in line for line in prompt)
    finally:
        platform.close()


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


def test_stage_event_replaces_an_active_plan_before_the_director_cadence(tmp_path: Path) -> None:
    platform = _platform(tmp_path, "replan-on-stage-event")
    try:
        store = platform.store
        with store.transaction():
            store.repositories.metadata.set("evolution", normalize_evolution_config({
                "director": {"cadence": 30, "policy": "llm", "targets": ["place", "rule"],
                             "context_events": ["voyage.opened"], "replan_on": ["voyage.opened"]},
            }))
            store.repositories.metadata.set("world_minute", 30)
        first = platform.plan_structure_with_provider(_Director("place"), 30)
        with store.transaction():
            store.repositories.metadata.set("world_minute", 31)
            event = store.repositories.events.append_event("voyage.opened", None, 31, {"summary": "A new passage opened"})
        provider = _Director("rule")
        new_plan = platform.plan_structure_with_provider(provider, 31)
        assert new_plan["created_minute"] == 31 and new_plan["event_cursor"] >= event
        old_plan = store.repositories.metadata.get("season_plan_history")[first["plan_id"]]
        assert old_plan["closure_reason"] == "world stage changed" and old_plan["replan_event_id"] == event
        assert "A new passage opened" in provider.contexts[0]
        assert platform.plan_structure_with_provider(provider, 32) is None
        assert provider.calls == 1
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
        # Windward's saved plan had 2 minutes left after 11 exhausted sessions.
        # The configured 30-minute interval gives the Director time to replace it.
        runner.director_cadence_minutes = 30
        repeated = {"retry_count": 11, "data": {"season_plan": tight}}
        assert runner._evolution_retry_delay(repeated, {"failed_check": "budget_exhausted"}) == 30
        assert runner._evolution_retry_delay({"data": {"season_plan": tight}}, {"failed_check": "budget_exhausted"}) == 1
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
                "name": "Cistern audit",
                "condition": "actor.alive",
                "effect": "policy.auto_director",
                "behavior_names": ["Inspect Cistern"],
                "wake": ["story"],
            },
        })
        assert created["status"] == "accepted", created

        runner = SimulationRunner(platform, {"director": provider})
        runner.run(1)
        assert provider.calls == 1
        with platform.store.transaction():
            for _ in range(201):
                platform.store.repositories.events.append_event("test.background", None, 3, {})
        accepted = platform.submit_behavior(BehaviorIntent(
            actor_id="mei_lan", behavior_ref="behavior:inspect_cistern",
            target={}, parameters={}, reason="Check the cistern under the new policy.",
            confidence=1.0, intent_id="director-proof",
        ))
        assert accepted["status"] == "accepted", accepted
        report = runner.run(81)
        proposal, = platform.store.repositories.evolution.list_world_proposals()
        assert proposal["observed"]["event_count"] > 200
        completed, = platform.store.repositories.events.events_by_ids([proposal["observed"]["outcome_event_id"]])
        assert completed["event_type"] == "behavior.completed" and completed["execution_id"] == accepted["execution_id"]
        followed, = completed["outcome"]["rule_followups"]
        assert (followed["rule"], followed["rule_id"], followed["execution_id"]) == (
            "Cistern audit", "auto_director_rule", accepted["execution_id"],
        )
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


def test_rule_evaluator_honors_commit_order_deadline_and_legacy_completion(tmp_path: Path) -> None:
    platform = _platform(tmp_path, "rule-evaluation-window")
    try:
        repo = platform.store.repositories
        with platform.store.transaction():
            def complete(minute):
                return repo.events.append_event("behavior.completed", "mei_lan", minute, {
                    "behavior_id": "inspect_cistern", "outcome": {"rule_followups": [{"rule": "Cistern audit"}]},
                })

            complete(0)
            for number in range(101):
                repo.evolution.put_world_proposal(f"future-{number}", "intent", "god", "event",
                                                  "pending_evaluation", 1, 100, {})
        rule = {"id": "cistern_check", "name": "Cistern audit", "condition": "actor.alive",
                "effect": "policy.audit", "behavior_names": ["Inspect Cistern"]}
        new = platform.submit_world_change("god", {"change_type": "rule", "summary": "Use the cistern policy.",
                                                  "evaluation_window_minutes": 1, "rule": rule})
        assert new["status"] == "accepted", new
        with platform.store.transaction():
            later = complete(2)
            repo.events.append_event("world.rule.followed", "mei_lan", 1, {"rule": "Cistern audit"})
            platform.proposal_evaluator.evaluate_due(5)
        evaluated, = repo.evolution.list_world_proposals(status="no_observed_effect")
        assert evaluated["proposal_id"] == new["proposal_id"]
        assert evaluated["observed"]["window"] == {"from_minute": 0, "to_minute": 1}

        with platform.store.transaction():
            # A legacy completion after the proposal in the same world minute is valid.
            repo.evolution.put_world_proposal("legacy", "intent", "god", "rule", "pending_evaluation", 0, 1,
                evaluated["expected"], {"event_id": new["event_id"], "effect": new["effect"]})
            in_window = complete(0)
            platform.proposal_evaluator.evaluate_due(5)
        observed, = repo.evolution.list_world_proposals(status="observed")
        assert observed["observed"]["outcome_event_id"] == in_window and in_window != later
    finally:
        platform.close()


def test_director_reads_configured_history_from_its_own_save(tmp_path):
    for name in ("east-port", "west-port"):
        platform = _platform(tmp_path, name)
        try:
            with platform.store.transaction():
                metadata = platform.store.repositories.metadata
                metadata.set("evolution", normalize_evolution_config({
                    "director": {"cadence": "1m", "policy": "llm", "targets": ["place"],
                                 "context_events": ["port.opened"], "context_residents": 2},
                }))
                events = platform.store.repositories.events
                events.append_event("port.opened", "captain", 0, {"summary": name})
                for i in range(12):
                    events.append_event("noise", None, 0, {"summary": f"irrelevant-{i}"})
            provider = _Director()
            platform.plan_structure_with_provider(provider, 1)
            prompt = provider.contexts[0]
            assert name in prompt and "captain" in prompt and "port.opened" in prompt
            residents = json.loads(prompt.split("existing options. ", 1)[1].split(" Last completed:", 1)[0])
            assert len(residents) == 2
            assert all(resident["name"] and resident["location"] for resident in residents)
            assert "irrelevant" not in prompt
            assert ("west-port" if name == "east-port" else "east-port") not in prompt
            # Closing an unused proposal changes the next model brief, even after reopening.
            with platform.store.transaction():
                plan = metadata.get("season_plan")
                plan.update(status="expired", closure_reason="registered content has no verified use")
                metadata.set("season_plan", plan)
                metadata.set("season_plan_history", {plan["plan_id"]: plan})
            path = tmp_path / "runtime" / "instances" / name / "world.sqlite3"
            platform.close()
            platform = Platform(path)
            platform.plan_structure_with_provider(provider, 2)
            assert "registered content has no verified use" in provider.contexts[-1]
            assert "expired" in provider.contexts[-1]
        finally:
            platform.close()
