"""Canonical model-facing schema for dynamic world definitions.

The schema is intentionally independent of any particular world pack.  The
Platform validator remains the authority for semantic checks, while this
module is the one source for the JSON shape shown to God and WorldBuilder.
"""

from __future__ import annotations

from typing import Any

from .definition_compiler import DEFINITION_TYPES, EFFECT_TYPES, SCALAR_PARAMETER_TYPES
from .schema_text import concise_schema


def propose_definition_schema() -> dict[str, Any]:
    """Return a fresh JSON Schema for the declarative definition proposal."""

    definition_properties: dict[str, Any] = {
        "verb": {
            "type": "string",
            "description": "Capability family, e.g. transfer_resource or consume; reuse a generic verb.",
        },
        "nodes": {
            "type": "array",
            "description": "Ordered executable nodes. Use an Action node with the exact visible Action name; include Travel before a destination Action.",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["action", "behavior", "sequence", "selector", "parallel", "condition", "wait"]},
                    "action_id": {"type": "string", "description": "Exact visible Action name."},
                    "behavior_id": {"type": "string", "description": "Exact visible behavior name."},
                    "parameters": {"type": "object", "description": "Action parameter names with exact visible values."},
                    "children": {"type": "array", "items": {"type": "object"}},
                },
                "additionalProperties": True,
            },
        },
        "parameters": {
            "type": "object",
            "description": "Action parameter_name -> configured scalar type name.",
            "additionalProperties": {
                "oneOf": [
                    {"type": "string", "enum": sorted(SCALAR_PARAMETER_TYPES)},
                    {"type": "object", "properties": {"type": {"type": "string", "enum": sorted(SCALAR_PARAMETER_TYPES)}}, "required": ["type"]},
                ],
            },
        },
        "duration": {
            "type": "object",
            "description": "Action duration: {minutes: positive number} or {mode: route_distance|recipe_duration}.",
        },
        "costs": {
            "type": "object",
            "description": "Action costs. Reserve carried inputs under costs.reserve; use item_parameter and optional item_quantity_parameter.",
        },
        "preconditions": {"type": "array", "items": {"type": "string"}, "description": "Optional machine predicates."},
        "effects": {"type": "array", "items": {"type": "object", "properties": {"type": {"type": "string", "enum": sorted(EFFECT_TYPES)}}, "additionalProperties": True}, "description": "Set fields; bind stock targets via context.<param>; positive delta needs max; withdrawal/take need amount and scope."},
        "progress_effects": {"type": "array", "items": {"type": "object", "properties": {"type": {"type": "string", "enum": sorted(EFFECT_TYPES)}}, "additionalProperties": True}, "description": "Observable effects while a long Action progresses."},
        "success": {"type": "object", "description": "Action success: {condition?: machine predicate, result: short label}."},
        "failure": {"type": "object", "description": "Action failure: {condition?: machine predicate, result: short label}."},
        "entry_conditions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Durable predicates only, e.g. actor.alive; [] if none.",
        },
        "success_results": {"type": "array", "items": {"type": "string"}, "description": "Short observable result labels."},
        "failure_results": {"type": "array", "items": {"type": "string"}, "description": "Short observable failure labels."},
        "summary": {"type": "string"},
        "outcome_events": {"type": "array", "items": {"type": "object", "description": "{event_type, summary, wake?: [story|god]}."}},
    }
    schema = {
        "type": "object",
        "properties": {
            "definition_type": {"type": "string", "enum": list(DEFINITION_TYPES)},
            "name": {"type": "string", "description": "Short visible name."},
            "definition": {
                "type": "object",
                "description": "Declarative fields for the selected type; use exact visible names, never code or internal IDs.",
                "properties": definition_properties,
                "additionalProperties": True,
            },
            "assign_to": {"type": "array", "items": {"type": "string"}, "description": "For a behavior, visible characters that can execute it now; empty for other types."},
            "followup_behavior": {
                "type": "object",
                "description": "Required for a new Action: bind one immediately executable behavior.",
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
                "description": "Required when the new Action consumes a recipe; register it atomically.",
                "properties": {
                    "name": {"type": "string", "description": "Visible recipe name."},
                    "definition": {"type": "object", "description": "Recipe definition with duration_minutes, inputs and outputs."},
                },
                "required": ["name", "definition"],
                "additionalProperties": False,
            },
            "evidence": {"type": "string", "description": "Observed result that justifies the definition."},
        },
        "required": ["definition_type", "name", "definition"],
        "additionalProperties": False,
    }
    return concise_schema(schema)

