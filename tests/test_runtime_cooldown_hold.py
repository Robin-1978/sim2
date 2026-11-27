"""The web runtime waits out a provider cooldown; only a stalled run pauses.

A provider cooldown lasts seconds (``llm/gateway.py`` caps it at 30).  The
supervisor used to pause the world on the first one and wait for an operator.
It now holds world time for the cooldown and goes on, and pauses for the
operator only after ``MAX_COOLDOWN_STALLS`` cooldown stops in a row that did
not advance a world minute.  The LLM-call and target-minute limits are still
checked on every pass.
"""

from __future__ import annotations

import time
import types
from pathlib import Path

import pytest

from sim2.api.runtime_supervisor import RuntimeSupervisor
from sim2.api.service import ControlService
from sim2.bootstrap.service import create_instance
from sim2.simulation.runner import SimulationReport, SimulationRunner

WORLD = Path(__file__).parents[1] / "worlds" / "starter_village"
ROLES = ("agent_decision", "agent_reflection", "god", "story")
COOLDOWN_STOP = "provider_cooldown:agent_decision,agent_reflection,god,story"


class _Provider:
    """Answers every call with a wait; its cooldown is whatever the test sets."""

    def __init__(self) -> None:
        self.until = 0.0
        self.stuck = False

    @property
    def cooldown_remaining(self) -> float:
        if self.stuck:
            return 100.0
        return max(0.0, self.until - time.monotonic())

    def complete_json(self, *, system, context, tools=None, messages=None):
        return {"intent_type": "wait", "reason": "Test provider keeps everyone idle."}


@pytest.fixture
def opened(tmp_path: Path):
    """Open instances with a supervisor each; close them after the test."""

    items: list[tuple[ControlService, RuntimeSupervisor]] = []

    def open_one(provider: _Provider, **limits):
        db_path = create_instance(WORLD, f"cooldown-{len(items)}", tmp_path / "runtime")
        service = ControlService(db_path)
        providers = {role: provider for role in ROLES}
        supervisor = RuntimeSupervisor(service, provider_factory=lambda name: providers)
        for name, value in limits.items():
            setattr(supervisor, name, value)
        items.append((service, supervisor))
        return service, supervisor

    yield open_one
    for service, supervisor in items:
        supervisor.stop(wait_seconds=3.0)
        service.close()


def _wait_until(predicate, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached in time")


def _pause_reasons(service: ControlService) -> list[str]:
    events = service.platform.store.repositories.events.recent_events(limit=500)
    return [str(event.get("reason")) for event in reversed(events) if event.get("event_type") == "runtime.paused"]


def _scripted_runner(script: list[str]):
    """A runner whose passes are scripted: ``stall``, ``progress`` or ``ok``.

    ``stall`` stops on a cooldown without advancing a minute, ``progress``
    advances one minute and then stops on a cooldown, ``ok`` advances one
    minute and stops for no reason.
    """

    class Scripted:
        calls = 0

        def __init__(self, platform, providers, **kwargs) -> None:
            self.platform = platform

        def provider_cooldown_seconds(self) -> float:
            return 0.0

        def run(self, minutes, *, require_running=True, **kwargs):
            type(self).calls += 1
            kind = script.pop(0)
            start = int(self.platform.status()["world_minute"])
            if kind in {"progress", "ok"}:
                self.platform.step()
            report = SimulationReport(start, int(self.platform.status()["world_minute"]))
            report.stop_reason = COOLDOWN_STOP if kind in {"stall", "progress"} else None
            return report

    return Scripted


NO_WAIT = {"COOLDOWN_MIN_WAIT_SECONDS": 0.0, "COOLDOWN_MAX_WAIT_SECONDS": 0.0}


def test_runtime_holds_world_time_through_a_cooldown_and_keeps_advancing(opened):
    provider = _Provider()
    service, supervisor = opened(provider)
    start_minute = service.status()["world_minute"]

    provider.until = time.monotonic() + 1.5
    supervisor.start(max_world_minutes=2, max_llm_calls=0)
    _wait_until(lambda: "provider_cooldown" in supervisor.status()["last_error"])
    held = supervisor.status()
    assert held["state"] == "running" and held["running"] is True
    assert held["world_minute"] == start_minute

    _wait_until(lambda: supervisor.status()["state"] == "stopped")
    done = supervisor.status()
    assert done["stop_reason"] == "target_minutes_reached"
    assert done["world_minute"] >= start_minute + 2
    assert done["last_error"] == ""
    assert _pause_reasons(service) == ["web-target-complete"]


def test_runtime_pauses_for_the_operator_when_the_cooldown_never_ends(opened, monkeypatch):
    provider = _Provider()
    provider.stuck = True
    service, supervisor = opened(provider, MAX_COOLDOWN_STALLS=3, **NO_WAIT)
    start_minute = service.status()["world_minute"]
    calls: list[int] = []
    original = SimulationRunner.run

    def counting_run(self, minutes, **kwargs):
        calls.append(minutes)
        return original(self, minutes, **kwargs)

    monkeypatch.setattr(SimulationRunner, "run", counting_run)

    supervisor.start(max_world_minutes=3, max_llm_calls=0)
    _wait_until(lambda: supervisor.status()["state"] == "cooldown")
    status = supervisor.status()
    assert status["running"] is False
    assert status["last_error"].startswith("provider_cooldown")
    assert status["world_minute"] == start_minute
    assert len(calls) == 3
    assert _pause_reasons(service) == ["web-provider-cooldown"]

    provider.stuck = False
    resumed = supervisor.resume("test")
    assert resumed["status"] == "accepted"
    _wait_until(lambda: supervisor.status()["state"] == "stopped")
    done = supervisor.status()
    assert done["stop_reason"] == "target_minutes_reached"
    assert done["world_minute"] >= start_minute + 3


def test_a_stall_count_restarts_after_a_pass_that_advanced_the_world(opened, monkeypatch):
    # Two stalls, a pass that advanced a minute before its cooldown, two more
    # stalls: no run of three stalls, so the runtime never pauses.
    script = ["stall", "stall", "progress", "stall", "stall", "ok", "ok"]
    scripted = _scripted_runner(script)
    monkeypatch.setattr("sim2.api.runtime_supervisor.SimulationRunner", scripted)
    service, supervisor = opened(_Provider(), MAX_COOLDOWN_STALLS=3, **NO_WAIT)

    supervisor.start(max_world_minutes=3, max_llm_calls=0)
    _wait_until(lambda: supervisor.status()["state"] in {"stopped", "cooldown", "failed"})
    assert supervisor.status()["state"] == "stopped"
    assert supervisor.status()["stop_reason"] == "target_minutes_reached"
    assert scripted.calls == 7
    assert "web-provider-cooldown" not in _pause_reasons(service)


def test_a_stall_count_restarts_after_a_pass_without_a_cooldown(opened, monkeypatch):
    script = ["stall", "stall", "ok", "stall", "stall", "ok", "ok"]
    scripted = _scripted_runner(script)
    monkeypatch.setattr("sim2.api.runtime_supervisor.SimulationRunner", scripted)
    service, supervisor = opened(_Provider(), MAX_COOLDOWN_STALLS=3, **NO_WAIT)

    supervisor.start(max_world_minutes=3, max_llm_calls=0)
    _wait_until(lambda: supervisor.status()["state"] in {"stopped", "cooldown", "failed"})
    assert supervisor.status()["state"] == "stopped"
    assert scripted.calls == 7
    assert "web-provider-cooldown" not in _pause_reasons(service)


def test_three_stalls_in_a_row_pause_at_the_third(opened, monkeypatch):
    script = ["stall", "stall", "stall"]
    scripted = _scripted_runner(script)
    monkeypatch.setattr("sim2.api.runtime_supervisor.SimulationRunner", scripted)
    service, supervisor = opened(_Provider(), MAX_COOLDOWN_STALLS=3, **NO_WAIT)

    supervisor.start(max_world_minutes=3, max_llm_calls=0)
    _wait_until(lambda: supervisor.status()["state"] in {"stopped", "cooldown", "failed"})
    assert supervisor.status()["state"] == "cooldown"
    assert scripted.calls == 3
    assert _pause_reasons(service) == ["web-provider-cooldown"]


def test_a_hold_goes_back_to_the_call_budget_check(opened, monkeypatch):
    # The first pass is a stall that also spends the one allowed LLM call.  The
    # runtime must meet the budget before it runs a second pass.
    script = ["stall", "ok"]

    class Spending(_scripted_runner(script)):
        def run(self, minutes, *, require_running=True, **kwargs):
            report = super().run(minutes, require_running=require_running, **kwargs)
            self.platform.store.repositories.llm.append_llm_call(
                "someone", "agent_decision", "decision", 1, "hash", {"error": "provider down"}, "llm_error"
            )
            return report

    monkeypatch.setattr("sim2.api.runtime_supervisor.SimulationRunner", Spending)
    service, supervisor = opened(_Provider(), MAX_COOLDOWN_STALLS=3, **NO_WAIT)

    supervisor.start(max_world_minutes=3, max_llm_calls=1)
    _wait_until(lambda: supervisor.status()["state"] in {"stopped", "paused_budget", "cooldown", "failed"})
    assert supervisor.status()["state"] == "paused_budget"
    assert supervisor.status()["stop_reason"] == "llm_call_budget_reached"
    assert script == ["ok"]
    assert _pause_reasons(service) == ["web-llm-budget"]


def test_pause_and_stop_end_a_hold_at_once(opened):
    provider = _Provider()
    provider.stuck = True
    service, supervisor = opened(provider)

    supervisor.start(max_world_minutes=3, max_llm_calls=0)
    _wait_until(lambda: "provider_cooldown" in supervisor.status()["last_error"])
    assert supervisor.running  # still holding, not paused: a hold of 30 s is under way
    started = time.monotonic()
    supervisor.pause("test")
    _wait_until(lambda: not supervisor.running, timeout=3.0)
    assert time.monotonic() - started < 3.0
    assert supervisor.status()["state"] == "paused"

    resumed = supervisor.resume("test")
    assert resumed["status"] == "accepted"
    _wait_until(lambda: "provider_cooldown" in supervisor.status()["last_error"])
    assert supervisor.running
    stopped = supervisor.stop(wait_seconds=3.0)
    assert stopped["state"] == "stopped" and stopped["running"] is False


def test_the_runtime_gives_up_after_ten_stalls_in_a_row():
    assert RuntimeSupervisor.MAX_COOLDOWN_STALLS == 10


@pytest.mark.parametrize("remaining, expected", [(0.0, 0.25), (0.1, 0.25), (5.0, 5.0), (100.0, 30.0)])
def test_the_wait_stays_between_the_floor_and_the_gateway_ceiling(remaining, expected):
    runner = types.SimpleNamespace(provider_cooldown_seconds=lambda: remaining)
    assert RuntimeSupervisor(None)._cooldown_wait_seconds(runner) == pytest.approx(expected)


def _runner_with(*values) -> SimulationRunner:
    runner = object.__new__(SimulationRunner)
    runner.providers = {f"role_{index}": types.SimpleNamespace(cooldown_remaining=value) for index, value in enumerate(values)}
    return runner


def test_cooldown_seconds_is_the_longest_one_left_on_any_provider():
    assert _runner_with(0.0, 2.5, 1.0).provider_cooldown_seconds() == 2.5
    assert _runner_with().provider_cooldown_seconds() == 0.0
    assert _runner_with(lambda: 4.0, 1.0).provider_cooldown_seconds() == 4.0
    assert _runner_with(None, "x", -3, "2").provider_cooldown_seconds() == 2.0
    assert _runner_with(-3.0).provider_cooldown_seconds() == 0.0


def _raises(error: Exception):
    def call():
        raise error

    return call


def _old_provider_cooling_down(provider) -> bool:
    value = getattr(provider, "cooldown_remaining", 0.0)
    try:
        remaining = value() if callable(value) else value
        return float(remaining or 0.0) > 0.0
    except (TypeError, ValueError):
        return False


COOLDOWN_VALUES = [
    0, 0.0, None, -1, -0.5, 0.001, 2.5, "3", "0", "x", float("nan"), float("inf"),
    lambda: 1.5, lambda: 0.0, _raises(ValueError("bad")), _raises(TypeError("bad")),
]


@pytest.mark.parametrize("value", COOLDOWN_VALUES, ids=[f"case{index}" for index in range(len(COOLDOWN_VALUES))])
def test_the_cooling_flag_still_means_what_it_meant(value):
    provider = types.SimpleNamespace(cooldown_remaining=value)
    assert SimulationRunner._provider_cooling_down(provider) is _old_provider_cooling_down(provider)
    assert SimulationRunner._provider_cooling_down(object()) is False
