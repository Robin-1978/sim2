"""Instance-dependent validation of a God-proposed definition.

``compile_definition_shape`` checks the structure of a definition.  The checks
here need the instance: which ids and names are taken, which visible names
resolve to registry ids, and which registered Actions and behaviors a behavior
tree refers to.  ``DefinitionService`` composes ``DefinitionValidationMixin``
with ``DefinitionRuntimeMixin``, which registers what passes.
"""

from __future__ import annotations

import copy
import re
from typing import Any
from uuid import uuid4

from ..world.definition_compiler import compile_definition_shape, definition_slug

DEFINITION_KINDS = frozenset({"attribute", "world_property", "action", "behavior", "recipe", "capability"})
# Definition kinds that live in one registry table each.
DEFINITION_TABLES = {"action": "action_def", "behavior": "bt_def", "recipe": "recipe_def", "capability": "capability_def"}


class DefinitionValidationMixin:
    """Validate and normalize God-proposed definitions against the instance."""

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

    @staticmethod
    def _definition_name_key(value: Any) -> str:
        return "".join(char for char in str(value or "").casefold() if char.isalnum())

    def resolve_registered_definition(self, table: str, value: Any, *, extra: dict[str, Any] | None = None) -> tuple[str | None, list[str]]:
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
        if kind not in DEFINITION_KINDS:
            return definition, errors
        if kind == "attribute":
            self._validate_attribute(name, definition, errors)
        elif kind == "world_property":
            self._validate_world_property(name, definition, errors)
        else:
            definition_id = self._claim_definition_id(kind, name, definition, errors)
            if kind == "action":
                self._validate_action(name, definition, definition_id, errors)
                self._check_world_resource_targets(definition, errors)
            elif kind == "behavior":
                self._validate_behavior(name, definition, definition_id, errors, extra_action)
            elif kind == "recipe":
                definition = self._validate_recipe(name, definition, definition_id, errors, extra_action)
            elif kind == "capability":
                self._validate_capability(name, definition, definition_id)
        return definition, errors

    def _validate_attribute(self, name: str, definition: dict[str, Any], errors: list[str]) -> None:
        definition.update({"id": str(definition.get("id") or definition_slug(name) or f"attribute_{uuid4().hex[:10]}"), "label": definition.get("label") or name, "prompt": definition.get("prompt") or str(name)})
        existing = self.store.repositories.metadata.get("attributes", []) or []
        if any(str(item.get("id")) == definition["id"] for item in existing if isinstance(item, dict)):
            errors.append(f"attribute already exists: {definition['id']}")
        if any(self._definition_name_key(name) == self._definition_name_key(item.get("label") or item.get("id")) for item in existing if isinstance(item, dict)):
            errors.append(f"attribute name already exists: {name}")
        if definition.get("vital") and any(item.get("vital") is True for item in existing if isinstance(item, dict)):
            errors.append("only one vital attribute may be configured")
        if definition.get("capacity") and any(item.get("capacity") is True for item in existing if isinstance(item, dict)):
            errors.append("only one capacity attribute may be configured")

    def _validate_world_property(self, name: str, definition: dict[str, Any], errors: list[str]) -> None:
        property_id = str(definition.get("id") or definition_slug(name) or f"property_{uuid4().hex[:10]}")
        if property_id in {str(key) for key in (self.store.repositories.metadata.get("world_properties", {}) or {})}:
            errors.append(f"world property already exists: {property_id}")
        if property_id not in {str(key) for key in (self.store.repositories.metadata.get("world_properties", {}) or {})} and any(self._definition_name_key(name) == self._definition_name_key(item) for item in (self.store.repositories.metadata.get("world_property_labels", {}) or {}).values()):
            errors.append(f"world property name already exists: {name}")
        definition.update({"id": property_id, "label": definition.get("label") or name, "prompt": definition.get("prompt") or str(name)})

    def _claim_definition_id(self, kind: str, name: str, definition: dict[str, Any], errors: list[str]) -> str:
        """Pick the registry id of a table-backed definition and report a taken id or name."""

        table = DEFINITION_TABLES[kind]
        explicit_id = str(definition.get("id") or "").strip()
        definition_id = explicit_id or definition_slug(name) or f"definition_{uuid4().hex[:10]}"
        if any(str(item.get("id")) == definition_id for item in self.store.repositories.entities.get_definitions(table)):
            errors.append(f"definition already exists: {definition_id}")
        if any(self._definition_name_key(name) in {self._definition_name_key(item.get(field)) for field in ("name", "label", "agent_label", "summary", "description") if item.get(field)} for item in self.store.repositories.entities.get_definitions(table)):
            errors.append(f"definition name already exists: {name}")
        return definition_id

    def _check_world_resource_targets(self, definition: dict[str, Any], errors: list[str]) -> None:
        """A world-scope stock delta must target an existing resource or material."""

        rules = self.store.repositories.metadata.get("rules", {}) or {}
        resource_types = {str(item) for item in (rules.get("resource_entity_types", ["resource", "material"]) or [])}
        for field in ("effects", "progress_effects"):
            for index, effect in enumerate(definition.get(field, []) or []):
                if not isinstance(effect, dict):
                    continue
                if str(effect.get("type") or effect.get("op") or "") != "world_resource_delta":
                    continue
                ref = str(effect.get("resource_id") or effect.get("resource") or "").strip()
                resource_id = str(self.targets.canonical_id(ref) or ref)
                try:
                    entity = self.store.repositories.entities.get_entity(resource_id)
                except KeyError:
                    errors.append(f"{field}[{index}].world_resource_delta target is not an existing resource: {ref or '<missing>'}")
                    continue
                if str(entity.get("entity_type") or "") not in resource_types:
                    errors.append(f"{field}[{index}].world_resource_delta target {ref} is not a resource or material")

    @staticmethod
    def _validate_action(name: str, definition: dict[str, Any], definition_id: str, errors: list[str]) -> None:
        _check_dynamic_inventory_parameters(definition, errors)
        _check_resource_transfers(definition, errors)
        _check_world_resource_deltas(definition, errors)
        # Keep the visible proposal name in the persisted definition.  The
        # registry id is intentionally machine-oriented, while audits,
        # AgentView and later definition references must be able to show
        # the exact human-facing label that God submitted.
        definition.update({"id": definition_id, "version": 1, "definition_version": 1, "name": definition.get("name") or name, "description": definition.get("description") or str(name), "definition_source": "god_proposal", "kind": "atomic", "executor": "effect_pipeline", "validation_status": "validated"})

    def _validate_behavior(
        self,
        name: str,
        definition: dict[str, Any],
        definition_id: str,
        errors: list[str],
        extra_action: dict[str, Any] | None,
    ) -> None:
        normalized_conditions = [self._normalize_inventory_condition(condition) for condition in definition.get("entry_conditions", []) or []]
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
        definition["nodes"] = BehaviorNodeNormalizer(self, errors, extra_action).normalize_tree(nodes)
        definition.pop("actions", None)
        definition.update({"id": definition_id, "version": 1, "definition_version": 1, "summary": definition.get("summary") or definition.get("description") or str(name), "agent_label": definition.get("agent_label") or name, "definition_source": "god_proposal", "kind": "proposed", "validation_status": "validated"})

    def _normalize_inventory_condition(self, condition: Any) -> str:
        """Resolve a visible item label inside one entry condition to its registry key.

        Conditions are machine predicates, but God often copies a visible item
        label from the world view.  Resolve only existing
        resource/material/object labels; unknown inventory keys may be newly
        produced by a bound recipe and remain as submitted.
        """

        return re.sub(r"actor\.inventory\.([^\s<>=!&|()]+)", self._normalize_inventory_key, str(condition))

    def _normalize_inventory_key(self, match: re.Match[str]) -> str:
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

    def _validate_recipe(
        self,
        name: str,
        definition: dict[str, Any],
        definition_id: str,
        errors: list[str],
        extra_action: dict[str, Any] | None,
    ) -> dict[str, Any]:
        definition = self._normalize_recipe_items(definition)
        # A recipe is only ever produced through an Action that consumes
        # recipes.  Without one the recipe can never be crafted by anybody.
        if not any(self._is_recipe_action(item) for item in self.store.repositories.entities.get_definitions("action_def")) and not self._is_recipe_action(extra_action or {}):
            errors.append(
                "no registered Action can craft a recipe (it must reserve recipe_inputs or use duration.mode recipe_duration); "
                "register such an Action and a behavior that uses it before adding recipes"
            )
        definition.update({"id": definition_id, "version": 1, "definition_version": 1, "name": definition.get("name") or name, "definition_source": "god_proposal", "validation_status": "validated"})
        return definition

    @staticmethod
    def _validate_capability(name: str, definition: dict[str, Any], definition_id: str) -> None:
        default = definition.get("default", definition.get("initial", 0))
        definition.update({"id": definition_id, "version": 1, "definition_version": 1, "name": definition.get("name") or name, "default": float(default) if isinstance(default, (int, float)) else 0.0, "value_path": definition.get("value_path") or f"agent.capabilities.{definition_id}", "definition_source": "god_proposal", "validation_status": "validated"})


def _check_dynamic_inventory_parameters(definition: dict[str, Any], errors: list[str]) -> None:
    """An outcome condition cannot read ``actor.inventory.<parameter>``: the key is only known at run time."""

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


def _check_world_resource_deltas(definition: dict[str, Any], errors: list[str]) -> None:
    """A world-scope stock delta must name one stock and a real numeric change."""

    for field in ("effects", "progress_effects"):
        for index, effect in enumerate(definition.get(field, []) or []):
            if not isinstance(effect, dict):
                continue
            if str(effect.get("type") or effect.get("op") or "") != "world_resource_delta":
                continue
            if not str(effect.get("resource_id") or effect.get("resource") or "").strip():
                errors.append(f"{field}[{index}].world_resource_delta requires resource_id")
            delta = effect.get("delta", effect.get("value"))
            if isinstance(delta, bool) or not isinstance(delta, (int, float)) or float(delta) == 0:
                errors.append(f"{field}[{index}].world_resource_delta requires a non-zero numeric delta")
            for bound in ("minimum", "maximum"):
                value = effect.get(bound)
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                    errors.append(f"{field}[{index}].world_resource_delta.{bound} must be numeric")


def _check_resource_transfers(definition: dict[str, Any], errors: list[str]) -> None:
    """A ``resource_transfer`` effect must move exactly what the Action reserved."""

    resource_transfer_effects = [
        effect
        for field in ("effects", "progress_effects")
        for effect in (definition.get(field, []) or [])
        if isinstance(effect, dict)
        and str(effect.get("type") or effect.get("op") or "") == "resource_transfer"
    ]
    if not resource_transfer_effects:
        return
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
        _check_transfer_effect(definition, reserve, item_parameter, effect, errors)


def _check_transfer_effect(
    definition: dict[str, Any],
    reserve: Any,
    item_parameter: Any,
    effect: dict[str, Any],
    errors: list[str],
) -> None:
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


class BehaviorNodeNormalizer:
    """Normalize a proposed behavior tree and record every problem in ``errors``.

    Visible names become registry ids.  One instance walks one tree: the node
    count is shared by the whole walk, which caps a behavior at 32 nodes.
    """

    MAX_NODES = 32
    MAX_DEPTH = 8
    ENTITY_PARAMETER_KEYS = frozenset({
        "target_id", "object_id", "location_id", "source_id", "area_id", "patient_id",
        "protected_target_id", "counterparty_id", "listener_id",
    })

    def __init__(self, service: Any, errors: list[str], extra_action: dict[str, Any] | None) -> None:
        self.service = service
        self.errors = errors
        self.extra_action = extra_action
        self.node_count = 0

    def normalize_tree(self, nodes: list[Any]) -> list[dict[str, Any]]:
        normalized_nodes: list[dict[str, Any]] = []
        for index, node in enumerate(nodes[: self.MAX_NODES]):
            normalized = self.normalize(node, str(index))
            if normalized is not None:
                normalized_nodes.append(normalized)
        if self.node_count > self.MAX_NODES:
            self.errors.append("a behavior may contain at most 32 nodes")
        return normalized_nodes

    def normalize(self, node: Any, path: str, depth: int = 0) -> dict[str, Any] | None:
        self.node_count += 1
        if self.node_count > self.MAX_NODES:
            return None
        if isinstance(node, str):
            node = {"type": "behavior", "behavior": node}
        if not isinstance(node, dict):
            self.errors.append(f"behavior node {path} must be an object")
            return None
        node_copy = copy.deepcopy(node)
        node_type = str(node_copy.get("type") or "action")
        if node_type == "action":
            self._normalize_action_node(node_copy, path)
        elif node_type in {"behavior", "subtree"}:
            self._normalize_behavior_reference(node_copy, path)
        elif node_type not in {"sequence", "selector", "parallel", "condition", "wait"}:
            self.errors.append(f"unsupported behavior node type: {node_type}")
        self._normalize_children(node_copy, path, depth)
        return node_copy

    def _normalize_action_node(self, node_copy: dict[str, Any], path: str) -> None:
        ref = node_copy.get("action_id") or node_copy.get("action") or node_copy.get("name")
        resolved, matches = self.service.resolve_registered_definition("action_def", ref, extra=self.extra_action)
        if not resolved:
            self.errors.append(f"unknown or ambiguous action in node {path}: {ref}; matches={matches}")
        else:
            node_copy["action_id"] = resolved
            action = (
                self.extra_action
                if self.extra_action and str(self.extra_action.get("id")) == str(resolved)
                else next((item for item in self.service.store.repositories.entities.get_definitions("action_def") if str(item.get("id")) == resolved), {})
            )
            parameters = node_copy.get("parameters") if isinstance(node_copy.get("parameters"), dict) else {}
            parameters = self._normalize_action_parameters(resolved, action, parameters)
            node_copy["parameters"] = parameters
            self._check_action_parameters(resolved, path, action, parameters)
        node_copy.pop("action", None)
        node_copy.pop("name", None)

    def _normalize_action_parameters(self, resolved: str, action: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
        """Persist canonical entity ids in a node's parameters.

        God sees visible entity names. Persist canonical IDs inside reusable
        behaviors just as normal Intent submission does; otherwise a
        valid-looking behavior can be registered and then fail every NPC
        view-time simulation because the later Action expects an ID.
        """

        normalized_parameters: dict[str, Any] = {}
        for key, value in parameters.items():
            key = str(key)
            if (
                isinstance(value, str)
                and value
                and not value.startswith("context.")
                and (key.endswith("_entity") or key in self.ENTITY_PARAMETER_KEYS)
            ):
                canonical = self.service.targets.canonical_id(value)
                if canonical:
                    parameter_spec = (action.get("parameters") or {}).get(key)
                    expected_type = str(parameter_spec.get("entity_type") or "") if isinstance(parameter_spec, dict) else ""
                    actual_type = str(self.service.store.repositories.entities.get_entity(canonical).get("entity_type") or "")
                    if expected_type and actual_type != expected_type:
                        self.errors.append(
                            f"action {resolved} parameter {key} requires a visible {expected_type}; "
                            f"{value} resolves to {actual_type} {canonical}"
                        )
                        continue
                    normalized_parameters[key] = canonical
                    continue
                visible_entities = [
                    str(dict(item).get("name") or dict(item).get("id"))
                    for item in self.service.store.connection.execute(
                        "SELECT id,name FROM entity WHERE id != 'player' ORDER BY name,id LIMIT 40"
                    ).fetchall()
                ]
                self.errors.append(
                    f"action {resolved} parameter {key} must use an exact visible entity name; "
                    f"visible examples: {', '.join(visible_entities)}"
                )
            normalized_parameters[key] = value
        return normalized_parameters

    def _check_action_parameters(self, resolved: str, path: str, action: dict[str, Any], parameters: dict[str, Any]) -> None:
        """Every node parameter must be declared by the Action, and every declared one supplied or defaulted."""

        action_parameters = action.get("parameters")
        if action_parameters is None:
            action_parameters = {}
        elif not isinstance(action_parameters, dict):
            self.errors.append(f"action {resolved} parameters must be an object")
            action_parameters = {}
        allowed_parameters = set(action_parameters.keys())
        unknown_parameters = sorted(set(parameters) - allowed_parameters)
        if unknown_parameters:
            self.errors.append(
                f"action {resolved} in node {path} has unknown parameter(s): {', '.join(unknown_parameters)}; "
                f"allowed parameters: {', '.join(sorted(allowed_parameters)) or 'none'}"
            )
        raw_defaults = action.get("parameter_defaults")
        if not isinstance(raw_defaults, dict):
            raw_defaults = {}
        parameter_defaults = set(raw_defaults.keys())
        missing_parameters = sorted(allowed_parameters - set(parameters) - parameter_defaults)
        if missing_parameters:
            self.errors.append(
                f"action {resolved} in node {path} is missing parameter(s): {', '.join(missing_parameters)}"
            )

    def _normalize_behavior_reference(self, node_copy: dict[str, Any], path: str) -> None:
        ref = node_copy.get("behavior_id") or node_copy.get("behavior") or node_copy.get("name")
        resolved, matches = self.service.resolve_registered_definition("bt_def", ref)
        if not resolved:
            self.errors.append(f"unknown or ambiguous behavior in node {path}: {ref}; matches={matches}")
        else:
            node_copy["behavior_id"] = resolved
        node_copy.pop("behavior", None)
        node_copy.pop("name", None)

    def _normalize_children(self, node_copy: dict[str, Any], path: str, depth: int) -> None:
        children = node_copy.get("children")
        if children is None:
            return
        if not isinstance(children, list):
            self.errors.append(f"behavior node {path}.children must be a list")
        elif depth >= self.MAX_DEPTH:
            self.errors.append(f"behavior node {path} exceeds maximum nesting depth")
        else:
            normalized_children = []
            for child_index, child in enumerate(children):
                normalized = self.normalize(child, f"{path}.{child_index}", depth + 1)
                if normalized is not None:
                    normalized_children.append(normalized)
            node_copy["children"] = normalized_children
