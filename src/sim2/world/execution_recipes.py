"""Recipe inputs and outputs attached to a persisted behavior execution."""

from __future__ import annotations

from typing import Any


def recipe_snapshots(
    store: Any, steps: list[dict[str, Any]], bindings: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    selected: set[str] = set()
    for step in steps:
        action = step.get("definition_snapshot") or {}
        parameters = step.get("parameters") or {}
        if (
            "recipe_id" in parameters
            or "recipe_id" in (action.get("parameters") or {})
            or "recipe_inputs" in ((action.get("costs") or {}).get("reserve") or {})
            or (action.get("duration") or {}).get("mode") == "recipe_duration"
        ):
            recipe_id = parameters.get("recipe_id")
            if isinstance(recipe_id, str) and recipe_id.startswith("context."):
                recipe_id = bindings.get(recipe_id.removeprefix("context."))
            if not isinstance(recipe_id, str) or not recipe_id:
                # A deferred binding can still select any currently defined recipe.
                return {row["id"]: row for row in store.repositories.entities.get_definitions("recipe_def")}
            selected.add(recipe_id)
    if not selected:
        return {}
    return {row["id"]: row for row in store.repositories.entities.get_definitions("recipe_def") if row["id"] in selected}


def execution_recipe(store: Any, action: dict[str, Any], recipe_id: Any) -> dict[str, Any] | None:
    if not isinstance(recipe_id, str) or not recipe_id:
        return None
    if "_recipe_snapshot" in action:
        recipe = action["_recipe_snapshot"]
        return recipe if recipe and recipe.get("id") == recipe_id else None
    return next((item for item in store.repositories.entities.get_definitions("recipe_def") if item.get("id") == recipe_id), None)


def pin_execution_recipe(
    store: Any, action: dict[str, Any], parameters: dict[str, Any], bt_data: dict[str, Any],
) -> dict[str, Any]:
    recipe_id = parameters.get("recipe_id")
    if not isinstance(recipe_id, str) or not recipe_id or "_recipe_snapshot" in action:
        return action
    pinned = bt_data.get("recipe_snapshots")
    recipe = pinned.get(recipe_id) if pinned is not None else execution_recipe(store, action, recipe_id)
    return {**action, "_recipe_snapshot": recipe}
