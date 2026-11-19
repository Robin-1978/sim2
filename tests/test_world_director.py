"""The deterministic Director chooses structural targets; it never writes content."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim2.bootstrap.service import create_instance
from sim2.evolution.config import normalize_evolution_config
from sim2.platform.runtime import Platform
from sim2.prompting.renderer import render_context
from sim2.world.growth import build_growth_ledger

WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"


def _town(tmp_path: Path, name: str) -> Platform:
    return Platform(create_instance(WORLD, name, tmp_path / "runtime"))


def _director_config(platform: Platform, *, targets: list[str], cadence: str = "1m", window: str = "10m") -> None:
    with platform.store.transaction():
        platform.store.repositories.metadata.set("evolution", normalize_evolution_config({
            "director": {"cadence": cadence, "window": window, "targets": targets},
        }))


def test_director_selects_a_target_and_queues_a_structural_task(tmp_path: Path) -> None:
    platform = _town(tmp_path, "director-plan")
    try:
        _director_config(platform, targets=["place"])
        before = build_growth_ledger(platform.store)["cells"]["place"]["count"]
        with platform.store.transaction():
            plan = platform._services["director"].tick(1)
        assert plan and plan["status"] == "active"
        assert plan["target_cell"] == "place"
        assert plan["baseline"] == before
        assert plan["reason"].startswith("place=")
        tasks = [item for item in platform.ready_evolution_tasks() if item["task_type"] == "god_structural"]
        assert tasks, platform.ready_evolution_tasks()
    finally:
        platform.close()


def test_director_completes_the_plan_when_the_target_cell_grows(tmp_path: Path) -> None:
    platform = _town(tmp_path, "director-advance")
    try:
        _director_config(platform, targets=["place"])
        with platform.store.transaction():
            first = platform._services["director"].tick(1)
        assert first and first["status"] == "active"
        place = platform.submit_entity_proposal("god", {
            "entity_type": "location",
            "name": "Director Test Place",
            "description": "A place created after the Director asked for one.",
            "state": {"routes": ["lantern_square"], "route_distances": {"lantern_square": 2}},
        })
        assert place["status"] == "accepted", place
        with platform.store.transaction():
            second = platform._services["director"].tick(2)
        assert second and second["status"] == "active"
        assert second["plan_id"] != first["plan_id"]
        assert second["baseline"] == first["baseline"] + 1
        completed = platform.store.repositories.metadata.get("season_plan")
        assert completed["status"] == "active"
        history = platform.store.repositories.metadata.get("season_plan_history", {}) or {}
        assert history[first["plan_id"]]["status"] == "completed"
        assert history[second["plan_id"]]["status"] == "active"
    finally:
        platform.close()


def test_active_season_plan_is_visible_to_god_and_story(tmp_path: Path) -> None:
    platform = _town(tmp_path, "director-prompt")
    try:
        with platform.store.transaction():
            platform.store.repositories.metadata.set("season_plan", {
                "plan_id": "plan:1:social",
                "target_cell": "social",
                "baseline": 0,
                "created_minute": 1,
                "expires_minute": 100,
                "reason": "place=4, verb=6, social=0, rule=2",
                "status": "active",
            })
        god_prompt = render_context(platform.view.build_decision_view("god", role="god"), "Review structural growth.")
        story_prompt = render_context(platform.view.build_decision_view("story", role="story"), "Continue the story.")
        for prompt in (god_prompt, story_prompt):
            assert "<season_plan>" in prompt
            assert "target cell: social" in prompt
        assert "Fill this cell with one reachable, executable growth unit" in god_prompt
        assert "do not claim a structural change" in story_prompt
    finally:
        platform.close()


def test_director_config_is_validated() -> None:
    compiled = normalize_evolution_config({"director": {"cadence": "1d", "window": "3d", "targets": ["place", "rule"]}})
    assert compiled["director"]["cadence_minutes"] == 1440
    assert compiled["director"]["window_minutes"] == 4320
    assert compiled["director"]["targets"] == ["place", "rule"]
    assert compiled["director"]["policy"] == "deterministic"
    assert compiled["director"]["max_cell_units"] == 0
    assert compiled["director"]["saturation"] == "idle"
    assert normalize_evolution_config({"director": {"policy": "llm"}})["director"]["policy"] == "llm"
    assert normalize_evolution_config({"director": {"max_cell_units": 8}})["director"]["max_cell_units"] == 8
    with pytest.raises(ValueError, match="max_cell_units must be a non-negative integer"):
        normalize_evolution_config({"director": {"max_cell_units": -1}})
    with pytest.raises(ValueError, match="saturation must be idle"):
        normalize_evolution_config({"director": {"saturation": "delete"}})
    with pytest.raises(ValueError, match="policy must be deterministic or llm"):
        normalize_evolution_config({"director": {"policy": "auto"}})
    with pytest.raises(ValueError, match="unknown targets"):
        normalize_evolution_config({"director": {"targets": ["economy"]}})
    with pytest.raises(ValueError, match="window must be at least one cadence"):
        normalize_evolution_config({"director": {"cadence": "2d", "window": "1d"}})

def test_world_change_dispatches_the_configured_story_wake(tmp_path: Path) -> None:
    platform = _town(tmp_path, "director-world-change-wake")
    try:
        with platform.store.transaction():
            platform.store.repositories.metadata.set("evolution", normalize_evolution_config({
                "wakes": [{
                    "id": "story_growth",
                    "on": "world.change.rule",
                    "for": "story",
                    "external": True,
                    "tools": ["propose_story_beat", "wait"],
                    "result": "Narrate the new rule.",
                }],
            }))
        result = platform.submit_world_change("god", {
            "change_type": "rule",
            "summary": "A test rule for the growth wake.",
            "rule": {
                "id": "director_test_rule",
                "condition": "event.recent.cistern.reviewed == true",
                "effect": "policy.director_test",
                "behavior_names": ["Inspect Cistern"],
                "wake": ["story"],
            },
        })
        assert result["status"] == "accepted", result
        tasks = [item for item in platform.ready_evolution_tasks() if item["role"] == "story"]
        assert any(item["task_type"] == "event_group:story_growth" for item in tasks), tasks
    finally:
        platform.close()


class _DirectorProvider:
    def __init__(self, target: str) -> None:
        self.target = target
        self.calls = 0
        self.system = ""
        self.context = ""
        self.tools = None

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.calls += 1
        self.system = system
        self.context = context
        self.tools = tools
        return {"target_cell": self.target, "reason": "The model chose the least developed cell."}


def test_llm_director_writes_the_same_plan_and_structural_task(tmp_path: Path) -> None:
    platform = _town(tmp_path, "director-llm")
    try:
        _director_config(platform, targets=["place", "resource"])
        provider = _DirectorProvider("resource")
        plan = platform._services["director"].plan_with_provider(provider, 1)
        assert plan and plan["status"] == "active"
        assert plan["target_cell"] == "resource"
        assert plan["reason"] == "The model chose the least developed cell."
        tasks = [item for item in platform.ready_evolution_tasks() if item["task_type"] == "god_structural"]
        assert tasks and tasks[0]["season_plan"]["target_cell"] == "resource"
        assert provider.calls == 1
        # The Director is a one-call policy chooser, not a ReAct agent.
        assert provider.tools is None
        assert "place=" in provider.context and "resource=" in provider.context
        assert len(provider.context) < 500 and len(provider.system) < 100
        assert "entity" not in provider.context.casefold()
    finally:
        platform.close()
