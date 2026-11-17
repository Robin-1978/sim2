"""Regression tests for the incapacitation lifecycle.

Before these rules an unconscious character was an absorbing state: its
behavior in flight was never failed, nothing ended the state, and the runner
kept asking it for decision turns.  The tests pin the generic engine
contract; Starter Village is only the configuration fixture.
"""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest
import yaml

from sim2.bootstrap.service import create_instance, validate_config
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform
from sim2.simulation.runner import SimulationRunner
from sim2.world.lifecycle import RecoveryRule, is_inactive, recovery_rule, validate_incapacitation_rule

WORLDS = Path(__file__).parents[1] / "worlds"
EXPLORER = "lu_xingzhou"


class WaitProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.calls.append(context)
        return {"intent_type": "wait", "reason": "Nothing worth doing."}


def _village(tmp_path: Path, name: str) -> Platform:
    return Platform(create_instance(WORLDS / "starter_village", name, tmp_path / "runtime"))


def _state(platform: Platform, agent_id: str = EXPLORER) -> dict:
    return platform.store.repositories.entities.get_agent(agent_id)["initial_state"]


def _set_state(platform: Platform, agent_id: str = EXPLORER, **values: object) -> None:
    with platform.store.transaction():
        agent = platform.store.repositories.entities.get_agent(agent_id)
        agent["initial_state"] = {**agent["initial_state"], **values}
        platform.store.repositories.entities.update_agent(agent_id, agent)


def _set_recovery(platform: Platform, **recovery: object) -> None:
    """Replace the world's recovery rule; no arguments removes it."""

    rules = dict(platform.store.repositories.metadata.get("rules") or {})
    rules.pop("incapacitation", None)
    if recovery:
        rules["incapacitation"] = {"recovery": recovery}
    with platform.store.transaction():
        platform.store.repositories.metadata.set("rules", rules)


def _events(platform: Platform, event_type: str) -> list[dict]:
    return [item for item in platform.store.repositories.events.recent_events(limit=500) if item["event_type"] == event_type]


# --- configuration contract ---------------------------------------------------------


def test_a_valid_recovery_rule_compiles() -> None:
    rules = {"vital_attribute": "health", "incapacitation": {"recovery": {"after_minutes": 60, "vital_fraction": 0.2}}}
    assert validate_incapacitation_rule(rules) == []
    assert recovery_rule(rules) == RecoveryRule(after_minutes=60, vital_fraction=0.2)
    assert recovery_rule({"vital_attribute": "health"}) is None


@pytest.mark.parametrize(
    ("incapacitation", "message"),
    [
        ([], "must be an object"),
        ({"revive": {}}, "unknown field"),
        ({"recovery": []}, "recovery must be an object"),
        ({"recovery": {"after_minutes": 0, "vital_fraction": 0.2}}, "after_minutes must be a positive integer"),
        ({"recovery": {"after_minutes": "1d", "vital_fraction": 0.2}}, "after_minutes must be a positive integer"),
        ({"recovery": {"after_minutes": 60, "vital_fraction": 0}}, "vital_fraction must be a number above 0"),
        ({"recovery": {"after_minutes": 60, "vital_fraction": 1.5}}, "vital_fraction must be a number above 0"),
        ({"recovery": {"after_minutes": 60}}, "vital_fraction must be a number above 0"),
        ({"recovery": {"after_minutes": 60, "vital_fraction": 0.2, "heal": 1}}, "unknown field"),
    ],
)
def test_a_malformed_recovery_rule_is_rejected(incapacitation: object, message: str) -> None:
    rules = {"vital_attribute": "health", "incapacitation": incapacitation}
    assert any(message in error for error in validate_incapacitation_rule(rules))
    assert recovery_rule(rules) is None


def test_a_recovery_rule_needs_a_vital_attribute() -> None:
    errors = validate_incapacitation_rule({"incapacitation": {"recovery": {"after_minutes": 60, "vital_fraction": 0.2}}})
    assert any("rules.vital_attribute" in error for error in errors)


def test_package_validation_reports_a_malformed_recovery_rule(tmp_path: Path) -> None:
    package = tmp_path / "bad-recovery"
    shutil.copytree(WORLDS / "starter_village", package)
    assert validate_config(package)["valid"] is True
    rules = yaml.safe_load((package / "rules.yaml").read_text(encoding="utf-8"))
    rules["incapacitation"] = {"recovery": {"after_minutes": 0, "vital_fraction": 0.25}}
    (package / "rules.yaml").write_text(yaml.safe_dump(rules, allow_unicode=True, sort_keys=False), encoding="utf-8")
    report = validate_config(package)
    assert report["valid"] is False
    assert any("after_minutes" in error for error in report["errors"])


def test_restored_vital_is_a_share_of_the_range_and_never_lower_than_now() -> None:
    rule = RecoveryRule(after_minutes=10, vital_fraction=0.25)
    assert rule.restored_vital(0, 100, 0) == 25
    assert rule.restored_vital(-20, 80, -20) == 5
    assert rule.restored_vital(0, 100, 60) == 60


def test_inactive_statuses_are_unconscious_and_dead() -> None:
    assert is_inactive({"status": "unconscious"}) and is_inactive({"status": "Dead"})
    assert not is_inactive({"status": "idle"}) and not is_inactive({"status": "executing"}) and not is_inactive(None)


# --- engine behaviour -----------------------------------------------------------------


def test_incapacitation_fails_the_behavior_in_flight(tmp_path: Path) -> None:
    platform = _village(tmp_path, "incapacitated-in-flight")
    try:
        accepted = platform.submit_behavior(BehaviorIntent(
            actor_id=EXPLORER, behavior_ref="behavior:explore_greywood", target={}, parameters={},
            reason="Explore the unknown area.", confidence=0.8, intent_id="explore-then-collapse",
        ))
        assert accepted["status"] == "accepted"
        assert _state(platform)["status"] == "executing"

        _set_state(platform, health=0)
        platform.step()

        state = _state(platform)
        minute = int(platform.store.repositories.metadata.get("world_minute", 0))
        assert state["status"] == "unconscious" and state["incapacitated_at"] == minute
        assert "incapacitated" in state["conditions"]
        assert not state.get("active_bt_instance") and not state.get("active_behavior_id") and not state.get("active_action")
        failed = [item for item in _events(platform, "behavior.failed") if item["actor_id"] == EXPLORER]
        assert failed and failed[-1]["result"] == "incapacitated"
        assert len(_events(platform, "agent.incapacitated")) == 1
        # The unconscious character is not progressed again: no stray second failure.
        for _ in range(3):
            platform.step()
        assert len([item for item in _events(platform, "behavior.failed") if item["actor_id"] == EXPLORER]) == 1
    finally:
        platform.close()


def test_unconscious_character_recovers_after_the_configured_period(tmp_path: Path) -> None:
    platform = _village(tmp_path, "incapacitation-recovery")
    try:
        _set_recovery(platform, after_minutes=5, vital_fraction=0.5)
        _set_state(platform, health=0)
        platform.step()
        assert _state(platform)["status"] == "unconscious"
        incapacitated_at = _state(platform)["incapacitated_at"]

        for _ in range(4):
            platform.step()
        assert _state(platform)["status"] == "unconscious"  # four of five minutes
        assert _events(platform, "agent.recovered") == []

        platform.step()
        state = _state(platform)
        assert state["status"] == "idle"
        assert state["health"] == 50
        assert "incapacitated" not in state["conditions"] and "incapacitated_at" not in state
        recovered = _events(platform, "agent.recovered")
        assert len(recovered) == 1
        assert recovered[0]["actor_id"] == EXPLORER and recovered[0]["after_minutes"] == 5
        assert recovered[0]["attribute"] == "health" and recovered[0]["value"] == 50
        assert int(platform.store.repositories.metadata.get("world_minute", 0)) == incapacitated_at + 5
    finally:
        platform.close()


def test_a_recovered_character_can_act_again(tmp_path: Path) -> None:
    platform = _village(tmp_path, "incapacitation-recovered-acts")
    try:
        _set_recovery(platform, after_minutes=3, vital_fraction=0.5)
        _set_state(platform, health=0)
        for _ in range(4):
            platform.step()
        assert _state(platform)["status"] == "idle"
        accepted = platform.submit_behavior(BehaviorIntent(
            actor_id=EXPLORER, behavior_ref="behavior:explore_greywood", target={}, parameters={},
            reason="Back on my feet.", confidence=0.8, intent_id="explore-after-recovery",
        ))
        assert accepted["status"] == "accepted"
    finally:
        platform.close()


def test_incapacitation_stays_absorbing_without_a_recovery_rule(tmp_path: Path) -> None:
    platform = _village(tmp_path, "incapacitation-absorbing")
    try:
        _set_recovery(platform)
        _set_state(platform, health=0)
        for _ in range(40):
            platform.step()
        assert _state(platform)["status"] == "unconscious"
        assert _events(platform, "agent.recovered") == []
    finally:
        platform.close()


def test_an_unstamped_unconscious_character_starts_its_clock_when_first_seen(tmp_path: Path) -> None:
    platform = _village(tmp_path, "incapacitation-unstamped")
    try:
        _set_recovery(platform, after_minutes=3, vital_fraction=0.5)
        _set_state(platform, status="unconscious", health=0)
        platform.step()
        stamped = _state(platform)["incapacitated_at"]
        assert stamped == int(platform.store.repositories.metadata.get("world_minute", 0))
        for _ in range(3):
            platform.step()
        assert _state(platform)["status"] == "idle"
    finally:
        platform.close()


def test_runner_asks_no_decision_turn_of_an_unconscious_character(tmp_path: Path) -> None:
    baseline = _village(tmp_path, "turns-baseline")
    knocked_out = _village(tmp_path, "turns-unconscious")
    try:
        _set_state(knocked_out, status="unconscious", health=0)
        everyone = SimulationRunner(baseline, {"agent_decision": WaitProvider()}).run(1)
        without_one = SimulationRunner(knocked_out, {"agent_decision": WaitProvider()}).run(1)
        assert everyone.decisions > 1
        assert without_one.decisions == everyone.decisions - 1
    finally:
        baseline.close()
        knocked_out.close()
