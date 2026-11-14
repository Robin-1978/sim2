"""Canonical structural compiler for declarative world definitions.

This module owns the part of a definition contract that does not require a
live world: accepted definition kinds, declarative fields, basic value shapes,
Action result contracts, effect names and the normalized shorthand accepted
from a model. Platform semantic validation (references, reachability,
resource conservation and duplicate definitions) remains in
``platform.definition_runtime`` because it needs the instance repositories.
"""

from __future__ import annotations

import copy
import re
from typing import Any


DEFINITION_TYPES: tuple[str, ...] = (
    "attribute",
    "world_property",
    "action",
    "behavior",
    "recipe",
    "capability",
)

SCALAR_PARAMETER_TYPES = frozenset({"string", "number", "integer", "boolean", "object", "array"})

EFFECT_TYPES = frozenset(
    {
        "need_delta", "state_delta", "attribute_delta", "attribute_set",
        "inventory_add", "inventory_remove", "inventory.consume_item",
        "relationship_delta", "public_event", "world_property_delta",
        "entity_state_set", "status_add", "status_remove", "location_set",
        "resource_transfer", "capability_xp", "capability.add_xp",
    }
)

FORBIDDEN_DECLARATIVE_FIELDS = frozenset({"handler", "python", "script", "sql", "executor_code", "code"})


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


def _validate_effects(definition: dict[str, Any], errors: list[str]) -> None:
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
            if effect_type not in EFFECT_TYPES:
                errors.append(f"unsupported effect type: {effect_type or '<missing>'}")


def _validate_result_contracts(definition: dict[str, Any], errors: list[str]) -> None:
    for field in ("success", "failure"):
        result = definition.get(field)
        if not isinstance(result, dict) or not str(result.get("result") or "").strip():
            errors.append(f"action.{field} must declare an object with result")


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
        _validate_parameter_specs(definition.get("parameters"), errors)
        for field in ("success", "failure"):
            if isinstance(definition.get(field), list) and len(definition[field]) == 1 and isinstance(definition[field][0], dict):
                definition[field] = definition[field][0]
        duration = definition.get("duration") if isinstance(definition.get("duration"), dict) else {}
        if not duration or not (_is_number(duration.get("minutes")) or duration.get("mode") in {"route_distance", "recipe_duration"}):
            errors.append("action.duration must declare minutes or a supported mode")
        if _is_number(duration.get("minutes")) and float(duration["minutes"]) <= 0:
            errors.append("action.duration.minutes must be positive")
        for field in ("preconditions", "effects", "progress_effects"):
            if field in definition and not isinstance(definition[field], list):
                errors.append(f"action.{field} must be a list")
        _validate_effects(definition, errors)
        _validate_result_contracts(definition, errors)
        return definition, errors

    if kind == "behavior":
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

