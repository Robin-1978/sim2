from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any, NamedTuple
from uuid import uuid4

from ..actions.conditions import ConditionEngine
from ..actions.effects import EffectPipeline
from ..evolution.memory import remember_role
from ..world.schema import AttributeSchema
from ..world.lifecycle import trim_unlocked_behaviors, unlocked_behavior_limit
from .definition_validation import DEFINITION_TABLES


@dataclass
class DefinitionPlan:
    """A proposal after validation: the normalized definitions and the characters they bind to."""

    definition_type: str
    name: str
    definition: dict[str, Any]
    errors: list[str]
    assignees: list[tuple[str, str]] = field(default_factory=list)
    followup: Any = None
    followup_recipe: Any = None
    followup_definition: dict[str, Any] | None = None
    followup_recipe_definition: dict[str, Any] | None = None
    followup_agents: list[tuple[str, str]] = field(default_factory=list)


class DefinitionEventIds(NamedTuple):
    """Ids of the ``definition.registered`` events one submission published."""

    definition: int
    recipe: int | None
    followup: int | None


def _references_action(node: Any, action_id: str) -> bool:
    """Whether a behavior tree contains an action node that runs ``action_id``."""

    if isinstance(node, dict):
        if str(node.get("type") or "action") == "action" and str(node.get("action_id") or "") == action_id:
            return True
        return any(_references_action(child, action_id) for child in (node.get("children") or []))
    if isinstance(node, list):
        return any(_references_action(child, action_id) for child in node)
    return False


class DefinitionRuntimeMixin:
    """Register declarative world definitions and resolve research assignees."""

    def resolve_research_assignees(self, state: dict[str, Any]) -> tuple[list[tuple[str, str]], list[str]]:
        """Resolve ``research_assignees`` to ``(agent_id, visible_name)`` pairs."""

        raw = state.get("research_assignees")
        if raw in (None, "", [], ()):
            return [], []
        if isinstance(raw, str):
            values: list[Any] = [raw]
        elif isinstance(raw, (list, tuple)):
            values = list(raw)
        else:
            return [], ["research_assignees must be a character name or a list of character names"]
        resolved: list[tuple[str, str]] = []
        errors: list[str] = []
        for value in values:
            canonical = self.targets.canonical_id(value) if isinstance(value, str) else None
            try:
                agent = self.store.repositories.entities.get_agent(str(canonical or value))
            except KeyError:
                errors.append(f"unknown character in research_assignees: {value}")
                continue
            resolved.append((str(agent["id"]), str(agent.get("name") or agent["id"])))
        return resolved, errors

    def unreachable_research_assignees(self, source_name: str, assignees: list[tuple[str, str]]) -> list[str]:
        """Return why an assignee's own decision view does not offer ``source_name``.

        A research source that no assigned character can reach is dead content:
        it is registered, announced and never studied.  The check reuses the
        character's decision view, so capability, aliveness and location
        reachability are judged by the same code that later offers the choice.
        """

        definitions = {str(item["id"]): item for item in self.store.repositories.entities.get_definitions("bt_def")}
        marker = "|source_id="
        problems: list[str] = []
        for agent_id, visible_name in assignees:
            options = self.view.option_snapshot(agent_id, bt_defs=definitions)["options"]
            if any(option.endswith(f"{marker}{source_name}") for option in options):
                continue
            if any(marker in option for option in options):
                problems.append(
                    f"{visible_name} can research other sources but cannot reach {source_name}; "
                    f"put it at a location {visible_name} can travel to"
                )
            else:
                problems.append(f"{visible_name} has no choice that researches a configured source")
        if problems:
            assigned = {agent_id for agent_id, _ in assignees}
            able = [
                str(agent.get("name") or agent["id"])
                for agent in self.store.repositories.entities.list_agents()
                if str(agent["id"]) not in assigned
                and any(marker in option for option in self.view.option_snapshot(str(agent["id"]), bt_defs=definitions)["options"])
            ]
            if able:
                problems.append("characters who can research configured sources now: " + ", ".join(sorted(able)[:6]))
        return problems

    @staticmethod
    def _research_focus_tokens(value: Any) -> set[str]:
        """Build a small language-independent focus signature for a source.

        The signature is a guardrail against a model creating a new document
        for the same unanswered question under a new title. It is deliberately
        structural and cheap; semantic retrieval remains a read-time feature.
        """

        text = str(value or "").casefold()
        tokens = set(re.findall(r"[a-z0-9]+", text))
        cjk = "".join(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))
        tokens.update(cjk[index:index + 2] for index in range(max(0, len(cjk) - 1)))
        return {token for token in tokens if token}

    @classmethod
    def research_focus(cls, state: dict[str, Any], description: Any = "") -> tuple[str, set[str]]:
        values: list[str] = []
        for key in ("research_novelty_key", "research_question"):
            if state.get(key):
                values.append(str(state[key]))
        discoveries = state.get("research_discoveries") or []
        if isinstance(discoveries, dict):
            discoveries = [discoveries]
        for item in discoveries if isinstance(discoveries, list) else []:
            if isinstance(item, dict):
                values.extend(str(item.get(key)) for key in ("fact", "question", "open_question") if item.get(key))
            elif item:
                values.append(str(item))
        if description:
            values.append(str(description))
        focus = " ".join(values).strip()
        return focus, cls._research_focus_tokens(focus)

    def research_duplicate(self, state: dict[str, Any], description: Any, location_id: Any) -> dict[str, Any] | None:
        focus, tokens = self.research_focus(state, description)
        if not tokens:
            return None
        explicit_key = str(state.get("research_novelty_key") or "").strip().casefold()
        for source in self.store.repositories.memory.research_source_catalog():
            try:
                entity = self.store.repositories.entities.get_entity(str(source["id"]))
            except KeyError:
                continue
            existing_state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
            existing_focus, existing_tokens = self.research_focus(existing_state, entity.get("description"))
            existing_key = str(existing_state.get("research_novelty_key") or "").strip().casefold()
            if explicit_key and existing_key and explicit_key == existing_key:
                return {"name": source.get("name"), "reason": f"research novelty key already exists: {explicit_key}"}
            if explicit_key and existing_key:
                key_tokens = self._research_focus_tokens(explicit_key)
                existing_key_tokens = self._research_focus_tokens(existing_key)
                key_overlap = len(key_tokens & existing_key_tokens) / max(1, len(key_tokens | existing_key_tokens))
                if key_overlap >= 0.4:
                    return {"name": source.get("name"), "reason": f"research novelty key overlaps {key_overlap:.0%} with existing key {existing_key}"}
            if not existing_tokens:
                continue
            overlap = len(tokens & existing_tokens) / max(1, len(tokens | existing_tokens))
            existing_location = existing_state.get("location_id") or entity.get("location_id")
            same_location = not location_id or not existing_location or str(location_id) == str(existing_location)
            if same_location and overlap >= 0.58:
                return {
                    "name": source.get("name"),
                    "reason": f"research focus overlaps {overlap:.0%} with existing source {source.get('name')}: {existing_focus[:180]}",
                }
        for row in self.store.connection.execute(
            "SELECT event_id,data_json FROM event_log WHERE event_type='research.completed' ORDER BY event_id DESC LIMIT 24"
        ).fetchall():
            try:
                payload = json.loads(row["data_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            fact = str(payload.get("fact") or payload.get("summary") or "")
            if not fact:
                continue
            fact_tokens = self._research_focus_tokens(fact)
            overlap = len(tokens & fact_tokens) / max(1, len(tokens | fact_tokens))
            if overlap >= 0.58:
                return {
                    "name": f"completed research event {row['event_id']}",
                    "reason": f"research question overlaps {overlap:.0%} with an already completed finding: {fact[:180]}",
                }
        return None

    def submit_definition_proposal(self, actor_id: str, proposal: dict[str, Any], *, contract: dict[str, Any] | None = None) -> dict[str, Any]:
        """Validate and register a new declarative definition in this instance."""

        intent_id = str(uuid4())
        plan = self._plan_definition(actor_id, proposal)
        data = {**proposal, "actor_id": actor_id, "definition": plan.definition}
        if contract:
            error = self._definition_contract_error(plan, contract, proposal)
            if error:
                return self._context.services["intent"].reject_intent(intent_id, actor_id, "propose_definition", data, {
                    "status": "rejected", "failed_check": "evolution_contract", "retryable": True, **error,
                })
        if plan.errors:
            return self._context.services["intent"].reject_intent(intent_id, actor_id, "propose_definition", data, {"status": "rejected", "message": "Definition proposal failed validation.", "failed_check": "definition_schema", "errors": plan.errors, "retryable": True})
        world_minute = int(self.store.repositories.metadata.get("world_minute", 0))
        try:
            with self.store.transaction():
                constraints = (contract or {}).get("behavior_constraints")
                if constraints and self._duplicate_constrained_behavior(plan.definition):
                    return self._context.services["intent"].reject_intent(intent_id, actor_id, "propose_definition", data, {
                        "status": "rejected", "failed_check": "evolution_contract", "retryable": True,
                        "message": "Choose an unused behavior id and a distinct combination of bindings.",
                    })
                self.store.repositories.evolution.create_intent(intent_id, actor_id, "propose_definition", data, world_minute)
                self._register_definition(plan)
                event_ids = self._publish_definition_events(plan, actor_id, world_minute, str(proposal.get("evidence") or ""))
                self._assign_new_behavior(plan, event_ids, world_minute)
                if actor_id == "god":
                    remember_role(self.store, "god", "world_definition", {
                        "summary": f"Registered {plan.definition_type}: {plan.name}.",
                        "definition_type": plan.definition_type,
                        "definition_id": plan.definition.get("id"),
                        "status": "accepted",
                    }, memory_key=f"god:definition:{intent_id}", source_event_id=event_ids.definition)
                result = self._definition_result(plan, intent_id, event_ids)
                self.store.repositories.evolution.finish_intent(intent_id, "accepted", result)
        except ValueError as exc:
            # A followup Behavior may only be rejected after its Action has
            # been staged in the transaction.  Return the normal typed
            # rejection so God can correct a missing Travel node instead of
            # crashing the proposal session.
            if "not executable" not in str(exc):
                raise
            if self.store.connection.in_transaction:
                self.store.connection.rollback()
            return self._context.services["intent"].reject_intent(
                intent_id,
                actor_id,
                "propose_definition",
                data,
                {
                    "status": "rejected",
                    "message": str(exc),
                    "failed_check": "definition_schema",
                    "errors": [str(exc)],
                    "retryable": True,
                },
            )
        if plan.definition_type == "attribute":
            self._refresh_attribute_dependencies()
        return result

    def _definition_contract_error(self, plan: DefinitionPlan, contract: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any] | None:
        required_type = str(contract.get("required_definition_type") or "").strip().casefold()
        if required_type and plan.definition_type != required_type:
            return {"message": f"This evolution task requires a {required_type} definition; choose that definition_type or wait."}
        required = contract.get("assign_to") or []
        required = [required] if isinstance(required, str) else required
        errors: list[str] = []
        audience = self._resolve_assignees(required, "assign_to", errors)
        supplied = {agent_id for agent_id, _ in [*plan.assignees, *plan.followup_agents]}
        expected = {agent_id for agent_id, _ in audience}
        if not plan.errors and contract.get("require_resident_assignee") and plan.definition_type in {"action", "behavior"}:
            agents = (self.store.repositories.entities.get_agent(agent_id) for agent_id in supplied)
            if not any(agent.get("role") != "player" and agent.get("controller") != "manual" for agent in agents):
                return {"message": "Include at least one autonomous resident in assign_to. The player may also participate."}
        constraints = contract.get("behavior_constraints")
        if errors or (expected - supplied) or (constraints and supplied != expected):
            return {"message": "Assign the behavior to the configured audience.", "required_assign_to": [name for _, name in audience] or required}
        if contract.get("require_story_outcome") and plan.definition_type == "action":
            followup = (proposal.get("followup_behavior") or {}).get("definition")
            if not any(
                isinstance(event, dict) and "story" in (event.get("wake") or [])
                for event in ((followup or {}).get("outcome_events") or [])
            ):
                return {"message": "This evolution task requires an outcome_events entry with wake ['story'] on its bound behavior."}
        if constraints:
            return self._constrained_behavior_error(plan, constraints)
        return None

    @staticmethod
    def _constrained_behavior_error(plan: DefinitionPlan, spec: dict[str, Any]) -> dict[str, Any] | None:
        definition = plan.definition
        nodes = definition.get("nodes") or []
        action_id = spec["action_id"]
        if (
            len(nodes) != 1 or not isinstance(nodes[0], dict)
            or nodes[0].get("type") != "action" or nodes[0].get("action_id") != action_id
            or plan.followup or plan.followup_recipe
            or any(definition.get(key) for key in ("agent_parameters", "parameter_defaults", "parameter_bindings", "outcome_events", "authorization_operation"))
        ):
            return {"message": f"Submit one deferred behavior with a single {action_id} Action and literal parameter bindings."}
        parameters = nodes[0].get("parameters") or {}
        choices = spec["parameters"]
        if set(parameters) != set(choices) or any(
            not any(type(parameters[key]) is type(value) and parameters[key] == value for value in values)
            for key, values in choices.items()
        ):
            return {"message": "Use only the configured parameter names and literal values.", "parameter_choices": choices}
        if (
            definition.get("deferred") is not True
            or type(definition.get("cooldown_minutes")) is not int
            or definition["cooldown_minutes"] != spec["cooldown_minutes"]
            or sorted(definition.get("entry_conditions") or []) != sorted(spec["entry_conditions"])
        ):
            return {"message": "Use the configured entry conditions and cooldown, with deferred set to true."}
        return None

    def _duplicate_constrained_behavior(self, definition: dict[str, Any]) -> bool:
        node = definition["nodes"][0]
        return any(
            row.get("id") == definition.get("id") or (
                row.get("status", "active") == "active"
                and len(row.get("nodes") or []) == 1
                and isinstance(row["nodes"][0], dict)
                and row["nodes"][0].get("action_id") == node["action_id"]
                and row["nodes"][0].get("parameters") == node["parameters"]
            )
            for row in self.store.repositories.entities.get_definitions("bt_def")
        )

    def _canonicalize_visible_item_references(self, definition_type: str, definition: dict[str, Any]) -> dict[str, Any]:
        """Replace visible item names in LLM-authored behavior references.

        A generated case often names the item ("封蜡灯") while the inventory
        key is the entity id.  Canonicalize the entry condition and node
        parameters at registration so the behavior becomes executable when the
        item is actually held, without teaching every condition site about
        every generated item.
        """

        if definition_type != "behavior":
            return definition
        aliases: dict[str, str] = {}
        for entity_type in ("item", "artifact", "relic", "object"):
            for summary in self.store.repositories.entities.entities_of_type(entity_type):
                name = str(summary.get("name") or "").strip()
                entity_id = str(summary.get("id") or "").strip()
                if name and entity_id:
                    aliases["".join(name.casefold().split())] = entity_id
        if not aliases:
            return definition
        import re

        result = copy.deepcopy(definition)

        def replace_condition(text: str) -> str:
            def replace(match: re.Match[str]) -> str:
                prefix, name = match.group(1), match.group(2)
                normalized = "".join(name.casefold().split())
                canonical = aliases.get(normalized)
                if not canonical:
                    candidates = {
                        entity_id for entity_name, entity_id in aliases.items()
                        if normalized and (normalized in entity_name or entity_name in normalized)
                    }
                    canonical = next(iter(candidates)) if len(candidates) == 1 else None
                return f"{prefix}.{canonical}" if canonical else match.group(0)
            return re.sub(r"(actor\.inventory|target\.inventory)\.([^\s()=<>!]+)", replace, str(text))

        result["entry_conditions"] = [replace_condition(item) for item in (result.get("entry_conditions") or [])]

        def replace_parameters(node: Any) -> None:
            if isinstance(node, dict):
                parameters = node.get("parameters")
                if isinstance(parameters, dict):
                    for key in ("item_id", "target_item", "source_item", "inventory_item"):
                        value = parameters.get(key)
                        if isinstance(value, str):
                            normalized = "".join(value.casefold().split())
                            canonical = aliases.get(normalized)
                            if not canonical:
                                candidates = {
                                    entity_id for entity_name, entity_id in aliases.items()
                                    if normalized and (normalized in entity_name or entity_name in normalized)
                                }
                                canonical = next(iter(candidates)) if len(candidates) == 1 else None
                            if canonical:
                                parameters[key] = canonical
                for value in node.values():
                    replace_parameters(value)
            elif isinstance(node, list):
                for item in node:
                    replace_parameters(item)

        replace_parameters(result.get("nodes") or [])
        return result

    def _plan_definition(self, actor_id: str, proposal: dict[str, Any]) -> DefinitionPlan:
        """Validate a proposal against the instance and resolve the characters it names."""

        definition_type = str(proposal.get("definition_type") or "").casefold()
        name = str(proposal.get("name") or "").strip()
        raw_definition = proposal.get("definition") if isinstance(proposal.get("definition"), dict) else {}
        raw_definition = self._canonicalize_visible_item_references(definition_type, raw_definition)
        definition, errors = self._validate_definition_proposal(definition_type, name, raw_definition)
        plan = DefinitionPlan(definition_type, name, definition, errors)
        if actor_id != "god":
            errors.append("only God may register world definitions")
        if errors:
            return plan
        assign_to = list(proposal.get("assign_to") or [])
        if definition_type != "behavior" and assign_to:
            errors.append("assign_to is only valid for behavior definitions")
        plan.assignees = self._resolve_assignees(assign_to, "assign_to", errors)
        if definition_type == "behavior" and not plan.assignees:
            errors.append(
                "behavior definitions must assign_to at least one existing character; "
                "a registered but unassigned behavior cannot participate in world evolution"
            )
        self._plan_followups(plan, proposal)
        self._check_action_observable_result(plan)
        return plan

    def _resolve_assignees(self, names: list[Any], label: str, errors: list[str]) -> list[tuple[str, str]]:
        """Resolve character names or ids to ``(agent_id, visible_name)``; unknown ones become errors."""

        resolved_agents: list[tuple[str, str]] = []
        for name_or_id in names:
            if str(name_or_id or "").strip().casefold() in {"player", "active_player"}:
                players = [
                    agent for agent in self.store.repositories.entities.list_agents()
                    if str(agent.get("role") or "").casefold() == "player"
                ]
                if len(players) == 1:
                    player = players[0]
                    resolved_agents.append((str(player["id"]), str(player.get("name") or player["id"])))
                elif not players:
                    errors.append(f"no active player exists for {label}: {name_or_id}")
                else:
                    errors.append(f"multiple players exist for {label}: use the exact player name")
                continue
            resolved = self.targets.canonical_id(name_or_id)
            try:
                agent = self.store.repositories.entities.get_agent(str(resolved or name_or_id))
            except KeyError:
                errors.append(f"unknown character in {label}: {name_or_id}")
                continue
            resolved_agents.append((str(agent["id"]), str(agent.get("name") or agent["id"])))
        return resolved_agents

    def _plan_followups(self, plan: DefinitionPlan, proposal: dict[str, Any]) -> None:
        """An Action is registered together with its first executable behavior and an optional recipe."""

        plan.followup = proposal.get("followup_behavior")
        plan.followup_recipe = proposal.get("followup_recipe")
        if plan.definition_type == "action":
            if plan.followup_recipe is not None:
                self._plan_followup_recipe(plan, plan.followup_recipe)
            if not isinstance(plan.followup, dict):
                plan.errors.append("Action definitions must include followup_behavior with an executable assigned BT")
            else:
                self._plan_followup_behavior(plan, plan.followup)
        elif plan.followup_recipe is not None:
            plan.errors.append("followup_recipe is only valid when definition_type is action")
        elif plan.followup is not None:
            plan.errors.append("followup_behavior is only valid when definition_type is action")

    def _plan_followup_recipe(self, plan: DefinitionPlan, followup_recipe: Any) -> None:
        if not isinstance(followup_recipe, dict):
            plan.errors.append("followup_recipe must be an object with name and definition")
            return
        recipe_name = str(followup_recipe.get("name") or "").strip()
        recipe_raw = followup_recipe.get("definition") if isinstance(followup_recipe.get("definition"), dict) else {}
        plan.followup_recipe_definition, recipe_errors = self._validate_definition_proposal(
            "recipe",
            recipe_name,
            recipe_raw,
            extra_action=plan.definition,
        )
        plan.errors.extend(f"followup_recipe: {error}" for error in recipe_errors)
        if plan.followup_recipe_definition and self._is_recipe_action(plan.definition):
            self._bind_recipe_to_action(plan.definition, plan.followup_recipe_definition)

    @staticmethod
    def _bind_recipe_to_action(definition: dict[str, Any], recipe_definition: dict[str, Any]) -> None:
        """Give a recipe-consuming Action the parameter, reservation and default that select the new recipe."""

        parameters = definition.setdefault("parameters", {})
        if isinstance(parameters, dict):
            parameters.setdefault("recipe_id", "string")
        costs = definition.setdefault("costs", {})
        if isinstance(costs, dict):
            reserve = costs.setdefault("reserve", {})
            if isinstance(reserve, dict):
                reserve.setdefault("recipe_inputs", 1)
        defaults = definition.setdefault("parameter_defaults", {})
        if isinstance(defaults, dict):
            defaults.setdefault("recipe_id", str(recipe_definition.get("id")))

    def _plan_followup_behavior(self, plan: DefinitionPlan, followup: dict[str, Any]) -> None:
        definition = plan.definition
        followup_name = str(followup.get("name") or "").strip()
        followup_raw = followup.get("definition") if isinstance(followup.get("definition"), dict) else {}
        followup_raw = copy.deepcopy(followup_raw)
        if plan.followup_recipe_definition:
            action_keys = {self._definition_name_key(str(definition.get("id"))), self._definition_name_key(plan.name)}
            self._inject_recipe_id(
                followup_raw.get("nodes") if "nodes" in followup_raw else followup_raw.get("actions"),
                action_keys,
                str(plan.followup_recipe_definition.get("id")),
            )
        plan.followup_definition, followup_errors = self._validate_definition_proposal(
            "behavior",
            followup_name,
            followup_raw,
            extra_action=definition,
        )
        plan.errors.extend(f"followup_behavior: {error}" for error in followup_errors)
        followup_assign_to = list(followup.get("assign_to") or [])
        if not followup_assign_to:
            plan.errors.append("followup_behavior.assign_to must name at least one existing character")
        plan.followup_agents = self._resolve_assignees(followup_assign_to, "followup_behavior.assign_to", plan.errors)
        if plan.followup_definition and not _references_action(plan.followup_definition.get("nodes") or [], str(definition.get("id"))):
            plan.errors.append("followup_behavior must contain an action node that references the new Action")

    def _inject_recipe_id(self, node: Any, action_keys: set[str], recipe_id: str) -> None:
        """Bind the new recipe into every node of the follow-up tree that runs the new Action."""

        if isinstance(node, dict):
            node_type = str(node.get("type") or "action")
            reference = node.get("action_id") or node.get("action") or node.get("name")
            if node_type == "action" and self._definition_name_key(reference) in action_keys:
                parameters = node.setdefault("parameters", {})
                if isinstance(parameters, dict):
                    # The model may repeat the visible recipe
                    # name here.  Persist the registry id that
                    # was created in this same transaction so
                    # the BT can resolve the recipe immediately.
                    parameters["recipe_id"] = recipe_id
            for child in node.get("children") or []:
                self._inject_recipe_id(child, action_keys, recipe_id)
        elif isinstance(node, list):
            for child in node:
                self._inject_recipe_id(child, action_keys, recipe_id)

    @staticmethod
    def _check_action_observable_result(plan: DefinitionPlan) -> None:
        """A bound Action must change something observable: an effect, an information channel or a recipe output."""

        if plan.definition_type == "action" and plan.followup_definition:
            definition, recipe = plan.definition, plan.followup_recipe_definition
            administrative = bool(plan.followup_definition.get("authorization_operation"))
            has_observable_channel = any(
                isinstance(definition.get(field), list) and bool(definition.get(field))
                for field in ("effects", "progress_effects", "capability_effects")
            ) or bool(definition.get("information")) or administrative
            has_recipe_output = bool(
                recipe
                and isinstance(recipe.get("outputs"), list)
                and recipe.get("outputs")
            )
            if not has_observable_channel and not has_recipe_output:
                plan.errors.append(
                    "Action must declare an observable effects/information channel or a bound Recipe output; "
                    "success and failure labels alone do not change the world"
                )

    def _register_definition(self, plan: DefinitionPlan) -> None:
        """Persist the validated definition(s) and everything that binds them to characters."""

        if plan.definition_type == "attribute":
            self._register_attribute(plan.definition)
        elif plan.definition_type == "world_property":
            self._register_world_property(plan.definition, plan.name)
        elif plan.definition_type in DEFINITION_TABLES:
            self._register_table_definition(plan)

    def _register_attribute(self, definition: dict[str, Any]) -> None:
        attributes = list(self.store.repositories.metadata.get("attributes", []) or [])
        attributes.append(definition)
        self.store.repositories.metadata.set("attributes", attributes)
        schema = AttributeSchema(attributes, self.store.repositories.metadata.get("rules", {}) or {})
        if definition.get("kind") == "need":
            rules = dict(self.store.repositories.metadata.get("rules", {}) or {})
            needs = list(rules.get("needs", []) or [])
            needs.append({
                "id": definition["id"],
                "target": definition.get("target", definition.get("default", 0)),
                "minimum": definition.get("min", 0),
                "gamma": definition.get("gamma", 1.0),
                "critical_threshold": definition.get("critical_threshold", 0.75),
                "critical_gain": definition.get("critical_gain", 1.0),
            })
            rules["needs"] = needs
            self.store.repositories.metadata.set("rules", rules)
            schema = AttributeSchema(attributes, rules)
        for agent in self.store.repositories.entities.list_agents():
            state = dict(agent.get("initial_state") or {})
            needs = dict(agent.get("needs") or {})
            if definition.get("kind") == "state" and definition["id"] not in state:
                state[definition["id"]] = schema.default(definition["id"], 0)
            if definition.get("kind") == "need" and definition["id"] not in needs:
                needs[definition["id"]] = schema.default(definition["id"], definition.get("target", 0))
            agent["initial_state"] = state
            agent["needs"] = needs
            self.store.repositories.entities.update_agent(agent["id"], agent)

    def _register_world_property(self, definition: dict[str, Any], name: str) -> None:
        properties = dict(self.store.repositories.metadata.get("world_properties", {}) or {})
        properties[str(definition["id"])] = definition.get("default")
        self.store.repositories.metadata.set("world_properties", properties)
        labels = dict(self.store.repositories.metadata.get("world_property_labels", {}) or {})
        labels[str(definition["id"])] = str(definition.get("label") or name)
        self.store.repositories.metadata.set("world_property_labels", labels)

    def _register_table_definition(self, plan: DefinitionPlan) -> None:
        definition, definition_type = plan.definition, plan.definition_type
        self.store.repositories.entities.put_definition(DEFINITION_TABLES[definition_type], str(definition["id"]), definition)
        if definition_type == "action" and plan.followup_recipe_definition:
            self.store.repositories.entities.put_definition("recipe_def", str(plan.followup_recipe_definition["id"]), plan.followup_recipe_definition)
        if definition_type == "action" and plan.followup_definition:
            self._register_followup_behavior(plan.followup_definition, plan.followup_agents)
        if definition_type == "capability":
            self._seed_capability(definition)
        if definition_type == "behavior":
            self._unlock_behavior(definition, plan.assignees)
            self._require_executable(
                definition,
                plan.assignees,
                default_reason="the behavior is not in the executable catalog",
                message_prefix="registered behavior is not executable for its assigned character(s): ",
                message_suffix="; use machine predicates only in entry_conditions (for example actor.alive or actor.can_research), include a Travel node before a destination Action, and correct the Action parameters",
            )

    def _register_followup_behavior(self, followup_definition: dict[str, Any], followup_agents: list[tuple[str, str]]) -> None:
        # Action and its first executable BT are committed in the
        # same transaction. A failed reachability check raises
        # before either definition can become visible.
        self.store.repositories.entities.put_definition("bt_def", str(followup_definition["id"]), followup_definition)
        self._unlock_behavior(followup_definition, followup_agents)
        self._require_executable(
            followup_definition,
            followup_agents,
            default_reason="the followup behavior is not in the executable catalog",
            message_prefix="Action followup behavior is not executable for its assigned character(s): ",
            message_suffix="; correct its machine predicates, parameters, travel node or assignee",
        )

    def _unlock_behavior(self, definition: dict[str, Any], agents: list[tuple[str, str]]) -> None:
        limit = unlocked_behavior_limit(self.store.repositories.metadata.get("rules", {}) or {})
        for agent_id, _ in agents:
            agent = self.store.repositories.entities.get_agent(agent_id)
            unlocked = list(agent.get("unlocked_bt_ids", []) or [])
            if definition["id"] not in unlocked:
                unlocked.append(definition["id"])
                agent["unlocked_bt_ids"] = unlocked
                agent["unlocked_bt_ids"] = trim_unlocked_behaviors(agent, limit)
                self.store.repositories.entities.update_agent(agent_id, agent)

    def _require_executable(
        self,
        definition: dict[str, Any],
        agents: list[tuple[str, str]],
        *,
        default_reason: str,
        message_prefix: str,
        message_suffix: str,
    ) -> None:
        """Raise unless the behavior is in every assigned character's executable catalog.

        A living world may register a Behavior whose entry conditions become
        true after a later player decision.  ``deferred: true`` keeps that
        future stage out of the current executable catalog while preserving
        the normal executable-catalog check for every immediate Behavior.
        """

        if definition.get("deferred") is True:
            return
        unavailable = []
        for agent_id, visible_name in agents:
            candidate = self.view.build_decision_view(agent_id, role="agent_decision", include_diagnostics=True)
            if not any(str(item.get("ref")) == f"behavior:{definition['id']}" for item in candidate.get("executable_behaviors", [])):
                reason = next(
                    (
                        item.get("failed")
                        for item in (candidate.get("unavailable_behavior_diagnostics") or [])
                        if str(item.get("behavior_id")) == str(definition["id"])
                    ),
                    [default_reason],
                )
                unavailable.append({"character": visible_name, "reasons": reason})
        if unavailable:
            raise ValueError(message_prefix + repr(unavailable) + message_suffix)

    def _seed_capability(self, definition: dict[str, Any]) -> None:
        for agent in self.store.repositories.entities.list_agents():
            capabilities = dict(agent.get("capabilities") or {})
            capabilities.setdefault(definition["id"], definition.get("default", 0.0))
            agent["capabilities"] = capabilities
            self.store.repositories.entities.update_agent(agent["id"], agent)

    @staticmethod
    def _followup_recipe_label(plan: DefinitionPlan) -> str:
        recipe = plan.followup_recipe_definition
        return str(plan.followup_recipe.get("name") or recipe.get("name") or recipe.get("id"))

    @staticmethod
    def _followup_behavior_label(plan: DefinitionPlan) -> str:
        behavior = plan.followup_definition
        return str(plan.followup.get("name") or behavior.get("agent_label") or behavior.get("id"))

    def _publish_definition_events(self, plan: DefinitionPlan, actor_id: str, world_minute: int, evidence: str) -> DefinitionEventIds:
        """Publish ``definition.registered`` for the definition and, for an Action, its recipe and behavior."""

        events = self._context.services["events"]
        definition = plan.definition
        event_id = events.publish("definition.registered", actor_id, world_minute, {"definition_type": plan.definition_type, "name": plan.name, "definition_id": definition.get("id"), "assigned_to": [visible for _, visible in plan.assignees], "evidence": evidence}, wake="configured")
        recipe_event_id = None
        if plan.definition_type == "action" and plan.followup_recipe_definition:
            recipe_event_id = events.publish(
                "definition.registered",
                actor_id,
                world_minute,
                {
                    "definition_type": "recipe",
                    "name": self._followup_recipe_label(plan),
                    "definition_id": plan.followup_recipe_definition.get("id"),
                    "bound_action_id": definition.get("id"),
                    "evidence": evidence,
                },
                wake="configured",
            )
        followup_event_id = None
        if plan.definition_type == "action" and plan.followup_definition:
            followup_event_id = events.publish(
                "definition.registered",
                actor_id,
                world_minute,
                {
                    "definition_type": "behavior",
                    "name": self._followup_behavior_label(plan),
                    "definition_id": plan.followup_definition.get("id"),
                    "assigned_to": [visible for _, visible in plan.followup_agents],
                    "bound_action_id": definition.get("id"),
                    "evidence": evidence,
                },
                wake="configured",
            )
        return DefinitionEventIds(event_id, recipe_event_id, followup_event_id)

    def _assign_new_behavior(self, plan: DefinitionPlan, event_ids: DefinitionEventIds, world_minute: int) -> None:
        # A newly assigned behavior is both a definition event and an
        # actor-facing commitment. Merely adding it to the executable
        # catalog leaves the NPC free to overlook the verified problem
        # that caused God to create it. Persist the declarative purpose
        # and wake the assigned actor through the same task gateway; no
        # resource, institution, or story name is special-cased here.
        if plan.definition_type == "behavior" and plan.assignees:
            assigned_definition, assigned_agents, assignment_event_id = plan.definition, plan.assignees, int(event_ids.definition)
        elif plan.definition_type == "action" and plan.followup_definition and plan.followup_agents:
            assigned_definition, assigned_agents, assignment_event_id = plan.followup_definition, plan.followup_agents, int(event_ids.followup or event_ids.definition)
        else:
            return
        assignment_goal = str(
            assigned_definition.get("goal")
            or assigned_definition.get("purpose")
            or assigned_definition.get("summary")
            or plan.name
        ).strip()
        behavior_id = str(assigned_definition.get("id") or "")
        if behavior_id:
            for assigned_id, _ in assigned_agents:
                assigned_agent = self.store.repositories.entities.get_agent(assigned_id)
                work = list(assigned_agent.get("behavior_assignments", []) or [])
                work.append({
                    "behavior_id": behavior_id,
                    "goal": assignment_goal,
                    "source_event_id": assignment_event_id,
                    "status": "assigned",
                    "assigned_minute": world_minute,
                })
                assigned_agent["behavior_assignments"] = work[-8:]
                self.store.repositories.entities.update_agent(assigned_id, assigned_agent)
                self._context.services["events"].queue_consumer(
                    "agent_decision",
                    assignment_event_id,
                    "definition.registered",
                    assigned_id,
                    world_minute,
                    task_type=f"behavior_assigned:{behavior_id}",
                    explicit=True,
                )

    def _definition_result(self, plan: DefinitionPlan, intent_id: str, event_ids: DefinitionEventIds) -> dict[str, Any]:
        result = {"status": "accepted", "intent_id": intent_id, "definition_type": plan.definition_type, "name": plan.name, "registered": True, "assigned_to": [visible for _, visible in plan.assignees], "event_id": event_ids.definition}
        if plan.definition_type == "action" and plan.followup_definition:
            result["bound_behavior"] = self._followup_behavior_label(plan)
            result["bound_behavior_id"] = plan.followup_definition.get("id")
            result["bound_assigned_to"] = [visible for _, visible in plan.followup_agents]
            result["bound_event_id"] = event_ids.followup
        if plan.definition_type == "action" and plan.followup_recipe_definition:
            result["bound_recipe"] = self._followup_recipe_label(plan)
            result["bound_recipe_id"] = plan.followup_recipe_definition.get("id")
            result["bound_recipe_event_id"] = event_ids.recipe
        return result

    def _refresh_attribute_dependencies(self) -> None:
        """Rebuild the schema, effect pipeline and condition engine that were compiled from the old attributes."""

        attributes = self.store.repositories.metadata.get("attributes", []) or []
        rules = self.store.repositories.metadata.get("rules", {}) or {}
        self._context.set_dependency("attribute_schema", AttributeSchema(attributes, rules))
        self._context.set_dependency("effects", EffectPipeline(attributes, resolve_location=self.targets.location_id))
        self._context.set_dependency("conditions", ConditionEngine(rules))
        self._context.set_dependency("rule_compiler", self._context.services["api"].new_rule_compiler())
