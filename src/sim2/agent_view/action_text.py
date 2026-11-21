"""Visible wording of Actions and behaviors: labels, costs, effects, predicates and choice keys."""

from __future__ import annotations

import re
from typing import Any


def label(value: str) -> str:
    return value.replace("_", " ").strip().title()


def choice_key(value: Any) -> str:
    """Normalize a model-facing label for deterministic exact matching."""

    return "".join(char for char in str(value or "").casefold() if char.isalnum())


def predicate_text(predicate: str) -> str:
    """Turn a configured predicate into one short sentence for the model."""

    text = str(predicate).replace("_", " ")
    # Dots in configured paths are separators (``actor.state.energy``), but
    # dots between digits are numeric decimals.  Replacing every dot made
    # thresholds such as ``0.9`` appear as ``0 9`` to the Agent.
    text = re.sub(r"(?<=[A-Za-z_])\.(?=[A-Za-z_])", " ", text).strip()
    return f"Configured condition: {text[:1].upper() + text[1:]}."


def _display_item(value: Any) -> str:
    return str(value).removeprefix("context.").replace("_", " ")


def _display_number(value: Any) -> str:
    """Keep preformatted context strings free of binary float tails."""

    if isinstance(value, float):
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _safe_number(value: Any, default: float = 1.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _cost_text(
    action: dict[str, Any],
    recipe: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
) -> list[str]:
    costs = action.get("costs", {})
    parameters = parameters or {}
    out: list[str] = []
    for phase, values in costs.items():
        if not values:
            continue
        if isinstance(values, dict):
            for key, value in values.items():
                if key == "recipe_inputs" and recipe:
                    scope_labels = {
                        "agent": "from carried inventory",
                        "world": "from world stock",
                        "auto": "from carried or shared stock",
                    }
                    inputs = ", ".join(
                        f"{item.get('quantity', 1)} {_display_item(item.get('item', 'input'))} "
                        f"{scope_labels.get(str(item.get('scope') or 'agent'), 'from configured stock')}"
                        for item in recipe.get("inputs", []) or []
                    )
                    out.append(f"Recipe inputs: {inputs or 'configured inputs'} (reserved before start)")
                    continue
                if key == "item_parameter":
                    item = parameters.get(str(value), value) if isinstance(value, str) else value
                    quantity_key = values.get("item_quantity_parameter")
                    quantity = parameters.get(str(quantity_key), 1) if quantity_key else 1
                    out.append(
                        f"Carried stock -{_safe_number(quantity):g} {_display_item(item)} "
                        "on success (reserved before start)"
                    )
                    continue
                if key == "item_quantity_parameter":
                    # The declared item line already carries the resolved
                    # quantity; showing the raw parameter key again is noise.
                    continue
                if key == "resource_parameter":
                    amount = values.get("resource_amount", 1)
                    out.append(
                        f"Source stock -{amount} selected resource on success "
                        "(reserved before start)"
                    )
                    continue
                if key == "resource_amount":
                    continue
                shown_value = "the selected target" if str(value).endswith("_id") else _display_item(value)
                out.append(f"{_display_item(key).title()} {shown_value} ({phase.replace('_', ' ')})")
        else:
            out.append(f"{phase.replace('_', ' ')}: {values}")
    return out or ["No direct resource cost."]


def _effect_text(action: dict[str, Any], recipe: dict[str, Any] | None = None, parameters: dict[str, Any] | None = None) -> list[str]:
    impacts: list[str] = []
    parameters = parameters or {}

    def resolved(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("context."):
            return parameters.get(value.removeprefix("context."), value)
        if isinstance(value, str) and value in parameters:
            return parameters[value]
        return value

    for effect in action.get("effects", []) or []:
        effect_type = str(effect.get("type") or effect.get("op") or "")
        if effect_type == "need_delta":
            impacts.append(f"{_display_item(effect.get('need', 'need')).title()} {float(effect.get('delta', 0)):g}")
        elif effect_type == "state_delta":
            impacts.append(f"{_display_item(effect.get('state', 'state')).title()} {float(effect.get('delta', 0)):g}")
        elif effect_type == "inventory_add":
            impacts.append(f"Inventory +{effect.get('quantity', 1)} {_display_item(resolved(effect.get('item', 'item')))}")
        elif effect_type in {"inventory_remove", "inventory.consume_item"}:
            impacts.append(f"Inventory -{effect.get('quantity', 1)} {_display_item(resolved(effect.get('item', 'item')))}")
        elif effect_type in {"attribute_delta", "attribute_set"}:
            attribute = _display_item(effect.get("attribute", "attribute"))
            if effect_type == "attribute_set":
                impacts.append(f"{attribute.title()} becomes {effect.get('value')}")
            elif effect.get("formula"):
                direction = "decreases" if str(effect.get("direction", "add")) == "subtract" else "increases"
                impacts.append(f"{attribute.title()} {direction} by the configured formula")
            else:
                impacts.append(f"{attribute.title()} changes by {effect.get('delta', 0)}")
        elif effect_type == "location_set":
            impacts.append("Arrive at the selected location")
        elif effect_type == "world_property_delta":
            impacts.append(f"World {_display_item(effect.get('property', 'property'))} changes by {effect.get('delta', effect.get('value', 0))}")
        elif effect_type == "world_resource_delta":
            resource = resolved(effect.get("resource_id") or effect.get("resource") or "resource")
            delta = _safe_number(effect.get("delta", effect.get("value", 0)), default=0.0)
            impacts.append(f"World resource {_display_item(resource)} {delta:+g}")
        elif effect_type == "resource_withdrawal":
            amount = _safe_number(resolved(effect.get("amount", effect.get("quantity", 1))))
            item = resolved(effect.get("output_item") or effect.get("inventory_item") or "item")
            if effect.get("only_if_missing"):
                impacts.append(f"Use {amount:g} {_display_item(item)} (carried or shared stock)")
            else:
                impacts.append(f"Withdraw {amount:g} {_display_item(item)} from shared stock")
        elif effect_type == "resource_transfer":
            quantity = effect.get("quantity_parameter")
            amount = _safe_number(resolved(parameters.get(str(quantity))) if quantity else resolved(effect.get("quantity", effect.get("amount", 1))))
            item = resolved(effect.get("source_item") or effect.get("source_item_parameter") or "item")
            impacts.append(f"Deposit {amount:g} {_display_item(item)} into shared stock")
        elif effect_type == "entity_state_set":
            # The world-property delta and the action title already describe
            # the visible facility change; raw entity/state flags are noise.
            continue
        elif effect_type:
            impacts.append(f"Platform applies configured {effect_type.replace('_', ' ')} effect")
    if recipe:
        outputs = ", ".join(f"{item.get('quantity', 1)} {_display_item(item.get('item', 'output'))}" for item in recipe.get("outputs", []) or [])
        if outputs:
            impacts.append(f"Produces {outputs}")
    progress = action.get("progress") or {}
    if isinstance(progress, dict) and progress.get("key"):
        impacts.append(f"Adds {progress.get('amount', 1)} progress; {progress.get('required', 1)} total progress completes the discovery")
    if action.get("information"):
        impacts.append("Records the configured information result")
    return list(dict.fromkeys(impacts)) or ["Platform applies configured state effects and records the result."]


def action_view(action: dict[str, Any], recipe: dict[str, Any] | None = None, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    success = action.get("success", {})
    failure = action.get("failure", {})
    return {
        "name": label(action.get("id", "action")),
        "summary": action.get("summary") or action.get("description") or label(action.get("id", "action")),
        "when": action.get("when") or action.get("precondition_text") or [predicate_text(item) for item in action.get("preconditions", [])] or ["Platform confirms the configured requirements."],
        "requires": action.get("requirements") or action.get("requirement_text") or [predicate_text(item) for item in action.get("preconditions", [])] or ["Platform confirms the configured requirements."],
        "target": "Platform selects a permitted target from the current view.",
        "time": f"{action.get('duration', {}).get('minutes', 'variable')} minutes",
        "cost": _cost_text(action, recipe, parameters),
        "impact": _effect_text(action, recipe, parameters),
        "success": [str(success.get("result", "configured success")).replace("_", " ")],
        "failure": [str(failure.get("result", "configured failure")).replace("_", " ")],
        "interrupt": action.get("interrupt_policy", "cancel"),
    }
