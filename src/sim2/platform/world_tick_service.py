from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..actions.capacity import check_action_capacity
from ..behavior.estimator import BehaviorDurationEstimator
from ..behavior.recovery import failure_block_recovered
from ..intents.models import BehaviorIntent
from ..world.context import calendar_context, configured_context_values
from ..world.bindings import resolve_binding
from ..world.lifecycle import (
    BUSY_STATUSES,
    INACTIVE_STATUSES,
    INCAPACITATED_CONDITION,
    INCAPACITATED_SINCE_KEY,
    UNCONSCIOUS,
    recovery_rule,
)
from ..world.navigation import (
    configured_location_tags,
    nearest_location_with_tag,
    nearest_resource_node,
    nearest_safe_location,
    reachable_locations,
)
from ..world.properties import world_property_key
from ..world.rules import RuleCompiler
from ..world.schema import AttributeSchema
from ..actions.conditions import ConditionEngine
from .services import PlatformService


class WorldTickService(PlatformService):
    """One-minute effects, signals and activity progression."""

    def _apply_minute_effects(self, minute: int, *, agents: list[dict[str, Any]] | None = None) -> None:
        rules = self.store.repositories.metadata.get("rules", {}) or {}
        minute_event_state = dict(self.store.repositories.metadata.get("minute_effect_event_state", {}) or {})
        minute_event_state_changed = False
        world_properties = self.store.repositories.metadata.get("world_properties", {}) or {}
        static_context_values = configured_context_values(rules, include_resolvers=False)
        tick_context = {**static_context_values, **calendar_context(self.store, minute)}
        for regeneration in rules.get("resource_regeneration", []) or []:
            every = max(1, int(regeneration.get("every_minutes", 1)))
            if minute % every != 0:
                continue
            try:
                entity = self.store.repositories.entities.get_entity(str(regeneration["resource_id"]))
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
            self.store.repositories.entities.put_entity(entity["id"], entity["entity_type"], entity["name"], payload)
            self.store.repositories.entities.sync_world_property_from_resource(entity["id"], after)
            self._context.services["events"].append("resource.regenerated", None, minute, {"resource_id": entity["id"], "amount": after - before, "total": after})
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
            for agent in agents if agents is not None else self.store.repositories.entities.list_agents():
                agent_changed = False
                for effect in agent_effects:
                    state = dict(agent.get("initial_state") or {})
                    before_state = dict(state)
                    before_needs = dict(agent.get("needs") or {})
                    condition = str(effect.get("condition") or "").strip()
                    if condition == "action.none" and state.get("status") in BUSY_STATUSES:
                        continue
                    if condition and condition != "action.none" and not self.conditions.evaluate(
                        condition,
                        agent=agent,
                        target={},
                        context={
                            **tick_context,
                            "current_location": self._context.services["execution"].entity_for_context(state.get("location_id")),
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
                            static_context=tick_context,
                        ) or minute_event_state_changed
                    state = dict(agent.get("initial_state") or {})
                    vital_value = float(state.get(vital, 1) or 0) if vital else 1
                    if vital and vital_value <= vital_minimum and state.get("status") not in INACTIVE_STATUSES:
                        # An unconscious character is never progressed again, so the
                        # behavior in flight fails now (its Actions declare
                        # ``actor.incapacitated`` as a failure condition).
                        self._context.services["activity"].abort_active_behavior(agent, "incapacitated", minute)
                        state = dict(agent.get("initial_state") or {})
                        state["status"] = UNCONSCIOUS
                        state[INCAPACITATED_SINCE_KEY] = minute
                        conditions = list(state.get("conditions") or [])
                        if INCAPACITATED_CONDITION not in conditions:
                            conditions.append(INCAPACITATED_CONDITION)
                        state["conditions"] = conditions
                        agent["initial_state"] = state
                        self._context.services["events"].append("agent.incapacitated", agent["id"], minute, {"reason": effect.get("id", "minute_effect")})
                if agent_changed:
                    changed_agents.append(agent)
            self.store.repositories.entities.update_agents_bulk(changed_agents)

        for effect in due_effects:
            scope = effect.get("scope")
            if scope == "world":
                properties = dict(self.store.repositories.metadata.get("world_properties", {}) or {})
                condition = str(effect.get("condition") or "").strip()
                if condition and not self.conditions.evaluate(
                    condition,
                    agent={},
                    target={},
                    world=properties,
                    context=tick_context,
                ):
                    continue
                definitions = effect.get("effects")
                if not isinstance(definitions, list) or not definitions:
                    definitions = [effect.get("effect", {}) or {}]
                changes = [
                    change for definition in definitions if isinstance(definition, dict)
                    if (change := self._apply_world_effect(definition, minute, tick_context))
                ]
                event_type = str(effect.get("event_type") or "").strip()
                if event_type:
                    payload = dict(effect.get("event_data") or {})
                    if effect.get("event_summary"):
                        payload["summary"] = str(effect["event_summary"])
                    if len(changes) == 1 and changes[0].get("type") == "world_property_delta":
                        payload.update({
                            "property": changes[0]["property"],
                            "before": changes[0]["before"],
                            "after": changes[0]["after"],
                        })
                    elif changes:
                        payload["effects"] = changes
                    payload["rule_id"] = str(effect.get("id") or "")
                    self._context.services["events"].publish(event_type, None, minute, payload, wake="configured")
            elif scope == "relationship":
                definition = effect.get("effect", {}) or {}
                relation_delta = float(definition.get("relation_strength", definition.get("delta", 0)))
                for relation in self.store.repositories.entities.list_relationships():
                    data = {key: value for key, value in relation.items() if key not in {"from", "to", "type"}}
                    data["relation_strength"] = float(data.get("relation_strength", data.get("value", 0))) + relation_delta
                    self.store.repositories.entities.put_relationship(relation["from"], relation["to"], relation["type"], data)
        if minute_event_state_changed:
            # Threshold state belongs to the whole minute. Persisting the
            # growing map after every agent/effect made a 1,000-agent tick
            # encode and decode the same JSON thousands of times.
            self.store.repositories.metadata.set("minute_effect_event_state", minute_event_state)

    def _apply_world_effect(self, definition: dict[str, Any], minute: int, context: dict[str, Any]) -> dict[str, Any] | None:
        """Apply one declarative world-scope effect and return its audit delta."""

        effect_type = str(definition.get("type") or definition.get("op") or "")
        if effect_type == "world_property_delta":
            properties = dict(self.store.repositories.metadata.get("world_properties", {}) or {})
            key = definition.get("world_property") or definition.get("property") or definition.get("need")
            if not key:
                return None
            key = self.world_property_key(str(key))
            before = float(properties.get(str(key), 0) or 0)
            after = before + float(definition.get("delta", 0) or 0)
            if definition.get("minimum") is not None:
                after = max(float(definition["minimum"]), after)
            if definition.get("maximum") is not None:
                after = min(float(definition["maximum"]), after)
            if abs(after - before) > 1e-12:
                after = self.set_world_property(str(key), after)["after"]
            return {"type": effect_type, "property": str(key), "before": before, "after": after}
        if effect_type == "world_property_set":
            properties = dict(self.store.repositories.metadata.get("world_properties", {}) or {})
            key = definition.get("world_property") or definition.get("property") or definition.get("need")
            if not key:
                return None
            key = self.world_property_key(str(key))
            before = properties.get(str(key))
            value = resolve_binding(definition.get("value"), context)
            value = self.set_world_property(str(key), value)["after"]
            return {"type": effect_type, "property": str(key), "before": before, "after": value}
        if effect_type == "world_resource_delta":
            resource_ref = definition.get("resource_id") or definition.get("resource")
            resource_id = str(resource_ref or "")
            resources = self._dependency("resources")
            if not resource_id or resources is None:
                return None
            current = resources._world_resource(resource_id)
            if current is None:
                return None
            before = float(current.get("amount", 0) or 0)
            after = before + float(definition.get("delta", definition.get("value", 0)) or 0)
            if definition.get("minimum") is not None:
                after = max(float(definition["minimum"]), after)
            if definition.get("maximum") is not None:
                after = min(float(definition["maximum"]), after)
            caps = ((self.store.repositories.metadata.get("rules", {}) or {}).get("resource_caps", {}) or {})
            try:
                resource_entity = self.store.repositories.entities.get_entity(resource_id)
            except KeyError:
                resource_entity = {}
            cap = caps.get(resource_id) or caps.get(str(resource_entity.get("name") or "")) or {}
            if isinstance(cap, dict):
                if isinstance(cap.get("min"), (int, float)):
                    after = max(float(cap["min"]), after)
                if isinstance(cap.get("max"), (int, float)):
                    after = min(float(cap["max"]), after)
            delta = after - before
            if abs(delta) > 1e-12:
                resources._change_world_resource(resource_id, delta, actor_id=None)
            return {"type": effect_type, "resource_id": resource_id, "before": before, "after": after}
        if effect_type == "entity_state_set":
            entity_id = definition.get("entity_id") or definition.get("target_entity")
            state_key = definition.get("state") or definition.get("property")
            if not entity_id or not state_key:
                return None
            try:
                entity = self.store.repositories.entities.get_entity(str(entity_id))
            except KeyError:
                return None
            entity_state = dict(entity.get("initial_state") or {})
            before = entity_state.get(str(state_key))
            after = definition.get("value")
            entity_state[str(state_key)] = after
            entity["initial_state"] = entity_state
            self.store.repositories.entities.put_entity(
                entity["id"], entity["entity_type"], entity["name"],
                {key: value for key, value in entity.items() if key not in {"id", "entity_type", "name"}},
            )
            self._context.services["events"].append(
                "entity.state_changed", None, minute,
                {"entity_id": entity["id"], "state": str(state_key), "before": before, "after": after, "source": "world_minute_effect"},
            )
            return {"type": effect_type, "entity_id": str(entity_id), "state": str(state_key), "before": before, "after": after}
        return None

    def _recover_incapacitated(self, minute: int, agents: list[dict[str, Any]]) -> None:
        """Wake unconscious characters once the configured recovery period has elapsed.

        Without ``rules.incapacitation.recovery`` the state stays absorbing.  A
        character that became unconscious through a path which did not stamp
        the minute starts its recovery clock when first seen here.
        """

        rule = recovery_rule(self.store.repositories.metadata.get("rules", {}) or {})
        vital = self.attribute_schema.vital_attribute()
        if rule is None or not vital:
            return
        minimum = self.attribute_schema.minimum(vital)
        maximum = self.attribute_schema.maximum(vital)
        changed: list[dict[str, Any]] = []
        for agent in agents:
            if (agent.get("initial_state") or {}).get("status") != UNCONSCIOUS:
                continue
            state = dict(agent["initial_state"])
            since = state.get(INCAPACITATED_SINCE_KEY)
            if isinstance(since, bool) or not isinstance(since, (int, float)):
                state[INCAPACITATED_SINCE_KEY] = minute
                agent["initial_state"] = state
                changed.append(agent)
                continue
            elapsed = minute - int(since)
            if elapsed < rule.after_minutes:
                continue
            self._context.services["activity"].abort_active_behavior(agent, "incapacitated", minute)
            state = dict(agent.get("initial_state") or {})
            restored = rule.restored_vital(minimum, maximum, float(state.get(vital) or minimum))
            state[vital] = restored
            state["status"] = "idle"
            state["conditions"] = [item for item in (state.get("conditions") or []) if item != INCAPACITATED_CONDITION]
            state.pop(INCAPACITATED_SINCE_KEY, None)
            agent["initial_state"] = state
            changed.append(agent)
            self._context.services["events"].append(
                "agent.recovered", agent["id"], minute,
                {"after_minutes": elapsed, "attribute": vital, "value": restored},
            )
        if changed:
            self.store.repositories.entities.update_agents_bulk(changed)

    def _emit_resource_safety_signals(self, minute: int) -> None:
        """Publish configured stock-safety signals at a crossing or repeat interval.

        A one-time crossing is the default.  Worlds with a persistent shortage
        may declare ``repeat_every_minutes`` so the same unresolved shortage
        is reviewed again after the configured interval; the signal remains
        declarative and the consumer's own cooldown still applies.
        """

        rules = self.store.repositories.metadata.get("rules", {}) or {}
        configured = rules.get("resource_safety", []) or []
        if not isinstance(configured, list):
            return
        state = dict(self.store.repositories.metadata.get("resource_safety_signal_state", {}) or {})
        changed = False
        for raw in configured:
            if not isinstance(raw, dict) or not raw.get("resource_id"):
                continue
            resource_id = str(raw["resource_id"])
            try:
                entity = self.store.repositories.entities.get_entity(resource_id)
            except KeyError:
                continue
            quantity = self._context.services["execution"].entity_quantity(entity)
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
                self._context.services["events"].publish(event_type, None, minute, {
                    "resource_id": resource_id,
                    "resource_name": entity.get("name", resource_id),
                    "quantity": quantity,
                    "minimum_reserve": floor,
                    "summary": f"{entity.get('name', resource_id)} is at or below its configured reserve floor.",
                    "signal_kind": "crossing" if not previous else "repeat",
                }, wake="configured")
                state[resource_id] = {"active": active, "last_emitted_minute": int(minute)}
                changed = True
            elif active != previous:
                state[resource_id] = {"active": active, "last_emitted_minute": last_emitted}
                changed = True
        if changed:
            self.store.repositories.metadata.set("resource_safety_signal_state", state)

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
            self._context.services["execution"].entity_for_context((agent.get("initial_state") or {}).get("location_id"))
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
            event_type = str(item.get("event_type") or "").strip()
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
                self._context.services["events"].append(event_type, agent["id"], minute, payload)
            if previous != active or state_key not in event_state:
                event_state[state_key] = active
                changed_state = True
        return changed_state

    def world_property_key(self, ref: str) -> str:
        return world_property_key(self.store, ref)

    def set_world_property(self, key: str, value: Any) -> dict[str, Any]:
        """Update one world property and its shared-resource projection atomically.

        A resource may also be exposed as a world property, for example
        ``village_food``.  The resource ledger remains the world quantity, while
        the property is the prompt/condition projection.  Updating only one side
        makes triggers, Agent context and God proposals disagree.
        """

        properties = dict(self.store.repositories.metadata.get("world_properties", {}) or {})
        before = properties.get(str(key))
        labels = (self.store.repositories.metadata.get("rules", {}) or {}).get("world_property_labels", {})
        spec = labels.get(key)
        if isinstance(spec, dict) and spec.get("unit") == "percent" and type(value) in (float, int):
            if isinstance(spec.get("max"), (float, int)):
                value = min(value, spec["max"])
            if isinstance(spec.get("min"), (float, int)):
                value = max(value, spec["min"])
        properties[str(key)] = value
        self.store.repositories.metadata.set("world_properties", properties)
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
            entity = self.store.repositories.entities.get_entity(str(key))
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
        self.store.repositories.entities.put_entity(
            entity["id"],
            entity["entity_type"],
            entity["name"],
            {name: item for name, item in entity.items() if name not in {"id", "entity_type", "name"}},
        )
        return {"entity_id": entity["id"], "before": before, "after": value}

    def step(self, *, defer_director: bool = False) -> dict[str, Any]:
        """Commit exactly one world-minute tick."""

        completed: list[str] = []
        failed: list[str] = []
        with self.store.transaction():
            # Read the clock only after BEGIN IMMEDIATE has acquired the
            # single-writer lock. Reading it before the transaction lets two
            # concurrent HTTP / control requests both compute the same next
            # minute and apply minute effects twice.
            snapshot = self.store.world_snapshot(include_agent_revisions=False)
            # ``transaction()`` reserves the commit revision before yielding
            # to the tick body. Report the revision observed immediately
            # before this tick; events still carry the reserved new revision.
            snapshot["world_revision"] = max(0, int(snapshot["world_revision"]) - 1)
            start = int(snapshot["world_minute"])
            minute = start + 1
            agents = self.store.repositories.entities.list_agents()
            agent_ids = {str(agent["id"]) for agent in agents}
            self._recover_incapacitated(minute, agents)
            self._apply_minute_effects(minute, agents=agents)
            for agent in agents:
                before = agent.get("initial_state", {}).get("active_bt_instance")
                if not before:
                    continue
                self._context.services["activity"].progress_agent(agent, minute)
                after = self.store.repositories.entities.get_agent(agent["id"]).get("initial_state", {})
                result = after.get("last_platform_result") or {}
                if result.get("status") == "completed" and result.get("behavior_ref"):
                    completed.append(agent["id"])
                if result.get("status") == "failed" and result.get("behavior_ref"):
                    failed.append(agent["id"])
            self.store.repositories.metadata.set("world_minute", minute)
            self._context.services["events"].append("world_minute.advanced", None, minute, {"minute": minute})
            self._context.services["evolution"].emit_world_rule_signals(minute, agent_ids=agent_ids)
            self._emit_resource_safety_signals(minute)
            self.proposal_evaluator.evaluate_due(minute)
            self._context.services["evolution"].emit_evolution_triggers(minute, agent_ids=agent_ids)
            self._context.services["stagnation"].emit_stagnation_signal(minute, agents=agents)
            if not defer_director:
                self._context.services["director"].tick(minute)
            self._context.services["evolution"].emit_due_evolution_tasks(minute)
        return {
            "status": "advanced",
            "from_minute": start,
            "to_minute": minute,
            "snapshot_revision": int(snapshot["world_revision"]),
            "world_revision": int(self.store.repositories.metadata.get("world_revision", 0) or 0),
            "completed_agents": sorted(set(completed)),
            "failed_agents": sorted(set(failed)),
        }
