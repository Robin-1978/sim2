from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from ..llm.config import LLMConfig
from ..llm.gateway import LLMRouter
from ..simulation.runner import SimulationRunner
from .service import ControlService


ProviderFactory = Callable[[str | None], dict[str, Any]]


class RuntimeSupervisor:
    """Own the one autonomous simulation loop exposed by the web server.

    The supervisor uses the same ControlService/Platform instance as the HTTP
    read and write endpoints. Platform transactions and read projections are
    serialized by store locks, while provider calls stay outside transactions.
    """

    ACTIVE_STATES = {"starting", "running", "pausing", "stopping"}
    # A provider cooldown lasts seconds (``llm/gateway.py`` caps it at 30), so
    # the loop holds world time for it and goes on.  Only a run of cooldown
    # stops that advanced no world minute is read as an outage and left to the
    # operator.  The LLM-call and target-minute limits are checked on every pass.
    MAX_COOLDOWN_STALLS = 10
    COOLDOWN_MIN_WAIT_SECONDS = 0.25
    COOLDOWN_MAX_WAIT_SECONDS = 30.0

    def __init__(self, service: ControlService, *, provider_factory: ProviderFactory | None = None):
        self.service = service
        self.provider_factory = provider_factory
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._pause_requested = False
        self._thread: threading.Thread | None = None
        self._runner: SimulationRunner | None = None
        self._providers: dict[str, Any] = {}
        self._state = "stopped"
        self._provider_name: str | None = None
        self._run_id: str | None = None
        self._start_minute: int | None = None
        self._target_minute: int | None = None
        self._max_llm_calls: int | None = None
        self._llm_calls_at_start = 0
        self._last_error = ""
        self._stop_reason = ""
        self._started_at: str | None = None
        self._finished_at: str | None = None
        self._history: list[dict[str, Any]] = []
        self._usage_at_start: dict[str, int] = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    def _providers_for(self, provider_name: str | None) -> dict[str, Any]:
        if self.provider_factory is not None:
            return self.provider_factory(provider_name)
        try:
            router = LLMRouter.from_default_config()
        except FileNotFoundError:
            # A fresh checkout can still be played with the deterministic
            # Platform loop: the clock, long actions, needs and authored
            # background BT actors keep running, while LLM-driven NPC turns
            # simply stay idle until ~/.sim2/llm.json exists.
            if provider_name:
                raise
            return {}
        roles = ("agent_decision", "agent_reflection", "god", "story")
        if provider_name:
            return {
                role: router.new_provider_for_name(provider_name, role=role)
                for role in roles
            }
        return {role: router.provider_for_role(role) for role in roles}

    @staticmethod
    def llm_configured() -> bool:
        try:
            LLMConfig.load()
            return True
        except FileNotFoundError:
            return False
        except Exception:
            return False

    @staticmethod
    def available_providers_for_config() -> list[str]:
        try:
            return sorted(LLMConfig.load().providers)
        except Exception:
            return []

    def available_providers(self) -> list[str]:
        return self.available_providers_for_config()

    def _usage(self) -> dict[str, Any]:
        with self.service.platform.store.read_transaction():
            return self.service.platform.store.repositories.llm.llm_cache_metrics()

    def status(self) -> dict[str, Any]:
        world_status = self.service.status()
        usage = self._usage()
        with self._lock:
            thread_alive = bool(self._thread and self._thread.is_alive())
            state = self._state
            if state in self.ACTIVE_STATES and not thread_alive:
                state = "paused" if state == "pausing" else "failed"
            return {
                "state": state,
                "running": state in self.ACTIVE_STATES and thread_alive,
                "thread_alive": thread_alive,
                "run_id": self._run_id,
                "provider": self._provider_name,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "start_minute": self._start_minute,
                "target_minute": self._target_minute,
                "world_minute": int(world_status.get("world_minute", 0)),
                "world_runtime_status": world_status.get("status"),
                "max_llm_calls": self._max_llm_calls,
                "llm_calls": int(usage.get("calls", 0)),
                "llm_calls_this_run": max(0, int(usage.get("calls", 0)) - self._llm_calls_at_start),
                "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                "completion_tokens": int(usage.get("completion_tokens", 0)),
                "last_error": self._last_error,
                "stop_reason": self._stop_reason,
                "available_providers": self.available_providers(),
                "llm_configured": self.llm_configured(),
                "history": list(self._history),
            }

    def _record_segment(self, state: str) -> None:
        """Append one finished run segment for the cost dashboard.

        Pause/resume/stop of the same run produce separate segments sharing
        a run_id. Exact duplicates (same run, state and end minute, e.g. a
        worker exit immediately followed by stop()) collapse into one entry.
        """

        if not self._run_id:
            return
        try:
            usage = self._usage()
            end_minute = int(self.service.platform.status().get("world_minute", 0))
        except Exception:
            return
        base = self._usage_at_start
        entry = {
            "run_id": self._run_id,
            "provider": self._provider_name,
            "state": state,
            "start_minute": self._start_minute,
            "end_minute": end_minute,
            "minutes_advanced": max(0, end_minute - int(self._start_minute or end_minute)),
            "llm_calls": max(0, int(usage.get("calls", 0)) - int(base.get("calls", 0))),
            "prompt_tokens": max(0, int(usage.get("prompt_tokens", 0)) - int(base.get("prompt_tokens", 0))),
            "completion_tokens": max(0, int(usage.get("completion_tokens", 0)) - int(base.get("completion_tokens", 0))),
            "stop_reason": self._stop_reason,
            "started_at": self._started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        if self._history:
            last = self._history[0]
            if (last.get("run_id"), last.get("state"), last.get("end_minute")) == (entry["run_id"], entry["state"], entry["end_minute"]):
                return
        self._history.insert(0, entry)
        del self._history[20:]

    def start(
        self,
        *,
        provider: str | None = None,
        max_world_minutes: int | None = 1440,
        max_llm_calls: int | None = 120,
    ) -> dict[str, Any]:
        with self._lock:
            if self.running:
                return {"status": "rejected", "failed_check": "already_running", **self.status()}
            limit = None if max_world_minutes in {None, 0} else max(1, int(max_world_minutes))
            call_limit = None if max_llm_calls in {None, 0} else max(1, int(max_llm_calls))
            self._provider_name = str(provider).strip() if provider else None
            try:
                providers = self._providers_for(self._provider_name)
            except Exception as exc:
                self._last_error = str(exc)
                self._state = "failed"
                return {"status": "rejected", "failed_check": "provider", "message": str(exc), **self.status()}
            start = int(self.service.platform.status().get("world_minute", 0))
            target = start + limit if limit is not None else None
            return self._launch(providers=providers, target_minute=target, max_llm_calls=call_limit, new_run=True)

    def _launch(
        self,
        *,
        providers: dict[str, Any],
        target_minute: int | None,
        max_llm_calls: int | None,
        new_run: bool,
    ) -> dict[str, Any]:
        platform = self.service.platform
        if platform.status().get("status") != "running":
            self.service.resume("web-run-resume" if not new_run else "web-run-start")
        usage = self._usage()
        if new_run:
            self._run_id = uuid4().hex
            self._start_minute = int(platform.status().get("world_minute", 0))
            self._llm_calls_at_start = int(usage.get("calls", 0))
            self._usage_at_start = {
                "calls": int(usage.get("calls", 0)),
                "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                "completion_tokens": int(usage.get("completion_tokens", 0)),
            }
            self._started_at = datetime.now(timezone.utc).isoformat()
        self._providers = providers
        self._runner = SimulationRunner(platform, providers, resolve_interactions=bool(providers.get("agent_decision")))
        self._target_minute = target_minute
        self._max_llm_calls = max_llm_calls
        self._last_error = ""
        self._stop_reason = ""
        self._pause_requested = False
        self._stop_event.clear()
        self._finished_at = None
        self._state = "starting"
        self._thread = threading.Thread(target=self._run, name="sim2-web-runtime", daemon=True)
        self._thread.start()
        return {"status": "accepted", **self.status()}

    def pause(self, reason: str = "web") -> dict[str, Any]:
        with self._lock:
            if not self.running:
                if self.service.status().get("status") == "running":
                    self.service.pause(reason)
                self._state = "paused"
                return {"status": "paused", **self.status()}
            # Pause becomes visible after the current complete minute. The
            # worker never aborts an in-flight LLM turn halfway through.
            self._pause_requested = True
            self._state = "pausing"
        self.service.pause(reason)
        return {"status": "pausing", **self.status()}

    def resume(self, reason: str = "web") -> dict[str, Any]:
        with self._lock:
            if self.running:
                return {"status": "rejected", "failed_check": "already_running", **self.status()}
        remaining_calls = (self._max_llm_calls - self._calls_this_run()) if self._max_llm_calls is not None else None
        if remaining_calls is not None and remaining_calls <= 0:
            self._stop_reason = "llm_call_budget_reached"
            self._state = "paused_budget"
            return {"status": "rejected", "failed_check": "llm_budget_exhausted", **self.status()}
        remaining_minutes = (
            self._target_minute - int(self.service.status().get("world_minute", 0))
            if self._target_minute is not None else None
        )
        if remaining_minutes is not None and remaining_minutes <= 0:
            self._stop_reason = "target_minutes_reached"
            self._state = "stopped"
            return {"status": "rejected", "failed_check": "target_complete", **self.status()}
        with self._lock:
            if self.running:
                return {"status": "rejected", "failed_check": "already_running", **self.status()}
            try:
                providers = self._providers_for(self._provider_name)
            except Exception as exc:
                self._last_error = str(exc)
                self._state = "failed"
                return {"status": "rejected", "failed_check": "provider", "message": str(exc), **self.status()}
            return self._launch(
                providers=providers,
                target_minute=self._target_minute,
                max_llm_calls=self._max_llm_calls,
                new_run=False,
            )

    def stop(self, reason: str = "web-stop", *, wait_seconds: float = 2.0) -> dict[str, Any]:
        with self._lock:
            thread = self._thread
            if thread and thread.is_alive():
                self._stop_reason = reason
                self._state = "stopping"
                self._stop_event.set()
        if thread and thread.is_alive():
            self.service.pause(reason)
        elif self.service.status().get("status") == "running":
            # A server restart can leave the persisted world marked running
            # even though this process has no worker. Stop must reconcile both
            # sides of the state before the operator can safely step or close.
            self.service.pause(reason)
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(wait_seconds)))
        with self._lock:
            if not thread or not thread.is_alive():
                self._state = "stopped"
                self._finished_at = datetime.now(timezone.utc).isoformat()
            else:
                self._state = "stopping"
        self._record_segment(self._state if isinstance(self._state, str) else "stopped")
        return {"status": "accepted", **self.status()}

    def step(self) -> dict[str, Any]:
        with self._lock:
            if self.running:
                return {"status": "rejected", "failed_check": "runtime_running", "message": "Pause or stop the automatic runner before stepping."}
        if self.service.status().get("status") == "running":
            self.service.pause("web-step")
        result = self.service.step()
        with self._lock:
            self._state = "paused"
        return result

    def _calls_this_run(self) -> int:
        return max(0, int(self._usage().get("calls", 0)) - self._llm_calls_at_start)

    def _cooldown_wait_seconds(self, runner: SimulationRunner) -> float:
        remaining = runner.provider_cooldown_seconds()
        return min(self.COOLDOWN_MAX_WAIT_SECONDS, max(self.COOLDOWN_MIN_WAIT_SECONDS, remaining))

    def _wait_out_cooldown(self, seconds: float) -> None:
        """Hold world time for a provider cooldown; a pause or stop request ends the wait."""

        deadline = time.monotonic() + seconds
        while not self._stop_event.is_set() and not self._pause_requested:
            left = deadline - time.monotonic()
            if left <= 0:
                return
            self._stop_event.wait(min(0.25, left))

    def _run(self) -> None:
        runner = self._runner
        platform = self.service.platform
        stalls = 0
        try:
            with self._lock:
                self._state = "running"
            while not self._stop_event.is_set():
                if self._pause_requested or platform.status().get("status") != "running":
                    self._state = "paused"
                    break
                minute = int(platform.status().get("world_minute", 0))
                if self._target_minute is not None and minute >= self._target_minute:
                    self._stop_reason = "target_minutes_reached"
                    self.service.pause("web-target-complete")
                    self._state = "stopped"
                    break
                if self._max_llm_calls is not None and self._calls_this_run() >= self._max_llm_calls:
                    self._stop_reason = "llm_call_budget_reached"
                    self.service.pause("web-llm-budget")
                    self._state = "paused_budget"
                    break
                report = runner.run(1)
                reason = str(report.stop_reason or "")
                if reason.startswith("provider_cooldown"):
                    stalls = 0 if report.end_minute > report.start_minute else stalls + 1
                    if stalls >= self.MAX_COOLDOWN_STALLS:
                        self._last_error = reason
                        self.service.pause("web-provider-cooldown")
                        self._state = "cooldown"
                        break
                    seconds = self._cooldown_wait_seconds(runner)
                    self._last_error = f"{reason}: world time is held for {seconds:.1f}s"
                    self._wait_out_cooldown(seconds)
                    self._last_error = ""
                    continue
                stalls = 0
                if reason == "runtime_not_running":
                    self._state = "paused"
                    break
                if reason:
                    self._last_error = reason
                    self._stop_reason = reason
                    self.service.pause("web-runtime-stop")
                    self._state = "failed"
                    break
            if self._stop_event.is_set():
                self._state = "stopped"
        except Exception as exc:  # pragma: no cover - provider failure integration path
            with self._lock:
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._stop_reason = "runtime_error"
                self._state = "failed"
            try:
                self.service.pause("web-runtime-error")
            except Exception:
                pass
        finally:
            with self._lock:
                self._finished_at = datetime.now(timezone.utc).isoformat()
                if self._state == "running":
                    self._state = "stopped"
                exit_state = self._state
            self._record_segment(exit_state if isinstance(exit_state, str) else "stopped")

    def advance_manual(self, minutes: int, *, provider_name: str | None = None, reason: str = "web-player-advance") -> dict[str, Any]:
        """Advance a manual player through the same synchronized runner.

        Manual time must not become a private player-only clock.  NPC decisions,
        interactions, evolution tasks and Platform minute effects all advance
        together, exactly as they do under automatic running.
        """

        with self._lock:
            if self.running:
                return {"status": "rejected", "failed_check": "already_running", "message": "Stop the automatic runner before advancing manually.", **self.status()}
            selected = str(provider_name).strip() if provider_name else self._provider_name
            try:
                providers = self._providers_for(selected)
            except Exception as exc:
                self._last_error = str(exc)
                return {"status": "rejected", "failed_check": "provider", "message": str(exc), **self.status()}
            if selected:
                self._provider_name = selected
        platform = self.service.platform
        report = SimulationRunner(platform, providers, resolve_interactions=bool(providers.get("agent_decision"))).run(max(1, min(int(minutes), 480)), require_running=False)
        if report.stop_reason and report.end_minute == report.start_minute:
            return {
                "status": "rejected",
                "failed_check": report.stop_reason,
                "message": f"Manual advance stopped before the first minute: {report.stop_reason}",
                "report": report.as_dict(),
                **self.status(),
            }
        return {"status": "accepted", "report": report.as_dict(), **self.status()}

    def resolve_pending_interactions(self, provider_name: str | None = None) -> dict[str, Any]:
        """Resolve zero-minute dialogue/trade replies without advancing the clock."""

        with self._lock:
            if self.running:
                return {"status": "queued", "message": "The running world will resolve this interaction.", **self.status()}
            selected = str(provider_name).strip() if provider_name else self._provider_name
            try:
                providers = self._providers_for(selected)
            except Exception as exc:
                self._last_error = str(exc)
                return {"status": "rejected", "failed_check": "provider", "message": str(exc), **self.status()}
            if selected:
                self._provider_name = selected
        report = SimulationRunner(self.service.platform, providers).resolve_pending_interactions()
        return {"status": "accepted", "report": report.as_dict(), **self.status()}

    def close(self) -> None:
        self.stop("web-server-close", wait_seconds=30.0)

