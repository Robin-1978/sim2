"""Validation for declarative action catalogs.

A catalog is data, but its entries still reference world state: one location,
one stock, one output item and a set of condition paths.  These checks make a
broken binding fail while a package is validated instead of after an actor has
already selected the entry.
"""

from __future__ import annotations

from typing import Any

from ..actions.conditions import ConditionEngine
from ..dsl.expressions import parse_expression, reference_paths
from ..world.definition_compiler import condition_syntax_error


def _resource_location(resource: dict[str, Any]) -> str:
    state = resource.get("initial_state") if isinstance(resource.get("initial_state"), dict) else {}
    return str(state.get("location_id") or resource.get("location_id") or "").strip()


def _effect_resource_id(effect: dict[str, Any]) -> str:
    return str(effect.get("resource_id") or effect.get("resource") or "").strip()


def _effect_output_item(effect: dict[str, Any]) -> str:
    return str(effect.get("output_item") or effect.get("inventory_item") or "").strip()


def _check_declared_paths(
    paths: list[str],
    *,
    owner: str,
    state_ids: set[str] | None,
    need_ids: set[str] | None,
    world_properties: set[str] | None,
    item_labels: set[str],
    errors: list[str],
) -> None:
    for path in paths:
        for prefix, declared, kind in (
            ("actor.state.", state_ids, "state attribute"),
            ("actor.need.", need_ids, "need"),
            ("actor.needs.", need_ids, "need"),
            ("world.", world_properties, "world property"),
            ("actor.inventory.", item_labels or None, "inventory item"),
        ):
            if declared is not None and path.startswith(prefix):
                key = path.removeprefix(prefix)
                if key not in declared and key.partition(".")[0] not in declared:
                    errors.append(f"{owner} references undeclared {kind} {key}")


def validate_action_catalogs(
    rules: dict[str, Any],
    *,
    resources: list[dict[str, Any]],
    location_ids: set[str],
    condition_engine: ConditionEngine,
    state_ids: set[str] | None = None,
    need_ids: set[str] | None = None,
    world_properties: set[str] | None = None,
    item_labels: dict[str, Any] | None = None,
) -> list[str]:
    """Return every invalid location, resource, effect or condition binding."""

    catalogs = rules.get("action_catalogs")
    if catalogs is None:
        return []
    if not isinstance(catalogs, dict):
        return ["rules.action_catalogs must be an object"]

    errors: list[str] = []
    resources_by_id = {
        str(item.get("id")): item
        for item in resources
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    labels = {str(key) for key in (item_labels or {})}

    for catalog_name, entries in catalogs.items():
        owner = f"action catalog {catalog_name}"
        if not isinstance(entries, list):
            errors.append(f"{owner} must be a list")
            continue
        seen_ids: set[str] = set()
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                errors.append(f"{owner}[{index}] must be an object")
                continue
            entry_id = str(entry.get("id") or "").strip()
            if not entry_id:
                errors.append(f"{owner}[{index}] requires a non-empty id")
                continue
            if entry_id in seen_ids:
                errors.append(f"{owner} has duplicate entry id {entry_id}")
                continue
            seen_ids.add(entry_id)
            entry_owner = f"{owner} entry {entry_id}"

            location = str(entry.get("location") or "").strip()
            if catalog_name == "gather" and not location:
                errors.append(f"{entry_owner} requires a location for gather entries")
            if location and location not in location_ids:
                errors.append(f"{entry_owner} references unknown location {location}")

            requires = entry.get("requires") or []
            if not isinstance(requires, list):
                errors.append(f"{entry_owner}.requires must be a list")
            else:
                for requirement_index, predicate in enumerate(requires):
                    if not isinstance(predicate, str) or not predicate.strip():
                        errors.append(f"{entry_owner}.requires[{requirement_index}] must be a non-empty condition")
                        continue
                    if error := condition_syntax_error(predicate):
                        errors.append(f"{entry_owner}.requires[{requirement_index}] has invalid expression: {error}")
                        continue
                    for path in condition_engine.unknown_paths(predicate):
                        errors.append(
                            f"{entry_owner}.requires[{requirement_index}] uses unsupported condition path: {path}"
                        )
                    _check_declared_paths(
                        list(dict.fromkeys(reference_paths(parse_expression(predicate)))),
                        owner=f"{entry_owner}.requires[{requirement_index}]",
                        state_ids=state_ids,
                        need_ids=need_ids,
                        world_properties=world_properties,
                        item_labels=labels,
                        errors=errors,
                    )

            effects = entry.get("effects") or []
            if not isinstance(effects, list):
                errors.append(f"{entry_owner}.effects must be a list")
                continue
            if catalog_name != "gather":
                continue

            resource_id = str(entry.get("resource_id") or "").strip()
            if not resource_id:
                errors.append(f"{entry_owner} requires resource_id for gather entries")
            else:
                resource = resources_by_id.get(resource_id)
                if resource is None:
                    errors.append(f"{entry_owner} references unknown resource {resource_id}")
                else:
                    resource_location = _resource_location(resource)
                    if not resource_location:
                        errors.append(f"{entry_owner} resource {resource_id} has no location")
                    elif location and resource_location != location:
                        errors.append(
                            f"{entry_owner} location {location} does not match "
                            f"resource {resource_id} location {resource_location}"
                        )

            withdrawals = [
                effect for effect in effects
                if isinstance(effect, dict)
                and str(effect.get("type") or effect.get("op") or "") == "resource_withdrawal"
            ]
            if not withdrawals:
                errors.append(f"{entry_owner} requires at least one resource_withdrawal effect")
            for effect in withdrawals:
                effect_resource = _effect_resource_id(effect)
                if not effect_resource:
                    errors.append(f"{entry_owner} resource_withdrawal requires resource_id")
                elif resource_id and effect_resource != resource_id:
                    errors.append(
                        f"{entry_owner} resource_withdrawal {effect_resource} "
                        f"does not match entry resource_id {resource_id}"
                    )
                amount = effect.get("amount", effect.get("quantity", 0))
                if isinstance(amount, bool) or not isinstance(amount, (int, float)) or float(amount) <= 0:
                    errors.append(f"{entry_owner} resource_withdrawal requires a positive amount")
                output_item = _effect_output_item(effect)
                if not output_item:
                    errors.append(f"{entry_owner} resource_withdrawal requires output_item")
                elif labels and output_item not in labels:
                    errors.append(f"{entry_owner} references undeclared output item {output_item}")

    return list(dict.fromkeys(errors))
