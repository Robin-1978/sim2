"""Canonical model-facing schema for dynamic world definitions.

The schema is intentionally independent of any particular world pack.  The
Platform validator remains the authority for semantic checks, while this
module is the one source for the JSON shape shown to God and WorldBuilder.
"""

from __future__ import annotations

from typing import Any


def propose_definition_schema() -> dict[str, Any]:
    """Return a fresh JSON Schema for the declarative definition proposal."""

    definition_properties: dict[str, Any] = {
        "nodes": {
            "type": "array",
            "description": "For a behavior, ordered executable nodes. Use an Action node with the exact registered Action name; include Travel before a destination Action when needed.",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["action", "behavior", "sequence", "selector", "parallel", "condition", "wait"]},
                    "action_id": {"type": "string", "description": "Exact visible Action name from world_action_catalog."},
                    "behavior_id": {"type": "string", "description": "Exact visible BT name when using a behavior node."},
                    "parameters": {"type": "object", "description": "Action parameter names and exact visible entity values."},
                    "children": {"type": "array", "items": {"type": "object"}},
                },
                "additionalProperties": True,
            },
        },
        "parameters": {
            "type": "object",
            "description": "For an Action: parameter_name -> one of string, number, integer, boolean, object, array. Do not use human-language placeholder values.",
        },
        "duration": {
            "type": "object",
            "description": "For an Action, use {minutes: positive number} or {mode: route_distance|recipe_duration}.",
        },
        "costs": {
            "type": "object",
            "description": "For an Action, reserve carried inputs under costs.reserve. Use item_parameter and optionally item_quantity_parameter for a variable amount.",
        },
        "preconditions": {"type": "array", "items": {"type": "string"}, "description": "Optional machine predicates for an Action."},
        "effects": {"type": "array", "items": {"type": "object"}, "description": "Observable Action effects. resource_transfer must bind its source and reserved quantity."},
        "success": {"type": "object", "description": "One Action success object: {condition?: machine predicate, result: short label}."},
        "failure": {"type": "object", "description": "One Action failure object: {condition?: machine predicate, result: short label}."},
        "entry_conditions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional durable machine predicates only, such as actor.alive, actor.can_research, actor.inventory.item > 0, actor.state.location_id == <visible location name>, or world[\"<property>\"] >= <number>. A bare world property is not a condition; compare it with a number or true/false. Do not write prose, goals, or claims like 'actor can reach the records'; use [] when Action preconditions and a Travel node are sufficient.",
        },
        "success_results": {"type": "array", "items": {"type": "string"}, "description": "Short observable result labels."},
        "failure_results": {"type": "array", "items": {"type": "string"}, "description": "Short observable failure labels."},
        "summary": {"type": "string"},
        "goal": {"type": "string"},
        "outcome_events": {"type": "array", "items": {"type": "object", "description": "Each event must use {event_type: new event name, summary: fact-based text, wake?: [story|god]}."}},
    }
    return {
        "type": "object",
        "properties": {
            "definition_type": {"type": "string", "enum": ["attribute", "world_property", "action", "behavior", "recipe", "capability"]},
            "name": {"type": "string", "description": "A short visible name. Platform derives the internal registry key."},
            "definition": {
                "type": "object",
                "description": "Declarative fields for the selected type. Use exact visible entity names; no free-form steps, code, handlers, SQL, or internal IDs.",
                "properties": definition_properties,
                "additionalProperties": True,
            },
            "assign_to": {"type": "array", "items": {"type": "string"}, "description": "For a behavior, required visible character names that can execute it; other definition types must leave this empty."},
            "followup_behavior": {
                "type": "object",
                "description": "Required for a new Action. Bind the Action to one immediately executable behavior in the same transaction.",
                "properties": {
                    "name": {"type": "string", "description": "Visible BT name."},
                    "assign_to": {"type": "array", "items": {"type": "string"}, "description": "Visible character names that can execute the BT now."},
                    "definition": {"type": "object", "description": "Behavior definition whose nodes include the new Action by its exact visible name."},
                },
                "required": ["name", "assign_to", "definition"],
                "additionalProperties": False,
            },
            "followup_recipe": {
                "type": "object",
                "description": "Required when the new Action consumes a recipe. Register the recipe atomically with the Action and followup BT.",
                "properties": {
                    "name": {"type": "string", "description": "Visible recipe name."},
                    "definition": {"type": "object", "description": "Recipe definition with duration_minutes, inputs and outputs."},
                },
                "required": ["name", "definition"],
                "additionalProperties": False,
            },
            "evidence": {"type": "string", "description": "The observed result that justifies this new definition."},
        },
        "required": ["definition_type", "name", "definition"],
        "additionalProperties": False,
    }

