from __future__ import annotations

from typing import Any

from .services import PlatformService


class EventService(PlatformService):
    """The single runtime owner for committed world-event creation.

    The UnitOfWork still owns the SQLite transaction and the typed repository
    still performs the insert.  Platform services call this boundary instead
    of reaching into the repository directly, so event metadata and future
    publication rules have one place to evolve.
    """

    def append(
        self,
        event_type: str,
        actor_id: str | None,
        minute: int,
        payload: dict[str, Any] | None = None,
        *,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> int:
        data = dict(payload or {})
        if causation_id is not None:
            data["causation_id"] = str(causation_id)
        if correlation_id is not None:
            data["correlation_id"] = str(correlation_id)
        return self.uow.events.append(str(event_type), actor_id, int(minute), data)

    def publish(
        self,
        event_type: str,
        actor_id: str | None,
        minute: int,
        payload: dict[str, Any] | None = None,
        *,
        wake: str | dict[str, Any] | list[str] | tuple[Any, ...] | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> int:
        """Append one fact and dispatch its declared wake in the same UoW.

        ``wake`` is deliberately a small publication vocabulary.  It carries
        no world meaning: ``configured`` uses the world wake policy,
        ``story_source`` creates the Story evidence barrier, and a mapping
        with ``wake`` delegates to explicitly declared follow-up consumers.
        """

        event_id = self.append(
            event_type,
            actor_id,
            minute,
            payload,
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
        if wake is not None:
            self.wake(event_id, event_type, actor_id, minute, wake=wake)
        return event_id

    def wake(
        self,
        event_id: int,
        event_type: str,
        actor_id: str | None,
        minute: int,
        *,
        wake: str | dict[str, Any] | list[str] | tuple[Any, ...],
    ) -> None:
        """Dispatch wake instructions for an already committed event."""

        wakes = self._context.dependencies.get("wakes")
        if wakes is None:
            raise RuntimeError("WakeService is not bound to EventService")
        if wake == "configured":
            wakes.configured(event_id, event_type, actor_id, int(minute))
            return
        if wake == "story_source":
            wakes.story_source(event_id, event_type, actor_id, int(minute))
            return
        if isinstance(wake, dict) and wake.get("configured"):
            wakes.configured(event_id, event_type, actor_id, int(minute))
        if isinstance(wake, dict) and wake.get("followups") is not None:
            followups = wake["followups"]
            if isinstance(followups, dict):
                wakes.followups(followups, event_id, event_type, actor_id, int(minute))
            else:
                wakes.followups({"wake": followups}, event_id, event_type, actor_id, int(minute))
            return
        if isinstance(wake, dict):
            wakes.followups(wake, event_id, event_type, actor_id, int(minute))
            return
        if isinstance(wake, (list, tuple)):
            wakes.followups({"wake": list(wake)}, event_id, event_type, actor_id, int(minute))
            return
        raise ValueError(f"unsupported wake publication: {wake!r}")

    def queue_consumer(
        self,
        consumer: str,
        event_id: int,
        event_type: str,
        actor_id: str | None,
        minute: int,
        *,
        task_type: str | None = None,
        explicit: bool = False,
    ) -> str | None:
        """Queue one configured consumer for an already recorded event."""

        wakes = self._context.dependencies.get("wakes")
        if wakes is None:
            raise RuntimeError("WakeService is not bound to EventService")
        return wakes.queue(
            str(consumer),
            int(event_id),
            str(event_type),
            actor_id,
            int(minute),
            task_type=task_type,
            explicit=explicit,
        )

