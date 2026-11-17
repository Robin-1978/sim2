"""Per-behavior gates of the decision view.

A catalog behavior reaches ``executable_behaviors`` only after every gate passes.
Each gate that rejects it must record why, because the API, the story runtime and
the world builder read those diagnostics to explain what to change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform

WORLD = Path(__file__).parents[1] / "worlds" / "starter_village"


def _platform(tmp_path: Path) -> Platform:
    return Platform(create_instance(WORLD, "view-gates-world", tmp_path / "runtime"))


def _action(action_id: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": action_id,
        "parameters": {},
        "preconditions": ["actor.alive"],
        "duration": {"minutes": 1},
        "costs": {"reserve": {}, "per_minute": {}},
        "success": {"result": "done"},
        **fields,
    }


def _behavior(behavior_id: str, action_ids: list[str], **fields: Any) -> dict[str, Any]:
    return {
        "id": behavior_id,
        "version": 1,
        "scope": "personal",
        "entry_conditions": ["actor.alive"],
        "nodes": [{"type": "action", "action_id": action_id} for action_id in action_ids],
        "success_results": ["done"],
        "failure_results": [],
        **fields,
    }


def _offer(platform: Platform, agent_id: str, behavior: dict[str, Any], *actions: dict[str, Any]) -> None:
    entities = platform.store.repositories.entities
    for action in actions:
        entities.put_definition("action_def", action["id"], action)
    entities.put_definition("bt_def", behavior["id"], behavior)
    agent = entities.get_agent(agent_id)
    agent["initial_bt_ids"].append(behavior["id"])
    entities.update_agent(agent_id, agent)
    platform.store.connection.commit()


def _view(platform: Platform, agent_id: str) -> tuple[set[str], dict[str, list[str]]]:
    view = platform.view.build_decision_view(agent_id, role="agent_decision", include_diagnostics=True)
    refs = {item["ref"] for item in view["executable_behaviors"]}
    failed = {item["behavior_id"]: item["failed"] for item in view["unavailable_behavior_diagnostics"]}
    return refs, failed


def test_diagnostics_are_only_returned_when_requested(tmp_path: Path):
    platform = _platform(tmp_path)
    try:
        assert "unavailable_behavior_diagnostics" not in platform.view.build_decision_view("su_wu", role="agent_decision")
        assert "unavailable_behavior_diagnostics" in platform.view.build_decision_view("su_wu", role="agent_decision", include_diagnostics=True)
    finally:
        platform.close()


def test_behavior_without_a_definition_is_reported_and_not_offered(tmp_path: Path):
    platform = _platform(tmp_path)
    try:
        agent = platform.store.repositories.entities.get_agent("su_wu")
        agent.setdefault("unlocked_bt_ids", []).append("ghost_behavior")
        platform.store.repositories.entities.update_agent("su_wu", agent)
        platform.store.connection.commit()

        refs, failed = _view(platform, "su_wu")

        assert failed["ghost_behavior"] == ["behavior definition is not registered"]
        assert "behavior:ghost_behavior" not in refs
        assert "behavior:get_food" in refs
    finally:
        platform.close()


def test_failed_entry_condition_is_reported_before_the_actions_are_checked(tmp_path: Path):
    platform = _platform(tmp_path)
    try:
        impossible = _action("probe_action", preconditions=["actor.state.energy >= 999"])
        _offer(platform, "su_wu", _behavior("probe_conditioned", ["probe_action"], entry_conditions=["actor.alive", "actor.state.energy >= 999"]), impossible)

        refs, failed = _view(platform, "su_wu")

        # The Action could not start either, but the first failing gate decides what is reported.
        assert failed["probe_conditioned"] == ["actor.state.energy >= 999"]
        assert "behavior:probe_conditioned" not in refs
    finally:
        platform.close()


def test_action_that_cannot_start_names_the_action_and_the_failing_check(tmp_path: Path):
    platform = _platform(tmp_path)
    try:
        _offer(platform, "su_wu", _behavior("probe_unstartable", ["probe_action"]), _action("probe_action", preconditions=["actor.state.energy >= 999"]))

        refs, failed = _view(platform, "su_wu")

        assert failed["probe_unstartable"] == ["Action probe_action cannot start: precondition not met: actor.state.energy >= 999"]
        assert "behavior:probe_unstartable" not in refs
    finally:
        platform.close()


def test_behavior_without_executable_steps_is_reported(tmp_path: Path):
    platform = _platform(tmp_path)
    try:
        _offer(platform, "su_wu", _behavior("probe_empty", []))
        # An Action step whose definition is gone contributes no step either.
        _offer(platform, "su_wu", _behavior("probe_ghost_action", ["no_such_action"]))

        refs, failed = _view(platform, "su_wu")

        assert failed["probe_empty"] == ["behavior has no executable Action steps"]
        assert failed["probe_ghost_action"] == ["behavior has no executable Action steps"]
        assert not {"behavior:probe_empty", "behavior:probe_ghost_action"} & refs
    finally:
        platform.close()


def test_later_steps_are_checked_against_the_state_left_by_earlier_steps(tmp_path: Path):
    platform = _platform(tmp_path)
    try:
        drain = _action("probe_drain", costs={"reserve": {}, "per_minute": {"energy": 60}}, duration={"minutes": 1})
        needs_energy = _action("probe_needs_energy", preconditions=["actor.state.energy >= 50"])
        agent = platform.store.repositories.entities.get_agent("su_wu")
        agent["initial_state"]["energy"] = 90
        platform.store.repositories.entities.update_agent("su_wu", agent)
        platform.store.connection.commit()
        _offer(platform, "su_wu", _behavior("probe_single", ["probe_needs_energy"]), needs_energy)
        _offer(platform, "su_wu", _behavior("probe_sequence", ["probe_drain", "probe_needs_energy"]), drain)

        refs, failed = _view(platform, "su_wu")

        assert "behavior:probe_single" in refs
        assert failed["probe_sequence"] == ["Action probe_needs_energy cannot start: precondition not met: actor.state.energy >= 50"]
        assert "behavior:probe_sequence" not in refs
    finally:
        platform.close()


def test_blocked_behavior_is_reported_until_a_recovery_event(tmp_path: Path):
    platform = _platform(tmp_path)
    try:
        for _ in range(3):
            platform.step()
        failed_at = int(platform.store.repositories.metadata.get("world_minute", 0))
        assert failed_at > 0
        agent = platform.store.repositories.entities.get_agent("su_wu")
        agent["initial_state"]["blocked_behaviors"] = {"get_food": {"result": "failed_test", "failed_at": failed_at}}
        platform.store.repositories.entities.update_agent("su_wu", agent)
        platform.store.connection.commit()

        refs, failed = _view(platform, "su_wu")

        assert failed["get_food"] == ["blocked after the last failed result (failed_test); wait for a configured recovery event"]
        assert "behavior:get_food" not in refs
        assert "behavior:inspect_object" in refs
    finally:
        platform.close()


def test_rule_denial_is_reported_with_the_rule_message(tmp_path: Path):
    platform = _platform(tmp_path)
    try:
        result = platform.submit_world_change("god", {
            "change_type": "rule",
            "summary": "Review cooking during the reserve review.",
            "rule": {
                "id": "cooking_review_gate",
                "condition": "world.village_food < 400",
                "effect": {"type": "behavior_constraint", "message": "Cooking is paused until the reserve review."},
                "behavior_names": ["Craft Meal"],
            },
        })
        assert result["status"] == "accepted"
        liang = platform.store.repositories.entities.get_agent("liang_he")
        liang["needs"]["satiety"] = 40
        platform.store.repositories.entities.update_agent("liang_he", liang)
        platform.store.connection.commit()

        refs, failed = _view(platform, "liang_he")

        assert failed["craft_meal"] == ["Cooking is paused until the reserve review."]
        assert "behavior:craft_meal" not in refs
        assert "behavior:get_food" in refs
    finally:
        platform.close()


def test_cooldown_is_reported_with_its_release_minute(tmp_path: Path):
    platform = _platform(tmp_path)
    try:
        _offer(platform, "su_wu", _behavior("probe_cooldown", ["probe_action"], cooldown_minutes=30), _action("probe_action"))
        refs, _ = _view(platform, "su_wu")
        assert "behavior:probe_cooldown" in refs

        assert platform.submit_behavior(BehaviorIntent("su_wu", "behavior:probe_cooldown", {}, {}, "Run once.", 0.8, "cooldown-run"))["status"] == "accepted"
        for _ in range(3):
            platform.step()
        started_minute = platform.store.repositories.activity.last_behavior_minute("su_wu", "probe_cooldown")
        assert started_minute is not None

        refs, failed = _view(platform, "su_wu")

        assert failed["probe_cooldown"] == [f"cooldown until world minute {started_minute + 30}"]
        assert "behavior:probe_cooldown" not in refs
    finally:
        platform.close()
