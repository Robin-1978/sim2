from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator


class EventRepository:
    """Event-log reads and writes inside the caller's Unit of Work."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def append(self, event_type: str, actor_id: str | None, minute: int, payload: dict[str, Any]) -> int:
        return self.store.append_event(event_type, actor_id, minute, payload)

    def recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.store.recent_events(limit=limit)

    def by_ids(self, event_ids: list[int]) -> list[dict[str, Any]]:
        return self.store.events_by_ids(event_ids)


class EvolutionRepository:
    """Evolution-task persistence; methods never open or commit a transaction."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def put_task(
        self,
        task_id: str,
        role: str,
        actor_id: str | None,
        task_type: str,
        minute: int,
        due_minute: int,
        *,
        status: str = "scheduled",
        data: dict[str, Any] | None = None,
    ) -> None:
        self.store.put_evolution_task(
            task_id,
            role,
            actor_id,
            task_type,
            minute,
            due_minute,
            status=status,
            data=data,
        )

    def due_tasks(self, world_minute: int) -> list[dict[str, Any]]:
        return self.store.due_evolution_tasks(world_minute)

    def ready_tasks(self) -> list[dict[str, Any]]:
        return self.store.ready_evolution_tasks()

    def mark_ready(self, task_id: str) -> None:
        self.store.mark_evolution_task_ready(task_id)

    def claim(self, task_id: str, world_minute: int) -> dict[str, Any] | None:
        return self.store.claim_evolution_task(task_id, world_minute)

    def recover_claimed(self, world_minute: int) -> list[str]:
        return self.store.recover_claimed_evolution_tasks(world_minute)

    def reschedule(self, task_id: str, due_minute: int, data: dict[str, Any] | None = None) -> None:
        self.store.reschedule_evolution_task(task_id, due_minute, data)

    def complete(self, task_id: str, completed_minute: int) -> None:
        self.store.complete_evolution_task(task_id, completed_minute)


class UnitOfWork:
    """One SQLite transaction with typed repository entry points.

    Repositories deliberately do not expose ``commit``.  The transaction
    belongs to this object and is the same boundary used by Platform.
    """

    def __init__(self, store: Any) -> None:
        self.store = store
        self.events = EventRepository(store)
        self.evolution = EvolutionRepository(store)

    @contextmanager
    def transaction(self) -> Iterator[UnitOfWork]:
        with self.store.transaction():
            yield self
