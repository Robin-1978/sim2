from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..actions.capacity import check_action_capacity
from ..behavior.estimator import BehaviorDurationEstimator
from ..behavior.recovery import failure_block_recovered
from ..intents.models import BehaviorIntent
from ..world.context import configured_context_values
from ..world.navigation import (
    configured_location_tags,
    nearest_location_with_tag,
    nearest_resource_node,
    nearest_safe_location,
    reachable_locations,
)
from ..world.time import world_time_view
from ..world.rules import RuleCompiler
from ..world.schema import AttributeSchema
from ..actions.conditions import ConditionEngine
from .services import PlatformService


class EvolutionService(PlatformService):
    """World signals and persisted evolution task lifecycle."""

    def _world_trigger_context(self) -> dict[str, Any]:
        context = dict(self.store.get_meta("world_properties", {}) or {})
        for resource in self.store.repositories.entities.entities_of_type("resource"):
            try:
                entity = self.store.repositories.entities.get_entity(resource["id"])
            except KeyError:
                continue
            amount = entity.get("amount", (entity.get("initial_state") or {}).get("quantity"))
            if isinstance(amount, (int, float)):
                self.store.repositories.entities.sync_world_property_from_resource(resource["id"], amount)
                context[resource["id"]] = amount
        return context

    def _recent_event_types(self, limit: int = 120) -> list[str]:
        rows = self.store.connection.execute(
            "SELECT DISTINCT event_type FROM event_log WHERE world_minute<=? ORDER BY event_id DESC LIMIT ?",
            (int(self.store.get_meta("world_minute", 0)), max(1, min(int(limit), 500))),
        ).fetchall()
        return [str(row["event_type"]) for row in rows]

    def _emit_world_rule_signals(self, minute: int, *, agent_ids: set[str] | None = None) -> None:
        """Emit edge-triggered signals from active declarative world rules."""

        world = self._world_trigger_context()
        signals = self.rule_compiler.signal_specs(agent={}, context={"recent_event_types": self._recent_event_types()}, world=world)
        state = dict(self.store.get_meta("world_rule_signal_state", {}) or {})
        for signal in signals:
            name = str(signal.get("name") or "world_rule")
            state_key = str(signal.get("id") or name)
            was_active = bool(state.get(state_key, False))
            if not was_active:
                event_id = self._context.services["events"].append(
                    str(signal.get("event_type") or f"world.rule.{name}"),
                    None,
                    minute,
                    {
                        "rule": name,
                        "condition": signal.get("condition"),
                        "effect": signal.get("effect"),
                        "signal": True,
                    },
                )
                for index, target in enumerate(signal.get("wake") or []):
                    target = str(target)
                    if target in (agent_ids if agent_ids is not None else {agent["id"] for agent in self.store.repositories.entities.list_agents()}):
                        role, actor_id = "agent_reflection", target
                    else:
                        role, actor_id = target, None
                    self._context.services["events"].queue_consumer(role, event_id, str(signal.get("event_type") or f"world.rule.{name}"), actor_id, minute, task_type=f"rule:{name}")
        active_ids = {str(item.get("id")) for item in signals}
        for rule in self.rule_compiler.all_rules():
            state_key = str(rule.get("id") or rule.get("name") or "world_rule")
            state[state_key] = state_key in active_ids
        self.store.set_meta("world_rule_signal_state", state)

    def _emit_evolution_triggers(self, minute: int, *, agent_ids: set[str] | None = None) -> None:
        evolution = self.store.get_meta("evolution", {}) or {}
        config = evolution.get("evolution", evolution) if isinstance(evolution, dict) else {}
        triggers = config.get("triggers", []) if isinstance(config, dict) else []
        states = dict(self.store.get_meta("evolution_trigger_state", {}) or {})
        world = self._world_trigger_context()
        known_agents = agent_ids if agent_ids is not None else {agent["id"] for agent in self.store.repositories.entities.list_agents()}
        for trigger in triggers:
            trigger_id = str(trigger.get("id", ""))
            if not trigger_id:
                continue
            expression = str(trigger.get("when", ""))
            active = self.conditions.evaluate(expression, agent={}, target={}, world=world, context={}) if expression else False
            previous = bool(states.get(trigger_id, False))
            if active and not previous:
                self._context.services["events"].append("evolution.triggered", None, minute, {"trigger_id": trigger_id, "condition": expression, "invoke": trigger.get("invoke", [])})
                for index, target in enumerate(trigger.get("invoke", []) or []):
                    target = str(target)
                    role_target, resolved_actor = self._context.services["api"]._resolve_evolution_consumer(target)
                    if role_target == "agent_decision":
                        role, task_type, actor_id = role_target, f"trigger:{trigger_id}", resolved_actor
                    elif role_target == "god":
                        role, task_type, actor_id = "god", target, None
                    elif role_target == "story":
                        role, task_type, actor_id = "story", target, None
                    elif target in known_agents:
                        role, task_type, actor_id = "agent_reflection", f"trigger:{trigger_id}", target
                    else:
                        role, task_type, actor_id = "agent_reflection", target, None
                    self.uow.evolution.put_task(f"trigger:{trigger_id}:{minute}:{index}", role, actor_id, task_type, minute, 0, data={"trigger_id": trigger_id})
            states[trigger_id] = active
        self.store.set_meta("evolution_trigger_state", states)

    def _emit_due_evolution_tasks(self, minute: int) -> None:
        for task in self.uow.evolution.due_tasks(minute):
            self.uow.evolution.mark_ready(task["task_id"])
            self._context.services["events"].append(
                "evolution.task_due",
                task.get("actor_id"),
                minute,
                {"task_id": task["task_id"], "role": task["role"], "task_type": task["task_type"]},
            )

    def ready_evolution_tasks(self) -> list[dict[str, Any]]:
        """Return low-frequency Agent tasks without invoking an LLM."""

        return self.uow.evolution.ready_tasks()

    def claim_evolution_task(self, task_id: str) -> dict[str, Any] | None:
        minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            return self.uow.evolution.claim(task_id, minute)

    def recover_claimed_evolution_tasks(self) -> list[str]:
        """Requeue due evolution work left behind by an interrupted process."""

        minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            task_ids = self.uow.evolution.recover_claimed(minute)
            if task_ids:
                self._context.services["events"].append(
                    "evolution.tasks_recovered",
                    None,
                    minute,
                    {"task_ids": task_ids, "reason": "previous process stopped after claim"},
                )
            return task_ids

    def complete_evolution_task(self, task_id: str) -> None:
        minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            self.uow.evolution.complete(task_id, minute)

    def retry_evolution_task(self, task_id: str, retry_minute: int, reason: str) -> None:
        """Return a failed low-frequency task to the scheduler with an audit event."""

        minute = int(self.store.get_meta("world_minute", 0))
        # A provider circuit breaker stops the runner before another request
        # can be issued, so a retry at the current minute cannot spin.  Keep
        # that boundary intact: advancing one world minute would make the
        # failed reflection/evolution turn observe a different world.  Other
        # rejections pass a future retry minute and retain their backoff.
        scheduled_minute = max(minute, int(retry_minute))
        with self.store.transaction():
            row = self.store.connection.execute(
                "SELECT data_json FROM evolution_task WHERE task_id=?", (task_id,)
            ).fetchone()
            data: dict[str, Any] = {}
            if row is not None:
                try:
                    parsed = json.loads(row["data_json"] or "{}")
                    if isinstance(parsed, dict):
                        data = parsed
                except (TypeError, ValueError, json.JSONDecodeError):
                    data = {}
            data["retry_count"] = int(data.get("retry_count", 0) or 0) + 1
            data["last_retry_reason"] = str(reason)
            self.uow.evolution.reschedule(task_id, scheduled_minute, data)
            if scheduled_minute == minute:
                # ``step()`` normally promotes due tasks to ready. A provider
                # retry intentionally does not step, so promote this one now
                # and let the resumed runner claim it before time advances.
                self.uow.evolution.mark_ready(task_id)
            self._context.services["events"].append(
                "evolution.task_retry",
                None,
                minute,
                {
                    "task_id": task_id,
                    "retry_minute": scheduled_minute,
                    "reason": str(reason),
                    "retry_count": data["retry_count"],
                },
            )
