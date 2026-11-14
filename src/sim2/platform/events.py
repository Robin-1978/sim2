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

