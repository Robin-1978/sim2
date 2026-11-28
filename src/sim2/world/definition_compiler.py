"""Canonical structural compiler for declarative world definitions.

This module owns the part of a definition contract that does not require a
live world: accepted definition kinds, declarative fields, basic value shapes,
Action result contracts, effect names and the normalized shorthand accepted
from a model. Platform semantic validation (references, reachability,
resource conservation and duplicate definitions) remains in
``platform.definition_validation`` because it needs the instance repositories.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Iterator

from ..dsl.expressions import ExpressionSyntaxError, parse_expression
from .action_tools import validate_tool_duration


DEFINITION_TYPES: tuple[str, ...] = (
    "attribute",
    "world_property",
    "action",
    "behavior",
    "recipe",
    "capability",
)

SCALAR_PARAMETER_TYPES = frozenset({"string", "number", "integer", "boolean", "object", "array"})

PROGRESS_EFFECT_TYPES = frozenset({
    "need_delta", "state_delta", "attribute_delta", "attribute_set", "inventory_add",
    "inventory_remove", "inventory.consume_item", "status_add", "status_remove", "location_set",
})
RECIPE_EFFECT_TYPES = frozenset({"entity_state_set", "capability.add_xp", "capability_xp"})

EFFECT_TYPES = PROGRESS_EFFECT_TYPES | frozenset(
    {
        "relationship_delta", "public_event", "world_property_delta", "world_property_set",
        "entity_state_set",
        "world_resource_delta", "resource_transfer", "resource_withdrawal", "resource_take", "catalog_action", "capability_xp", "capability.add_xp",
        "institution_allocation",
        "route_edit", "resident_controller_set",
    }
)

FORBIDDEN_DECLARATIVE_FIELDS = frozenset({"handler", "python", "script", "sql", "executor_code", "code", "_recipe_snapshot"})


def definition_slug(value: Any) -> str:
    """Return the stable machine key used for a new definition."""

    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")
    return text[:80]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_parameter_specs(parameters: Any, errors: list[str]) -> None:
    if parameters is None:
        return
    if not isinstance(parameters, dict):
        errors.append("action.parameters must be an object")
        return
    for parameter_name, parameter_spec in parameters.items():
        if isinstance(parameter_spec, str):
            if parameter_spec not in SCALAR_PARAMETER_TYPES:
                errors.append(
                    f"action.parameters.{parameter_name} must use a type name "
                    "(string, number, integer, boolean, object, array), not a placeholder"
                )
        elif isinstance(parameter_spec, dict):
            if parameter_spec.get("type") not in SCALAR_PARAMETER_TYPES:
                errors.append(f"action.parameters.{parameter_name}.type must be a supported type name")
        else:
            errors.append(f"action.parameters.{parameter_name} must declare a supported type")


def bind_legacy_inventory_effect(
    effect: dict[str, Any], declarations: dict[str, Any], parameters: dict[str, Any]
) -> dict[str, Any]:
    """Read the parameter pair found in inventory effects in older saves."""
    if (effect.get("type") or effect.get("op")) not in {"inventory_add", "inventory_remove", "inventory.consume_item"}:
        return effect
    item_ref = effect.get("item_id")
    quantity_key = effect.get("quantity_parameter")
    if (not isinstance(item_ref, str) or not item_ref.startswith("context.")
            or not isinstance(quantity_key, str) or not quantity_key
            or any(key in effect for key in ("item", "amount", "quantity"))):
        return effect
    item_key = item_ref.removeprefix("context.")
    if item_key not in declarations or quantity_key not in declarations:
        return effect
    item, quantity = parameters.get(item_key), parameters.get(quantity_key)
    if not isinstance(item, str) or not item.strip() or item.startswith("context.") or not _is_number(quantity) or quantity <= 0:
        return effect
    bound = dict(effect)
    del bound["item_id"], bound["quantity_parameter"]
    return {**bound, "item": item, "quantity": quantity}


def effect_payload_error(effect: dict[str, Any], field: str = "effects") -> str | None:
    effect_type = effect.get("type") or effect.get("op")
    if effect_type in {"inventory_add", "inventory_remove", "inventory.consume_item"}:
        quantity = effect.get("quantity", effect.get("amount", 1))
        if (not str(effect.get("item") or "").strip() or "quantity_parameter" in effect
                or not (_is_number(quantity) and quantity > 0 or isinstance(quantity, str) and quantity.startswith("context."))):
            return f"{effect_type} requires item and a positive quantity or context.<quantity parameter>"
    if effect_type == "relationship_delta" and any(key in effect for key in ("from", "to", "relationship_type")):
        return "relationship_delta changes the executing character's relation; use target_id and relation"
    if effect_type == "entity_state_set":
        entity = effect.get("entity_id") or effect.get("target_entity")
        state = effect.get("state") or effect.get("property")
        if not str(entity or "").strip() or not str(state or "").strip() or "value" not in effect:
            return "entity_state_set requires entity_id, state and value"
    if effect_type == "public_event" and not str(effect.get("event") or "").strip():
        return "public_event requires event"
    if effect_type == "route_edit":
        mode = effect.get("mode", "link")
        distance = effect.get("distance")
        if not all(isinstance(effect.get(key), str) and effect[key].strip() for key in ("from", "to")):
            return "route_edit requires from and to location references"
        if mode not in {"link", "unlink"}:
            return "route_edit mode must be link or unlink"
        if mode == "link" and not (_is_number(distance) and 0 < distance < float("inf")):
            return "route_edit link requires a finite positive distance"
    if effect_type == "resident_controller_set" and effect.get("controller") not in {"bt", "llm"}:
        return "resident_controller_set controller must be bt or llm"
    if field == "progress_effects" and effect_type not in PROGRESS_EFFECT_TYPES:
        return f"{effect_type} requires completion; declare it in effects"
    if field == "recipe.effects" and effect_type not in RECIPE_EFFECT_TYPES:
        return f"recipe.effects does not support {effect_type}; declare it on the action"
    return None


def _validate_effects(definition: dict[str, Any], errors: list[str], *fields: str) -> None:
    for field in fields:
        values = definition.get(field.rsplit(".", 1)[-1], [])
        if not isinstance(values, list):
            errors.append(f"{field} must be a list")
            continue
        for index, effect in enumerate(values):
            if not isinstance(effect, dict):
                errors.append(f"{field}[{index}] must be an object")
                continue
            effect_type = str(effect.get("type") or effect.get("op") or "")
            if effect_type not in EFFECT_TYPES:
                errors.append(f"unsupported effect type: {effect_type or '<missing>'}")
            elif error := effect_payload_error(effect, field):
                errors.append(f"{field}[{index}] {error}")


def _validate_result_contracts(definition: dict[str, Any], errors: list[str]) -> None:
    for field in ("success", "failure"):
        result = definition.get(field)
        if not isinstance(result, dict) or not str(result.get("result") or "").strip():
            errors.append(f"action.{field} must declare an object with result")


def action_conditions(definition: dict[str, Any]) -> Iterator[tuple[str, Any]]:
    preconditions = definition.get("preconditions")
    if isinstance(preconditions, list):
        for index, predicate in enumerate(preconditions):
            yield f"preconditions[{index}]", predicate
    for field in ("success", "failure"):
        outcome = definition.get(field)
        if isinstance(outcome, dict) and "condition" in outcome:
            yield f"{field}.condition", outcome["condition"]


def condition_syntax_error(predicate: Any) -> str | None:
    if not isinstance(predicate, str):
        return "condition must be a string"
    if predicate.strip():
        try:
            parse_expression(predicate)
        except ExpressionSyntaxError as exc:
            return str(exc)
    return None


def _validate_outcome_events(definition: dict[str, Any], errors: list[str]) -> None:
    outcome_events = definition.get("outcome_events")
    if outcome_events is None:
        return
    if not isinstance(outcome_events, list):
        errors.append("behavior.outcome_events must be a list")
        return
    for index, event in enumerate(outcome_events):
        if not isinstance(event, dict) or not str(event.get("event_type") or "").strip():
            errors.append(f"behavior.outcome_events[{index}] must declare event_type and summary")
        elif not str(event.get("summary") or "").strip():
            errors.append(f"behavior.outcome_events[{index}] must declare event_type and summary")


def compile_definition_shape(
    definition_type: str,
    name: str,
    raw_definition: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Normalize and structurally validate one declarative definition."""

    kind = str(definition_type or "").strip().casefold()
    definition = copy.deepcopy(raw_definition if isinstance(raw_definition, dict) else {})
    errors: list[str] = []
    if kind not in DEFINITION_TYPES:
        return definition, [f"unsupported definition_type: {kind}"]
    if not str(name or "").strip():
        errors.append("name is required")
    errors.extend(f"field is not declarative: {field}" for field in FORBIDDEN_DECLARATIVE_FIELDS if field in definition)

    if kind == "attribute":
        attr_kind = str(definition.get("kind") or "").casefold()
        if attr_kind not in {"state", "need"}:
            errors.append("attribute.kind must be state or need")
        for field in ("min", "max", "default"):
            if not _is_number(definition.get(field)):
                errors.append(f"attribute.{field} must be numeric")
        if all(_is_number(definition.get(field)) for field in ("min", "max", "default")):
            if float(definition["min"]) > float(definition["max"]):
                errors.append("attribute.min must not exceed attribute.max")
            if not float(definition["min"]) <= float(definition["default"]) <= float(definition["max"]):
                errors.append("attribute.default must be within min and max")
        if attr_kind == "state" and definition.get("cost_mode") not in {"increase", "decrease"}:
            errors.append("state attributes must declare cost_mode")
        if not str(definition.get("label") or name).strip():
            errors.append("attribute.label is required")
        return definition, errors

    if kind == "world_property":
        default = definition.get("default")
        if not (_is_number(default) or isinstance(default, (str, bool))):
            errors.append("world_property.default must be a number or string")
        return definition, errors

    if kind == "action":
        shorthand_reserve = definition.pop("reserve", None)
        if shorthand_reserve is not None:
            if not isinstance(shorthand_reserve, dict):
                errors.append("action.reserve must be an object")
            elif isinstance(costs := definition.setdefault("costs", {}), dict):
                if isinstance(reserve := costs.setdefault("reserve", {}), dict):
                    for key, value in shorthand_reserve.items():
                        reserve.setdefault(str(key), 1 if value is True else value)
        costs = definition.get("costs", {})
        if not isinstance(costs, dict):
            errors.append("action.costs must be an object")
        else:
            for section in ("reserve", "per_minute", "on_success", "on_failure", "refund"):
                if section in costs and not isinstance(costs[section], dict):
                    errors.append(f"action.costs.{section} must be an object")
            reserve = costs.get("reserve", {})
            if isinstance(reserve, dict):
                parameters = definition.get("parameters") or {}
                for key in ("item_parameter", "item_quantity_parameter"):
                    if key in reserve and (not isinstance(parameters, dict) or not isinstance(reserve[key], str)
                                           or reserve[key] not in parameters):
                        errors.append(f"action.costs.reserve.{key} must name a declared action parameter")
        _validate_parameter_specs(definition.get("parameters"), errors)
        for field in ("success", "failure"):
            if isinstance(definition.get(field), list) and len(definition[field]) == 1 and isinstance(definition[field][0], dict):
                definition[field] = definition[field][0]
        duration = definition.get("duration") if isinstance(definition.get("duration"), dict) else {}
        if not duration or not (_is_number(duration.get("minutes")) or duration.get("mode") in {"route_distance", "recipe_duration", "until_day_phase"}):
            errors.append("action.duration must declare minutes or a supported mode")
        if duration.get("mode") == "until_day_phase" and not (isinstance(duration.get("phase"), str) and duration["phase"].strip()):
            errors.append("action.duration.phase must name a configured calendar phase")
        if error := validate_tool_duration(definition):
            errors.append(error)
        if _is_number(duration.get("minutes")) and float(duration["minutes"]) <= 0:
            errors.append("action.duration.minutes must be positive")
        for field in ("preconditions", "effects", "progress_effects"):
            if field in definition and not isinstance(definition[field], list):
                errors.append(f"action.{field} must be a list")
        _validate_effects(definition, errors, "effects", "progress_effects")
        _validate_result_contracts(definition, errors)
        for field, predicate in action_conditions(definition):
            if error := condition_syntax_error(predicate):
                errors.append(f"action.{field} has invalid expression: {error}")
        return definition, errors

    if kind == "behavior":
        _validate_effects(definition, errors, "outcome_effects")
        _validate_outcome_events(definition, errors)
        if definition.get("nodes") is None and isinstance(definition.get("actions"), list):
            definition["nodes"] = [{"type": "action", "action": item} for item in definition["actions"]]
        nodes = definition.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            errors.append("behavior.nodes or behavior.actions must contain at least one step")
        elif len(nodes) > 32:
            errors.append("a behavior may contain at most 32 nodes")
        return definition, errors

    if kind == "recipe":
        _validate_effects(definition, errors, "recipe.effects")
        duration = definition.get("duration_minutes")
        if not _is_number(duration) or float(duration) <= 0:
            errors.append("recipe.duration_minutes must be positive")
        for field in ("inputs", "outputs"):
            values = definition.get(field)
            if not isinstance(values, list) or not values:
                errors.append(f"recipe.{field} must be a non-empty list")
            elif any(
                not isinstance(item, dict)
                or not item.get("item")
                or not _is_number(item.get("quantity"))
                or float(item.get("quantity", 0)) <= 0
                for item in values
            ):
                errors.append(f"recipe.{field} entries need item and positive quantity")
        return definition, errors

    default = definition.get("default", definition.get("initial", 0))
    if not _is_number(default) or not 0 <= float(default) <= 1:
        errors.append("capability.default must be between 0 and 1")
    return definition, errors

