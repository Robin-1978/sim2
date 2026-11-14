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


class WorldTickService(PlatformService):
    """One-minute effects, signals and activity progression."""

    def _apply_minute_effects(self, minute: int, *, agents: list[dict[str, Any]] | None = None) -> None:
        rules = self.store.get_meta("rules", {}) or {}
        minute_event_state = dict(self.store.get_meta("minute_effect_event_state", {}) or {})
        minute_event_state_changed = False
        world_properties = self.store.get_meta("world_properties", {}) or {}
        static_context_values = configured_context_values(rules, include_resolvers=False)
        for regeneration in rules.get("resource_regeneration", []) or []:
            every = max(1, int(regeneration.get("every_minutes", 1)))
            if minute % every != 0:
                continue
            try:
                entity = self.store.get_entity(str(regeneration["resource_id"]))
            except KeyError:
                continue
            state = dict(entity.get("initial_state") or {})
            key = "amount" if isinstance(entity.get("amount"), (int, float)) else "quantity"
            before = float(entity.get("amount") if key == "amount" else state.get("quantity", 0) or 0)
            cap = float(regeneration.get("cap", before) or before)
            after = min(cap, before + float(regeneration.get("amount", 0) or 0))
            if after <= before:
                continue
            if key == "amount":
                entity["amount"] = after
            else:
                state[key] = after
                entity["initial_state"] = state
            payload = {name: value for name, value in entity.items() if name not in {"id", "entity_type", "name"}}
            self.store.put_entity(entity["id"], entity["entity_type"], entity["name"], payload)
            self.store.sync_world_property_from_resource(entity["id"], after)
            self.store.append_event("resource.regenerated", None, minute, {"resource_id": entity["id"], "amount": after - before, "total": after})
        due_effects = [
            effect
            for effect in (rules.get("minute_effects", []) or [])
            if minute % max(1, int(effect.get("every_minutes", 1))) == 0
        ]
        agent_effects = [effect for effect in due_effects if effect.get("scope") == "agent"]
        if agent_effects:
            changed_agents: list[dict[str, Any]] = []
            vital = self.attribute_schema.vital_attribute()
            vital_minimum = self.attribute_schema.minimum(vital) if vital else 0
            for agent in agents if agents is not None else self.store.list_agents():
                agent_changed = False
                for effect in agent_effects:
                    state = dict(agent.get("initial_state") or {})
                    before_state = dict(state)
                    before_needs = dict(agent.get("needs") or {})
                    condition = str(effect.get("condition") or "").strip()
                    if condition == "action.none" and state.get("status") in {"executing", "paused"}:
                        continue
                    if condition and condition != "action.none" and not self.conditions.evaluate(
                        condition,
                        agent=agent,
                        target={},
                        context={
                            **static_context_values,
                            "current_location": self._entity_for_context(state.get("location_id")),
                        },
                        world=world_properties,
                    ):
                        continue
                    self._apply_simple_effect(agent, effect.get("effect", {}) or {})
                    agent_changed = True
                    if effect.get("events"):
                        minute_event_state_changed = self._emit_minute_effect_events(
                            effect,
                            agent,
                            minute,
                            before_state,
                            before_needs,
                            event_state=minute_event_state,
                            rules=rules,
                            world=world_properties,
                            static_context=static_context_values,
                        ) or minute_event_state_changed
                    state = dict(agent.get("initial_state") or {})
                    vital_value = float(state.get(vital, 1) or 0) if vital else 1
                    if vital and vital_value <= vital_minimum and state.get("status") not in {"unconscious", "dead"}:
                        state["status"] = "unconscious"
                        conditions = list(state.get("conditions") or [])
                        if "incapacitated" not in conditions:
                            conditions.append("incapacitated")
                        state["conditions"] = conditions
                        agent["initial_state"] = state
                        self.store.append_event("agent.incapacitated", agent["id"], minute, {"reason": effect.get("id", "minute_effect")})
                if agent_changed:
                    changed_agents.append(agent)
            self.store.update_agents_bulk(changed_agents)

        for effect in due_effects:
            scope = effect.get("scope")
            definition = effect.get("effect", {}) or {}
            if scope == "world":
                key = definition.get("world_property") or definition.get("property") or definition.get("need")
                if key:
                    properties = dict(self.store.get_meta("world_properties", {}) or {})
                    before = float(properties.get(key, 0) or 0)
                    after = before + float(definition.get("delta", 0) or 0)
                    if definition.get("minimum") is not None:
                        after = max(float(definition["minimum"]), after)
                    if definition.get("maximum") is not None:
                        after = min(float(definition["maximum"]), after)
                    if abs(after - before) > 1e-12:
                        self._set_world_property(str(key), after)
            elif scope == "relationship":
                relation_delta = float(definition.get("relation_strength", definition.get("delta", 0)))
                for relation in self.store.list_relationships():
                    data = {key: value for key, value in relation.items() if key not in {"from", "to", "type"}}
                    data["relation_strength"] = float(data.get("relation_strength", data.get("value", 0))) + relation_delta
                    self.store.put_relationship(relation["from"], relation["to"], relation["type"], data)
        if minute_event_state_changed:
            # Threshold state belongs to the whole minute. Persisting the
            # growing map after every agent/effect made a 1,000-agent tick
            # encode and decode the same JSON thousands of times.
            self.store.set_meta("minute_effect_event_state", minute_event_state)

    def _emit_resource_safety_signals(self, minute: int) -> None:
        """Publish configured stock-safety signals at a crossing or repeat interval.

        A one-time crossing is the default.  Worlds with a persistent shortage
        may declare ``repeat_every_minutes`` so the same unresolved shortage
        is reviewed again after the configured interval; the signal remains
        declarative and the consumer's own cooldown still applies.
        """

        rules = self.store.get_meta("rules", {}) or {}
        configured = rules.get("resource_safety", []) or []
        if not isinstance(configured, list):
            return
        state = dict(self.store.get_meta("resource_safety_signal_state", {}) or {})
        changed = False
        for raw in configured:
            if not isinstance(raw, dict) or not raw.get("resource_id"):
                continue
            resource_id = str(raw["resource_id"])
            try:
                entity = self.store.get_entity(resource_id)
            except KeyError:
                continue
            quantity = self._entity_quantity(entity)
            floor = float(raw.get("minimum_reserve", raw.get("reserve_floor", 0)) or 0)
            active = quantity <= floor
            previous_state = state.get(resource_id, False)
            if isinstance(previous_state, dict):
                previous = bool(previous_state.get("active", False))
                last_emitted = int(previous_state.get("last_emitted_minute", -10**9) or -10**9)
            else:
                previous = bool(previous_state)
                last_emitted = -10**9
            repeat_every = max(0, int(raw.get("repeat_every_minutes", 0) or 0))
            should_emit = active and (
                not previous
                or (repeat_every > 0 and int(minute) - last_emitted >= repeat_every)
            )
            if should_emit:
                event_type = str(raw.get("event_type") or "resource.safety_low")
                event_id = self.store.append_event(event_type, None, minute, {
                    "resource_id": resource_id,
                    "resource_name": entity.get("name", resource_id),
                    "quantity": quantity,
                    "minimum_reserve": floor,
                    "summary": f"{entity.get('name', resource_id)} is at or below its configured reserve floor.",
                    "signal_kind": "crossing" if not previous else "repeat",
                })
                self.wakes.configured(event_id, event_type, None, minute)
                state[resource_id] = {"active": active, "last_emitted_minute": int(minute)}
                changed = True
            elif active != previous:
                state[resource_id] = {"active": active, "last_emitted_minute": last_emitted}
                changed = True
        if changed:
            self.store.set_meta("resource_safety_signal_state", state)

    def _apply_simple_effect(self, agent: dict[str, Any], effect: dict[str, Any]) -> None:
        self.effects.apply(agent, effect)

    def _emit_minute_effect_events(
        self,
        rule: dict[str, Any],
        agent: dict[str, Any],
        minute: int,
        before_state: dict[str, Any],
        before_needs: dict[str, Any],
        *,
        event_state: dict[str, Any],
        rules: dict[str, Any],
        world: dict[str, Any],
        static_context: dict[str, Any],
    ) -> bool:
        """Emit configured threshold or recurring events after a minute effect."""

        configured = rule.get("events", []) or []
        if not isinstance(configured, list):
            return False
        changed_state = False
        conditions = " ".join(
            str(item.get("condition") or rule.get("condition") or "")
            for item in configured
            if isinstance(item, dict)
        )
        location = (
            self._entity_for_context((agent.get("initial_state") or {}).get("location_id"))
            if "location." in conditions or "current_location" in conditions
            else {}
        )
        context = {
            **static_context,
            "current_location": location,
            "location_id": (agent.get("initial_state") or {}).get("location_id"),
        }
        resolver_names = (rules.get("context_resolvers", {}) or {}).keys()
        if any(f"context.{name}" in conditions for name in resolver_names):
            context.update(configured_context_values(rules, agent))
        for index, item in enumerate(configured):
            if not isinstance(item, dict):
                continue
            event_type = str(item.get("event_type") or item.get("event") or "").strip()
            condition = str(item.get("condition") or rule.get("condition") or "").strip()
            if not event_type or not condition:
                continue
            active = bool(self.conditions.evaluate(condition, agent=agent, target={}, context=context, world=world))
            state_key = f"{agent.get('id')}:{rule.get('id', index)}:{item.get('id', index)}"
            previous = bool(event_state.get(state_key, False))
            edge = str(item.get("edge", "each")).strip().lower()
            if edge == "enter":
                emit = active and not previous
            elif edge == "exit":
                emit = not active and previous
            else:
                emit = active
            if emit:
                changed: dict[str, dict[str, Any]] = {}
                for key in set(before_state) | set((agent.get("initial_state") or {})):
                    before = before_state.get(key)
                    after = (agent.get("initial_state") or {}).get(key)
                    if before != after:
                        changed[key] = {"before": before, "after": after}
                for key in set(before_needs) | set(agent.get("needs") or {}):
                    before = before_needs.get(key)
                    after = (agent.get("needs") or {}).get(key)
                    if before != after:
                        changed[key] = {"before": before, "after": after}
                payload = dict(item.get("data") or {})
                payload.update({"rule_id": rule.get("id"), "edge": edge, "changes": changed})
                if item.get("summary"):
                    payload["summary"] = str(item["summary"])
                if item.get("trend") is True:
                    payload["trend"] = True
                self.store.append_event(event_type, agent["id"], minute, payload)
            if previous != active or state_key not in event_state:
                event_state[state_key] = active
                changed_state = True
        return changed_state

    def _set_world_property(self, key: str, value: Any) -> dict[str, Any]:
        """Update one world property and its shared-resource projection atomically.

        A resource may also be exposed as a world property, for example
        ``village_food``.  The resource ledger remains the world quantity, while
        the property is the prompt/condition projection.  Updating only one side
        makes triggers, Agent context and God proposals disagree.
        """

        properties = dict(self.store.get_meta("world_properties", {}) or {})
        before = properties.get(str(key))
        properties[str(key)] = value
        self.store.set_meta("world_properties", properties)
        projection = self._sync_world_resource_projection(str(key), value)
        effect = {"world_property": str(key), "before": before, "after": value}
        if projection:
            effect["resource_projection"] = projection
        return effect

    def _sync_world_resource_projection(self, key: str, value: Any) -> dict[str, Any] | None:
        """Keep a numeric shared resource equal to its world-property view."""

        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        try:
            entity = self.store.get_entity(str(key))
        except KeyError:
            return None
        if entity.get("entity_type") != "resource":
            return None
        state = dict(entity.get("initial_state") or {})
        before = entity.get("amount", state.get("quantity", state.get("amount")))
        if not isinstance(before, (int, float)):
            return None
        if "amount" in entity:
            entity["amount"] = value
        else:
            state_key = "quantity" if "quantity" in state else "amount"
            state[state_key] = value
            entity["initial_state"] = state
        self.store.put_entity(
            entity["id"],
            entity["entity_type"],
            entity["name"],
            {name: item for name, item in entity.items() if name not in {"id", "entity_type", "name"}},
        )
        return {"entity_id": entity["id"], "before": before, "after": value}

    def step(self) -> dict[str, Any]:
        """Commit exactly one world-minute tick."""

        snapshot = self.store.world_snapshot(include_agent_revisions=False)
        start = int(snapshot["world_minute"])
        minute = start + 1
        completed: list[str] = []
        failed: list[str] = []
        with self.store.transaction():
            agents = self.store.list_agents()
            agent_ids = {str(agent["id"]) for agent in agents}
            self._apply_minute_effects(minute, agents=agents)
            for agent in agents:
                before = agent.get("initial_state", {}).get("active_bt_instance")
                if not before:
                    continue
                self._progress_agent(agent, minute)
                after = self.store.get_agent(agent["id"]).get("initial_state", {})
                result = after.get("last_platform_result") or {}
                if result.get("status") == "completed" and result.get("behavior_ref"):
                    completed.append(agent["id"])
                if result.get("status") == "failed" and result.get("behavior_ref"):
                    failed.append(agent["id"])
            self.store.set_meta("world_minute", minute)
            self.store.append_event("world_minute.advanced", None, minute, {"minute": minute})
            self._emit_world_rule_signals(minute, agent_ids=agent_ids)
            self._emit_resource_safety_signals(minute)
            self.proposal_evaluator.evaluate_due(minute)
            self._emit_evolution_triggers(minute, agent_ids=agent_ids)
            self._emit_stagnation_signal(minute, agents=agents)
            self._emit_due_evolution_tasks(minute)
        return {
            "status": "advanced",
            "from_minute": start,
            "to_minute": minute,
            "snapshot_revision": int(snapshot["world_revision"]),
            "world_revision": int(self.store.get_meta("world_revision", 0) or 0),
            "completed_agents": sorted(set(completed)),
            "failed_agents": sorted(set(failed)),
        }
