from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..evolution.wakes import EventWakeScheduler


class WakeService:
    """Own all evolution-task creation from committed world events.

    The service is deliberately small: ``EventWakeScheduler`` computes the
    configured evidence threshold, while this class resolves consumers and
    persists the resulting task. Activity, social and world-definition code
    publish wakes here instead of writing evolution tasks directly.
    """

    def __init__(self, uow: Any, resolve_consumer: Callable[[str, str | None], tuple[str, str | None]]):
        self.uow = uow
        self.store = uow.store
        self._resolve_consumer = resolve_consumer
        self.scheduler = EventWakeScheduler(self.store)

    def queue(
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
        role, resolved_actor = self._resolve_consumer(str(consumer), actor_id)
        evolution = self.store.get_meta("evolution", {}) or {}
        evolution = evolution.get("evolution", evolution) if isinstance(evolution, dict) else {}
        consumers = ((evolution.get("event_wake_policy", {}) or {}).get("consumers", {}) or {}) if isinstance(evolution, dict) else {}
        manual_events = {
            str(item)
            for item in ((consumers.get(role, {}) or {}).get("manual_event_types") or [])
        }
        if not explicit and str(event_type) in manual_events:
            return None
        if role in {"agent_decision", "agent_reflection"} and resolved_actor:
            task_id = f"{role}:event:{int(event_id)}:{resolved_actor}"
            self.uow.evolution.put_task(
                task_id,
                role,
                resolved_actor,
                str(task_type or f"event:{event_type}"),
                int(minute),
                0,
                status="ready",
                data={
                    "source_events": [{"event_id": int(event_id), "event_type": str(event_type), "actor_id": actor_id}],
                    "source_event_id": int(event_id),
                    "source_event_type": str(event_type),
                    "source_actor_id": actor_id,
                },
            )
            return task_id
        return self.scheduler.queue(
            role,
            int(event_id),
            str(event_type),
            resolved_actor,
            int(minute),
            task_type=task_type,
            explicit=explicit,
        )

    def configured(self, event_id: int, event_type: str, actor_id: str | None, minute: int) -> None:
        """Publish one event to all consumers declared by the world package."""

        root = self.store.get_meta("evolution", {}) or {}
        config = root.get("evolution", root) if isinstance(root, dict) else {}
        policy = config.get("event_wake_policy", {}) if isinstance(config, dict) else {}
        consumers = policy.get("consumers", {}) if isinstance(policy, dict) else {}
        if not isinstance(consumers, dict):
            return
        for role in consumers:
            self.queue(str(role), event_id, event_type, actor_id, minute, task_type=f"event:{event_type}")

    def followups(self, configured: dict[str, Any], event_id: int, event_type: str, actor_id: str | None, minute: int) -> None:
        """Publish explicitly declared event consumers from a definition."""

        for consumer in configured.get("wake", configured.get("wake_roles", [])) or []:
            if str(consumer).strip():
                self.queue(
                    str(consumer),
                    event_id,
                    event_type,
                    actor_id,
                    minute,
                    task_type=f"event:{event_type}",
                    explicit=True,
                )

    def story_source(self, event_id: int, event_type: str, actor_id: str | None, minute: int) -> str:
        """Create the Story barrier for a validated narrative source event."""

        task_id = f"story:event:{int(event_id)}"
        self.uow.evolution.put_task(
            task_id,
            "story",
            "story",
            f"story:{event_type}",
            int(minute),
            0,
            data={
                "source_event_id": int(event_id),
                "source_event_type": str(event_type),
                "source_actor_id": actor_id,
            },
        )
        return task_id
