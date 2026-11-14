from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..actions.conditions import ConditionEngine
from ..actions.effects import EffectPipeline
from ..evolution.story import eligible_story_beats, story_evidence_event_types
from ..evolution.memory import remember_role
from ..world.definition_compiler import compile_definition_shape, definition_slug
from ..world.schema import AttributeSchema
from .proposal_errors import _UnreachableProposal


class DefinitionRuntimeMixin:
    """Validate and register declarative world definitions."""

    def _normalize_entity_conditions(self, definition: dict[str, Any]) -> dict[str, Any]:
        """Canonicalize visible location names used in machine conditions.

        God sees visible names, while persisted state stores entity keys.  A
        condition such as ``actor.state.location_id == Storage Ring`` must
        therefore be compiled to the configured location key before the
        executable catalog and runtime evaluate it.  Only location operands
        are normalized here; arbitrary condition text remains declarative
        data and is never interpreted as code.
        """

        normalized = copy.deepcopy(definition)
        fields = ("actor.state.location_id", "context.location_id", "context.destination_id", "target.location_id")
        field_pattern = "|".join(re.escape(field) for field in fields)
        pattern = re.compile(rf"(?P<lhs>{field_pattern})\s*(?P<op>==|!=)\s*(?P<rhs>[^&|]+)")

        def condition(value: Any) -> Any:
            if not isinstance(value, str):
                return value

            def replace(match: re.Match[str]) -> str:
                rhs = match.group("rhs").strip()
                if rhs.startswith("context.") or rhs.startswith("world["):
                    return match.group(0)
                visible = rhs.strip("\"'")
                canonical = self.targets.canonical_id(visible)
                if not canonical:
                    return match.group(0)
                return f"{match.group('lhs')} {match.group('op')} {canonical}"

            return pattern.sub(replace, value)

        if isinstance(normalized.get("preconditions"), list):
            normalized["preconditions"] = [condition(item) for item in normalized["preconditions"]]
        for field in ("success", "failure"):
            result = normalized.get(field)
            if isinstance(result, dict) and "condition" in result:
                result["condition"] = condition(result.get("condition"))
        if isinstance(normalized.get("entry_conditions"), list):
            normalized["entry_conditions"] = [condition(item) for item in normalized["entry_conditions"]]
        return normalized

    @staticmethod
    def _is_recipe_action(action: dict[str, Any]) -> bool:
        """Whether an Action definition can consume a recipe (same contract as the behavior compiler)."""

        duration = action.get("duration") if isinstance(action.get("duration"), dict) else {}
        costs = action.get("costs") if isinstance(action.get("costs"), dict) else {}
        reserve = costs.get("reserve") if isinstance(costs.get("reserve"), dict) else {}
        return duration.get("mode") == "recipe_duration" or "recipe_inputs" in reserve

    def _normalize_recipe_items(self, definition: dict[str, Any]) -> dict[str, Any]:
        """Resolve visible names for existing resource items to registry keys.

        God sees visible world names, while inventories and shared resource
        nodes use stable registry keys.  New output items may intentionally be
        introduced by a recipe and therefore remain as submitted; only names
        that already resolve to a resource/material/object are canonicalized.
        """

        normalized = copy.deepcopy(definition)

        def item_key(value: Any) -> Any:
            if not isinstance(value, str) or not value.strip():
                return value
            canonical = self.targets.canonical_id(value)
            if not canonical:
                return value
            try:
                entity = self.store.repositories.entities.get_entity(str(canonical))
            except KeyError:
                return value
            if str(entity.get("entity_type") or "") in {"resource", "material", "object"}:
                return str(canonical)
            return value

        for field in ("inputs", "outputs", "requires"):
            values = normalized.get(field)
            if not isinstance(values, list):
                continue
            for item in values:
                if isinstance(item, dict) and "item" in item:
                    item["item"] = item_key(item.get("item"))
        limits = normalized.get("limits")
        if isinstance(limits, dict):
            limits = [limits]
            normalized["limits"] = limits
        if isinstance(limits, list):
            for item in limits:
                if isinstance(item, dict) and "item" in item:
                    item["item"] = item_key(item.get("item"))
        return normalized

    def _resolve_research_assignees(self, state: dict[str, Any]) -> tuple[list[tuple[str, str]], list[str]]:
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

    def _unreachable_research_assignees(self, source_name: str, assignees: list[tuple[str, str]]) -> list[str]:
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
    def _research_focus(cls, state: dict[str, Any], description: Any = "") -> tuple[str, set[str]]:
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

    def _research_duplicate(self, state: dict[str, Any], description: Any, location_id: Any) -> dict[str, Any] | None:
        focus, tokens = self._research_focus(state, description)
        if not tokens:
            return None
        explicit_key = str(state.get("research_novelty_key") or "").strip().casefold()
        for source in self.store.repositories.memory.research_source_catalog():
            try:
                entity = self.store.repositories.entities.get_entity(str(source["id"]))
            except KeyError:
                continue
            existing_state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
            existing_focus, existing_tokens = self._research_focus(existing_state, entity.get("description"))
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

    @staticmethod
    def _definition_name_key(value: Any) -> str:
        return "".join(char for char in str(value or "").casefold() if char.isalnum())

    def _resolve_registered_definition(self, table: str, value: Any, *, extra: dict[str, Any] | None = None) -> tuple[str | None, list[str]]:
        """Resolve a visible definition name without exposing registry IDs."""

        key = self._definition_name_key(value)
        if not key:
            return None, []
        matches: list[str] = []
        if extra and table == "action_def":
            candidates = [extra.get("id"), extra.get("name"), extra.get("label"), extra.get("description")]
            if any(key == self._definition_name_key(candidate) for candidate in candidates if candidate):
                matches.append(str(extra.get("id")))
        for item in self.store.repositories.entities.get_definitions(table):
            candidates = [item.get("id"), item.get("name"), item.get("label"), item.get("agent_label"), item.get("summary"), item.get("description")]
            if any(key == self._definition_name_key(candidate) for candidate in candidates if candidate):
                matches.append(str(item["id"]))
        return (matches[0] if len(matches) == 1 else None), matches

    def _validate_definition_proposal(
        self,
        definition_type: str,
        name: str,
        raw: dict[str, Any],
        *,
        extra_action: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        """Validate and normalize a God definition before SQLite registration."""

        kind = str(definition_type or "").strip().casefold()
        raw = self._normalize_entity_conditions(raw or {})
        definition, errors = compile_definition_shape(kind, name, raw)
        if kind not in {"attribute", "world_property", "action", "behavior", "recipe", "capability"}:
            return definition, errors

        if kind == "attribute":
            definition.update({"id": str(definition.get("id") or definition_slug(name) or f"attribute_{uuid4().hex[:10]}"), "label": definition.get("label") or name, "prompt": definition.get("prompt") or str(name)})
            existing = self.store.get_meta("attributes", []) or []
            if any(str(item.get("id")) == definition["id"] for item in existing if isinstance(item, dict)):
                errors.append(f"attribute already exists: {definition['id']}")
            if any(self._definition_name_key(name) == self._definition_name_key(item.get("label") or item.get("id")) for item in existing if isinstance(item, dict)):
                errors.append(f"attribute name already exists: {name}")
            if definition.get("vital") and any(item.get("vital") is True for item in existing if isinstance(item, dict)):
                errors.append("only one vital attribute may be configured")
            if definition.get("capacity") and any(item.get("capacity") is True for item in existing if isinstance(item, dict)):
                errors.append("only one capacity attribute may be configured")
            return definition, errors

        if kind == "world_property":
            property_id = str(definition.get("id") or definition_slug(name) or f"property_{uuid4().hex[:10]}")
            if property_id in {str(key) for key in (self.store.get_meta("world_properties", {}) or {})}:
                errors.append(f"world property already exists: {property_id}")
            if property_id not in {str(key) for key in (self.store.get_meta("world_properties", {}) or {})} and any(self._definition_name_key(name) == self._definition_name_key(item) for item in (self.store.get_meta("world_property_labels", {}) or {}).values()):
                errors.append(f"world property name already exists: {name}")
            definition.update({"id": property_id, "label": definition.get("label") or name, "prompt": definition.get("prompt") or str(name)})
            return definition, errors
        table = {"action": "action_def", "behavior": "bt_def", "recipe": "recipe_def", "capability": "capability_def"}[kind]
        explicit_id = str(definition.get("id") or "").strip()
        definition_id = explicit_id or definition_slug(name) or f"definition_{uuid4().hex[:10]}"
        if any(str(item.get("id")) == definition_id for item in self.store.repositories.entities.get_definitions(table)):
            errors.append(f"definition already exists: {definition_id}")
        if any(self._definition_name_key(name) in {self._definition_name_key(item.get(field)) for field in ("name", "label", "agent_label", "summary", "description") if item.get(field)} for item in self.store.repositories.entities.get_definitions(table)):
            errors.append(f"definition name already exists: {name}")

        if kind == "action":
            parameters = definition.get("parameters")
            for field in ("success", "failure"):
                result_contract = definition.get(field)
                condition = result_contract.get("condition") if isinstance(result_contract, dict) else None
                if condition and isinstance(parameters, dict):
                    dynamic_inventory_keys = {
                        str(match.group(1))
                        for match in re.finditer(r"actor\.inventory\.([A-Za-z_][A-Za-z0-9_]*)", str(condition))
                        if str(match.group(1)) in {str(key) for key in parameters}
                    }
                    if dynamic_inventory_keys:
                        errors.append(
                            f"action.{field}.condition treats dynamic parameter(s) {', '.join(sorted(dynamic_inventory_keys))} as inventory keys; use a literal visible item or omit the condition"
                        )
            resource_transfer_effects = [
                effect
                for field in ("effects", "progress_effects")
                for effect in (definition.get(field, []) or [])
                if isinstance(effect, dict)
                and str(effect.get("type") or effect.get("op") or "") == "resource_transfer"
            ]
            if resource_transfer_effects:
                reserve = ((definition.get("costs") or {}).get("reserve") or {}) if isinstance(definition.get("costs"), dict) else {}
                success_condition = str((definition.get("success") or {}).get("condition") or "") if isinstance(definition.get("success"), dict) else ""
                if re.search(r"\bactor\.inventory\.[A-Za-z_][A-Za-z0-9_]*\b", success_condition):
                    errors.append(
                        "resource_transfer success condition cannot inspect actor.inventory after reservation; use actor.alive or an observable world/target result"
                    )
                item_parameter = reserve.get("item_parameter") if isinstance(reserve, dict) else None
                if not item_parameter:
                    errors.append("resource_transfer requires costs.reserve.item_parameter so the transfer is conserved")
                elif not isinstance(definition.get("parameters"), dict) or str(item_parameter) not in definition["parameters"]:
                    errors.append(f"resource_transfer item_parameter {item_parameter!r} must be declared in action.parameters")
                for effect in resource_transfer_effects:
                    source_parameter = effect.get("source_item_parameter")
                    if source_parameter and str(source_parameter) != str(item_parameter):
                        errors.append("resource_transfer.source_item_parameter must match costs.reserve.item_parameter")
                    if source_parameter and (
                        not isinstance(definition.get("parameters"), dict)
                        or str(source_parameter) not in definition["parameters"]
                    ):
                        errors.append(f"resource_transfer source parameter {source_parameter!r} is not declared in action.parameters")
                    reserve_quantity_parameter = reserve.get("item_quantity_parameter")
                    effect_quantity_parameter = effect.get("quantity_parameter")
                    if reserve_quantity_parameter:
                        if not isinstance(definition.get("parameters"), dict) or str(reserve_quantity_parameter) not in definition["parameters"]:
                            errors.append(f"resource_transfer quantity parameter {reserve_quantity_parameter!r} is not declared in action.parameters")
                        if effect_quantity_parameter and str(effect_quantity_parameter) != str(reserve_quantity_parameter):
                            errors.append("resource_transfer.quantity_parameter must match costs.reserve.item_quantity_parameter")
                        elif not effect_quantity_parameter:
                            quantity = effect.get("quantity", effect.get("amount"))
                            if quantity != f"context.{reserve_quantity_parameter}":
                                errors.append(
                                    "resource_transfer must bind quantity_parameter to costs.reserve.item_quantity_parameter"
                                )
                    else:
                        if effect_quantity_parameter:
                            errors.append(
                                "resource_transfer.quantity_parameter requires costs.reserve.item_quantity_parameter"
                            )
                        quantity = effect.get("quantity", effect.get("amount"))
                        if quantity is None:
                            quantity = 1
                        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or float(quantity) != 1:
                            errors.append(
                                "resource_transfer needs quantity 1 by default, or declare item_quantity_parameter for a variable amount"
                            )
            # Keep the visible proposal name in the persisted definition.  The
            # registry id is intentionally machine-oriented, while audits,
            # AgentView and later definition references must be able to show
            # the exact human-facing label that God submitted.
            definition.update({"id": definition_id, "version": 1, "definition_version": 1, "name": definition.get("name") or name, "description": definition.get("description") or str(name), "definition_source": "god_proposal", "kind": "atomic", "executor": "effect_pipeline", "validation_status": "validated"})
        elif kind == "behavior":
            normalized_conditions: list[str] = []
            for condition in definition.get("entry_conditions", []) or []:
                condition_text = str(condition)
                # Conditions are machine predicates, but God often copies a
                # visible item label from the world view.  Resolve only
                # existing resource/material/object labels; unknown inventory
                # keys may be newly produced by a bound recipe and remain as
                # submitted.
                def normalize_inventory_key(match: re.Match[str]) -> str:
                    key = match.group(1)
                    canonical = self.targets.canonical_id(key)
                    if canonical:
                        try:
                            entity = self.store.repositories.entities.get_entity(str(canonical))
                        except KeyError:
                            entity = {}
                        if str(entity.get("entity_type") or "") in {"resource", "material", "object"}:
                            return f"actor.inventory.{canonical}"
                    return match.group(0)

                condition_text = re.sub(r"actor\.inventory\.([^\s<>=!&|()]+)", normalize_inventory_key, condition_text)
                normalized_conditions.append(condition_text)
            definition["entry_conditions"] = normalized_conditions
            for condition in normalized_conditions:
                if "event.recent." in str(condition):
                    errors.append(
                        "a reusable God-proposed behavior must not use event.recent.* entry conditions; "
                        "use durable actor, capability, location, inventory, relationship, or world state"
                    )
            nodes = definition.get("nodes")
            if not isinstance(nodes, list) or not nodes:
                nodes = []
            normalized_nodes: list[dict[str, Any]] = []
            node_count = 0

            def normalize_node(node: Any, path: str, depth: int = 0) -> dict[str, Any] | None:
                nonlocal node_count
                node_count += 1
                if node_count > 32:
                    return None
                if isinstance(node, str):
                    node = {"type": "behavior", "behavior": node}
                if not isinstance(node, dict):
                    errors.append(f"behavior node {path} must be an object")
                    return None
                node_copy = copy.deepcopy(node)
                node_type = str(node_copy.get("type") or "action")
                if node_type == "action":
                    ref = node_copy.get("action_id") or node_copy.get("action") or node_copy.get("name")
                    resolved, matches = self._resolve_registered_definition("action_def", ref, extra=extra_action)
                    if not resolved:
                        errors.append(f"unknown or ambiguous action in node {path}: {ref}; matches={matches}")
                    else:
                        node_copy["action_id"] = resolved
                        action = (
                            extra_action
                            if extra_action and str(extra_action.get("id")) == str(resolved)
                            else next((item for item in self.store.repositories.entities.get_definitions("action_def") if str(item.get("id")) == resolved), {})
                        )
                        parameters = node_copy.get("parameters") if isinstance(node_copy.get("parameters"), dict) else {}
                        # God sees visible entity names. Persist canonical IDs
                        # inside reusable behaviors just as normal Intent
                        # submission does; otherwise a valid-looking behavior
                        # can be registered and then fail every NPC view-time
                        # simulation because the later Action expects an ID.
                        normalized_parameters: dict[str, Any] = {}
                        entity_parameter_keys = {
                            "target_id", "object_id", "location_id", "source_id", "area_id", "patient_id",
                            "protected_target_id", "counterparty_id", "listener_id",
                        }
                        for key, value in parameters.items():
                            key = str(key)
                            if (
                                isinstance(value, str)
                                and value
                                and not value.startswith("context.")
                                and (key.endswith("_entity") or key in entity_parameter_keys)
                            ):
                                canonical = self.targets.canonical_id(value)
                                if canonical:
                                    parameter_spec = (action.get("parameters") or {}).get(key)
                                    expected_type = str(parameter_spec.get("entity_type") or "") if isinstance(parameter_spec, dict) else ""
                                    actual_type = str(self.store.repositories.entities.get_entity(canonical).get("entity_type") or "")
                                    if expected_type and actual_type != expected_type:
                                        errors.append(
                                            f"action {resolved} parameter {key} requires a visible {expected_type}; "
                                            f"{value} resolves to {actual_type} {canonical}"
                                        )
                                        continue
                                    normalized_parameters[key] = canonical
                                    continue
                                visible_entities = [
                                    str(dict(item).get("name") or dict(item).get("id"))
                                    for item in self.store.connection.execute(
                                        "SELECT id,name FROM entity WHERE id != 'player' ORDER BY name,id LIMIT 40"
                                    ).fetchall()
                                ]
                                errors.append(
                                    f"action {resolved} parameter {key} must use an exact visible entity name; "
                                    f"visible examples: {', '.join(visible_entities)}"
                                )
                            normalized_parameters[key] = value
                        parameters = normalized_parameters
                        node_copy["parameters"] = parameters
                        action_parameters = action.get("parameters")
                        if action_parameters is None:
                            action_parameters = {}
                        elif not isinstance(action_parameters, dict):
                            errors.append(f"action {resolved} parameters must be an object")
                            action_parameters = {}
                        allowed_parameters = set(action_parameters.keys())
                        unknown_parameters = sorted(set(parameters) - allowed_parameters)
                        if unknown_parameters:
                            errors.append(
                                f"action {resolved} in node {path} has unknown parameter(s): {', '.join(unknown_parameters)}; "
                                f"allowed parameters: {', '.join(sorted(allowed_parameters)) or 'none'}"
                            )
                        raw_defaults = action.get("parameter_defaults")
                        if not isinstance(raw_defaults, dict):
                            raw_defaults = {}
                        parameter_defaults = set(raw_defaults.keys())
                        missing_parameters = sorted(allowed_parameters - set(parameters) - parameter_defaults)
                        if missing_parameters:
                            errors.append(
                                f"action {resolved} in node {path} is missing parameter(s): {', '.join(missing_parameters)}"
                            )
                    node_copy.pop("action", None)
                    node_copy.pop("name", None)
                elif node_type in {"behavior", "subtree"}:
                    ref = node_copy.get("behavior_id") or node_copy.get("behavior") or node_copy.get("name")
                    resolved, matches = self._resolve_registered_definition("bt_def", ref)
                    if not resolved:
                        errors.append(f"unknown or ambiguous behavior in node {path}: {ref}; matches={matches}")
                    else:
                        node_copy["behavior_id"] = resolved
                    node_copy.pop("behavior", None)
                    node_copy.pop("name", None)
                elif node_type not in {"sequence", "selector", "parallel", "condition", "wait"}:
                    errors.append(f"unsupported behavior node type: {node_type}")
                children = node_copy.get("children")
                if children is not None:
                    if not isinstance(children, list):
                        errors.append(f"behavior node {path}.children must be a list")
                    elif depth >= 8:
                        errors.append(f"behavior node {path} exceeds maximum nesting depth")
                    else:
                        normalized_children = []
                        for child_index, child in enumerate(children):
                            normalized = normalize_node(child, f"{path}.{child_index}", depth + 1)
                            if normalized is not None:
                                normalized_children.append(normalized)
                        node_copy["children"] = normalized_children
                return node_copy

            for index, node in enumerate(nodes[:32]):
                normalized = normalize_node(node, str(index))
                if normalized is not None:
                    normalized_nodes.append(normalized)
            if node_count > 32:
                errors.append("a behavior may contain at most 32 nodes")
            definition["nodes"] = normalized_nodes
            definition.pop("actions", None)
            definition.update({"id": definition_id, "version": 1, "definition_version": 1, "summary": definition.get("summary") or definition.get("description") or str(name), "agent_label": definition.get("agent_label") or name, "definition_source": "god_proposal", "kind": "proposed", "validation_status": "validated"})
        elif kind == "recipe":
            definition = self._normalize_recipe_items(definition)
            # A recipe is only ever produced through an Action that consumes
            # recipes.  Without one the recipe can never be crafted by anybody.
            if not any(self._is_recipe_action(item) for item in self.store.repositories.entities.get_definitions("action_def")) and not self._is_recipe_action(extra_action or {}):
                errors.append(
                    "no registered Action can craft a recipe (it must reserve recipe_inputs or use duration.mode recipe_duration); "
                    "register such an Action and a behavior that uses it before adding recipes"
                )
            definition.update({"id": definition_id, "version": 1, "definition_version": 1, "name": definition.get("name") or name, "definition_source": "god_proposal", "validation_status": "validated"})
        elif kind == "capability":
            default = definition.get("default", definition.get("initial", 0))
            definition.update({"id": definition_id, "version": 1, "definition_version": 1, "name": definition.get("name") or name, "default": float(default) if isinstance(default, (int, float)) else 0.0, "value_path": definition.get("value_path") or f"agent.capabilities.{definition_id}", "definition_source": "god_proposal", "validation_status": "validated"})
        return definition, errors

    def submit_definition_proposal(self, actor_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        """Validate and register a new declarative definition in this instance."""

        intent_id = str(uuid4())
        definition_type = str(proposal.get("definition_type") or "").casefold()
        name = str(proposal.get("name") or "").strip()
        raw_definition = proposal.get("definition") if isinstance(proposal.get("definition"), dict) else {}
        definition, errors = self._validate_definition_proposal(definition_type, name, raw_definition)
        if actor_id != "god":
            errors.append("only God may register world definitions")
        assign_to = list(proposal.get("assign_to") or [])
        resolved_agents: list[tuple[str, str]] = []
        if definition_type != "behavior" and assign_to:
            errors.append("assign_to is only valid for behavior definitions")
        for name_or_id in assign_to:
            resolved = self.targets.canonical_id(name_or_id)
            try:
                agent = self.store.repositories.entities.get_agent(str(resolved or name_or_id))
            except KeyError:
                errors.append(f"unknown character in assign_to: {name_or_id}")
                continue
            resolved_agents.append((str(agent["id"]), str(agent.get("name") or agent["id"])))
        if definition_type == "behavior" and not resolved_agents:
            errors.append(
                "behavior definitions must assign_to at least one existing character; "
                "a registered but unassigned behavior cannot participate in world evolution"
            )
        followup = proposal.get("followup_behavior")
        followup_recipe = proposal.get("followup_recipe")
        followup_definition: dict[str, Any] | None = None
        followup_recipe_definition: dict[str, Any] | None = None
        followup_agents: list[tuple[str, str]] = []
        if definition_type == "action":
            if followup_recipe is not None:
                if not isinstance(followup_recipe, dict):
                    errors.append("followup_recipe must be an object with name and definition")
                else:
                    recipe_name = str(followup_recipe.get("name") or "").strip()
                    recipe_raw = followup_recipe.get("definition") if isinstance(followup_recipe.get("definition"), dict) else {}
                    followup_recipe_definition, recipe_errors = self._validate_definition_proposal(
                        "recipe",
                        recipe_name,
                        recipe_raw,
                        extra_action=definition,
                    )
                    errors.extend(f"followup_recipe: {error}" for error in recipe_errors)
                    if followup_recipe_definition and self._is_recipe_action(definition):
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
                            defaults.setdefault("recipe_id", str(followup_recipe_definition.get("id")))
            if not isinstance(followup, dict):
                errors.append("Action definitions must include followup_behavior with an executable assigned BT")
            else:
                followup_name = str(followup.get("name") or "").strip()
                followup_raw = followup.get("definition") if isinstance(followup.get("definition"), dict) else {}
                followup_raw = copy.deepcopy(followup_raw)
                if followup_recipe_definition:
                    recipe_id = str(followup_recipe_definition.get("id"))
                    action_id = str(definition.get("id"))

                    def inject_recipe_id(node: Any) -> None:
                        if isinstance(node, dict):
                            node_type = str(node.get("type") or "action")
                            reference = node.get("action_id") or node.get("action") or node.get("name")
                            if node_type == "action" and self._definition_name_key(reference) in {
                                self._definition_name_key(action_id),
                                self._definition_name_key(name),
                            }:
                                parameters = node.setdefault("parameters", {})
                                if isinstance(parameters, dict):
                                    # The model may repeat the visible recipe
                                    # name here.  Persist the registry id that
                                    # was created in this same transaction so
                                    # the BT can resolve the recipe immediately.
                                    parameters["recipe_id"] = recipe_id
                            for child in node.get("children") or []:
                                inject_recipe_id(child)
                        elif isinstance(node, list):
                            for child in node:
                                inject_recipe_id(child)

                    inject_recipe_id(followup_raw.get("nodes") if "nodes" in followup_raw else followup_raw.get("actions"))
                followup_definition, followup_errors = self._validate_definition_proposal(
                    "behavior",
                    followup_name,
                    followup_raw,
                    extra_action=definition,
                )
                errors.extend(f"followup_behavior: {error}" for error in followup_errors)
                followup_assign_to = list(followup.get("assign_to") or [])
                if not followup_assign_to:
                    errors.append("followup_behavior.assign_to must name at least one existing character")
                for name_or_id in followup_assign_to:
                    resolved = self.targets.canonical_id(name_or_id)
                    try:
                        agent = self.store.repositories.entities.get_agent(str(resolved or name_or_id))
                    except KeyError:
                        errors.append(f"unknown character in followup_behavior.assign_to: {name_or_id}")
                        continue
                    followup_agents.append((str(agent["id"]), str(agent.get("name") or agent["id"])))

                def uses_action(node: Any) -> bool:
                    if isinstance(node, dict):
                        if str(node.get("type") or "action") == "action" and str(node.get("action_id") or "") == str(definition.get("id")):
                            return True
                        return any(uses_action(child) for child in (node.get("children") or []))
                    if isinstance(node, list):
                        return any(uses_action(child) for child in node)
                    return False

                if followup_definition and not uses_action(followup_definition.get("nodes") or []):
                    errors.append("followup_behavior must contain an action node that references the new Action")
        elif followup_recipe is not None:
            errors.append("followup_recipe is only valid when definition_type is action")
        elif followup is not None:
            errors.append("followup_behavior is only valid when definition_type is action")
        if definition_type == "action" and followup_definition:
            has_observable_channel = any(
                isinstance(definition.get(field), list) and bool(definition.get(field))
                for field in ("effects", "progress_effects", "capability_effects")
            ) or bool(definition.get("information"))
            has_recipe_output = bool(
                followup_recipe_definition
                and isinstance(followup_recipe_definition.get("outputs"), list)
                and followup_recipe_definition.get("outputs")
            )
            if not has_observable_channel and not has_recipe_output:
                errors.append(
                    "Action must declare an observable effects/information channel or a bound Recipe output; "
                    "success and failure labels alone do not change the world"
                )
        data = {**proposal, "actor_id": actor_id, "definition": definition}
        if errors:
            return self._context.services["intent"]._reject_intent(intent_id, actor_id, "propose_definition", data, {"status": "rejected", "message": "Definition proposal failed validation.", "failed_check": "definition_schema", "errors": errors, "retryable": True})
        world_minute = int(self.store.get_meta("world_minute", 0))
        table = {"action": "action_def", "behavior": "bt_def", "recipe": "recipe_def", "capability": "capability_def"}.get(definition_type)
        with self.store.transaction():
            self.store.repositories.evolution.create_intent(intent_id, actor_id, "propose_definition", data, world_minute)
            if definition_type == "attribute":
                attributes = list(self.store.get_meta("attributes", []) or [])
                attributes.append(definition)
                self.store.set_meta("attributes", attributes)
                schema = AttributeSchema(attributes, self.store.get_meta("rules", {}) or {})
                if definition.get("kind") == "need":
                    rules = dict(self.store.get_meta("rules", {}) or {})
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
                    self.store.set_meta("rules", rules)
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
            elif definition_type == "world_property":
                properties = dict(self.store.get_meta("world_properties", {}) or {})
                properties[str(definition["id"])] = definition.get("default")
                self.store.set_meta("world_properties", properties)
                labels = dict(self.store.get_meta("world_property_labels", {}) or {})
                labels[str(definition["id"])] = str(definition.get("label") or name)
                self.store.set_meta("world_property_labels", labels)
            elif table:
                self.store.repositories.entities.put_definition(table, str(definition["id"]), definition)
                if definition_type == "action" and followup_recipe_definition:
                    self.store.repositories.entities.put_definition("recipe_def", str(followup_recipe_definition["id"]), followup_recipe_definition)
                if definition_type == "action" and followup_definition:
                    # Action and its first executable BT are committed in the
                    # same transaction. A failed reachability check raises
                    # before either definition can become visible.
                    self.store.repositories.entities.put_definition("bt_def", str(followup_definition["id"]), followup_definition)
                    for agent_id, _ in followup_agents:
                        agent = self.store.repositories.entities.get_agent(agent_id)
                        unlocked = list(agent.get("unlocked_bt_ids", []) or [])
                        if followup_definition["id"] not in unlocked:
                            unlocked.append(followup_definition["id"])
                            agent["unlocked_bt_ids"] = unlocked
                            self.store.repositories.entities.update_agent(agent_id, agent)
                    unavailable = []
                    for agent_id, visible_name in followup_agents:
                        candidate = self.view.build_decision_view(agent_id, role="agent_decision", include_diagnostics=True)
                        if not any(str(item.get("ref")) == f"behavior:{followup_definition['id']}" for item in candidate.get("executable_behaviors", [])):
                            reason = next(
                                (
                                    item.get("failed")
                                    for item in (candidate.get("unavailable_behavior_diagnostics") or [])
                                    if str(item.get("behavior_id")) == str(followup_definition["id"])
                                ),
                                ["the followup behavior is not in the executable catalog"],
                            )
                            unavailable.append({"character": visible_name, "reasons": reason})
                    if unavailable:
                        raise ValueError(
                            "Action followup behavior is not executable for its assigned character(s): "
                            + repr(unavailable)
                            + "; correct its machine predicates, parameters, travel node or assignee"
                        )
                if definition_type == "capability":
                    for agent in self.store.repositories.entities.list_agents():
                        capabilities = dict(agent.get("capabilities") or {})
                        capabilities.setdefault(definition["id"], definition.get("default", 0.0))
                        agent["capabilities"] = capabilities
                        self.store.repositories.entities.update_agent(agent["id"], agent)
                if definition_type == "behavior":
                    for agent_id, _ in resolved_agents:
                        agent = self.store.repositories.entities.get_agent(agent_id)
                        unlocked = list(agent.get("unlocked_bt_ids", []) or [])
                        if definition["id"] not in unlocked:
                            unlocked.append(definition["id"])
                            agent["unlocked_bt_ids"] = unlocked
                            self.store.repositories.entities.update_agent(agent_id, agent)
                    unavailable = []
                    for agent_id, visible_name in resolved_agents:
                        candidate = self.view.build_decision_view(agent_id, role="agent_decision", include_diagnostics=True)
                        if not any(str(item.get("ref")) == f"behavior:{definition['id']}" for item in candidate.get("executable_behaviors", [])):
                            reason = next(
                                (
                                    item.get("failed")
                                    for item in (candidate.get("unavailable_behavior_diagnostics") or [])
                                    if str(item.get("behavior_id")) == str(definition["id"])
                                ),
                                ["the behavior is not in the executable catalog"],
                            )
                            unavailable.append({"character": visible_name, "reasons": reason})
                    if unavailable:
                        raise ValueError(
                            "registered behavior is not executable for its assigned character(s): "
                            + repr(unavailable)
                            + "; use machine predicates only in entry_conditions (for example actor.alive or actor.can_research), include a Travel node before a destination Action, and correct the Action parameters"
                        )
            event_id = self._context.services["events"].publish("definition.registered", actor_id, world_minute, {"definition_type": definition_type, "name": name, "definition_id": definition.get("id"), "assigned_to": [visible for _, visible in resolved_agents], "evidence": str(proposal.get("evidence") or "")}, wake="configured")
            followup_recipe_event_id = None
            if definition_type == "action" and followup_recipe_definition:
                followup_recipe_event_id = self._context.services["events"].publish(
                    "definition.registered",
                    actor_id,
                    world_minute,
                    {
                        "definition_type": "recipe",
                        "name": str(followup_recipe.get("name") or followup_recipe_definition.get("name") or followup_recipe_definition.get("id")),
                        "definition_id": followup_recipe_definition.get("id"),
                        "bound_action_id": definition.get("id"),
                        "evidence": str(proposal.get("evidence") or ""),
                    },
                    wake="configured",
                )
            followup_event_id = None
            if definition_type == "action" and followup_definition:
                followup_event_id = self._context.services["events"].publish(
                    "definition.registered",
                    actor_id,
                    world_minute,
                    {
                        "definition_type": "behavior",
                        "name": str(followup.get("name") or followup_definition.get("agent_label") or followup_definition.get("id")),
                        "definition_id": followup_definition.get("id"),
                        "assigned_to": [visible for _, visible in followup_agents],
                        "bound_action_id": definition.get("id"),
                        "evidence": str(proposal.get("evidence") or ""),
                    },
                    wake="configured",
                )
            # A newly assigned behavior is both a definition event and an
            # actor-facing commitment. Merely adding it to the executable
            # catalog leaves the NPC free to overlook the verified problem
            # that caused God to create it. Persist the declarative purpose
            # and wake the assigned actor through the same task gateway; no
            # resource, institution, or story name is special-cased here.
            assignments: tuple[dict[str, Any], list[tuple[str, str]], int | None] | None = None
            if definition_type == "behavior" and resolved_agents:
                assignments = (definition, resolved_agents, int(event_id))
            elif definition_type == "action" and followup_definition and followup_agents:
                assignments = (followup_definition, followup_agents, int(followup_event_id or event_id))
            if assignments:
                assigned_definition, assigned_agents, assignment_event_id = assignments
                assignment_goal = str(
                    assigned_definition.get("goal")
                    or assigned_definition.get("purpose")
                    or assigned_definition.get("summary")
                    or name
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
            if actor_id == "god":
                remember_role(self.store, "god", "world_definition", {
                    "summary": f"Registered {definition_type}: {name}.",
                    "definition_type": definition_type,
                    "definition_id": definition.get("id"),
                    "status": "accepted",
                }, memory_key=f"god:definition:{intent_id}", source_event_id=event_id)
            result = {"status": "accepted", "intent_id": intent_id, "definition_type": definition_type, "name": name, "registered": True, "assigned_to": [visible for _, visible in resolved_agents], "event_id": event_id}
            if definition_type == "action" and followup_definition:
                result["bound_behavior"] = str(followup.get("name") or followup_definition.get("agent_label") or followup_definition.get("id"))
                result["bound_behavior_id"] = followup_definition.get("id")
                result["bound_assigned_to"] = [visible for _, visible in followup_agents]
                result["bound_event_id"] = followup_event_id
            if definition_type == "action" and followup_recipe_definition:
                result["bound_recipe"] = str(followup_recipe.get("name") or followup_recipe_definition.get("name") or followup_recipe_definition.get("id"))
                result["bound_recipe_id"] = followup_recipe_definition.get("id")
                result["bound_recipe_event_id"] = followup_recipe_event_id
            self.store.repositories.evolution.finish_intent(intent_id, "accepted", result)
        if definition_type == "attribute":
            attributes = self.store.get_meta("attributes", []) or []
            rules = self.store.get_meta("rules", {}) or {}
            self._context.set_dependency("attribute_schema", AttributeSchema(attributes, rules))
            self._context.set_dependency("effects", EffectPipeline(attributes))
            self._context.set_dependency("conditions", ConditionEngine(rules))
            self._context.set_dependency("rule_compiler", self._context.services["api"]._new_rule_compiler())
        return result

