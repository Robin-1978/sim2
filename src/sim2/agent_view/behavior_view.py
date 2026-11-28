"""Compiling the behaviors a character may choose: gates, step simulation, target guidance and the offered entry."""

from __future__ import annotations

import copy
import re
from functools import partial
from typing import Any

from ..behavior.estimator import BehaviorDurationEstimator
from ..behavior.plan import compile_behavior_plan
from ..behavior.recovery import failure_block_recovered
from ..actions.effects import EffectPipeline
from ..world.action_tools import best_time_tool
from ..world.navigation import (
    configured_location_tags,
    movement_speed,
    nearest_location_with_tag,
    nearest_resource_node,
    nearest_safe_location,
    reachable_locations,
    resource_is_at_location,
)
from ..world.authorization import authorization_for_behavior
from ..actions.catalogs import catalog_entries, entry_cost_available, entry_location, entry_resource_available
from ..world.bindings import iter_behavior_actions, resolve_behavior_effect
from ..world.contested import resource_take_effect
from ..world.context import action_condition_context, calendar_context, configured_context_values
from ..world.definition_compiler import bind_legacy_inventory_effect
from ..world.time import day_phase_duration
from .action_text import action_view, label, predicate_text
from .decision_frame import DecisionFrame, Guidance, Simulation


class BehaviorViewMixin:
    """Methods of ``AgentViewCompiler`` for this concern; they use the attributes its constructor creates."""

    def _decision_frame(self, agent_id: str, agent: dict[str, Any], rules: dict[str, Any]) -> DecisionFrame:
        configured_context = configured_context_values(rules, agent)
        bt_defs = {x["id"]: x for x in self._definitions("bt_def")}
        action_defs = {x["id"]: x for x in self._definitions("action_def")}
        recipe_defs = {x["id"]: x for x in self._definitions("recipe_def")}
        estimator = BehaviorDurationEstimator(
            action_defs,
            bt_defs,
            recipe_defs,
            self._route_distance,
            capability_modifiers=(self.store.repositories.metadata.get("rules", {}) or {}).get("capability_modifiers", {}),
            travel_speed=lambda values: movement_speed(self.store, agent, values),
            phase_duration=partial(day_phase_duration, self.store),
        )
        fallback_ids = (self.store.repositories.metadata.get("rules", {}) or {}).get("fallback_behavior_ids", [])
        catalog_ids = list(dict.fromkeys(
            list(agent.get("initial_bt_ids", []))
            + list(agent.get("unlocked_bt_ids", []))
            + list(fallback_ids)
        ))
        state = agent.get("initial_state", {})
        location_id = state.get("location_id")
        safe_location_id = nearest_safe_location(self.store, location_id)
        resource_node = nearest_resource_node(self.store, location_id)
        health_location_id = nearest_location_with_tag(self.store, location_id, configured_location_tags(self.store, "health_service"))
        known_route_destinations = reachable_locations(self.store, location_id)
        mapped_areas = self.memory_repo.explored_areas(agent_id)
        researched_sources = self.memory_repo.researched_sources(agent_id)
        known_fact_keys = [item.get("fact_key") for item in self.memory_repo.list_knowledge_facts(agent_id, limit=1000) if item.get("fact_key")]
        configured_research_sources: set[str] = {
            str(value)
            for bt_id in catalog_ids
            for value in [((bt_defs.get(str(bt_id), {}).get("parameter_defaults", {}) or {}).get("source_id"))]
            if value
        }
        configured_research_sources = {
            str(self.targets.canonical_id(value) or value)
            for value in configured_research_sources
        }
        configured_research_sources.update(
            str(self.targets.canonical_id(value) or value)
            for value in (agent.get("research_targets", []) or [])
            if value
        )
        research_sources = self._pending_research_sources(agent, known_fact_keys, location_id, configured_research_sources)
        current_location = {}
        if location_id:
            try:
                current_location = self.entities_repo.get_entity(location_id)
            except KeyError:
                pass
        participants = self.targets.visible_agents(agent)
        # A single visible participant is safe to infer. With multiple
        # participants, one candidate is used only to validate the BT; the
        # Agent must submit an explicit visible target.
        default_target = participants[0] if len(participants) == 1 else None
        validation_target = default_target or (participants[0] if participants else None)
        return DecisionFrame(
            agent_id=agent_id, agent=agent, rules=rules, configured_context=configured_context,
            bt_defs=bt_defs, action_defs=action_defs, recipe_defs=recipe_defs, estimator=estimator,
            catalog_ids=catalog_ids, state=state, location_id=location_id, safe_location_id=safe_location_id,
            resource_node=resource_node, health_location_id=health_location_id,
            known_route_destinations=known_route_destinations, mapped_areas=mapped_areas,
            researched_sources=researched_sources, known_fact_keys=known_fact_keys,
            research_sources=research_sources, current_location=current_location,
            participants=participants, validation_target=validation_target,
        )

    def _compile_behavior(self, frame: DecisionFrame, bt_id: Any) -> dict[str, Any] | None:
        """Return the executable view of one catalog behavior, or record why it is not."""

        bt = frame.bt_defs.get(bt_id)
        if not bt:
            frame.diagnostics.append({"behavior_id": bt_id, "failed": ["behavior definition is not registered"]})
            return None
        gates = self._behavior_gates(frame, bt_id, bt)
        if gates is None:
            return None
        behavior_context, cooldown_minutes = gates
        simulation = self._simulate_behavior(frame, bt_id, behavior_context)
        if simulation is None:
            return None
        guidance = self._target_guidance(frame, bt)
        entry = self._behavior_entry(frame, bt_id, bt, simulation, guidance, cooldown_minutes)
        decision = authorization_for_behavior(
            self.store,
            frame.agent,
            bt,
            {},
            rule_compiler=self.rule_compiler,
            decision=str(bt.get("authorization_decision") or "") or None,
        )
        resource_key, resource_options = "", []
        if decision.get("gated"):
            resource_key, resource_options = self._annotate_parameter_authorization(
                frame, bt, guidance.parameter_options
            )
        if resource_options:
            available_options = [item for item in resource_options if item.get("available", True)]
            if available_options:
                # The capability is usable, but only through the bindings that
                # are not blocked.  The prompt marks each option and the
                # admission check still validates the submitted one.
                decision = {**decision, "available": True, "reasons": []}
            else:
                decision = authorization_for_behavior(
                    self.store,
                    frame.agent,
                    bt,
                    {resource_key: resource_options[0].get("value")},
                    rule_compiler=self.rule_compiler,
                    decision=str(bt.get("authorization_decision") or "") or None,
                )
        entry["eligibility"] = {
            "available": bool(decision.get("available")),
            "gated": bool(decision.get("gated")),
            "reasons": list(decision.get("reasons") or []),
            "rule_id": decision.get("rule_id"),
            "mode": decision.get("mode"),
            "authorization": decision.get("authorization"),
        }
        risk = self._contested_risk(frame, bt)
        if risk:
            entry["risk"] = risk
        if decision.get("gated") and not decision.get("available"):
            reasons = list(decision.get("reasons") or [])
            entry["attemptable"] = bool(reasons) and all(bool(item.get("attemptable")) for item in reasons)
            entry["blocked_reason"] = reasons[0].get("message") if reasons else "Authorization is not available."
            entry["blocked_code"] = reasons[0].get("type") if reasons else None
        return entry

    def _behavior_gates(self, frame: DecisionFrame, bt_id: Any, bt: dict[str, Any]) -> tuple[dict[str, Any], int] | None:
        """Blocked, entry-condition, rule-denial and cooldown checks; None records a diagnostic."""

        agent_id, agent, state = frame.agent_id, frame.agent, frame.state
        diagnostics = frame.diagnostics
        configured_context = frame.configured_context
        research_sources = frame.research_sources
        current_location, location_id = frame.current_location, frame.location_id
        safe_location_id, resource_node, health_location_id = frame.safe_location_id, frame.resource_node, frame.health_location_id
        known_route_destinations, mapped_areas, researched_sources = frame.known_route_destinations, frame.mapped_areas, frame.researched_sources
        known_fact_keys = frame.known_fact_keys
        participants, validation_target = frame.participants, frame.validation_target
        blocked = (state.get("blocked_behaviors") or {}).get(str(bt_id)) if isinstance(state, dict) else None
        if isinstance(blocked, dict) and not failure_block_recovered(self.store, blocked):
            diagnostics.append({
                "behavior_id": bt_id,
                "failed": [f"blocked after the last failed result ({blocked.get('result', 'failed')}); wait for a configured recovery event"],
            })
            return None
        behavior_context = dict(configured_context)
        # Behavior defaults are part of the executable contract, not only a
        # submission-time convenience.  The decision view must evaluate the
        # same resource binding before it can offer the behavior.
        for key, value in (bt.get("parameter_defaults") or {}).items():
            behavior_context.setdefault(str(key), value)
        uses_source_parameter = any(
            value == "context.source_id"
            for node in self._iter_behavior_actions(bt_id)
            for value in (node.get("parameters") or {}).values()
        )
        if uses_source_parameter and research_sources:
            selected_source = research_sources[0]
            behavior_context.update({
                "source_id": selected_source["id"],
                "source_location_id": selected_source.get("location_id"),
            })
        stock = self._representative_stock(frame, bt)
        if stock:
            behavior_context["resource_id"] = stock[0]
            bindings = bt.get("parameter_bindings") if isinstance(bt.get("parameter_bindings"), dict) else {}
            agent_parameters = bt.get("agent_parameters") if isinstance(bt.get("agent_parameters"), dict) else {}
            if (
                str(bindings.get("resource_location_id") or "") == "resource_location"
                or "resource_location_id" in agent_parameters
            ):
                behavior_context["resource_location_id"] = stock[1]
        recipe = self._representative_recipe(frame, bt)
        if recipe:
            behavior_context["recipe_id"] = recipe
        condition_context = {
            "current_location": current_location,
            "location_id": location_id,
            "safe_location_id": safe_location_id,
            "resource_location_id": (resource_node or {}).get("location_id"),
            "resource_tag": (resource_node or {}).get("resource_tag"),
            "health_location_id": health_location_id,
            "known_route_destinations": known_route_destinations,
            "mapped_areas": mapped_areas,
            "researched_sources": researched_sources,
            "known_fact_keys": known_fact_keys,
            "inventory": agent.get("inventory", {}) or {},
            "recent_event_types": self._recent_event_types(),
            **calendar_context(self.store),
            "target": {"id": validation_target.get("id"), "available": True} if validation_target else {"available": False},
        }
        condition_context.update(behavior_context)
        condition_context.update(self._interaction_context(agent, participants))
        if behavior_context.get("resource_id"):
            resource_id = str(behavior_context["resource_id"])
            condition_context["resource.here"] = resource_is_at_location(
                self.store,
                resource_id,
                location_id,
            )
            try:
                resource = self.entities_repo.get_entity(resource_id)
            except KeyError:
                resource = {}
            resource_state = resource.get("initial_state") if isinstance(resource.get("initial_state"), dict) else {}
            amount = float(resource_state.get("quantity", resource_state.get("amount", 0)) or 0)
            condition_context["resource.amount"] = amount
            condition_context["resource.available"] = amount > 0
        failed_conditions = [
            str(predicate)
            for predicate in bt.get("entry_conditions", []) or []
            if not self.conditions.evaluate(
                str(predicate), agent=agent, target=condition_context["target"],
                context=condition_context,
                world=self.store.repositories.metadata.get("world_properties", {}) or {},
            )
        ]
        if failed_conditions:
            diagnostics.append({"behavior_id": bt_id, "failed": failed_conditions})
            return None
        denial = self.rule_compiler.behavior_denial(
            behavior=bt,
            agent=agent,
            context=condition_context,
            world=self.store.repositories.metadata.get("world_properties", {}) or {},
        )
        if denial:
            diagnostics.append({"behavior_id": bt_id, "failed": [str(denial.get("message") or "denied by an active rule")]})
            return None
        cooldown_minutes = max(0, int(bt.get("cooldown_minutes", 0) or 0))
        if cooldown_minutes:
            last_minute = self.activity_repo.last_behavior_minute(agent_id, bt_id)
            current_minute = int(self.store.repositories.metadata.get("world_minute", 0))
            if last_minute is not None and current_minute - last_minute < cooldown_minutes:
                diagnostics.append({"behavior_id": bt_id, "failed": [f"cooldown until world minute {last_minute + cooldown_minutes}"]})
                return None
        return behavior_context, cooldown_minutes

    def _representative_recipe(self, frame: DecisionFrame, bt: dict[str, Any]) -> str:
        """Pick one currently craftable recipe for a generic recipe selection."""

        if "recipe_id" not in (bt.get("agent_parameters") or {}):
            return ""
        location_tags = set(frame.current_location.get("tags") or [])
        inventory = frame.agent.get("inventory", {}) or {}
        for recipe in self.entities_repo.get_definitions("recipe_def"):
            station_tags = set(recipe.get("station_tags") or [])
            if station_tags and not station_tags & location_tags:
                continue
            if any(
                str(item.get("scope") or "agent") == "agent"
                and float(inventory.get(str(item.get("item")), 0) or 0) < float(item.get("quantity", 0) or 0)
                for item in (recipe.get("inputs") or [])
                if isinstance(item, dict)
            ):
                continue
            return str(recipe.get("id") or "")
        return ""

    def _representative_stock(self, frame: DecisionFrame, bt: dict[str, Any]) -> tuple[str, str] | None:
        """Pick the stock a parameterized withdrawal/take behavior will use.

        The Agent chooses the exact stock at submission time.  For the decision
        view we validate one representative binding, preferring a stock the
        actor can already reach, so a generic Travel-bound behavior is shown as
        executable instead of being rejected for a remote default.
        """

        for effect_type in ("resource_withdrawal", "resource_take"):
            effect = resolve_behavior_effect(bt, effect_type, frame.action_defs, frame.bt_defs)
            if not effect:
                continue
            reference = effect.get("resource_id") or effect.get("resource")
            if reference and not str(reference).startswith("context."):
                resource_id = str(self.targets.canonical_id(str(reference)) or reference)
                try:
                    resource = self.entities_repo.get_entity(resource_id)
                except KeyError:
                    return None
                state = resource.get("initial_state") if isinstance(resource.get("initial_state"), dict) else {}
                return resource_id, str(state.get("location_id") or resource.get("location_id") or "")
            resource_keys = {"resource_id", "resource", "target_resource"}
            if not (resource_keys & set((bt.get("agent_parameters") or {}).keys())):
                continue
            rules = self.store.repositories.metadata.get("rules", {}) or {}
            entity_types = [str(item) for item in (rules.get("resource_entity_types", ["resource", "material"]) or [])]
            candidates: list[tuple[int, str, str]] = []
            for entity_type in entity_types:
                for row in self.entities_repo.entities_of_type(entity_type):
                    try:
                        entity = self.entities_repo.get_entity(str(row["id"]))
                    except KeyError:
                        continue
                    state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
                    location_id = str(state.get("location_id") or entity.get("location_id") or "")
                    rank = 0 if location_id == frame.location_id else 1 if location_id in frame.known_route_destinations else 2
                    candidates.append((rank, str(entity.get("id") or row["id"]), location_id))
            if candidates:
                candidates.sort(key=lambda item: (item[0], item[1]))
                _, resource_id, location_id = candidates[0]
                return resource_id, location_id
        return None

    def _simulate_behavior(self, frame: DecisionFrame, bt_id: Any, behavior_context: dict[str, Any]) -> Simulation | None:
        """Replay every step against a copy of the agent so later steps see earlier effects."""

        sim = Simulation(agent=copy.deepcopy(frame.agent), location=dict(frame.current_location))
        simulated_state = sim.agent.setdefault("initial_state", {})
        simulated_state.pop("last_action_result", None)
        frame.estimator.capabilities = sim.agent.get("capabilities", {}) or {}
        frame.estimator.inventory = sim.agent.get("inventory", {}) or {}
        try:
            plan = compile_behavior_plan(bt_id, frame.bt_defs, frame.action_defs)
        except ValueError as error:
            frame.diagnostics.append({"behavior_id": bt_id, "failed": [str(error)]})
            return None

        index = 0
        condition_checked = False
        while index < len(plan):
            node = plan[index]
            kind = node.get("type")
            if kind == "jump":
                index = node["to"]
                continue
            if kind == "condition" and sim.elapsed_minutes:
                # A future condition may depend on effects or rules that the
                # real world will settle after this available prefix.
                break
            if kind == "condition":
                expression = str(node.get("expression") or "").strip()
                if not self._preview_behavior_condition(frame, sim, behavior_context, expression):
                    sim.unavailable = True
                    sim.unavailable_action = "condition"
                    sim.unavailable_reason = expression
                else:
                    condition_checked = True
            elif kind == "wait":
                minutes = node["definition_snapshot"]["duration"]["minutes"]
                sim.steps.append(label("wait"))
                sim.elapsed_minutes += minutes
            else:
                self._simulate_action(frame, sim, node, behavior_context)
            if sim.unavailable:
                if "on_failure" not in node:
                    break
                sim.unavailable = False
                sim.agent.get("initial_state", {}).pop("last_action_result", None)
                index = node["on_failure"]
            else:
                index += 1
        if sim.unavailable:
            reason = sim.unavailable_reason or ""
            if not reason.startswith("cooldown until"):
                reason = f"Action {sim.unavailable_action} cannot start: {reason}"
            frame.diagnostics.append({"behavior_id": bt_id, "failed": [reason]})
            return None
        if not sim.steps:
            if not condition_checked:
                frame.diagnostics.append({"behavior_id": bt_id, "failed": ["behavior has no executable Action steps"]})
                return None
            sim.steps.append("Check condition")
        return sim

    def _preview_behavior_condition(
        self, frame: DecisionFrame, sim: Simulation, parameters: dict[str, Any], expression: str,
    ) -> bool:
        target = frame.validation_target
        context = action_condition_context(
            self.store, sim.agent, parameters, target, canonical_id=self.targets.canonical_id,
            recent_event_types=self._recent_event_types(), location=sim.location,
        )
        context.update(self._interaction_context(sim.agent, frame.participants))
        return bool(expression) and self.conditions.evaluate(
            expression, agent=sim.agent, target={**parameters, **(target or {})},
            context=context, world=self.store.repositories.metadata.get("world_properties", {}) or {},
        )

    def _simulate_action(self, frame: DecisionFrame, sim: Simulation, node: dict[str, Any], behavior_context: dict[str, Any]) -> bool:
        """Apply one Action step to the simulation; False when that Action cannot start."""

        action_defs, recipe_defs, estimator = frame.action_defs, frame.recipe_defs, frame.estimator
        known_fact_keys = frame.known_fact_keys
        safe_location_id, resource_node, health_location_id = frame.safe_location_id, frame.resource_node, frame.health_location_id
        validation_target = frame.validation_target
        simulated_agent, simulated_location = sim.agent, sim.location
        steps, costs, impacts = sim.steps, sim.costs, sim.impacts
        action_successes, action_failures = sim.successes, sim.failures
        action = action_defs.get(node.get("action_id"))
        if not action:
            return True
        simulated_state = simulated_agent.setdefault("initial_state", {})
        parameters = self._view_action_parameters(node.get("parameters") or {}, simulated_state, behavior_context, safe_location_id=safe_location_id, resource_node=resource_node, health_location_id=health_location_id, default_target_id=(validation_target or {}).get("id"))
        # A configured destination Action is complete when the actor
        # is already there. It contributes no visible step to the
        # surrounding BT, while the remaining steps stay executable.
        if (
            action.get("requires_destination")
            and action.get("completion", {}).get("if_already_at_destination")
            and parameters.get("location_id") == simulated_state.get("location_id")
        ):
            simulated_state["last_action_result"] = (action.get("success") or {}).get("result", "completed")
            return True
        action_minutes = estimator._action_minutes(action, parameters, simulated_state.get("location_id"), elapsed_minutes=sim.elapsed_minutes)
        reservations: list[dict[str, Any]] = []
        unavailable_reason = self._action_unavailable_reason(
            simulated_agent, action, parameters, simulated_location, action_minutes, reservations=reservations,
        )
        if unavailable_reason is not None:
            sim.unavailable = True
            sim.unavailable_action = str(action.get("id") or "unknown action")
            sim.unavailable_reason = unavailable_reason
            return False
        recipe_action = (
            action.get("duration", {}).get("mode") == "recipe_duration"
            or "recipe_inputs" in (action.get("costs", {}).get("reserve", {}) or {})
        )
        recipe = recipe_defs.get(str(parameters.get("recipe_id"))) if recipe_action else None
        action_for_view = action
        progress_definition = action.get("progress") if isinstance(action.get("progress"), dict) else {}
        if progress_definition.get("key") and "{source_id}" in str(progress_definition.get("key")) and parameters.get("source_id"):
            try:
                research_source = self.entities_repo.get_entity(str(parameters["source_id"]))
            except KeyError:
                research_source = {}
            source_state = research_source.get("initial_state") if isinstance(research_source.get("initial_state"), dict) else {}
            discoveries = self.memory_repo.research_discoveries(str(parameters["source_id"]))
            known = set(known_fact_keys)
            current_stage = next((item for item in discoveries if str(item.get("id")) not in known and str(item.get("status") or "active").casefold() not in {"obsolete", "disabled"}), None)
            required = (current_stage or {}).get("required")
            if required is None:
                required = research_source.get("research_required", source_state.get("research_required"))
            if required is None:
                required = (self.store.repositories.metadata.get("rules", {}) or {}).get("research_default_required")
            if required is not None:
                action_for_view = copy.deepcopy(action)
                action_for_view.setdefault("progress", {})["required"] = required
        view = action_view(action_for_view, recipe, parameters)
        tool = best_time_tool(action, parameters, estimator.inventory)
        if tool:
            base_minutes = estimator._action_minutes(
                action, parameters, simulated_state.get("location_id"),
                elapsed_minutes=sim.elapsed_minutes, use_tools=False,
            )
            if base_minutes > action_minutes:
                impacts.insert(0, f"Tool {tool[0]} saves {base_minutes - action_minutes} minutes")
        steps.append(view["name"])
        costs.extend(view["cost"])
        impacts.extend(view["impact"][:3])
        action_successes.extend(view["success"])
        action_failures.extend(view["failure"])
        schema = self._attribute_schema()
        for cost_key, rate in (action.get("costs", {}).get("per_minute", {}) or {}).items():
            if cost_key == "time" or not isinstance(rate, (int, float)) or rate <= 0:
                continue
            target_state = simulated_state if cost_key in simulated_state else simulated_agent.get("needs", {})
            if cost_key not in target_state:
                continue
            current = float(target_state.get(cost_key, schema.default(str(cost_key), 0)) or 0)
            delta = current + float(rate) * action_minutes if schema.cost_mode(str(cost_key)) == "increase" else current - float(rate) * action_minutes
            target_state[cost_key] = schema.clamp(str(cost_key), delta)
        inventory = simulated_agent.setdefault("inventory", {})
        for reservation in reservations:
            if reservation["scope"] == "agent":
                key = reservation["key"]
                inventory[key] -= reservation["amount"]
        # Only replay effects on this copy of the actor. Shared stocks and
        # other participants are checked again when the Platform executes it.
        pipeline = EffectPipeline(resolve_location=self.targets.canonical_id)
        for effect in action.get("effects") or []:
            if not isinstance(effect, dict):
                continue
            effect_type = effect.get("type") or effect.get("op")
            if effect_type not in {
                "inventory_add", "inventory_remove", "inventory.consume_item", "location_set",
            }:
                continue
            if effect.get("target") == "target" and validation_target and validation_target.get("id") != simulated_agent.get("id"):
                continue
            effect = bind_legacy_inventory_effect(effect, action.get("parameters") or {}, parameters)
            resolved = {
                key: parameters.get(value.removeprefix("context."), value)
                if isinstance(value, str) and value.startswith("context.") else value
                for key, value in effect.items()
            }
            if effect_type != "location_set":
                item = resolved.get("item")
                amount = resolved.get("quantity", resolved.get("amount", 1))
                if not isinstance(item, str) or not item or item.startswith("context.") or not isinstance(amount, (int, float)):
                    continue
            pipeline.apply(simulated_agent, resolved, target=parameters)
        estimator.inventory = simulated_agent.get("inventory") or {}
        location_id = (simulated_agent.get("initial_state") or {}).get("location_id")
        if location_id and location_id != simulated_location.get("id"):
            try:
                simulated_location = self.entities_repo.get_entity(str(location_id))
            except KeyError:
                simulated_location = {}
        sim.location = simulated_location
        sim.elapsed_minutes += action_minutes
        return True

    def _target_guidance(self, frame: DecisionFrame, bt: dict[str, Any]) -> Guidance:
        bt_defs, participants, research_sources = frame.bt_defs, frame.participants, frame.research_sources
        safe_location_id, resource_node, health_location_id = frame.safe_location_id, frame.resource_node, frame.health_location_id
        target_text = "Platform resolves permitted targets from the current view."
        target_keys = self._target_parameter_keys(bt, bt_defs)
        target_required = bool(target_keys) and len(participants) > 1
        target_options = [{"id": str(item.get("id", "")), "name": str(item.get("name", item.get("id", "participant")))} for item in participants[:8]]
        parameter_options: dict[str, list[dict[str, Any]]] = {}
        source_parameter = (bt.get("agent_parameters", {}) or {}).get("source_id")
        if source_parameter is not None and research_sources:
            parameter_options["source_id"] = [
                {
                    "name": str(item.get("name") or item.get("id")),
                    "location": str(item.get("location_name") or item.get("location_id") or "configured source"),
                    "remaining_stages": int(item.get("remaining_stages", 0) or 0),
                    "question": next(
                        (
                            str(discovery.get("fact") or discovery.get("question") or "")
                            for discovery in (item.get("discoveries") or [])
                            if isinstance(discovery, dict) and (discovery.get("fact") or discovery.get("question"))
                        ),
                        "",
                    ),
                }
                for item in research_sources[:12]
            ]
            target_text = "Choose one configured research source: " + "; ".join(
                f"{item['name']} ({item['remaining_stages']} stage(s) remaining at {item['location']})"
                + (f" — {item['question'][:120]}" if item.get("question") else "")
                for item in parameter_options["source_id"]
            )
        params = bt.get("agent_parameters") or {}
        entry_keys = {"entry_id", "entry"}
        if entry_keys & set(params):
            catalog = str(bt.get("catalog") or "").strip()
            entries = catalog_entries(self.store, catalog)
            location_id = str(frame.location_id or "")
            tier = str(bt.get("tier") or "").strip()
            options: list[dict[str, Any]] = []
            for entry in entries:
                if entry_location(entry) and entry_location(entry) != location_id:
                    continue
                if tier and str(entry.get("tier") or "").strip() != tier:
                    continue
                condition_context = {
                    "current_location": frame.current_location,
                    "location_id": location_id,
                    "inventory": frame.agent.get("inventory", {}) or {},
                }
                if any(
                    not self.conditions.evaluate(
                        str(predicate), agent=frame.agent, target={}, context=condition_context,
                        world=self.store.repositories.metadata.get("world_properties", {}) or {},
                    )
                    for predicate in (entry.get("requires") or [])
                ):
                    continue
                if not entry_cost_available(self.store, entry, frame.agent, entity_quantity=self._entity_quantity):
                    continue
                if not entry_resource_available(self.store, entry, entity_quantity=self._entity_quantity):
                    continue
                options.append({
                    "name": str(entry.get("label") or entry.get("id")),
                    "value": str(entry.get("id") or ""),
                    "location": entry_location(entry),
                })
            if options:
                for key in entry_keys & set(params):
                    parameter_options[key] = options[:24]
                target_text = "Choose one visible action: " + "; ".join(item["name"] for item in options[:8])
            else:
                target_text = "No configured action is currently available here."
        recipe_keys = {"recipe_id"}
        if recipe_keys & set(params):
            recipe_options = []
            location_tags = set(frame.current_location.get("tags") or [])
            inventory = frame.agent.get("inventory", {}) or {}
            item_labels = (self.store.repositories.metadata.get("rules", {}) or {}).get("item_labels") or {}

            def quantities(items: list[dict[str, Any]]) -> str:
                return "、".join(
                    f"{item_labels.get(record['item'], record['item'])}×{record.get('quantity', 1)}"
                    for record in items if isinstance(record, dict) and record.get("item")
                )
            for recipe in self.entities_repo.get_definitions("recipe_def"):
                station_tags = set(recipe.get("station_tags") or [])
                if station_tags and not station_tags & location_tags:
                    continue
                inputs = recipe.get("inputs") or []
                if any(
                    str(item.get("scope") or "agent") == "agent"
                    and float(inventory.get(str(item.get("item")), 0) or 0) < float(item.get("quantity", 0) or 0)
                    for item in inputs
                    if isinstance(item, dict)
                ):
                    continue
                label = str(recipe.get("name") or recipe.get("id"))
                costs = quantities(inputs)
                outputs = quantities(recipe.get("outputs") or [])
                trade = f"{costs} → {outputs}" if costs else outputs
                recipe_options.append({
                    "name": f"{label}（{trade}，{recipe.get('duration_minutes', 0)} 分钟）" if trade else label,
                    "value": str(recipe.get("id") or ""),
                })
            if recipe_options:
                for key in recipe_keys & set(params):
                    parameter_options[key] = recipe_options[:24]
                target_text = "Choose one visible recipe: " + "; ".join(item["name"] for item in recipe_options[:8])
            else:
                target_text = "No recipe is currently craftable at this station."
        resource_keys = {"resource_id", "resource", "target_resource", "resource_tag"}
        if resource_keys & set(params):
            rules = self.store.repositories.metadata.get("rules", {}) or {}
            resource_types = [str(item) for item in (rules.get("resource_entity_types", ["resource", "material"]) or [])]
            resources = []
            for entity_type in resource_types:
                for row in self.entities_repo.entities_of_type(entity_type):
                    try:
                        entity = self.entities_repo.get_entity(str(row["id"]))
                    except KeyError:
                        continue
                    state = entity.get("initial_state") or {}
                    quantity = entity.get("amount", state.get("quantity", state.get("amount")))
                    location = state.get("location_id") or entity.get("location_id")
                    resources.append({
                        "name": str(entity.get("name") or entity.get("id")),
                        "quantity": quantity if isinstance(quantity, (int, float)) else 0,
                        "location": self._entity_name(location) if location else "",
                    })
            if resources:
                resources.sort(key=lambda item: (-float(item["quantity"] or 0), item["name"]))
                for key in resource_keys & set(params):
                    parameter_options[key] = resources[:12]
                target_text = "Choose a visible resource: " + "; ".join(
                    f"{item['name']} ({item['quantity']:g}{' at ' + item['location'] if item['location'] else ''})"
                    for item in resources[:6]
                )
                if str((bt.get("parameter_bindings") or {}).get("resource_location_id") or "") == "resource_location":
                    target_text += ". Platform travels to the selected stock's location before the operation."
        location_keys = {"location_id", "location", "destination", "destination_id"}
        if location_keys & set(params):
            locations = []
            for location_id in frame.known_route_destinations or []:
                # A destination choice must actually move the actor.  Offering
                # the current location turns travel into a permanent no-op
                # loop for background controllers and a pointless dialog
                # option for the player.
                if str(location_id) == str(frame.location_id or ""):
                    continue
                name = self._entity_name(location_id)
                if name:
                    locations.append({"name": name, "value": str(location_id)})
            if locations:
                for key in location_keys & set(params):
                    parameter_options[key] = locations[:8]
                target_text = "Choose a reachable destination: " + ", ".join(item["name"] for item in locations[:6])
        item_keys = {"item_id", "item", "consumable"}
        if item_keys & set(params):
            items = []
            for item_id, quantity in (frame.agent.get("inventory") or {}).items():
                items.append({"name": self._entity_name(item_id), "quantity": quantity})
            if items:
                for key in item_keys & set(params):
                    parameter_options[key] = items[:8]
                target_text = "Choose a carried item: " + ", ".join(
                    f"{item['name']} ({item['quantity']})" for item in items[:6]
                )
        bound_resource_text = ""
        if not (resource_keys & set(params)):
            for effect_type, verb_text in (
                ("resource_transfer", "Transfer into"),
                ("resource_withdrawal", "Withdraw from"),
                ("resource_take", "Take from"),
            ):
                effect = resolve_behavior_effect(bt, effect_type, frame.action_defs, frame.bt_defs)
                if not effect:
                    continue
                reference = effect.get("resource_id") or effect.get("resource")
                if not reference or str(reference).startswith("context."):
                    continue
                resource_id = str(self.targets.canonical_id(str(reference)) or reference)
                try:
                    resource = self.entities_repo.get_entity(resource_id)
                except KeyError:
                    continue
                state = resource.get("initial_state") if isinstance(resource.get("initial_state"), dict) else {}
                location_id = state.get("location_id") or resource.get("location_id")
                location_name = self._entity_name(location_id) if location_id else ""
                item_name = ""
                if effect_type == "resource_transfer":
                    item_parameter = str(effect.get("source_item_parameter") or "")
                    for _, binding in iter_behavior_actions(bt, frame.action_defs, frame.bt_defs):
                        value = binding.get(item_parameter) if item_parameter else None
                        if isinstance(value, str) and not value.startswith("context."):
                            item_name = self._entity_name(value)
                            break
                target_text = f"{verb_text} {resource.get('name') or resource_id}"
                if location_name:
                    target_text += f" at {location_name}"
                target_text += "."
                if effect_type == "resource_transfer":
                    target_text += (
                        f" Platform reserves the carried {item_name} from your inventory."
                        if item_name else " Platform reserves the carried input from your inventory."
                    )
                elif effect_type == "resource_take":
                    target_text += " Observers may see the take."
                bound_resource_text = target_text
                break
        bindings = bt.get("parameter_bindings", {}) or {}
        binding_values = {
            "nearest_safe_location": safe_location_id,
            "nearest_health_service": health_location_id,
            "nearest_resource_node": (resource_node or {}).get("location_id"),
            "nearest_resource_tag": (resource_node or {}).get("resource_tag"),
        }
        bound_value = next((binding_values.get(str(resolver)) for resolver in bindings.values() if binding_values.get(str(resolver))), None)
        if bound_value and any(str(resolver) == "nearest_safe_location" for resolver in bindings.values()):
            try:
                target_text = f"Configured safe location: {self.entities_repo.get_entity(bound_value).get('name', bound_value)}."
            except KeyError:
                target_text = "Configured safe location is available."
        elif bound_value and any(str(resolver) == "nearest_health_service" for resolver in bindings.values()):
            try:
                target_text = f"Health service: {self.entities_repo.get_entity(bound_value).get('name', bound_value)}."
            except KeyError:
                target_text = "Configured health service is available."
        elif resource_node and any(str(resolver) == "nearest_resource_node" for resolver in bindings.values()):
            try:
                resource = self.entities_repo.get_entity(resource_node["resource_tag"])
                location = self.entities_repo.get_entity(resource_node["location_id"])
                target_text = (
                    f"Collect {resource.get('name', resource_node['resource_tag'])} from "
                    f"{location.get('name', resource_node['location_id'])}. "
                    "Source stock decreases and carried inventory increases."
                )
            except KeyError:
                target_text = "Collect the configured resource from the nearest known node; source stock decreases."
        elif resource_node and any(
            node.get("type") == "action"
            and any(value == "context.resource_tag" for value in (node.get("parameters") or {}).values())
            for node in bt.get("nodes", []) or []
        ):
            try:
                resource = self.entities_repo.get_entity(resource_node["resource_tag"])
                location = self.entities_repo.get_entity(resource_node["location_id"])
                target_text = f"Resource: {resource.get('name', resource_node['resource_tag'])} at {location.get('name', resource_node['location_id'])}. Platform reserves the configured amount."
            except KeyError:
                target_text = "A configured resource is available at the nearest known node."
        elif bound_resource_text:
            target_text = bound_resource_text
        elif target_keys and participants:
            names = ", ".join(str(item.get("name", item.get("id"))) for item in participants[:8])
            if target_required:
                target_text = f"Choose one participant explicitly: {names}. Platform will reject a missing or ambiguous target."
            else:
                target_text = f"Participant: {names}. Platform uses this participant unless another permitted target is supplied."
        return Guidance(target_text, target_required, target_options, parameter_options, source_parameter)

    def _annotate_parameter_authorization(
        self,
        frame: DecisionFrame,
        bt: dict[str, Any],
        parameter_options: dict[str, list[dict[str, Any]]],
    ) -> tuple[str, list[dict[str, Any]]]:
        """Mark which visible resource bindings the current authority allows."""

        parameters = bt.get("agent_parameters") or {}
        key = next(
            (name for name in ("resource_id", "resource", "target_resource") if name in parameters),
            "",
        )
        options = list(parameter_options.get(key) or []) if key else []
        if not key or not options:
            return "", []
        decision = str(bt.get("authorization_decision") or "") or None
        for option in options:
            name = str(option.get("name") or "")
            value = str(self.targets.canonical_id(name) or name)
            option["value"] = value
            result = authorization_for_behavior(
                self.store,
                frame.agent,
                bt,
                {key: value},
                rule_compiler=self.rule_compiler,
                decision=decision,
            )
            available = bool(result.get("available") or not result.get("gated"))
            option["available"] = available
            reasons = list(result.get("reasons") or [])
            if not available and reasons:
                option["blocked_reason"] = str(reasons[0].get("message") or "")
        return key, options

    def _contested_risk(self, frame: DecisionFrame, bt: dict[str, Any]) -> dict[str, Any] | None:
        """Describe the deterministic witnesses a configured take would have now."""

        effect = resource_take_effect(bt, frame.action_defs, frame.bt_defs)
        if not effect:
            return None
        resource_id = str(effect.get("resource_id") or effect.get("resource") or "")
        if resource_id.startswith("context."):
            # A generic take binds the stock at execution time.  Use the
            # nearest visible resource for the pre-choice risk view; the
            # actual intent still resolves the submitted parameter.
            resource_id = str((frame.resource_node or {}).get("resource_tag") or "")
            if not resource_id and frame.location_id:
                rules = self.store.repositories.metadata.get("rules", {}) or {}
                resource_types = {str(item) for item in (rules.get("resource_entity_types", ["resource", "material"]) or [])}
                for item in self.entities_repo.entities_at_location(frame.location_id):
                    if str(item.get("entity_type") or "") in resource_types:
                        resource_id = str(item.get("id") or "")
                        break
        location_id = ""
        try:
            resource = self.entities_repo.get_entity(resource_id)
        except KeyError:
            resource = {}
        state = resource.get("initial_state") if isinstance(resource.get("initial_state"), dict) else {}
        location_id = str(state.get("location_id") or resource.get("location_id") or "")
        world = self.store.repositories.metadata.get("world_properties", {}) or {}
        observers = [
            str(item.get("name") or item.get("id"))
            for item in (self.entities_repo.entities_at_location(location_id) if location_id else [])
            if str(item.get("entity_type") or "") == "agent"
            and str(item.get("id") or "") != str(frame.agent_id)
            and self.conditions.evaluate("actor.alive", agent=item, target={}, context={}, world=world)
        ]
        return {
            "observers": sorted(observers),
            "detection": "observed" if observers else "unobserved",
            "consequence": "A violation is recorded; witnesses lose trust.",
        }

    def _behavior_entry(
        self, frame: DecisionFrame, bt_id: Any, bt: dict[str, Any], sim: Simulation, guidance: Guidance, cooldown_minutes: int
    ) -> dict[str, Any]:
        estimator, research_sources, location_id = frame.estimator, frame.research_sources, frame.location_id
        safe_location_id = frame.safe_location_id
        steps, costs, impacts = sim.steps, sim.costs, sim.impacts
        action_successes, action_failures = sim.successes, sim.failures
        target_text, target_required, target_options = guidance.text, guidance.required, guidance.options
        parameter_options, source_parameter = guidance.parameter_options, guidance.source_parameter
        estimate_parameters = {"safe_location_id": safe_location_id} if safe_location_id else {}
        if source_parameter is not None and research_sources:
            source = research_sources[0]
            estimate_parameters.update(source_id=source["id"], source_location_id=source.get("location_id"))
        minutes = estimator.behavior_minutes(bt_id, estimate_parameters, location_id)
        destination = sim.location.get("id")
        route = (
            {"kind": "travel", "destination_id": destination, "duration_minutes": minutes}
            if len(steps) == 1 and destination and destination != location_id and not bt.get("agent_parameters")
            else {}
        )
        return {
            **route,
            "ref": f"behavior:{bt_id}",
            "registration": "platform_validated",
            "catalog_source": bt.get("kind", "configured"),
            "name": bt.get("summary") or label(bt_id),
            "choice": bt.get("agent_label") or bt.get("summary") or label(bt_id),
            "summary": bt.get("description") or bt.get("summary") or label(bt_id),
            "purpose": bt.get("purpose") or bt.get("description") or bt.get("summary") or label(bt_id),
            "when": bt.get("when") or bt.get("entry_descriptions") or [predicate_text(item) for item in bt.get("entry_conditions", [])] or ["The current goal makes this useful."],
            "requires": bt.get("requirements") or bt.get("requirement_descriptions") or [predicate_text(item) for item in bt.get("entry_conditions", [])] or ["Platform confirms all step requirements."],
            "target": target_text,
            "target_required": target_required,
            "target_options": target_options if target_required else [],
            "parameters": [
                f"{name} ({'required' if spec.get('required', False) else 'optional'} {spec.get('type', 'value')}): {spec.get('description', 'configured input')}"
                for name, raw_spec in (bt.get("agent_parameters", {}) or {}).items()
                for spec in [raw_spec if isinstance(raw_spec, dict) else {"type": str(raw_spec)}]
            ],
            "steps": steps[:5],
            "time": f"{minutes} minutes",
            "parameter_options": parameter_options,
            "cost": self._merge_cost_lines(costs)[:6] or ["Costs are defined by the selected steps."],
            "impact": list(dict.fromkeys(impacts))[:3] or ["Platform applies configured effects and records events."],
            "success": [str(item).replace("_", " ") for item in bt.get("success_results", [])] or list(dict.fromkeys(action_successes))[:4] or ["Platform reports the configured success result."],
            "failure": [str(item).replace("_", " ") for item in bt.get("failure_results", [])] or list(dict.fromkeys(action_failures))[:4] or ["Platform reports a failed or interrupted step."],
            "interrupt": bt.get("interrupt_policy", "cancel"),
            "cooldown_minutes": cooldown_minutes,
            "tags": bt.get("tags", []),
            "goal_tags": bt.get("goal_tags", []),
            "drive_tags": bt.get("drive_tags", []),
            "supports_drive_tags": bt.get("supports_drive_tags", []),
        }

    @staticmethod
    def _merge_cost_lines(costs: list[str]) -> list[str]:
        """Collapse repeated per-minute costs into one readable total.

        A behavior made of Travel plus a service used to show two separate
        "Stamina ... (per minute)" lines.  They are both real, but presenting
        them as unrelated rows made the action card look duplicated.  Keep
        non-rate lines untouched and sum rates for the same attribute.
        """

        rates: dict[str, float] = {}
        labels: dict[str, str] = {}
        for line in costs:
            text = str(line)
            match = re.match(r"^(.+?) ([0-9]+(?:\.[0-9]+)?) \(per minute\)$", text)
            if match:
                key = match.group(1).casefold()
                labels.setdefault(key, match.group(1))
                rates[key] = rates.get(key, 0.0) + float(match.group(2))
        combined: list[str] = []
        emitted_rates: set[str] = set()
        for line in costs:
            text = str(line)
            match = re.match(r"^(.+?) ([0-9]+(?:\.[0-9]+)?) \(per minute\)$", text)
            if match:
                key = match.group(1).casefold()
                if key in emitted_rates:
                    continue
                emitted_rates.add(key)
                combined.append(f"{labels[key]} {rates[key]:g} (per minute)")
            elif text not in combined:
                combined.append(text)
        return combined

    @staticmethod
    def _target_parameter_keys(behavior: dict[str, Any], behavior_defs: dict[str, dict[str, Any]] | None = None) -> set[str]:
        """Return target fields from every configured BT control-flow node."""

        keys: set[str] = set()
        target_context_names = {"target_id", "patient_id", "protected_target_id", "counterparty_id", "listener_id"}
        visited: set[str] = set()

        def visit(nodes: list[Any], owner_id: str | None = None) -> None:
            if owner_id:
                if owner_id in visited:
                    return
                visited.add(owner_id)
            for raw in nodes or []:
                node = raw if isinstance(raw, dict) else {"type": "behavior", "behavior_id": raw}
                node_type = node.get("type")
                if node_type == "action":
                    for key, value in (node.get("parameters") or {}).items():
                        if isinstance(value, str) and value.startswith("context.") and value.removeprefix("context.") in target_context_names:
                            keys.add(str(key))
                elif node_type in {"sequence", "parallel", "root", "selector"}:
                    visit(node.get("children", []) or [])
                elif node_type in {"behavior", "subtree"} and behavior_defs and node.get("behavior_id"):
                    nested_id = str(node["behavior_id"])
                    nested = behavior_defs.get(nested_id) or {}
                    visit(nested.get("nodes", []) or [], nested_id)

        visit(behavior.get("nodes", []) or [], str(behavior.get("id", "")) or None)
        return keys

    def _iter_behavior_actions(self, behavior_id: str, *, seen: set[str] | None = None):
        """Yield configured Action nodes from any BT control-flow shape."""

        seen = set(seen or set())
        if behavior_id in seen:
            return
        seen.add(behavior_id)
        definitions = {item["id"]: item for item in self._definitions("bt_def")}
        behavior = definitions.get(behavior_id) or {}

        def visit(nodes: list[Any], branch_seen: set[str]):
            for raw in nodes or []:
                node = raw if isinstance(raw, dict) else {"type": "behavior", "behavior_id": raw}
                node_type = node.get("type")
                if node_type == "action" and node.get("action_id"):
                    yield node
                elif node_type in {"behavior", "subtree"} and node.get("behavior_id"):
                    nested_id = str(node["behavior_id"])
                    if nested_id not in branch_seen:
                        nested = definitions.get(nested_id) or {}
                        yield from visit(nested.get("nodes", []) or [], branch_seen | {nested_id})
                elif node_type in {"sequence", "parallel", "root", "selector"}:
                    yield from visit(node.get("children", []) or [], branch_seen)

        yield from visit(behavior.get("nodes", []) or [], seen)
