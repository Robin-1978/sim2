"""Resumable manual-advance tasks.

A player wait is no longer a synchronous request.  The handler returns 202 with
a task id; this runner advances the world one minute per chunk, releasing the
server control lock between chunks, and publishes progress on the stream.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .context import ApiError


@dataclass
class TaskRecord:
    task_id: str
    player_id: str
    instance_id: str
    provider: str | None
    requested_minutes: int
    state: str = "queued"
    completed_minutes: int = 0
    start_minute: int = 0
    world_minute: int = 0
    follow_active_task: bool = False
    decisions: int = 0
    llm_errors: int = 0
    stop_reason: str = ""
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "player_id": self.player_id,
            "state": self.state,
            "requested_minutes": int(self.requested_minutes),
            "completed_minutes": int(self.completed_minutes),
            "world_minute": int(self.world_minute),
            "decisions": int(self.decisions),
            "llm_errors": int(self.llm_errors),
            "stop_reason": self.stop_reason,
            "result": dict(self.result),
            "error": self.error,
        }


class ManualTaskRunner:
    MAX_TASKS = 200

    def __init__(self, server: Any):
        self.server = server
        self._lock = threading.RLock()
        self._tasks: dict[str, TaskRecord] = {}
        self._active: dict[str, str] = {}

    def submit(
        self,
        player_id: str,
        *,
        minutes: int,
        provider: str | None,
        instance_id: str,
    ) -> dict[str, Any]:
        minutes = max(1, min(int(minutes), 480))
        with self._lock:
            current = self._active.get(player_id)
            if current and current in self._tasks and self._tasks[current].state in {"queued", "running", "cancelling"}:
                raise ApiError(409, "task_running", "a manual advance is already running for this player", failed_check="task_running")
            runtime = self.server.runtime_status()
            if runtime.get("running"):
                raise ApiError(
                    409,
                    "runtime_running",
                    "The autonomous world is already advancing; pause or stop it before using manual wait.",
                    failed_check="runtime_running",
                )
            record = TaskRecord(
                task_id=f"task_{uuid4().hex}",
                player_id=str(player_id),
                instance_id=str(instance_id),
                provider=str(provider).strip() if provider else None,
                requested_minutes=minutes,
            )
            state = self.server.player_state(player_id)
            record.world_minute = int(state.get("world_minute", 0) or 0)
            record.start_minute = record.world_minute
            record.follow_active_task = bool(state.get("active"))
            self._tasks[record.task_id] = record
            self._active[player_id] = record.task_id
            self._evict_locked()
            thread = threading.Thread(
                target=self._run,
                args=(record,),
                name=f"sim2-manual-task-{record.task_id[-6:]}",
                daemon=True,
            )
            record.thread = thread
            thread.start()
        self._publish("task.progress", record)
        return record.snapshot()

    def get(self, player_id: str, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._tasks.get(str(task_id))
            if record is None or record.player_id != str(player_id):
                return None
            return record.snapshot()

    def cancel(self, player_id: str, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._tasks.get(str(task_id))
            if record is None or record.player_id != str(player_id):
                return None
            if record.state in {"queued", "running"}:
                record.state = "cancelling"
                record.cancel.set()
            return record.snapshot()

    # ---- runner -------------------------------------------------------------

    def _run(self, record: TaskRecord) -> None:
        with self._lock:
            if record.state == "cancelling":
                record.state = "cancelled"
        if record.state == "cancelled":
            self._finish(record, "cancelled")
            return
        record.state = "running"
        self._publish("task.progress", record)
        while record.completed_minutes < record.requested_minutes:
            if record.cancel.is_set():
                record.state = "cancelled"
                self._finish(record, "cancelled")
                return
            try:
                result = self.server.advance_player_chunk(
                    record.player_id,
                    1,
                    provider_name=record.provider,
                    instance_id=record.instance_id,
                )
            except Exception as exc:  # pragma: no cover - unexpected service failure
                record.state = "failed"
                record.error = str(exc)
                self._finish(record, "failed")
                return
            if result.get("status") != "accepted":
                record.state = "failed"
                record.error = str(result.get("message") or "manual advance was rejected")
                record.stop_reason = str(result.get("failed_check") or "")
                self._finish(record, "failed")
                return
            report = result.get("report") if isinstance(result.get("report"), dict) else {}
            record.completed_minutes += 1
            record.decisions += int(report.get("decisions", 0) or 0)
            record.llm_errors += int(report.get("llm_errors", 0) or 0)
            record.stop_reason = str(report.get("stop_reason") or record.stop_reason or "")
            record.world_minute = int(result.get("world_minute", report.get("end_minute", record.world_minute)) or record.world_minute)
            self._publish("task.progress", record)
            state = self.server.player_state(record.player_id)
            if record.follow_active_task and not state.get("active"):
                break
            # A manual minute is a real Platform tick; yield between chunks so
            # read requests and the SSE hub are not starved by a tight loop.
            time.sleep(0.01)
        if record.cancel.is_set():
            record.state = "cancelled"
        else:
            record.state = "completed"
        self._finish(record, record.state)

    def _finish(self, record: TaskRecord, state: str) -> None:
        record.state = state
        record.result = {
            "status": "accepted" if state == "completed" else "rejected",
            "report": {
                "start_minute": record.start_minute,
                "end_minute": record.world_minute,
                "decisions": record.decisions,
                "llm_errors": record.llm_errors,
                "stop_reason": record.stop_reason or ("completed" if state == "completed" else state),
            },
            "world_minute": record.world_minute,
        }
        with self._lock:
            if self._active.get(record.player_id) == record.task_id:
                self._active.pop(record.player_id, None)
        self._publish("task.completed", record)

    def _publish(self, kind: str, record: TaskRecord) -> None:
        try:
            state = self.server.player_state(record.player_id)
            revision = int(state.get("world_revision", 0) or 0)
        except Exception:
            revision = 0
        self.server.stream.publish(
            kind,
            {"player_id": record.player_id, "task": record.snapshot()},
            revision=revision,
            world_minute=record.world_minute,
        )

    def _evict_locked(self) -> None:
        if len(self._tasks) <= self.MAX_TASKS:
            return
        finished = [item for item in self._tasks.values() if item.state in {"completed", "failed", "cancelled"}]
        finished.sort(key=lambda item: item.created_at)
        for item in finished[: max(0, len(self._tasks) - self.MAX_TASKS)]:
            self._tasks.pop(item.task_id, None)

    def close(self) -> None:
        with self._lock:
            records = list(self._tasks.values())
        for record in records:
            record.cancel.set()
        for record in records:
            if record.thread is not None and record.thread.is_alive():
                record.thread.join(timeout=5.0)

    def cancel_all(self) -> None:
        """Ask every queued or running task to stop at its next chunk boundary."""

        with self._lock:
            records = list(self._tasks.values())
        for record in records:
            if record.state in {"queued", "running"}:
                record.state = "cancelling"
                record.cancel.set()
