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
from ..world.schema import AttributeSchema


class _UnreachableProposal(ValueError):
    """Roll a proposal back because the content it adds could never be used.

    Raised inside the proposal transaction after the content was written, so
    the check sees the world exactly as the affected characters would.
    """

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class WorldEvolutionRuntimeMixin:
    """Validated intent handlers separated from the Platform coordinator."""

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
                entity = self.store.get_entity(str(canonical))
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
                agent = self.store.get_agent(str(canonical or value))
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

        definitions = {str(item["id"]): item for item in self.store.get_definitions("bt_def")}
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
                for agent in self.store.list_agents()
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
        for key in ("research_novelty_key", "research_gap_key", "research_question", "open_question"):
            if state.get(key):
                values.append(str(state[key]))
        discoveries = state.get("research_discoveries") or state.get("research_discovery") or []
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
        explicit_key = str(state.get("research_novelty_key") or state.get("research_gap_key") or "").strip().casefold()
        for source in self.store.research_source_catalog():
            try:
                entity = self.store.get_entity(str(source["id"]))
            except KeyError:
                continue
            existing_state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
            existing_focus, existing_tokens = self._research_focus(existing_state, entity.get("description"))
            existing_key = str(existing_state.get("research_novelty_key") or existing_state.get("research_gap_key") or "").strip().casefold()
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
    def _definition_slug(value: Any) -> str:
        text = re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")
        return text[:80]

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
        for item in self.store.get_definitions(table):
            candidates = [item.get("id"), item.get("name"), item.get("label"), item.get("agent_label"), item.get("summary"), item.get("description")]
            if any(key == self._definition_name_key(candidate) for candidate in candidates if candidate):
                matches.append(str(item["id"]))
        return (matches[0] if len(matches) == 1 else None), matches

    def _validate_definition_effects(self, definition: dict[str, Any]) -> list[str]:
        allowed = {
            "need_delta", "state_delta", "attribute_delta", "attribute_set",
            "inventory_add", "inventory_remove", "inventory.consume_item",
            "relationship_delta", "public_event", "world_property_delta",
            "entity_state_set", "status_add", "status_remove", "location_set",
            "capability_xp", "capability.add_xp",
        }
        errors: list[str] = []
        for field in ("effects", "progress_effects"):
            values = definition.get(field, []) or []
            if not isinstance(values, list):
                errors.append(f"{field} must be a list")
                continue
            for index, effect in enumerate(values):
                if not isinstance(effect, dict):
                    errors.append(f"{field}[{index}] must be an object")
                    continue
                effect_type = str(effect.get("type") or effect.get("op") or "")
                if effect_type not in allowed:
                    errors.append(f"unsupported effect type: {effect_type or '<missing>'}")
        return errors

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
        definition = copy.deepcopy(raw if isinstance(raw, dict) else {})
        errors: list[str] = []
        if kind not in {"attribute", "world_property", "action", "behavior", "recipe", "capability"}:
            return definition, [f"unsupported definition_type: {kind}"]
        if not str(name or "").strip():
            errors.append("name is required")
        forbidden = {"handler", "python", "script", "sql", "executor_code", "code"}
        errors.extend(f"field is not declarative: {field}" for field in forbidden if field in definition)

        if kind == "attribute":
            attr_kind = str(definition.get("kind") or "").casefold()
            if attr_kind not in {"state", "need"}:
                errors.append("attribute.kind must be state or need")
            for field in ("min", "max", "default"):
                if not isinstance(definition.get(field), (int, float)) or isinstance(definition.get(field), bool):
                    errors.append(f"attribute.{field} must be numeric")
            if all(isinstance(definition.get(field), (int, float)) for field in ("min", "max", "default")):
                if float(definition["min"]) > float(definition["max"]):
                    errors.append("attribute.min must not exceed attribute.max")
                if not float(definition["min"]) <= float(definition["default"]) <= float(definition["max"]):
                    errors.append("attribute.default must be within min and max")
            if attr_kind == "state" and definition.get("cost_mode") not in {"increase", "decrease"}:
                errors.append("state attributes must declare cost_mode")
            if not str(definition.get("label") or name).strip():
                errors.append("attribute.label is required")
            definition.update({"id": str(definition.get("id") or self._definition_slug(name) or f"attribute_{uuid4().hex[:10]}"), "label": definition.get("label") or name, "prompt": definition.get("prompt") or str(name)})
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
            property_id = str(definition.get("id") or self._definition_slug(name) or f"property_{uuid4().hex[:10]}")
            default = definition.get("default")
            if not isinstance(default, (int, float, str, bool)) or isinstance(default, bool):
                errors.append("world_property.default must be a number or string")
            if property_id in {str(key) for key in (self.store.get_meta("world_properties", {}) or {})}:
                errors.append(f"world property already exists: {property_id}")
            if property_id not in {str(key) for key in (self.store.get_meta("world_properties", {}) or {})} and any(self._definition_name_key(name) == self._definition_name_key(item) for item in (self.store.get_meta("world_property_labels", {}) or {}).values()):
                errors.append(f"world property name already exists: {name}")
            definition.update({"id": property_id, "label": definition.get("label") or name, "prompt": definition.get("prompt") or str(name)})
            return definition, errors
        table = {"action": "action_def", "behavior": "bt_def", "recipe": "recipe_def", "capability": "capability_def"}[kind]
        explicit_id = str(definition.get("id") or "").strip()
        definition_id = explicit_id or self._definition_slug(name) or f"definition_{uuid4().hex[:10]}"
        if any(str(item.get("id")) == definition_id for item in self.store.get_definitions(table)):
            errors.append(f"definition already exists: {definition_id}")
        if any(self._definition_name_key(name) in {self._definition_name_key(item.get(field)) for field in ("name", "label", "agent_label", "summary", "description") if item.get(field)} for item in self.store.get_definitions(table)):
            errors.append(f"definition name already exists: {name}")

        if kind == "action":
            # Models naturally shorten ``costs.reserve`` to ``reserve`` when
            # proposing a new recipe action.  Accept that generic shorthand
            # and normalize it before validation; the persisted contract is
            # still the same one used by configured Actions.
            shorthand_reserve = definition.pop("reserve", None)
            if shorthand_reserve is not None:
                costs = definition.setdefault("costs", {})
                if not isinstance(costs, dict):
                    errors.append("action.costs must be an object")
                else:
                    reserve = costs.setdefault("reserve", {})
                    if not isinstance(reserve, dict):
                        errors.append("action.costs.reserve must be an object")
                    elif isinstance(shorthand_reserve, dict):
                        for key, value in shorthand_reserve.items():
                            reserve.setdefault(str(key), 1 if value is True else value)
            duration = definition.get("duration") if isinstance(definition.get("duration"), dict) else {}
            if not duration or not (isinstance(duration.get("minutes"), (int, float)) or duration.get("mode") in {"route_distance", "recipe_duration"}):
                errors.append("action.duration must declare minutes or a supported mode")
            if isinstance(duration.get("minutes"), (int, float)) and float(duration["minutes"]) <= 0:
                errors.append("action.duration.minutes must be positive")
            for field in ("preconditions", "effects", "progress_effects"):
                if field in definition and not isinstance(definition[field], list):
                    errors.append(f"action.{field} must be a list")
            errors.extend(self._validate_definition_effects(definition))
            for field in ("success", "failure"):
                if not isinstance(definition.get(field), dict) or not str((definition.get(field) or {}).get("result") or "").strip():
                    errors.append(f"action.{field} must declare an object with result")
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
                            entity = self.store.get_entity(str(canonical))
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
            if nodes is None and isinstance(definition.get("actions"), list):
                nodes = [{"type": "action", "action": item} for item in definition["actions"]]
            if not isinstance(nodes, list) or not nodes:
                errors.append("behavior.nodes or behavior.actions must contain at least one step")
                nodes = []
            normalized_nodes: list[dict[str, Any]] = []
            if len(nodes) > 32:
                errors.append("a behavior may contain at most 32 nodes")
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
                            else next((item for item in self.store.get_definitions("action_def") if str(item.get("id")) == resolved), {})
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
                                    actual_type = str(self.store.get_entity(canonical).get("entity_type") or "")
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
                        allowed_parameters = set((action.get("parameters") or {}).keys())
                        unknown_parameters = sorted(set(parameters) - allowed_parameters)
                        if unknown_parameters:
                            errors.append(
                                f"action {resolved} in node {path} has unknown parameter(s): {', '.join(unknown_parameters)}; "
                                f"allowed parameters: {', '.join(sorted(allowed_parameters)) or 'none'}"
                            )
                        parameter_defaults = set((action.get("parameter_defaults") or {}).keys())
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
            duration = definition.get("duration_minutes")
            if not isinstance(duration, (int, float)) or isinstance(duration, bool) or float(duration) <= 0:
                errors.append("recipe.duration_minutes must be positive")
            for field in ("inputs", "outputs"):
                values = definition.get(field)
                if not isinstance(values, list) or not values:
                    errors.append(f"recipe.{field} must be a non-empty list")
                elif any(not isinstance(item, dict) or not item.get("item") or not isinstance(item.get("quantity"), (int, float)) or float(item.get("quantity", 0)) <= 0 for item in values):
                    errors.append(f"recipe.{field} entries need item and positive quantity")
            # A recipe is only ever produced through an Action that consumes
            # recipes.  Without one the recipe can never be crafted by anybody.
            if not any(self._is_recipe_action(item) for item in self.store.get_definitions("action_def")) and not self._is_recipe_action(extra_action or {}):
                errors.append(
                    "no registered Action can craft a recipe (it must reserve recipe_inputs or use duration.mode recipe_duration); "
                    "register such an Action and a behavior that uses it before adding recipes"
                )
            definition.update({"id": definition_id, "version": 1, "definition_version": 1, "name": definition.get("name") or name, "definition_source": "god_proposal", "validation_status": "validated"})
        elif kind == "capability":
            default = definition.get("default", definition.get("initial", 0))
            if not isinstance(default, (int, float)) or isinstance(default, bool) or not 0 <= float(default) <= 1:
                errors.append("capability.default must be between 0 and 1")
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
                agent = self.store.get_agent(str(resolved or name_or_id))
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
                        agent = self.store.get_agent(str(resolved or name_or_id))
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
        data = {**proposal, "actor_id": actor_id, "definition": definition}
        if errors:
            return self._reject_intent(intent_id, actor_id, "propose_definition", data, {"status": "rejected", "message": "Definition proposal failed validation.", "failed_check": "definition_schema", "errors": errors, "retryable": True})
        world_minute = int(self.store.get_meta("world_minute", 0))
        table = {"action": "action_def", "behavior": "bt_def", "recipe": "recipe_def", "capability": "capability_def"}.get(definition_type)
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, "propose_definition", data, world_minute)
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
                for agent in self.store.list_agents():
                    state = dict(agent.get("initial_state") or {})
                    needs = dict(agent.get("needs") or {})
                    if definition.get("kind") == "state" and definition["id"] not in state:
                        state[definition["id"]] = schema.default(definition["id"], 0)
                    if definition.get("kind") == "need" and definition["id"] not in needs:
                        needs[definition["id"]] = schema.default(definition["id"], definition.get("target", 0))
                    agent["initial_state"] = state
                    agent["needs"] = needs
                    self.store.update_agent(agent["id"], agent)
            elif definition_type == "world_property":
                properties = dict(self.store.get_meta("world_properties", {}) or {})
                properties[str(definition["id"])] = definition.get("default")
                self.store.set_meta("world_properties", properties)
                labels = dict(self.store.get_meta("world_property_labels", {}) or {})
                labels[str(definition["id"])] = str(definition.get("label") or name)
                self.store.set_meta("world_property_labels", labels)
            elif table:
                self.store.put_definition(table, str(definition["id"]), definition)
                if definition_type == "action" and followup_recipe_definition:
                    self.store.put_definition("recipe_def", str(followup_recipe_definition["id"]), followup_recipe_definition)
                if definition_type == "action" and followup_definition:
                    # Action and its first executable BT are committed in the
                    # same transaction. A failed reachability check raises
                    # before either definition can become visible.
                    self.store.put_definition("bt_def", str(followup_definition["id"]), followup_definition)
                    for agent_id, _ in followup_agents:
                        agent = self.store.get_agent(agent_id)
                        unlocked = list(agent.get("unlocked_bt_ids", []) or [])
                        if followup_definition["id"] not in unlocked:
                            unlocked.append(followup_definition["id"])
                            agent["unlocked_bt_ids"] = unlocked
                            self.store.update_agent(agent_id, agent)
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
                    for agent in self.store.list_agents():
                        capabilities = dict(agent.get("capabilities") or {})
                        capabilities.setdefault(definition["id"], definition.get("default", 0.0))
                        agent["capabilities"] = capabilities
                        self.store.update_agent(agent["id"], agent)
                if definition_type == "behavior":
                    for agent_id, _ in resolved_agents:
                        agent = self.store.get_agent(agent_id)
                        unlocked = list(agent.get("unlocked_bt_ids", []) or [])
                        if definition["id"] not in unlocked:
                            unlocked.append(definition["id"])
                            agent["unlocked_bt_ids"] = unlocked
                            self.store.update_agent(agent_id, agent)
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
            event_id = self.store.append_event("definition.registered", actor_id, world_minute, {"definition_type": definition_type, "name": name, "definition_id": definition.get("id"), "assigned_to": [visible for _, visible in resolved_agents], "evidence": str(proposal.get("evidence") or "")})
            self._wake_configured_evolution_consumers(event_id, "definition.registered", actor_id, world_minute)
            followup_recipe_event_id = None
            if definition_type == "action" and followup_recipe_definition:
                followup_recipe_event_id = self.store.append_event(
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
                )
                self._wake_configured_evolution_consumers(followup_recipe_event_id, "definition.registered", actor_id, world_minute)
            followup_event_id = None
            if definition_type == "action" and followup_definition:
                followup_event_id = self.store.append_event(
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
                )
                self._wake_configured_evolution_consumers(followup_event_id, "definition.registered", actor_id, world_minute)
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
            self.store.finish_intent(intent_id, "accepted", result)
        if definition_type == "attribute":
            attributes = self.store.get_meta("attributes", []) or []
            self.attribute_schema = AttributeSchema(attributes, self.store.get_meta("rules", {}) or {})
            self.effects = EffectPipeline(attributes)
            self.conditions = ConditionEngine(self.store.get_meta("rules", {}) or {})
            self.rule_compiler = self._new_rule_compiler()
        return result

    def _normalize_god_agent_state(self, entity_id: str, proposal: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Initialize a new runtime character from configured world defaults."""

        errors: list[str] = []
        state = copy.deepcopy(proposal.get("state") if isinstance(proposal.get("state"), dict) else {})
        name = str(proposal.get("name") or "").strip()
        role = str(state.get("role") or "").strip()
        goal = str(state.get("ultimate_goal") or "").strip()
        if not role:
            errors.append("agent.state.role is required")
        if not goal:
            errors.append("agent.state.ultimate_goal is required")

        initial_state = dict(state.get("initial_state") or {})
        raw_location = proposal.get("location_id") or initial_state.get("location_id")
        location_id = self.targets.canonical_id(str(raw_location or ""))
        if not location_id:
            errors.append("agent.state.initial_state.location_id must name an existing location")
        else:
            try:
                location = self.store.get_entity(location_id)
                if str(location.get("entity_type")) != "location":
                    errors.append(f"{raw_location} is not a location")
            except KeyError:
                errors.append(f"agent location does not exist: {raw_location}")
        if location_id:
            initial_state["location_id"] = location_id

        rules = self.store.get_meta("rules", {}) or {}
        schema = AttributeSchema(self.store.get_meta("attributes", []) or [], rules)
        for attribute_id, value in schema.state_defaults().items():
            initial_state.setdefault(attribute_id, value)
        initial_state.setdefault("status", "idle")
        initial_state.setdefault("current_goal_id", "ultimate_goal")
        initial_state.setdefault("injuries", [])
        initial_state.setdefault("conditions", [])
        initial_state.setdefault("active_bt_instance", None)
        initial_state.setdefault("active_action", None)
        initial_state.setdefault("action_progress", 0.0)
        initial_state.setdefault("remaining_minutes", 0)
        initial_state.setdefault("last_platform_result", None)
        for attribute_id in schema.state_defaults():
            if attribute_id in initial_state:
                initial_state[attribute_id] = schema.clamp(attribute_id, initial_state[attribute_id])

        needs = dict(state.get("needs") or {})
        for need_id, value in schema.need_defaults().items():
            needs.setdefault(need_id, value)
        for item in rules.get("needs", []) or []:
            if isinstance(item, dict) and item.get("id"):
                needs.setdefault(str(item["id"]), float(item.get("target", 100)))
        for need_id, value in list(needs.items()):
            needs[need_id] = schema.clamp(str(need_id), value)

        capability_defs = {str(item["id"]): item for item in self.store.get_definitions("capability_def")}
        capabilities = {
            capability_id: float(definition.get("default", 0) or 0)
            if isinstance(definition.get("default", 0), (int, float)) and not isinstance(definition.get("default", 0), bool)
            else 0.0
            for capability_id, definition in capability_defs.items()
        }
        provided_capabilities = state.get("capabilities") if isinstance(state.get("capabilities"), dict) else {}
        unknown_capabilities = sorted(set(provided_capabilities) - set(capability_defs)) if capability_defs else []
        if unknown_capabilities:
            errors.append("agent.state.capabilities references unknown capabilities: " + ", ".join(unknown_capabilities))
        for capability_id in set(provided_capabilities) & set(capability_defs):
            value = provided_capabilities[capability_id]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= float(value) <= 1:
                errors.append(f"agent capability {capability_id} must be between 0 and 1")
            else:
                capabilities[capability_id] = float(value)

        initial_bt_ids: list[str] = []
        for value in state.get("initial_bt_ids") or []:
            behavior_id, matches = self._resolve_registered_definition("bt_def", value)
            if not behavior_id:
                errors.append(f"unknown or ambiguous initial behavior: {value}; matches={matches}")
            elif behavior_id not in initial_bt_ids:
                initial_bt_ids.append(behavior_id)
        if not initial_bt_ids:
            errors.append("agent.state.initial_bt_ids must include at least one existing behavior")

        faction_ids: list[str] = []
        for value in state.get("faction_ids") or []:
            faction_id = self.targets.canonical_id(str(value))
            if not faction_id:
                errors.append(f"unknown faction: {value}")
                continue
            try:
                faction = self.store.get_entity(faction_id)
                if str(faction.get("entity_type")) != "faction":
                    errors.append(f"{value} is not a faction")
                else:
                    faction_ids.append(faction_id)
            except KeyError:
                errors.append(f"unknown faction: {value}")

        normalized = {
            **state,
            "id": entity_id,
            "name": name,
            "role": role,
            "ultimate_goal": goal,
            "traits": dict(state.get("traits") or {}),
            "beliefs": list(state.get("beliefs") or []),
            "needs": needs,
            "capabilities": capabilities,
            "initial_bt_ids": initial_bt_ids,
            "unlocked_bt_ids": [],
            "inventory": dict(state.get("inventory") or {}),
            "permissions": dict(state.get("permissions") or {}),
            "faction_ids": faction_ids,
            "combat_stats": dict(state.get("combat_stats") or {}),
            "initial_state": initial_state,
            "story_goals": list(state.get("story_goals") or []),
            "knowledge_seed": list(state.get("knowledge_seed") or []),
        }
        return normalized, errors

    def submit_entity_proposal(self, actor_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        intent_id = str(uuid4())
        entity_id = str(proposal.get("entity_id") or f"entity_{uuid4().hex[:10]}")
        data = {**proposal, "entity_id": entity_id, "actor_id": actor_id}
        errors = []
        if not proposal.get("entity_type"):
            errors.append("entity_type is required")
        if not proposal.get("name"):
            errors.append("name is required")
        proposed_name = str(proposal.get("name") or "").strip()
        if proposed_name:
            if len(proposed_name) > 120 or "\n" in proposed_name or "\r" in proposed_name or "<" in proposed_name or ">" in proposed_name:
                errors.append("name must be a short visible label without markup or embedded parameter text")
            name_key = "".join(proposed_name.casefold().split())
            duplicate = self.store.connection.execute(
                "SELECT id,entity_type,name FROM entity WHERE name IS NOT NULL"
            ).fetchall()
            existing = next(
                (row for row in duplicate if "".join(str(row["name"]).casefold().split()) == name_key),
                None,
            )
            if existing is not None:
                errors.append(
                    f"an entity with visible name {existing['name']} already exists "
                    f"({existing['entity_type']}:{existing['id']}); use it instead of creating a duplicate"
                )
        if actor_id == "god":
            rules = self.store.get_meta("rules", {}) or {}
            resource_types = {str(item) for item in (rules.get("resource_entity_types", ["resource", "material"]) or [])}
            entity_type = str(proposal.get("entity_type", ""))
            state = proposal.get("state") if isinstance(proposal.get("state"), dict) else {}
            quantity = state.get("quantity", state.get("amount"))
            if entity_type in resource_types and isinstance(quantity, (int, float)) and float(quantity) > 0:
                errors.append("God cannot create a stocked resource directly; a behavior result must create or recover the supply")
        if proposal.get("location_id"):
            try:
                self.store.get_entity(str(proposal["location_id"]))
            except KeyError:
                errors.append("location_id does not exist")
        proposed_state = proposal.get("state") if isinstance(proposal.get("state"), dict) else {}
        research_values = proposed_state.get("research_discoveries")
        if research_values is None:
            research_values = proposed_state.get("research_discovery")
        if str(proposal.get("entity_type") or "") == "research_source" and research_values in (None, [], {}):
            errors.append("a research_source must declare at least one research_discovery")
        if research_values is not None:
            stages = [research_values] if isinstance(research_values, dict) else research_values if isinstance(research_values, list) else []
            if not stages:
                errors.append("research_discoveries must be a non-empty list or object")
            for index, stage in enumerate(stages):
                if isinstance(stage, str):
                    continue
                if not isinstance(stage, dict) or not str(stage.get("id") or stage.get("fact_key") or stage.get("fact") or "").strip():
                    errors.append(f"research_discoveries[{index}] must declare an id or fact")
        if "research_required" in proposed_state and (
            not isinstance(proposed_state.get("research_required"), (int, float))
            or isinstance(proposed_state.get("research_required"), bool)
            or float(proposed_state.get("research_required", 0) or 0) < 1
        ):
            errors.append("research_required must be a positive number")
        research_values_present = proposed_state.get("research_discoveries") is not None or proposed_state.get("research_discovery") is not None
        if research_values_present and not proposed_state.get("research_assignees"):
            errors.append("a dynamic research source must declare at least one research_assignee")
        if research_values_present:
            duplicate_research = self._research_duplicate(proposed_state, proposal.get("description"), proposal.get("location_id") or proposed_state.get("location_id"))
            if duplicate_research:
                errors.append(
                    f"research source repeats existing evidence ({duplicate_research['name']}): {duplicate_research['reason']}; "
                    "submit a genuinely different research question or wait"
                )
        research_assignees, assignee_errors = self._resolve_research_assignees(proposed_state)
        errors.extend(assignee_errors)
        try:
            self.store.get_entity(entity_id)
            errors.append("entity_id already exists")
        except KeyError:
            pass
        if errors:
            if any("visible name" in error and "duplicate" in error for error in errors):
                failed_check = "entity_name_duplicate"
            elif any("research source repeats existing evidence" in error for error in errors):
                failed_check = "research_novelty_duplicate"
            else:
                failed_check = "god_resource_creation" if any("stocked resource" in error for error in errors) else "entity_schema"
            return self._reject_intent(intent_id, actor_id, "propose_entity", data, {"status": "rejected", "message": "Entity proposal failed validation.", "failed_check": failed_check, "errors": errors, "retryable": True})
        world_minute = int(self.store.get_meta("world_minute", 0))
        # Entity proposals use one compact ``state`` object at the Agent
        # boundary.  Location topology is the one structural exception: the
        # navigation engine reads routes/tags/public/safe from the location
        # definition itself, while ``initial_state`` is reserved for mutable
        # state such as a location condition.  Promote only the configured
        # structural fields so a God-created settlement is immediately
        # navigable without adding a world-specific branch.
        proposed_state = dict(proposal.get("state") or {})
        entity_type = str(proposal.get("entity_type") or "")
        if proposed_state.get("research_discoveries") is not None or proposed_state.get("research_discovery") is not None:
            focus, _ = self._research_focus(proposed_state, proposal.get("description"))
            if focus:
                proposed_state.setdefault("research_focus", focus)
        entity_data = {"description": proposal.get("description", ""), "initial_state": proposed_state}
        agent_state: dict[str, Any] | None = None
        if entity_type == "agent":
            agent_state, agent_errors = self._normalize_god_agent_state(entity_id, proposal)
            if agent_errors:
                return self._reject_intent(intent_id, actor_id, "propose_entity", data, {"status": "rejected", "message": "Agent proposal failed validation.", "failed_check": "agent_schema", "errors": agent_errors, "retryable": True})
        elif entity_type == "location":
            for field in ("routes", "tags", "public", "safe", "discoveries", "map_position", "region_id"):
                if field in proposed_state:
                    entity_data[field] = proposed_state.pop(field)
            entity_data["initial_state"] = proposed_state
        if proposal.get("location_id"):
            entity_data["initial_state"]["location_id"] = proposal["location_id"]
        try:
            with self.store.transaction():
                self.store.create_intent(intent_id, actor_id, "propose_entity", data, world_minute)
                if agent_state is not None:
                    self.store.put_agent(entity_id, agent_state, str(proposal["name"]))
                    for knowledge in agent_state.get("knowledge_seed", []):
                        if not isinstance(knowledge, dict):
                            continue
                        fact_key = str(knowledge.get("id") or knowledge.get("fact_key") or "").strip()
                        if fact_key:
                            self.store.put_knowledge_fact(entity_id, fact_key, {
                                "fact": str(knowledge.get("fact") or knowledge.get("summary") or fact_key),
                                "source_type": str(knowledge.get("source_type") or "god_created"),
                                "confidence": float(knowledge.get("confidence", 1.0)),
                                "claim_status": str(knowledge.get("claim_status") or "observed"),
                            })
                else:
                    self.store.put_entity(entity_id, entity_type, str(proposal["name"]), entity_data)
                    if research_values_present:
                        focus, _ = self._research_focus(proposed_state, proposal.get("description"))
                        gap_key = str(proposed_state.get("research_novelty_key") or proposed_state.get("research_gap_key") or f"source:{entity_id}")
                        question = str(proposed_state.get("research_question") or proposed_state.get("open_question") or focus or proposal.get("description") or proposal.get("name"))
                        self.store.upsert_knowledge_gap(
                            gap_key,
                            "active",
                            gap_key,
                            question,
                            source_id=entity_id,
                            data={"source_name": str(proposal.get("name") or entity_id), "actor_id": str(actor_id)},
                            world_minute=world_minute,
                        )
                    if research_assignees:
                        # Judge reachability with the assignees' own views of
                        # the world that already contains the new source.
                        problems = self._unreachable_research_assignees(proposed_name, research_assignees)
                        if problems:
                            raise _UnreachableProposal(problems)
                event_id = self.store.append_event("entity.created", actor_id, world_minute, {"intent_id": intent_id, "entity_id": entity_id, "entity_type": entity_type, "summary": proposal.get("description", "")})
                self._wake_configured_evolution_consumers(event_id, "entity.created", actor_id, world_minute)
                if actor_id == "god":
                    remember_role(self.store, "god", "world_entity", {
                        "summary": f"Created {entity_type}: {proposal.get('name', entity_id)}.",
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "status": "accepted",
                    }, memory_key=f"god:entity:{intent_id}", source_event_id=event_id)
                result = {"status": "accepted", "intent_id": intent_id, "entity_id": entity_id, "event_id": event_id, "applied": True}
                self.store.finish_intent(intent_id, "accepted", result)
        except _UnreachableProposal as exc:
            # The transaction has rolled back: nothing of the proposal exists.
            return self._reject_intent(intent_id, actor_id, "propose_entity", data, {
                "status": "rejected",
                "message": "The proposed research source would be unreachable for its assignees; nothing was created.",
                "failed_check": "entity_unreachable",
                "errors": exc.errors,
                "retryable": True,
            })
        return result

    def submit_story_beat(
        self,
        actor_id: str,
        proposal: dict[str, Any],
        *,
        source_event_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Activate one configured story beat after Platform checks its evidence."""

        intent_id = str(uuid4())
        proposal = dict(proposal or {})

        def visible_label(value: Any) -> str:
            text = str(value or "").strip()
            # Models sometimes serialize a visible name with one extra pair
            # of quotes inside the JSON string. Remove only wrapping quotes;
            # exact Platform name resolution still decides whether it exists.
            for _ in range(2):
                if len(text) >= 2 and text[0] == text[-1] and text[0] in {"\"", "'"}:
                    text = text[1:-1].strip()
                else:
                    break
            return text

        for key in ("beat", "summary", "fact", "goal_hint", "research_source", "reason"):
            if key in proposal and isinstance(proposal[key], str):
                proposal[key] = visible_label(proposal[key])
        if isinstance(proposal.get("audience"), list):
            proposal["audience"] = [visible_label(item) for item in proposal["audience"]]
        if isinstance(proposal.get("unlock_choices"), list):
            proposal["unlock_choices"] = [visible_label(item) for item in proposal["unlock_choices"]]
        story_root = self.store.get_meta("story", {}) or {}
        story_root = story_root.get("story", story_root) if isinstance(story_root, dict) else {}
        beats = list(story_root.get("beats", []) or []) if isinstance(story_root, dict) else []
        state = dict(self.store.get_meta("story_state", {}) or {})
        recent = self.store.recent_events(limit=500)
        bound_sources = self.store.events_by_ids([int(item) for item in (source_event_ids or []) if int(item) > 0])
        configured_source_types = {str(item) for item in (story_root.get("emergent_source_event_types") or [])}
        source_event = bound_sources[-1] if bound_sources else next(
            (
                item for item in recent[:30]
                if bool(item.get("story_source"))
                or str(item.get("event_type", "")) in configured_source_types
            ),
            None,
        )
        observed_events = {str(item.get("event_type", "")) for item in recent}
        fact_rows = self.store.connection.execute("SELECT fact_key FROM knowledge_fact").fetchall()
        observed_facts = {str(row["fact_key"]) for row in fact_rows}
        scoped_beats = eligible_story_beats(
            beats,
            state,
            source_event_type=story_evidence_event_types(source_event),
            observed_events=observed_events,
            observed_facts=observed_facts,
        )
        choice = str(proposal.get("beat", "")).strip()
        key = "".join(char for char in choice.casefold() if char.isalnum())
        all_matches = [
            beat for beat in beats if isinstance(beat, dict)
            and key == "".join(char for char in str(beat.get("agent_label") or beat.get("summary") or beat.get("id", "")).casefold() if char.isalnum())
        ]
        matches = [
            beat for beat in scoped_beats if isinstance(beat, dict)
            and key == "".join(char for char in str(beat.get("agent_label") or beat.get("summary") or beat.get("id", "")).casefold() if char.isalnum())
        ]
        emergent = False
        emergent_summary = str(proposal.get("summary") or proposal.get("fact") or "").strip()
        if not matches and not all_matches and not scoped_beats and emergent_summary:
            summary = emergent_summary
            if len(summary) < 8 or len(summary) > 500:
                return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "An emergent story beat summary must be 8 to 500 characters.", "failed_check": "story_summary", "retryable": True})
            proposal.setdefault("summary", summary)
            beat = {
                "id": f"emergent:{uuid4().hex[:12]}",
                "agent_label": choice or summary[:80],
                "summary": summary,
                "audience": list(proposal.get("audience", []) or []),
                "fact": str(proposal.get("fact", "")).strip(),
                "goal_hint": str(proposal.get("goal_hint", "")).strip(),
                "research_source": str(proposal.get("research_source", "")).strip(),
                "unlock_choices": list(proposal.get("unlock_choices", []) or []),
                "public_event": "story.emergent_beat",
            }
            # An emergent chapter must not disappear because its audience did
            # an unrelated maintenance action. If Story names follow-up
            # choices, use those visible choices as the completion boundary;
            # otherwise leave the chapter active until a later Story result
            # explicitly supersedes or closes it.
            if beat["unlock_choices"]:
                beat["followup_behaviors"] = list(beat["unlock_choices"])
            matches = [beat]
            emergent = True
        if len(matches) != 1:
            return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {
                "status": "rejected", "message": "Choose one exact configured story beat from the current story view.", "failed_check": "story_beat_choice", "retryable": True,
                "available_choices": [str(beat.get("agent_label") or beat.get("summary") or beat.get("id", "")) for beat in scoped_beats if isinstance(beat, dict)],
            })
        beat = matches[0]
        beat_id = str(beat.get("id", ""))
        current = state.get(beat_id) if isinstance(state.get(beat_id), dict) else {}
        if current.get("status") in {"active", "completed"}:
            return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "This story beat is already active or completed.", "failed_check": "story_beat_state", "retryable": False})
        if emergent and source_event is None:
            return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "An emergent beat needs a recent discovery, research, recovery, or relationship result.", "failed_check": "story_evidence", "retryable": True})
        if emergent and source_event:
            prior_emergent = list(state.get("emergent_beats", []) or [])
            if any(int(item.get("source_event_id", -1)) == int(source_event.get("event_id", -2)) for item in prior_emergent if isinstance(item, dict)):
                return {"status": "wait", "reason": "This validated result already has an active emergent story beat; wait for a new discovery or relationship result."}
            beat["source_event_id"] = int(source_event.get("event_id"))
            beat["source_event_type"] = str(source_event.get("event_type") or "validated.result")
            beat["source_event_source_id"] = str(source_event.get("source_id") or "")
            beat["source_event_stage_id"] = str(source_event.get("stage_id") or "")
            # A new event does not automatically make a new story beat useful.
            # Repeated water-safety or pact-stability outcomes often arrive as
            # separate validated events but carry the same fact. Keep one
            # durable narrative thread until the fact itself changes; the
            # source event remains in the audit log for causal analysis.
            def story_signature(value: Any) -> str:
                return "".join(char for char in str(value or "").casefold() if char.isalnum())

            candidate_signature = story_signature(beat.get("fact") or beat.get("summary"))
            if len(candidate_signature) >= 16 and any(
                story_signature(item.get("fact") or item.get("summary")) == candidate_signature
                for item in prior_emergent
                if isinstance(item, dict)
            ):
                return {"status": "wait", "reason": "A materially identical validated result already has a story beat; wait for new evidence or a changed result."}
            # An emergent beat may interpret a validated result, but it cannot
            # silently invent a later delivery, public reading, gathering, or
            # movement. Require a matching event in the same chain before
            # allowing those claims into persistent story state.
            narrative = " ".join(str(proposal.get(key, "")) for key in ("summary", "fact", "goal_hint"))
            source_text = " ".join(
                str(source_event.get(key, ""))
                for key in ("event_type", "summary", "fact", "result", "actor_id", "location")
            )
            narrative_numbers = set(re.findall(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?", narrative))
            source_numbers = set(re.findall(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?", source_text))
            if not narrative_numbers <= source_numbers:
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_story_beat",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "An emergent beat adds a numeric detail absent from the validated source event. State only the source result or wait for a confirming observation.",
                        "failed_check": "story_unverified_numeric_detail",
                        "retryable": True,
                    },
                )
            claim_guard = story_root.get("narrative_claim_guard", {}) or {}
            action_claims = tuple(str(item).casefold() for item in (claim_guard.get("terms") or []))
            if any(term in narrative.casefold() for term in action_claims):
                source_actor = str(source_event.get("actor_id") or "")
                source_event_id = int(source_event.get("event_id", 0) or 0)
                followup_event_types = {str(item) for item in (claim_guard.get("followup_event_types") or [])}
                followup_behavior_tags = {str(item) for item in (claim_guard.get("followup_behavior_tags") or [])}
                behaviors = {str(item.get("id")): item for item in self.store.get_definitions("bt_def")}

                def supports_claim(item: dict[str, Any]) -> bool:
                    if str(item.get("event_type", "")) in followup_event_types:
                        return True
                    if str(item.get("event_type", "")) != "behavior.completed":
                        return False
                    behavior = behaviors.get(str(item.get("behavior_id", "")), {})
                    tags = {
                        str(value)
                        for key in ("tags", "goal_tags", "drive_tags")
                        for value in (behavior.get(key) or [])
                    }
                    return bool(tags & followup_behavior_tags)

                followup = next(
                    (
                        item for item in recent
                        if int(item.get("event_id", 0) or 0) > source_event_id
                        and str(item.get("actor_id") or "") == source_actor
                        and supports_claim(item)
                    ),
                    None,
                )
                if followup is None:
                    return self._reject_intent(
                        intent_id,
                        actor_id,
                        "propose_story_beat",
                        proposal,
                        {
                            "status": "rejected",
                            "message": "This emergent beat claims a later delivery, meeting, reading, or movement that is not present in a validated follow-up event. State only the source fact or wait until the action occurs.",
                            "failed_check": "story_unverified_action_claim",
                            "retryable": True,
                        },
                    )
        requested_research_source = str(proposal.get("research_source") or "").strip()
        if requested_research_source and source_event:
            requested_source_id = str(self.targets.canonical_id(requested_research_source) or "")
            if not requested_source_id or not self.store.research_discoveries(requested_source_id):
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_story_beat",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "research_source must be an exact visible configured source with research stages.",
                        "failed_check": "story_research_source",
                        "retryable": True,
                    },
                )
            source_event_id = str(source_event.get("source_id") or "")
            source_event_type = str(source_event.get("event_type") or "")
            # A research result already names the source that produced it.
            # Story may assign a source for a non-research event, but it may
            # not retell one research result as if it came from another
            # object. This is a causal check, not a vocabulary check.
            if source_event_type == "research.completed" and (not source_event_id or requested_source_id != source_event_id):
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_story_beat",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "The assigned research source must match the source named by the validated research result.",
                        "failed_check": "story_research_source_evidence",
                        "retryable": True,
                    },
                )
        trigger_events = {str(item) for item in (beat.get("trigger_events", []) or [])}
        if trigger_events and not trigger_events & observed_events:
            return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "The configured story beat has no observed trigger event yet.", "failed_check": "story_trigger", "retryable": True})
        required_facts = {str(item) for item in (beat.get("trigger_facts", []) or [])}
        if required_facts:
            if not required_facts <= observed_facts:
                return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "The configured story beat is missing one or more required facts.", "failed_check": "story_facts", "retryable": True, "missing_facts": sorted(required_facts - observed_facts)})
        minute = int(self.store.get_meta("world_minute", 0))
        raw_audience = [str(item) for item in (beat.get("audience", []) or [])]
        resolved_audience: list[str] = []
        for value in raw_audience[:8]:
            canonical = self.targets.canonical_id(value)
            if canonical and self.store.connection.execute("SELECT 1 FROM agent_state WHERE id=?", (canonical,)).fetchone():
                resolved_audience.append(canonical)
        if raw_audience and not resolved_audience:
            return self._reject_intent(intent_id, actor_id, "propose_story_beat", proposal, {"status": "rejected", "message": "An emergent beat needs at least one exact visible character audience.", "failed_check": "story_audience", "retryable": True})
        audience = resolved_audience
        if emergent:
            beat["audience"] = list(audience)
        research_source_id = ""
        requested_source = str(proposal.get("research_source") or beat.get("research_source") or "").strip()
        if requested_source:
            research_source_id = str(self.targets.canonical_id(requested_source) or "")
            if not research_source_id or not self.store.research_discoveries(research_source_id):
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_story_beat",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "research_source must be an exact visible configured source with research stages.",
                        "failed_check": "story_research_source",
                        "retryable": True,
                    },
                )
            beat["research_source_id"] = research_source_id
        registered = {item["id"] for item in self.store.get_definitions("bt_def")}
        labels = {
            "".join(char for char in str(item.get("agent_label") or item.get("summary") or item.get("id", "")).casefold() if char.isalnum()): str(item.get("id"))
            for item in self.store.get_definitions("bt_def")
        }
        requested_unlocks = [str(value) for value in (beat.get("unlock_choices", []) or [])]
        unresolved_unlocks = [
            value for value in requested_unlocks
            if "".join(char for char in value.casefold() if char.isalnum()) not in labels
        ]
        if unresolved_unlocks:
            return self._reject_intent(
                intent_id,
                actor_id,
                "propose_story_beat",
                proposal,
                {
                    "status": "rejected",
                    "message": "Every follow-up must be an exact visible behavior already registered in the audience catalog.",
                    "failed_check": "story_followup_choice",
                    "retryable": True,
                    "invalid_choices": unresolved_unlocks,
                },
            )
        beat["unlock_behavior_ids"] = [
            labels["".join(char for char in str(value).casefold() if char.isalnum())]
            for value in beat.get("unlock_choices", [])
            if "".join(char for char in str(value).casefold() if char.isalnum()) in labels
        ]
        if audience and beat.get("unlock_behavior_ids"):
            # A narrative continuation is useful only when at least one named
            # audience member can actually start that behavior now.  Checking
            # the same executable catalog exposed to NPC decisions prevents a
            # Story beat from assigning research, crafting, or institution
            # work to a character who has no such behavior or lacks its
            # prerequisites.
            unavailable_followups: list[dict[str, Any]] = []
            for behavior_id in beat["unlock_behavior_ids"]:
                executable_for = []
                for audience_id in audience:
                    candidate = self.view.build_decision_view(audience_id, role="agent_decision", include_diagnostics=True)
                    if any(str(item.get("ref")) == f"behavior:{behavior_id}" for item in candidate.get("executable_behaviors", [])):
                        executable_for.append(audience_id)
                if not executable_for:
                    unavailable_followups.append({
                        "behavior": next((str(value) for value in requested_unlocks if labels.get("".join(char for char in value.casefold() if char.isalnum())) == behavior_id), behavior_id),
                        "audience": list(audience),
                    })
            if unavailable_followups:
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_story_beat",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "Every story follow-up must be executable by at least one audience member now.",
                        "failed_check": "story_followup_executable",
                        "retryable": True,
                        "unavailable_followups": unavailable_followups,
                    },
                )
        if emergent and source_event:
            candidate_followup_values = tuple(sorted(str(item) for item in (beat.get("unlock_behavior_ids") or [])))
            candidate_followups = candidate_followup_values or ("<no-explicit-followup>",)
            def narrative_signature(value: Any) -> str:
                return "".join(char for char in str(value or "") if not char.isdigit() and char.isalnum()).casefold()
            candidate_content = "" if candidate_followup_values else narrative_signature(
                " ".join(str(beat.get(key) or "") for key in ("summary", "fact", "goal_hint"))
            )
            source_type = str(source_event.get("event_type") or "")
            source_id = str(source_event.get("source_id") or "")
            stage_id = str(source_event.get("stage_id") or "")
            audience_key = tuple(sorted(str(item) for item in audience))
            for prior in state.get("emergent_beats", []) or []:
                if not isinstance(prior, dict):
                    continue
                if (
                    str(prior.get("source_event_type") or "") == source_type
                    and str(prior.get("source_event_source_id") or "") == source_id
                    and str(prior.get("source_event_stage_id") or "") == stage_id
                    and tuple(sorted(str(item) for item in (prior.get("audience") or []))) == audience_key
                    and (tuple(sorted(str(item) for item in (prior.get("unlock_behavior_ids") or []))) or ("<no-explicit-followup>",)) == candidate_followups
                    and (candidate_followup_values or narrative_signature(" ".join(str(prior.get(key) or "") for key in ("summary", "fact", "goal_hint"))) == candidate_content)
                ):
                    return {"status": "wait", "reason": "This validated result already has the same audience and continuation; wait for a different outcome, stage, or executable follow-up."}
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, "propose_story_beat", proposal, minute)
            state[beat_id] = {"status": "active", "started_minute": minute, "actor_id": actor_id, "audience": list(audience)}
            if research_source_id:
                state[beat_id]["research_source_id"] = research_source_id
            if emergent:
                emergent_beats = list(state.get("emergent_beats", []) or [])
                emergent_beats.append({key: value for key, value in beat.items() if key != "unlock_choices"})
                state["emergent_beats"] = emergent_beats[-64:]
            self.store.set_meta("story_state", state)
            for audience_id in audience:
                try:
                    target = self.store.get_agent(audience_id)
                except KeyError:
                    continue
                goal_hint = str(beat.get("goal_hint") or proposal.get("goal_hint") or "").strip()
                if goal_hint:
                    goals = list(target.get("story_goals", []) or [])
                    if goal_hint not in goals:
                        goals.append(goal_hint)
                    target["story_goals"] = goals[-8:]
                for behavior_id in beat.get("unlock_behavior_ids", []) or []:
                    behavior_id = str(behavior_id)
                    if behavior_id in registered:
                        unlocked = list(target.get("unlocked_bt_ids", []) or [])
                        if behavior_id not in unlocked and behavior_id not in (target.get("initial_bt_ids", []) or []):
                            unlocked.append(behavior_id)
                        target["unlocked_bt_ids"] = unlocked
                if research_source_id:
                    targets = list(target.get("research_targets", []) or [])
                    if research_source_id not in targets:
                        targets.append(research_source_id)
                    target["research_targets"] = targets[-16:]
                fact = str(beat.get("fact") or proposal.get("fact") or "").strip()
                if not fact and not emergent:
                    # Behavior entry conditions may depend on the public
                    # ``story:<beat_id>`` key.  A configured beat must grant it
                    # even when the model supplies no separate fact text.
                    fact = str(beat.get("summary") or beat.get("agent_label") or "").strip()
                if fact:
                    self.store.merge_knowledge_fact(target["id"], f"story:{beat_id}", {"fact": fact, "source_type": "story", "source_id": beat_id, "confidence": 0.9, "observed_at": minute})
                self.store.update_agent(target["id"], target)
            event_id = self.store.append_event("story.beat.activated", actor_id, minute, {"beat_id": beat_id, "summary": beat.get("summary", ""), "audience": audience, "research_source_id": research_source_id, "public_event": beat.get("public_event"), "reason": proposal.get("reason", "")})
            remember_role(self.store, "story", "story_thread", {
                "summary": str(beat.get("summary") or beat.get("fact") or ""),
                "beat_id": beat_id,
                "goal_hint": str(beat.get("goal_hint") or proposal.get("goal_hint") or ""),
                "audience": list(audience),
                "source_event_type": str((source_event or {}).get("event_type") or ""),
                "status": "active",
            }, memory_key=f"story:beat:{beat_id}:{source_event.get('event_id') if isinstance(source_event, dict) else event_id}", source_event_id=int((source_event or {}).get("event_id") or event_id))
            self.store.finish_intent(intent_id, "accepted", {"status": "accepted", "intent_id": intent_id, "beat": beat.get("agent_label") or beat_id, "event_id": event_id, "activated": True})
        return {"status": "accepted", "intent_id": intent_id, "beat": beat.get("agent_label") or beat_id, "event_id": event_id, "activated": True}

    def submit_world_change(self, actor_id: str, proposal: dict[str, Any], *, origin_role: str | None = None) -> dict[str, Any]:
        intent_id = str(uuid4())
        if not proposal.get("change_type") or not proposal.get("summary"):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "change_type and summary are required.", "retryable": True})
        change_type = str(proposal["change_type"])
        if change_type not in {"world_property", "rule", "relationship", "faction", "event"}:
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "Unsupported world change type.", "failed_check": "change_type", "retryable": True})
        if change_type in {"world_property", "event"} and proposal.get("property"):
            current_properties = self.store.get_meta("world_properties", {}) or {}
            proposal = {**proposal, "property": self._canonical_world_property(str(proposal["property"]), current_properties)}
            property_id = str(proposal["property"])
            if actor_id == "god" and not self._god_property_change_allowed(property_id):
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_world_change",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "God cannot directly change a world property. Submit a rule, entity, faction, or narrative proposal; world behavior must produce the state change.",
                        "failed_check": "god_direct_property_change",
                        "retryable": True,
                    },
                )
        if change_type == "world_property" and not proposal.get("property"):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "property is required for a world_property change.", "failed_check": "property", "retryable": True})
        if change_type == "rule" and not isinstance(proposal.get("rule"), dict):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "rule object is required for a rule change.", "failed_check": "rule", "retryable": True})
        if change_type == "rule" and isinstance(proposal.get("rule"), dict):
            valid, message = self.rule_compiler.validate(dict(proposal["rule"]))
            if not valid:
                return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": message, "failed_check": "rule_schema", "retryable": True})
            conditions_valid, conditions_message = self.rule_compiler.validate_declared_conditions(
                dict(proposal["rule"]), list(proposal.get("conditions") or [])
            )
            if not conditions_valid:
                return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": conditions_message, "failed_check": "rule_condition_semantics", "retryable": True})
            declared_valid, declared_message = self.rule_compiler.validate_declared_effects(
                dict(proposal["rule"]),
                list(proposal.get("effects") or []) + list(proposal.get("expected_effects") or []),
            )
            if not declared_valid:
                return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": declared_message, "failed_check": "rule_effect_semantics", "retryable": True})
            proposed_id = str(proposal["rule"].get("id") or proposal["rule"].get("name") or "").strip()
            compiled_rule = self.rule_compiler.compile(dict(proposal["rule"]))
            wake_roles = {str(item).strip() for item in (compiled_rule.get("wake") or []) if str(item).strip()}
            if origin_role and str(origin_role) in wake_roles:
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_world_change",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "A dynamic signal cannot wake the same role that proposed it. Use a different consumer, an executable follow-up, or wait for new evidence.",
                        "failed_check": "rule_self_wake",
                        "retryable": True,
                    },
                )
            existing_rules = (self.store.get_meta("rules", {}) or {}).get("world_rules", []) or []
            signal_conflict = self.rule_compiler.signal_conflict(dict(proposal["rule"]), existing_rules)
            if signal_conflict:
                conflict_type, conflict_name = signal_conflict
                if conflict_type == "cycle":
                    message = "A signal rule cannot wake the same consumer from another signal rule. Use the behavior result as the next state change, or wait for a new world event."
                    failed_check = "rule_signal_cycle"
                else:
                    message = f"A signal for this event and follow-up already exists ({conflict_name}); review its outcome instead of adding another signal."
                    failed_check = "rule_duplicate"
                return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": message, "failed_check": failed_check, "retryable": True})
            if proposed_id and any(str(item.get("id") or item.get("name") or "").strip() == proposed_id for item in existing_rules if isinstance(item, dict)):
                return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "A world rule with this name is already registered; review its existing outcome instead of adding a duplicate.", "failed_check": "rule_duplicate", "retryable": True})
        if change_type == "relationship" and not isinstance(proposal.get("relationship"), dict):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "relationship object is required for a relationship change.", "failed_check": "relationship", "retryable": True})
        if change_type == "faction" and not isinstance(proposal.get("faction"), dict):
            return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {"status": "rejected", "message": "faction object is required for a faction change.", "failed_check": "faction", "retryable": True})
        if change_type == "event" and not proposal.get("property"):
            structured_mutation_types = {"state_delta", "need_delta", "attribute_delta", "attribute_set", "inventory_add", "inventory_remove", "world_property_delta", "relationship_delta", "entity_state_set"}
            structured_mutation = [item.get("type") or item.get("op") for item in (proposal.get("effects") or []) if isinstance(item, dict) and str(item.get("type") or item.get("op") or "") in structured_mutation_types]
            if structured_mutation:
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_world_change",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "Event effects must be narrative only: remove typed state-mutating effects. Use a world_property, relationship, faction, or rule proposal for structured change, or let a behavior outcome change state.",
                        "failed_check": "event_effects",
                        "retryable": True,
                    },
                )
        if change_type in {"world_property", "event"} and proposal.get("property"):
            property_name = str(proposal["property"])
            current_properties = self.store.get_meta("world_properties", {}) or {}
            before_value = current_properties.get(property_name)
            value = proposal.get("value")
            if isinstance(before_value, bool):
                if not isinstance(value, bool):
                    return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {
                        "status": "rejected", "message": f"World property {property_name} is boolean; value must be true or false.", "failed_check": "property_type", "retryable": True,
                    })
            elif isinstance(before_value, (int, float)):
                if isinstance(value, bool):
                    return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {
                        "status": "rejected", "message": f"World property {property_name} is numeric; value must be a number.", "failed_check": "property_type", "retryable": True,
                    })
                if isinstance(value, str):
                    try:
                        value = float(value.strip())
                    except ValueError:
                        return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {
                            "status": "rejected", "message": f"World property {property_name} is numeric; value must be a number.", "failed_check": "property_type", "retryable": True,
                        })
                elif not isinstance(value, (int, float)):
                    return self._reject_intent(intent_id, actor_id, "propose_world_change", proposal, {
                        "status": "rejected", "message": f"World property {property_name} is numeric; value must be a number.", "failed_check": "property_type", "retryable": True,
                    })
                if isinstance(before_value, float):
                    value = float(value)
                elif isinstance(value, float) and value.is_integer():
                    value = int(value)
            proposal = {**proposal, "value": value}
            if change_type in {"world_property", "event"} and property_name in current_properties and current_properties.get(property_name) == value:
                return self._reject_intent(
                    intent_id,
                    actor_id,
                    "propose_world_change",
                    proposal,
                    {
                        "status": "rejected",
                        "message": "The proposed world property already has this value; submit a real state change or a narrative event without a property.",
                        "failed_check": "no_state_change",
                        "retryable": True,
                    },
                )
        world_minute = int(self.store.get_meta("world_minute", 0))
        evaluation_window = max(1, min(int(proposal.get("evaluation_window_minutes", 1440) or 1440), 43200))
        evaluation_due_minute = world_minute + evaluation_window
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, "propose_world_change", proposal, world_minute)
            effect: dict[str, Any] = {}
            if change_type == "world_property":
                effect = self._set_world_property(str(proposal["property"]), proposal.get("value"))
            elif change_type == "rule":
                rules = dict(self.store.get_meta("rules", {}) or {})
                world_rules = list(rules.get("world_rules", []))
                world_rules.append(dict(proposal["rule"]))
                rules["world_rules"] = world_rules
                self.store.set_meta("rules", rules)
                self.conditions = ConditionEngine(rules)
                self.rule_compiler = self._new_rule_compiler()
                effect = {"rule_added": dict(proposal["rule"]), "rule_count": len(world_rules)}
            elif change_type == "relationship":
                relation = dict(proposal["relationship"])
                source = relation.pop("from", relation.pop("from_id", ""))
                target = relation.pop("to", relation.pop("to_id", ""))
                # God and Story use visible names at the model boundary. Keep
                # the persisted relationship graph canonical so later views
                # and effects can resolve both directions reliably.
                participants = relation.pop("participants", None)
                if (not source or not target) and isinstance(participants, list) and len(participants) >= 2:
                    source, target = participants[0], participants[1]
                source = self.targets.canonical_id(str(source)) if source else None
                target = self.targets.canonical_id(str(target)) if target else None
                relation_type = str(relation.pop("type", "related"))
                if not source or not target:
                    self.store.finish_intent(intent_id, "rejected", {"status": "rejected", "message": "relationship from and to are required.", "failed_check": "relationship", "retryable": True})
                    return {"status": "rejected", "message": "relationship from and to are required.", "failed_check": "relationship", "retryable": True}
                self.store.put_relationship(str(source), str(target), relation_type, relation)
                effect = {"relationship": {"from": str(source), "to": str(target), "type": relation_type, **relation}}
            elif change_type == "faction":
                faction = dict(proposal["faction"])
                faction_id = str(faction.pop("id", faction.pop("faction_id", "")))
                if not faction_id:
                    self.store.finish_intent(intent_id, "rejected", {"status": "rejected", "message": "faction id is required.", "failed_check": "faction.id", "retryable": True})
                    return {"status": "rejected", "message": "faction id is required.", "failed_check": "faction.id", "retryable": True}
                try:
                    current = self.store.get_entity(faction_id)
                except KeyError:
                    self.store.finish_intent(intent_id, "rejected", {"status": "rejected", "message": "faction does not exist.", "failed_check": "faction.id", "retryable": True})
                    return {"status": "rejected", "message": "faction does not exist.", "failed_check": "faction.id", "retryable": True}
                current.update(faction)
                self.store.put_entity(faction_id, "faction", current.get("name", faction_id), {key: value for key, value in current.items() if key not in {"id", "entity_type", "name"}})
                effect = {"faction_id": faction_id, "changed_fields": sorted(faction), "after": {key: current.get(key) for key in faction}}
            elif change_type == "event" and proposal.get("property"):
                effect = self._set_world_property(str(proposal["property"]), proposal.get("value"))
            else:
                effect = {"event_recorded": True, "state_changed": False}
            event_data = {
                "intent_id": intent_id,
                "change_type": change_type,
                "summary": str(proposal.get("summary", "")),
                "scope": str(proposal.get("scope", "world")),
                "conditions": list(proposal.get("conditions") or []),
                "effects": list(proposal.get("effects") or []),
                "expected_effects": list(proposal.get("expected_effects") or proposal.get("effects") or []),
                "effect": effect,
                "proposal_id": intent_id,
                "evaluation_due_minute": evaluation_due_minute,
            }
            expected = self._world_proposal_expectation(change_type, proposal, effect)
            event_data["expected_state"] = expected
            event_id = self.store.append_event(f"world.change.{change_type}", actor_id, world_minute, event_data)
            self.store.put_world_proposal(intent_id, intent_id, actor_id, change_type, "pending_evaluation", world_minute, evaluation_due_minute, {"effects": event_data["expected_effects"], "summary": event_data["summary"], "state": expected}, {"event_id": event_id, "effect": effect})
            if actor_id == "god":
                remember_role(self.store, "god", "world_proposal", {
                    "summary": str(proposal.get("summary", "")),
                    "change_type": change_type,
                    "expected_effects": list(event_data["expected_effects"]),
                    "status": "pending_evaluation",
                    "evaluation_due_minute": evaluation_due_minute,
                }, memory_key=f"god:proposal:{intent_id}", source_event_id=event_id)
            result = {"status": "accepted", "intent_id": intent_id, "proposal_id": intent_id, "event_id": event_id, "evaluation_status": "pending_evaluation", "evaluation_due_minute": evaluation_due_minute, "applied": True, "applied_effects": bool(effect.get("state_changed", True)), "change_type": change_type, "effect": effect, "expected_effects": event_data["expected_effects"]}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def _world_proposal_expectation(self, change_type: str, proposal: dict[str, Any], effect: dict[str, Any]) -> dict[str, Any]:
        """Compile the small machine-checkable expectation for God review.

        Free-form prose remains an explanation for the model.  Evaluation
        uses typed fields or a configured follow-up event, so unrelated events
        cannot make a proposal look successful merely because something
        happened during its observation window.
        """

        expected: dict[str, Any] = {"change_type": str(change_type), "mode": "signal_only"}
        if change_type in {"world_property", "event"} and proposal.get("property"):
            expected.update({"mode": "property_value", "property": str(proposal["property"]), "target_value": proposal.get("value")})
        elif change_type == "rule":
            rule = proposal.get("rule") if isinstance(proposal.get("rule"), dict) else {}
            expected.update({
                "mode": "followup_event",
                "rule_id": str(rule.get("id") or rule.get("name") or ""),
                "followup_behaviors": list(rule.get("behavior_names") or rule.get("behavior_ids") or rule.get("action_names") or []),
            })
        elif change_type == "relationship":
            expected.update({"mode": "relationship_state", "relationship": dict(effect.get("relationship") or proposal.get("relationship") or {})})
        elif change_type == "faction":
            expected.update({"mode": "faction_state", "faction_id": effect.get("faction_id"), "fields": dict(effect.get("after") or {})})
        return expected

    @staticmethod
    def _canonical_world_property(value: str, properties: dict[str, Any]) -> str:
        """Map a readable property label to its configured storage key."""

        def normalized(text: str) -> str:
            return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", str(text).casefold())).strip("_")

        candidate = normalized(value)
        for key in properties:
            if normalized(str(key)) == candidate:
                return str(key)
        return candidate or str(value).strip()

    def _god_property_change_allowed(self, property_id: str) -> bool:
        """Return whether the package explicitly permits God to set a property.

        The default is deny. Shared resources and personal/social outcomes must
        be changed by configured behaviors; a package may opt in to a narrow
        world property only when it has a documented reason.
        """

        try:
            entity = self.store.get_entity(property_id)
        except KeyError:
            entity = None
        if entity and entity.get("entity_type") == "resource":
            return False
        policy = self.store.get_meta("rules", {}).get("world_change_policy", {}) or {}
        allowed = policy.get("god_direct_properties", []) if isinstance(policy, dict) else []
        return property_id in {str(item) for item in (allowed or [])}

    def validate_world_template(self, template: dict[str, Any]) -> dict[str, Any]:
        from ..bootstrap.service import validate_world_template

        report = validate_world_template(template)
        errors = list(report["errors"])
        return {
            "status": "ok",
            "valid": not errors,
            "errors": errors,
            "failed_check": "template" if errors else None,
            "message": "Template is valid." if not errors else "Template needs correction.",
            "retryable": bool(errors),
        }

    def submit_world_template(self, actor_id: str, template: dict[str, Any]) -> dict[str, Any]:
        validation = self.validate_world_template(template)
        if not validation["valid"]:
            return {"status": "rejected", "message": "World template failed validation.", "errors": validation["errors"], "retryable": True}
        intent_id = str(uuid4())
        world_minute = int(self.store.get_meta("world_minute", 0))
        with self.store.transaction():
            self.store.create_intent(intent_id, actor_id, "submit_world_template", {"template": template}, world_minute)
            self.store.append_event("world.template.accepted", actor_id, world_minute, {"template_keys": sorted(template)})
            result = {"status": "accepted", "intent_id": intent_id, "template_valid": True, "message": "Template accepted for new-instance bootstrap."}
            self.store.finish_intent(intent_id, "accepted", result)
        return result

    def bootstrap_world_from_template(self, template: dict[str, Any], instance_id: str, runtime_root: Path | None = None) -> dict[str, Any]:
        from ..bootstrap.service import create_instance_from_template

        validation = self.validate_world_template(template)
        if not validation["valid"]:
            return {"status": "rejected", "message": "World template failed validation.", "errors": validation["errors"], "retryable": True}
        db_path = create_instance_from_template(template, instance_id, runtime_root)
        return {"status": "accepted", "instance_id": instance_id, "db_path": str(db_path)}
