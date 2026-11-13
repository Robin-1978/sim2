from __future__ import annotations

import shutil
import tempfile
import re
from pathlib import Path
from typing import Any

import yaml

from ..actions.conditions import ConditionEngine
from ..evolution.config import normalize_evolution_config
from ..persistence.store import WorldStore, config_hash
from ..world.schema import AttributeSchema


CONFIG_COLLECTIONS = {
    "locations.yaml": "locations",
    "objects.yaml": "objects",
    "factions.yaml": "factions",
    "agents.yaml": "agents",
    "actions.yaml": "actions",
    "behavior_trees.yaml": "behavior_trees",
    "recipes.yaml": "recipes",
    "capabilities.yaml": "capabilities",
    "attributes.yaml": "attributes",
    "opening_events.yaml": "opening_events",
}


def world_template_schema() -> dict[str, Any]:
    """JSON Schema used by the CLI, WorldBuilder and model tool channel."""

    identified = {
        "type": "object",
        "properties": {"id": {"type": "string", "minLength": 1}, "name": {"type": "string"}},
        "required": ["id"],
        "additionalProperties": True,
    }
    typed_entity = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "type": {"type": "string", "minLength": 1},
            "name": {"type": "string"},
            "location_id": {"type": "string"},
            "initial_state": {"type": "object"},
        },
        "required": ["id", "type"],
        "additionalProperties": True,
    }
    location = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "name": {"type": "string"},
            "public": {},
            "safe": {"type": "boolean"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "routes": {"type": "array", "items": {"type": "string"}},
            "map_position": {"type": "object"},
        },
        "required": ["id", "name", "routes"],
        "additionalProperties": True,
    }
    attribute = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "kind": {"type": "string", "enum": ["state", "need"]},
            "label": {"type": "string"},
            "unit": {"type": "string"},
            "min": {"type": "number"},
            "max": {"type": "number"},
            "default": {"type": "number"},
            "cost_mode": {"type": "string", "enum": ["increase", "decrease"]},
            "prompt": {"type": "string"},
        },
        "required": ["id", "kind", "label", "min", "max", "default", "prompt"],
        "additionalProperties": True,
    }
    action = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
            "parameters": {"type": "object"},
            "preconditions": {"type": "array", "items": {"type": "string"}},
            "duration": {"type": "object"},
            "costs": {
                "type": "object",
                "properties": {
                    "reserve": {"type": "object"},
                    "per_minute": {"type": "object", "additionalProperties": {"type": "number", "minimum": 0}},
                    "on_success": {"type": "object"},
                    "on_failure": {"type": "object"},
                    "refund": {"type": "object"},
                },
                "required": ["reserve", "per_minute", "on_success", "on_failure", "refund"],
                "additionalProperties": False,
            },
            "effects": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "attribute": {"type": "string"},
                        "state": {"type": "string"},
                        "need": {"type": "string"},
                        "property": {"type": "string"},
                        "delta": {"type": "number"},
                        "item": {"type": "string"},
                        "quantity": {"type": "number"},
                        "location_id": {"type": "string"},
                        "entity_id": {"type": "string"},
                        "value": {},
                    },
                    "required": ["type"],
                    "additionalProperties": True,
                },
            },
            "success": {
                "type": "object",
                "properties": {"condition": {"type": "string"}, "result": {"type": "string"}},
                "required": ["condition", "result"],
                "additionalProperties": True,
            },
            "failure": {
                "type": "object",
                "properties": {"condition": {"type": "string"}, "result": {"type": "string"}},
                "required": ["condition", "result"],
                "additionalProperties": True,
            },
        },
        "required": ["id", "description", "parameters", "preconditions", "duration", "costs", "effects", "success", "failure"],
        "additionalProperties": True,
    }
    behavior = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "agent_label": {"type": "string"},
            "summary": {"type": "string"},
            "entry_conditions": {"type": "array", "items": {"type": "string"}},
            "estimated_minutes": {"type": "integer", "minimum": 1},
            "nodes": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["action", "subtree", "behavior", "sequence", "parallel", "selector", "condition"]},
                        "action_id": {"type": "string"},
                        "behavior_id": {"type": "string"},
                        "parameters": {"type": "object"},
                        "expression": {"type": "string"},
                        "children": {"type": "array", "items": {"type": "object"}},
                    },
                    "required": ["type"],
                    "additionalProperties": True,
                },
            },
        },
        "required": ["id", "agent_label", "summary", "nodes"],
        "additionalProperties": True,
    }
    recipe_item = {
        "type": "object",
        "properties": {
            "item": {"type": "string", "minLength": 1},
            "quantity": {"type": "number", "exclusiveMinimum": 0},
            "scope": {"type": "string", "enum": ["agent", "world", "auto"]},
        },
        "required": ["item", "quantity"],
        "additionalProperties": True,
    }
    recipe = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "name": {"type": "string"},
            "station_tags": {"type": "array", "items": {"type": "string"}},
            "inputs": {"type": "array", "items": recipe_item},
            "outputs": {"type": "array", "minItems": 1, "items": recipe_item},
            "duration_minutes": {"type": "integer", "minimum": 1},
            "requires": {"type": "array", "items": {"type": "object"}},
            "effects": {"type": "array", "items": {"type": "object"}},
            "limits": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["id", "name", "inputs", "outputs", "duration_minutes"],
        "additionalProperties": True,
    }
    agent = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "name": {"type": "string"},
            "role": {"type": "string"},
            "ultimate_goal": {"type": "string"},
            "traits": {"type": "object"},
            "beliefs": {"type": "array"},
            "needs": {"type": "object"},
            "capabilities": {"type": "object"},
            "initial_bt_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
            "initial_state": {"type": "object"},
            "inventory": {"type": "object"},
        },
        "required": ["id", "name", "role", "ultimate_goal", "traits", "beliefs", "needs", "capabilities", "initial_bt_ids", "initial_state", "inventory"],
        "additionalProperties": True,
    }
    rules_schema = {
        "type": "object",
        "properties": {
            "vital_attribute": {"type": "string"},
            "capacity_attribute": {"type": "string"},
            "needs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "target": {"type": "number"},
                        "minimum": {"type": "number"},
                        "gamma": {"type": "number", "exclusiveMinimum": 0},
                        "critical_threshold": {"type": "number", "minimum": 0, "maximum": 1},
                        "critical_gain": {"type": "number", "minimum": 0},
                    },
                    "required": ["id", "target", "minimum", "gamma", "critical_threshold", "critical_gain"],
                    "additionalProperties": True,
                },
            },
            "motivation_model": {"type": "object"},
            "minute_effects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "scope": {"type": "string", "enum": ["agent", "world", "relationship"]},
                        "every_minutes": {"type": "integer", "minimum": 1},
                        "condition": {"type": "string"},
                        "effect": {"type": "object"},
                    },
                    "required": ["id", "scope", "every_minutes", "effect"],
                    "additionalProperties": True,
                },
            },
            "fallback_behavior_ids": {"type": "array", "items": {"type": "string"}},
            "memory_quality": {"type": "object"},
            "context_values": {"type": "object"},
            "item_groups": {
                "type": "object",
                "additionalProperties": {"type": "array", "items": {"type": "string"}},
            },
            "context_resolvers": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["first_inventory_item"]},
                        "group": {"type": "string"},
                    },
                    "required": ["type", "group"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["vital_attribute", "capacity_attribute", "needs"],
        "additionalProperties": True,
    }
    return {
        "type": "object",
        "properties": {
            "template_version": {"type": "string"},
            "world": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "title": {"type": "string"},
                    "premise": {"type": "string"},
                    "genre": {"type": "string"},
                    "locale": {"type": "string"},
                    "time_unit": {"type": "string", "enum": ["minute"]},
                    "properties": {"type": "object"},
                    "random_seed": {"type": "integer"},
                },
                "required": ["id", "title", "premise", "properties"],
                "additionalProperties": True,
            },
            "attributes": {"type": "array", "items": attribute},
            "locations": {"type": "array", "minItems": 1, "items": location},
            "resources": {"type": "array", "items": typed_entity},
            "objects": {"type": "array", "items": typed_entity},
            "factions": {"type": "array", "items": identified},
            "agents": {"type": "array", "minItems": 1, "items": agent},
            "capabilities": {"type": "array", "items": identified},
            "actions": {"type": "array", "minItems": 1, "items": action},
            "recipes": {"type": "array", "items": recipe},
            "behavior_trees": {"type": "array", "minItems": 1, "items": behavior},
            "relationships": {"type": "array", "items": {"type": "object"}},
            "rules": rules_schema,
            "evolution": {"type": "object"},
            "story": {"type": "object"},
            "player_template": {"type": "object"},
            "initial_events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "type": {"type": "string", "minLength": 1},
                        "world_minute": {"type": "integer", "minimum": 0},
                        "payload": {"type": "object"},
                    },
                    "required": ["id", "type", "world_minute", "payload"],
                    "additionalProperties": True,
                },
            },
            "prompts": {"type": "object"},
        },
        "required": ["template_version", "world", "attributes", "locations", "agents", "actions", "behavior_trees", "rules"],
        "additionalProperties": False,
    }


def validate_world_template(template: dict[str, Any]) -> dict[str, Any]:
    """Validate the single-object authoring format used by WorldBuilder."""

    errors: list[str] = []
    if not isinstance(template, dict):
        return {"valid": False, "errors": ["template must be an object"]}
    world = template.get("world")
    if not isinstance(world, dict) or not str(world.get("id") or "").strip():
        errors.append("world.id is required")
    if template.get("template_version") is not None and not isinstance(template.get("template_version"), str):
        errors.append("template_version must be a string")

    collection_names = (
        "attributes", "locations", "resources", "objects", "factions", "agents",
        "capabilities", "actions", "recipes", "behavior_trees", "relationships",
        "initial_events",
    )
    collections: dict[str, list[dict[str, Any]]] = {}
    for key in collection_names:
        value = template.get(key, [])
        if not isinstance(value, list):
            errors.append(f"{key} must be a list")
            value = []
        collections[key] = [item for item in value if isinstance(item, dict)]
        if len(collections[key]) != len(value):
            errors.append(f"{key} entries must be objects")
        ids = [str(item.get("id") or "") for item in collections[key] if key != "relationships"]
        if any(not value for value in ids):
            errors.append(f"{key} entries require non-empty ids")
        if len(ids) != len(set(ids)):
            errors.append(f"duplicate id in {key}")

    attribute_ids = {str(item.get("id")) for item in collections["attributes"]}
    location_ids = {str(item.get("id")) for item in collections["locations"]}
    faction_ids = {str(item.get("id")) for item in collections["factions"]}
    agent_ids = {str(item.get("id")) for item in collections["agents"]}
    action_ids = {str(item.get("id")) for item in collections["actions"]}
    behavior_ids = {str(item.get("id")) for item in collections["behavior_trees"]}
    capability_ids = {str(item.get("id")) for item in collections["capabilities"]}

    for required_collection in ("attributes", "locations", "agents", "actions", "behavior_trees"):
        if not collections[required_collection]:
            errors.append(f"{required_collection} must contain at least one entry")
    world_properties = (world or {}).get("properties", {}) if isinstance(world, dict) else {}
    if not isinstance(world_properties, dict):
        errors.append("world.properties must be an object")
        world_properties = {}
    duplicate_scopes = attribute_ids & {str(key) for key in world_properties}
    if duplicate_scopes:
        errors.append("character attributes and world properties must use distinct ids: " + ", ".join(sorted(duplicate_scopes)))
    rules = template.get("rules") if isinstance(template.get("rules"), dict) else {}
    condition_engine = ConditionEngine(rules)

    for attribute in collections["attributes"]:
        for field in ("kind", "label", "min", "max", "default", "prompt"):
            if field not in attribute:
                errors.append(f"attribute {attribute.get('id')} is missing {field}")
        if attribute.get("kind") == "state" and attribute.get("cost_mode") not in {"increase", "decrease"}:
            errors.append(f"state attribute {attribute.get('id')} must declare cost_mode")
        minimum, maximum, default = attribute.get("min"), attribute.get("max"), attribute.get("default")
        if all(isinstance(value, (int, float)) for value in (minimum, maximum, default)):
            if float(minimum) >= float(maximum):
                errors.append(f"attribute {attribute.get('id')} min must be lower than max")
            if not float(minimum) <= float(default) <= float(maximum):
                errors.append(f"attribute {attribute.get('id')} default must be within its range")
    state_ids = {str(item.get("id")) for item in collections["attributes"] if item.get("kind") == "state"}
    need_ids = {str(item.get("id")) for item in collections["attributes"] if item.get("kind") == "need"}
    for location in collections["locations"]:
        if not isinstance(location.get("routes"), list):
            errors.append(f"location {location.get('id')} must declare routes as a list")
        for route in location.get("routes", []) or []:
            if str(route) not in location_ids:
                errors.append(f"location {location.get('id')} references unknown route {route}")
    for item in collections["resources"] + collections["objects"]:
        if not str(item.get("type") or "").strip():
            errors.append(f"{item.get('id')} requires a non-empty type")
        location_id = item.get("location_id") or (item.get("initial_state") or {}).get("location_id")
        if location_id and str(location_id) not in location_ids:
            errors.append(f"{item.get('id')} references unknown location {location_id}")
    visible_locations = [item for item in collections["locations"] if item.get("public") != "hidden"]
    if len(visible_locations) > 1:
        for location in visible_locations:
            if not location.get("routes"):
                errors.append(f"visible location {location.get('id')} must connect to at least one route")

    allowed_effects = {
        "need_delta", "state_delta", "attribute_delta", "attribute_set",
        "inventory_add", "inventory_remove", "inventory.consume_item",
        "relationship_delta", "public_event", "world_property_delta",
        "entity_state_set", "status_add", "status_remove", "location_set",
        "institution_allocation",
    }

    def validate_expression(expression: Any, owner: str) -> None:
        text = str(expression or "")
        if text and condition_engine.evaluate_known(text, agent={}, target={}, context={}, world={}) is None:
            errors.append(f"{owner} uses an unsupported condition path: {text}")
        for path in re.findall(r"actor\.state\.([A-Za-z0-9_]+)", text):
            if path not in state_ids:
                errors.append(f"{owner} references undeclared state attribute {path}")
        for path in re.findall(r"actor\.need\.([A-Za-z0-9_]+)", text):
            if path not in need_ids:
                errors.append(f"{owner} references undeclared need {path}")
        for path in re.findall(r"world\.([A-Za-z0-9_]+)", text):
            if path not in world_properties:
                errors.append(f"{owner} references undeclared world property {path}")
        for attribute_id in attribute_ids:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(attribute_id)}(?![A-Za-z0-9_])", text) and not any(
                path in text for path in (f"actor.state.{attribute_id}", f"actor.need.{attribute_id}", f"target.state.{attribute_id}")
            ):
                errors.append(f"{owner} must reference {attribute_id} through actor.state, actor.need or target.state")

    def validate_effect(effect: dict[str, Any], owner: str) -> None:
        effect_type = str(effect.get("type") or "")
        if effect_type in {"need_delta", "state_delta", "world_property_delta", "relationship_delta"}:
            if not isinstance(effect.get("delta"), (int, float)):
                errors.append(f"{owner} {effect_type} requires numeric delta")
        elif effect_type == "attribute_delta":
            if not isinstance(effect.get("delta"), (int, float)) and not str(effect.get("formula") or "").strip():
                errors.append(f"{owner} attribute_delta requires numeric delta or formula")
        elif effect_type == "attribute_set" and "value" not in effect:
            errors.append(f"{owner} attribute_set requires value")
        elif effect_type in {"inventory_add", "inventory_remove", "inventory.consume_item"}:
            if not str(effect.get("item") or "").strip() or not isinstance(effect.get("quantity", 1), (int, float)):
                errors.append(f"{owner} {effect_type} requires item and numeric quantity")
        elif effect_type == "location_set" and not str(effect.get("location_id") or "").strip():
            errors.append(f"{owner} location_set requires location_id")
        elif effect_type == "entity_state_set":
            if not str(effect.get("entity_id") or "").strip() or not str(effect.get("state") or "").strip() or "value" not in effect:
                errors.append(f"{owner} entity_state_set requires entity_id, state and value")
        elif effect_type == "institution_allocation":
            required = ("institution", "pool_entity", "item")
            if any(not str(effect.get(key) or "").strip() for key in required):
                errors.append(f"{owner} institution_allocation requires institution, pool_entity and item")
        elif effect_type == "public_event" and not str(effect.get("event") or "").strip():
            errors.append(f"{owner} public_event requires event")
        elif effect_type in {"status_add", "status_remove"} and not str(effect.get("status") or "").strip():
            errors.append(f"{owner} {effect_type} requires status")
        if effect_type == "world_property_delta" and not str(effect.get("property") or "").strip():
            errors.append(f"{owner} world_property_delta requires property")
        if effect_type == "relationship_delta" and not str(effect.get("relation") or "").strip():
            errors.append(f"{owner} relationship_delta requires relation")

    for action in collections["actions"]:
        duration = action.get("duration")
        if not isinstance(duration, dict):
            errors.append(f"action {action.get('id')} requires a duration object")
        elif not (
            isinstance(duration.get("minutes"), (int, float)) and float(duration.get("minutes")) >= 1
        ) and str(duration.get("mode") or "") not in {"route_distance", "recipe"}:
            errors.append(f"action {action.get('id')} duration needs positive minutes or a supported mode")
        for condition in action.get("preconditions", []) or []:
            validate_expression(condition, f"action {action.get('id')} precondition")
        costs = action.get("costs")
        required_cost_sections = {"reserve", "per_minute", "on_success", "on_failure", "refund"}
        if not isinstance(costs, dict) or not required_cost_sections <= set(costs):
            errors.append(f"action {action.get('id')} costs must declare reserve, per_minute, on_success, on_failure and refund")
        elif any(not isinstance(costs.get(key), dict) for key in required_cost_sections):
            errors.append(f"action {action.get('id')} cost sections must be objects")
        else:
            for attribute_id in costs.get("per_minute", {}):
                if attribute_id != "time" and str(attribute_id) not in attribute_ids:
                    errors.append(f"action {action.get('id')} cost references undeclared attribute {attribute_id}")
                value = costs.get("per_minute", {}).get(attribute_id)
                if isinstance(value, (int, float)) and float(value) < 0:
                    errors.append(f"action {action.get('id')} per-minute cost {attribute_id} must be non-negative; cost_mode controls direction")
            for resource_id in costs.get("reserve", {}):
                if str(resource_id) in attribute_ids:
                    errors.append(f"action {action.get('id')} must put state cost {resource_id} in costs.per_minute, not resource reserve")
        effects = action.get("effects", [])
        if not isinstance(effects, list):
            errors.append(f"action {action.get('id')}.effects must be a list")
            effects = []
        if not effects:
            errors.append(f"action {action.get('id')} requires at least one typed effect")
        for effect in effects:
            if not isinstance(effect, dict) or str(effect.get("type") or "") not in allowed_effects:
                errors.append(f"action {action.get('id')} has unsupported or missing effect type")
                continue
            effect_type = str(effect["type"])
            validate_effect(effect, f"action {action.get('id')} effect")
            if effect_type in {"state_delta", "attribute_delta", "attribute_set"}:
                key = str(effect.get("attribute") or effect.get("state") or "")
                if key not in state_ids:
                    errors.append(f"action {action.get('id')} effect references undeclared state attribute {key}")
            if effect_type == "need_delta" and str(effect.get("need") or "") not in need_ids:
                errors.append(f"action {action.get('id')} effect references undeclared need {effect.get('need')}")
            if effect_type == "world_property_delta" and str(effect.get("property") or "") not in world_properties:
                errors.append(f"action {action.get('id')} effect references undeclared world property {effect.get('property')}")
        if not isinstance(action.get("success"), dict) or not isinstance(action.get("success", {}).get("condition"), str) or not isinstance(action.get("success", {}).get("result"), str):
            errors.append(f"action {action.get('id')} requires a success condition and result")
        if not isinstance(action.get("failure"), dict) or not isinstance(action.get("failure", {}).get("condition"), str) or not isinstance(action.get("failure", {}).get("result"), str):
            errors.append(f"action {action.get('id')} requires a failure condition and result")
    for behavior in collections["behavior_trees"]:
        nodes = behavior.get("nodes", [])
        if not isinstance(nodes, list) or not nodes:
            errors.append(f"behavior {behavior.get('id')} requires at least one node")
            continue
        allowed_nodes = {"action", "subtree", "behavior", "sequence", "parallel", "selector", "condition"}

        def validate_node(node: Any, path: str) -> None:
            if not isinstance(node, dict):
                errors.append(f"behavior {behavior.get('id')} contains a non-object node at {path}")
                return
            node_type = str(node.get("type") or "")
            if node_type not in allowed_nodes:
                errors.append(f"behavior {behavior.get('id')} has unsupported node type {node_type or 'missing'} at {path}")
                return
            if node_type == "action" and str(node.get("action_id")) not in action_ids:
                errors.append(f"behavior {behavior.get('id')} references unknown action {node.get('action_id')}")
            elif node_type in {"subtree", "behavior"} and str(node.get("behavior_id")) not in behavior_ids:
                errors.append(f"behavior {behavior.get('id')} references unknown behavior {node.get('behavior_id')}")
            elif node_type in {"sequence", "parallel", "selector"}:
                children = node.get("children")
                if not isinstance(children, list) or not children:
                    errors.append(f"behavior {behavior.get('id')} {node_type} at {path} requires children")
                else:
                    for index, child in enumerate(children):
                        validate_node(child, f"{path}.children[{index}]")
            elif node_type == "condition" and not str(node.get("expression") or ""):
                errors.append(f"behavior {behavior.get('id')} condition at {path} requires expression")

        for index, node in enumerate(nodes):
            validate_node(node, f"nodes[{index}]")
        for condition in behavior.get("entry_conditions", []) or []:
            validate_expression(condition, f"behavior {behavior.get('id')} entry condition")
        if not str(behavior.get("agent_label") or "").strip():
            errors.append(f"behavior {behavior.get('id')} requires a human-readable agent_label")
    assigned_behaviors: set[str] = set()
    for agent in collections["agents"]:
        state = agent.get("initial_state") if isinstance(agent.get("initial_state"), dict) else {}
        for field in ("name", "role", "ultimate_goal", "initial_state", "initial_bt_ids", "needs", "capabilities", "inventory"):
            if field not in agent:
                errors.append(f"agent {agent.get('id')} is missing {field}")
        if not state.get("location_id"):
            errors.append(f"agent {agent.get('id')} requires initial_state.location_id")
        elif str(state["location_id"]) not in location_ids:
            errors.append(f"agent {agent.get('id')} references unknown location {state['location_id']}")
        if not state.get("status"):
            errors.append(f"agent {agent.get('id')} requires initial_state.status")
        for attribute_id in state_ids:
            if attribute_id not in state:
                errors.append(f"agent {agent.get('id')} must explicitly initialize state attribute {attribute_id}")
        needs = agent.get("needs") if isinstance(agent.get("needs"), dict) else {}
        for need_id in need_ids:
            if need_id not in needs:
                errors.append(f"agent {agent.get('id')} must explicitly initialize need {need_id}")
        for need_id in needs:
            if str(need_id) not in need_ids:
                errors.append(f"agent {agent.get('id')} references undeclared need {need_id}")
        if not agent.get("initial_bt_ids"):
            errors.append(f"agent {agent.get('id')} requires at least one initial behavior")
        for behavior_id in agent.get("initial_bt_ids", []) or []:
            if str(behavior_id) not in behavior_ids:
                errors.append(f"agent {agent.get('id')} references unknown behavior {behavior_id}")
            else:
                assigned_behaviors.add(str(behavior_id))
        for capability_id in (agent.get("capabilities") or {}):
            if capability_ids and str(capability_id) not in capability_ids:
                errors.append(f"agent {agent.get('id')} references unknown capability {capability_id}")
        for key in state:
            if key in {"location_id", "status", "injuries", "conditions", "active_bt_instance", "active_action", "remaining_minutes"}:
                continue
            if attribute_ids and key not in attribute_ids:
                errors.append(f"agent {agent.get('id')} has undeclared state attribute {key}")
    for behavior_id in behavior_ids - assigned_behaviors:
        errors.append(f"behavior {behavior_id} is not assigned to any agent")

    for recipe in collections["recipes"]:
        if not isinstance(recipe.get("inputs"), list):
            errors.append(f"recipe {recipe.get('id')} inputs must be a list")
        if not isinstance(recipe.get("outputs"), list) or not recipe.get("outputs"):
            errors.append(f"recipe {recipe.get('id')} requires at least one output")
        if not isinstance(recipe.get("duration_minutes"), int) or int(recipe.get("duration_minutes", 0)) < 1:
            errors.append(f"recipe {recipe.get('id')} requires positive integer duration_minutes")
        for item in list(recipe.get("inputs") or []) + list(recipe.get("outputs") or []):
            if not isinstance(item, dict) or not str(item.get("item") or "").strip():
                errors.append(f"recipe {recipe.get('id')} contains an item without a name")
                continue
            if not isinstance(item.get("quantity"), (int, float)) or float(item.get("quantity", 0)) <= 0:
                errors.append(f"recipe {recipe.get('id')} item {item.get('item')} requires positive quantity")
            if item.get("scope") is not None and str(item.get("scope")) not in {"agent", "world", "auto"}:
                errors.append(f"recipe {recipe.get('id')} item {item.get('item')} has unsupported scope {item.get('scope')}")

    context_values = rules.get("context_values", {}) or {}
    item_groups = rules.get("item_groups", {}) or {}
    context_resolvers = rules.get("context_resolvers", {}) or {}
    if not isinstance(context_values, dict):
        errors.append("rules.context_values must be an object")
        context_values = {}
    if not isinstance(item_groups, dict):
        errors.append("rules.item_groups must be an object of string lists")
        item_groups = {}
    else:
        for group, items in item_groups.items():
            if not isinstance(items, list) or not items or any(not isinstance(item, str) or not item for item in items):
                errors.append(f"rules.item_groups.{group} must be a non-empty list of item names")
    if not isinstance(context_resolvers, dict):
        errors.append("rules.context_resolvers must be an object")
        context_resolvers = {}
    else:
        for name, resolver in context_resolvers.items():
            if not isinstance(resolver, dict) or str(resolver.get("type") or "") != "first_inventory_item":
                errors.append(f"rules.context_resolvers.{name} has an unsupported resolver")
                continue
            group = str(resolver.get("group") or "")
            if group not in item_groups:
                errors.append(f"rules.context_resolvers.{name} references unknown item group {group or 'missing'}")
    available_context_values = {str(key) for key in context_values} | {str(key) for key in context_resolvers}
    for action in collections["actions"]:
        for parameter, default in (action.get("parameter_defaults", {}) or {}).items():
            if not isinstance(default, dict):
                continue
            if str(default.get("resolver") or "") != "context_value":
                errors.append(f"action {action.get('id')} parameter default {parameter} has an unsupported resolver")
                continue
            key = str(default.get("key") or parameter)
            if key not in available_context_values:
                errors.append(f"action {action.get('id')} parameter default {parameter} references unknown context value {key}")
    vital = str(rules.get("vital_attribute") or "")
    capacity = str(rules.get("capacity_attribute") or "")
    if vital not in state_ids:
        errors.append("rules.vital_attribute must name a declared state attribute")
    if capacity not in state_ids:
        errors.append("rules.capacity_attribute must name a declared state attribute")
    raw_need_rules = rules.get("needs")
    if not isinstance(raw_need_rules, list):
        errors.append("rules.needs must be a list of objects with id, target, minimum, gamma, critical_threshold and critical_gain")
        raw_need_rules = []
    need_rules = {str(item.get("id")): item for item in raw_need_rules if isinstance(item, dict) and item.get("id")}
    for need_id in need_ids:
        if need_id not in need_rules:
            errors.append(f"rules.needs must define satisfaction target and nonlinear drive settings for {need_id}")
            continue
        missing = [field for field in ("target", "minimum", "gamma", "critical_threshold", "critical_gain") if field not in need_rules[need_id]]
        if missing:
            errors.append(f"rules.needs entry {need_id} is missing {', '.join(missing)}")
    for minute_effect in rules.get("minute_effects", []) or []:
        if not isinstance(minute_effect, dict):
            errors.append("rules.minute_effects entries must be objects")
            continue
        missing = [field for field in ("id", "scope", "every_minutes", "effect") if field not in minute_effect]
        if missing:
            errors.append(f"minute effect is missing {', '.join(missing)}")
            continue
        if minute_effect.get("scope") not in {"agent", "world", "relationship"}:
            errors.append(f"minute effect {minute_effect.get('id')} has unsupported scope")
        if not isinstance(minute_effect.get("every_minutes"), int) or int(minute_effect["every_minutes"]) < 1:
            errors.append(f"minute effect {minute_effect.get('id')} needs positive integer every_minutes")
        effect = minute_effect.get("effect")
        if not isinstance(effect, dict) or str(effect.get("type") or "") not in allowed_effects:
            errors.append(f"minute effect {minute_effect.get('id')} needs one supported typed effect")
            continue
        validate_expression(minute_effect.get("condition"), f"minute effect {minute_effect.get('id')} condition")
        effect_type = str(effect.get("type") or "")
        validate_effect(effect, f"minute effect {minute_effect.get('id')} effect")
        if effect_type in {"state_delta", "attribute_delta", "attribute_set"}:
            key = str(effect.get("attribute") or effect.get("state") or "")
            if key not in state_ids:
                errors.append(f"minute effect {minute_effect.get('id')} references undeclared state attribute {key}")
        if effect_type == "need_delta" and str(effect.get("need") or "") not in need_ids:
            errors.append(f"minute effect {minute_effect.get('id')} references undeclared need {effect.get('need')}")
        if effect_type == "world_property_delta" and str(effect.get("property") or "") not in world_properties:
            errors.append(f"minute effect {minute_effect.get('id')} references undeclared world property {effect.get('property')}")
    known_relation_entities = agent_ids | faction_ids
    for relation in collections["relationships"]:
        if str(relation.get("from") or "") not in known_relation_entities:
            errors.append(f"relationship references unknown source {relation.get('from')}")
        if str(relation.get("to") or "") not in known_relation_entities | {"player"}:
            errors.append(f"relationship references unknown target {relation.get('to')}")
    for event in collections["initial_events"]:
        if not str(event.get("type") or "").strip():
            errors.append(f"initial event {event.get('id')} requires type")
        if not isinstance(event.get("world_minute"), int) or int(event.get("world_minute", -1)) < 0:
            errors.append(f"initial event {event.get('id')} requires non-negative integer world_minute")
        if not isinstance(event.get("payload"), dict):
            errors.append(f"initial event {event.get('id')} requires payload object")
    return {"valid": not errors, "errors": list(dict.fromkeys(errors))}


def describe_config(config_dir: Path) -> dict[str, Any]:
    """Return a concise author-facing package inventory and validation report."""

    config_dir = Path(config_dir).resolve()
    validation = validate_config(config_dir)
    manifest = validation.get("manifest") or {}
    counts: dict[str, int] = {}
    for filename, key in CONFIG_COLLECTIONS.items():
        values = (load_yaml(config_dir, filename, {}) or {}).get(key, [])
        counts[key] = len(values) if isinstance(values, list) else 0
    counts["behavior_trees"] += len((load_yaml(config_dir, "core_behavior_trees.yaml", {}) or {}).get("behavior_trees", []) or [])
    locations = load_yaml(config_dir, "locations.yaml", {}) or {}
    counts["resources"] = len(locations.get("resources", []) or [])
    return {
        "valid": validation["valid"],
        "errors": validation["errors"],
        "world_id": manifest.get("world_id"),
        "config_version": manifest.get("config_version"),
        "time_unit": manifest.get("time_unit", "minute"),
        "counts": counts,
        "files": manifest.get("files", []),
    }


def load_yaml(config_dir: Path, name: str, default: Any) -> Any:
    path = config_dir / name
    if not path.exists():
        return default
    return yaml.safe_load(path.read_text(encoding="utf-8")) or default


def _ids(values: list[dict[str, Any]], label: str, errors: list[str]) -> set[str]:
    result: set[str] = set()
    for value in values:
        value_id = value.get("id")
        if not isinstance(value_id, str) or not value_id:
            errors.append(f"{label} entry has no non-empty id")
            continue
        if value_id in result:
            errors.append(f"duplicate {label} id: {value_id}")
        result.add(value_id)
    return result


def _validate_event_wake_references(
    data: dict[str, list[dict[str, Any]]],
    rules: dict[str, Any],
    evolution: dict[str, Any],
    errors: list[str],
) -> None:
    """Ensure declared wakes cannot bypass a consumer's evidence policy.

    Event groups are admission rules as well as batching rules.  A producer
    that names a grouped consumer must emit an event covered by one group or
    by that consumer's explicit direct-event/task allowlist.  Otherwise a
    spelling mismatch would only surface during a long simulation.
    """

    root = evolution.get("evolution", evolution) if isinstance(evolution, dict) else {}
    wake_policy = root.get("event_wake_policy", {}) if isinstance(root, dict) else {}
    consumers = wake_policy.get("consumers", {}) if isinstance(wake_policy, dict) else {}
    if not isinstance(consumers, dict):
        return

    producers: list[tuple[str, str, list[str], str]] = []

    def add(source: str, definition: Any, *, default_event: str = "", task_type: str = "") -> None:
        if not isinstance(definition, dict):
            return
        wake = definition.get("wake", definition.get("wake_roles", [])) or []
        if isinstance(wake, str):
            wake = [wake]
        roles = [str(item) for item in wake if str(item)]
        if not roles:
            return
        event_type = str(definition.get("event_type") or definition.get("event") or default_event).strip()
        if not event_type:
            errors.append(f"{source} declares wake consumers but no result event_type")
            return
        producers.append((source, event_type, roles, task_type or f"event:{event_type}"))

    for action in data.get("actions", []):
        action_id = str(action.get("id") or "action")
        add(f"action {action_id} information", action.get("information"))
        # The generic research progress channel emits this completion event.
        add(f"action {action_id} progress", action.get("progress"), default_event="research.completed")
    for behavior in data.get("behavior_trees", []):
        behavior_id = str(behavior.get("id") or "behavior")
        for index, outcome in enumerate(behavior.get("outcome_events", []) or []):
            add(f"behavior {behavior_id} outcome_events[{index}]", outcome)
    for progress_key, definition in (rules.get("civilization_progress", {}) or {}).items():
        add(f"civilization progress {progress_key}", definition, default_event="civilization.milestone")
    for rule in rules.get("world_rules", []) or []:
        if not isinstance(rule, dict):
            continue
        rule_id = str(rule.get("id") or rule.get("name") or "rule")
        add(
            f"world rule {rule_id}",
            rule,
            default_event=f"world.rule.{rule_id}",
            task_type=f"rule:{rule_id}",
        )

    produced_by_role: dict[str, set[str]] = {}
    for _, event_type, roles, _ in producers:
        for role in roles:
            produced_by_role.setdefault(role, set()).add(event_type)
    # Events are published by Platform once and then routed through each
    # consumer's own policy. A behavior does not need to name every consumer
    # in its local ``wake`` list merely to make the event a valid producer for
    # another role's threshold group.
    produced_events_all = {event_type for _, event_type, _, _ in producers}

    for source, event_type, roles, task_type in producers:
        for role in roles:
            policy = consumers.get(role)
            if not isinstance(policy, dict):
                continue
            groups = [
                item for item in (policy.get("event_groups") or policy.get("groups") or [])
                if isinstance(item, dict)
            ]
            if not groups:
                continue
            grouped_events = {
                str(value)
                for group in groups
                for value in (group.get("event_types") or [])
            }
            direct_events = {str(value) for value in (policy.get("direct_event_types") or [])}
            direct_prefixes = [str(value) for value in (policy.get("direct_task_type_prefixes") or [])]
            if event_type in grouped_events or event_type in direct_events:
                continue
            if any(task_type.startswith(prefix) for prefix in direct_prefixes if prefix):
                continue
            errors.append(
                f"{source} wakes {role} with unregistered event {event_type}; "
                "add it to an event group or the consumer's explicit direct-event policy"
            )

    for role, policy in consumers.items():
        if not isinstance(policy, dict):
            continue
        external_events = {str(value) for value in (policy.get("external_event_types") or [])}
        produced_events = produced_events_all | external_events
        for group in (policy.get("event_groups") or policy.get("groups") or []):
            if not isinstance(group, dict):
                continue
            for event_type in {str(value) for value in (group.get("event_types") or [])}:
                if event_type not in produced_events:
                    errors.append(
                        f"event group {group.get('id', 'group')} for {role} has no producer for {event_type}; "
                        "add a matching wake declaration or list it as an explicit external event"
                    )


def validate_config(config_dir: Path) -> dict[str, Any]:
    """Validate generic package references before creating a SQLite file."""

    config_dir = Path(config_dir).resolve()
    errors: list[str] = []
    manifest = load_yaml(config_dir, "manifest.yaml", {})
    if not isinstance(manifest, dict):
        return {"valid": False, "errors": ["manifest.yaml must contain an object"]}
    if not manifest.get("config_version"):
        errors.append("manifest.config_version is required")
    declared_files = manifest.get("files", [])
    if declared_files and not isinstance(declared_files, list):
        errors.append("manifest.files must be a list")
    for name in declared_files if isinstance(declared_files, list) else []:
        if not isinstance(name, str) or not name or Path(name).is_absolute() or ".." in Path(name).parts:
            errors.append(f"invalid manifest file path: {name!r}")
        elif not (config_dir / name).is_file():
            errors.append(f"declared config file is missing: {name}")

    data: dict[str, list[dict[str, Any]]] = {}
    for filename, key in CONFIG_COLLECTIONS.items():
        values = load_yaml(config_dir, filename, {}).get(key, [])
        if not isinstance(values, list):
            errors.append(f"{filename}.{key} must be a list")
            values = []
        data[key] = values
    core_behaviors = load_yaml(config_dir, "core_behavior_trees.yaml", {}).get("behavior_trees", [])
    if not isinstance(core_behaviors, list):
        errors.append("core_behavior_trees.yaml.behavior_trees must be a list")
        core_behaviors = []
    data["behavior_trees"] = list(core_behaviors) + data["behavior_trees"]
    for attribute in data["attributes"]:
        if not isinstance(attribute, dict) or not attribute.get("id"):
            continue
        for field in ("kind", "min", "max", "default", "label", "prompt"):
            if field not in attribute:
                errors.append(f"attribute {attribute.get('id')} is missing {field}")
        if attribute.get("kind") == "state" and attribute.get("cost_mode") not in {"increase", "decrease"}:
            errors.append(f"state attribute {attribute.get('id')} must declare cost_mode")
    ids_by_key = {key: _ids(values, key, errors) for key, values in data.items()}

    location_ids = ids_by_key["locations"]
    action_ids = ids_by_key["actions"]
    behavior_ids = ids_by_key["behavior_trees"]
    agent_ids = ids_by_key["agents"]
    object_ids = ids_by_key["objects"]
    faction_ids = ids_by_key["factions"]
    known_entities = location_ids | object_ids | agent_ids | faction_ids

    for location in data["locations"]:
        for route in location.get("routes", []):
            if route not in location_ids:
                errors.append(f"location {location.get('id')} references unknown route {route}")
    for obj in data["objects"]:
        location_id = obj.get("initial_state", {}).get("location_id")
        if location_id and location_id not in location_ids:
            errors.append(f"object {obj.get('id')} references unknown location {location_id}")
    for action in data["actions"]:
        if not isinstance(action.get("id"), str):
            continue
        for parameter in (action.get("parameters") or {}).values():
            if isinstance(parameter, dict) and parameter.get("entity_ref") and parameter["entity_ref"] not in known_entities:
                errors.append(f"action {action.get('id')} references unknown entity {parameter['entity_ref']}")
    for behavior in data["behavior_trees"]:
        for node in behavior.get("nodes", []):
            if not isinstance(node, dict):
                errors.append(f"behavior {behavior.get('id')} contains a non-object node")
                continue
            if node.get("type") == "action" and node.get("action_id") not in action_ids:
                errors.append(f"behavior {behavior.get('id')} references unknown action {node.get('action_id')}")
            if node.get("type") in {"subtree", "behavior"} and node.get("behavior_id") not in behavior_ids:
                errors.append(f"behavior {behavior.get('id')} references unknown behavior {node.get('behavior_id')}")
            if node.get("type") in {"selector", "sequence", "parallel"}:
                for child in node.get("children", []):
                    if isinstance(child, str) and child not in behavior_ids:
                        errors.append(f"behavior {behavior.get('id')} references unknown child behavior {child}")
    for agent in data["agents"]:
        for behavior_id in agent.get("initial_bt_ids", []):
            if behavior_id not in behavior_ids:
                errors.append(f"agent {agent.get('id')} references unknown behavior {behavior_id}")
        for faction_id in agent.get("faction_ids", []):
            if faction_id not in faction_ids:
                errors.append(f"agent {agent.get('id')} references unknown faction {faction_id}")
    player_template = load_yaml(config_dir, "player_template.yaml", {}).get("player", {})
    for behavior_id in player_template.get("initial_bt_ids", []) or []:
        if behavior_id not in behavior_ids:
            errors.append(f"player template references unknown behavior {behavior_id}")
    for faction in data["factions"]:
        leader_id = faction.get("leader_id")
        if leader_id and leader_id not in agent_ids:
            errors.append(f"faction {faction.get('id')} references unknown leader {leader_id}")
    relationships = load_yaml(config_dir, "factions.yaml", {}).get("relationships", [])
    for relation in relationships:
        if relation.get("from") not in agent_ids:
            errors.append(f"relationship references unknown source {relation.get('from')}")
        if relation.get("to") not in agent_ids and relation.get("to") != "player":
            errors.append(f"relationship references unknown target {relation.get('to')}")

    rules = load_yaml(config_dir, "rules.yaml", {}) or {}
    try:
        evolution = normalize_evolution_config(load_yaml(config_dir, "evolution.yaml", {}) or {})
    except (TypeError, ValueError) as exc:
        errors.append(f"evolution.yaml: {exc}")
        evolution = {}
    _validate_event_wake_references(data, rules, evolution, errors)

    return {"valid": not errors, "errors": errors, "manifest": manifest}


def create_instance(config_dir: Path, instance_id: str, runtime_root: Path | None = None) -> Path:
    config_dir = Path(config_dir).resolve()
    validation = validate_config(config_dir)
    if not validation["valid"]:
        raise ValueError("Invalid world configuration: " + "; ".join(validation["errors"]))
    runtime_root = Path(runtime_root or (Path.home() / ".sim2"))
    db_path = runtime_root / "instances" / instance_id / "world.sqlite3"
    if db_path.exists():
        raise FileExistsError(f"world instance already exists: {instance_id}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = WorldStore(db_path)
    try:
        manifest = validation["manifest"]
        with store.transaction():
            store.set_meta("world_id", manifest.get("world_id", instance_id))
            store.set_meta("instance_id", instance_id)
            store.set_meta("config_version", manifest.get("config_version", "unknown"))
            store.set_meta("config_hash", config_hash(config_dir))
            store.set_meta("world_minute", manifest.get("start_world_minute", 0))
            store.set_meta("time_unit", manifest.get("time_unit", "minute"))
            store.set_meta("runtime_status", "running")
            store.set_meta("random_seed", manifest.get("random_seed"))

            setting = load_yaml(config_dir, "setting.yaml", {})
            store.set_meta("setting", setting)
            world_state = load_yaml(config_dir, "world_state.yaml", {}).get("world_state", {})
            store.set_meta("world_properties", world_state.get("properties", {}))
            rules = load_yaml(config_dir, "rules.yaml", {})
            store.set_meta("rules", rules)
            store.set_meta("attributes", load_yaml(config_dir, "attributes.yaml", {}).get("attributes", []))
            attribute_schema = AttributeSchema(store.get_meta("attributes", []) or [], rules)
            default_needs = attribute_schema.need_defaults()
            for item in (rules.get("needs", []) or []):
                if isinstance(item, dict) and item.get("id"):
                    default_needs.setdefault(str(item["id"]), float(item.get("target", 100)))
            evolution = normalize_evolution_config(load_yaml(config_dir, "evolution.yaml", {}) or {})
            store.set_meta("evolution", evolution)
            story = load_yaml(config_dir, "story.yaml", {})
            store.set_meta("story", story)
            store.set_meta("story_state", {})
            store.set_meta("player_template", load_yaml(config_dir, "player_template.yaml", {}))

            for location in load_yaml(config_dir, "locations.yaml", {}).get("locations", []):
                store.put_entity(location["id"], "location", location.get("name", location["id"]), location)
            for obj in load_yaml(config_dir, "objects.yaml", {}).get("objects", []):
                store.put_entity(obj["id"], obj.get("type", "object"), obj.get("name", obj["id"]), obj)
            locations = load_yaml(config_dir, "locations.yaml", {})
            resource_baseline: dict[str, float] = {}
            for resource in locations.get("resources", []):
                store.put_entity(
                    resource["id"],
                    "resource",
                    resource.get("name", resource["id"]),
                    resource,
                )
                amount = resource.get("amount")
                state = resource.get("initial_state") if isinstance(resource.get("initial_state"), dict) else {}
                if amount is None:
                    amount = state.get("quantity", state.get("amount"))
                if isinstance(amount, (int, float)):
                    resource_baseline[str(resource["id"])] = float(amount)
                    store.sync_world_property_from_resource(str(resource["id"]), amount)
            store.set_meta("resource_baseline", resource_baseline)
            for faction in load_yaml(config_dir, "factions.yaml", {}).get("factions", []):
                store.put_entity(faction["id"], "faction", faction.get("name", faction["id"]), faction)
            for agent in load_yaml(config_dir, "agents.yaml", {}).get("agents", []):
                state = dict(agent)
                needs = dict(state.get("needs") or {})
                for need_id, value in default_needs.items():
                    needs.setdefault(need_id, value)
                state["needs"] = needs
                initial_state = dict(state.get("initial_state") or {})
                for attribute_id, value in attribute_schema.state_defaults().items():
                    initial_state.setdefault(attribute_id, value)
                state["initial_state"] = initial_state
                store.put_agent(agent["id"], state, agent.get("name", agent["id"]))
                for knowledge in agent.get("knowledge_seed", []):
                    if isinstance(knowledge, dict):
                        fact_key = str(knowledge.get("id") or knowledge.get("fact_key") or "").strip()
                        if not fact_key:
                            continue
                        fact_data = {
                            "fact": str(knowledge.get("fact") or knowledge.get("summary") or fact_key),
                            "source_type": str(knowledge.get("source_type") or "config_seed"),
                            "confidence": float(knowledge.get("confidence", 1.0)),
                            "claim_status": str(knowledge.get("claim_status") or "observed"),
                        }
                    else:
                        fact_key = str(knowledge)
                        fact_data = {"fact": fact_key, "source_type": "config_seed", "confidence": 1.0, "claim_status": "observed"}
                    store.put_knowledge_fact(agent["id"], fact_key, fact_data)
            schedule = evolution.get("evolution", evolution).get("schedule", {}) if isinstance(evolution, dict) else {}
            for agent in load_yaml(config_dir, "agents.yaml", {}).get("agents", []):
                agent_id = agent["id"]
                reflection_cadence = int(schedule.get("agent_reflection_minutes", 0) or 0)
                innovation_cadence = int(schedule.get("agent_innovation_minutes", 0) or 0)
                if reflection_cadence > 0:
                    store.put_evolution_task(f"reflection:{agent_id}", "agent_reflection", agent_id, "daily_reflection", reflection_cadence, reflection_cadence)
                if innovation_cadence > 0:
                    store.put_evolution_task(f"innovation:{agent_id}", "agent_reflection", agent_id, "weekly_innovation", innovation_cadence, innovation_cadence)
            for task_type, role, key in [
                ("god_local", "god", "god_local_minutes"),
                ("god_structural", "god", "god_structural_minutes"),
            ]:
                cadence = int(schedule.get(key, 0) or 0)
                if cadence > 0:
                    store.put_evolution_task(task_type, role, None, task_type, cadence, cadence)
            for relation in load_yaml(config_dir, "factions.yaml", {}).get("relationships", []):
                relation_data = dict(relation)
                from_id = str(relation_data.pop("from"))
                to_id = str(relation_data.pop("to"))
                relation_type = str(relation_data.pop("type", "related"))
                store.put_relationship(from_id, to_id, relation_type, relation_data)
            for table, filename, key in [
                ("action_def", "actions.yaml", "actions"),
                ("bt_def", "behavior_trees.yaml", "behavior_trees"),
                ("recipe_def", "recipes.yaml", "recipes"),
                ("capability_def", "capabilities.yaml", "capabilities"),
            ]:
                definitions = load_yaml(config_dir, filename, {}).get(key, [])
                if filename == "behavior_trees.yaml":
                    definitions = list(load_yaml(config_dir, "core_behavior_trees.yaml", {}).get("behavior_trees", []) or []) + list(definitions)
                for definition in definitions:
                    store.put_definition(table, definition["id"], definition)
            prompt_dir = config_dir / "prompts"
            if prompt_dir.is_dir():
                for prompt_path in sorted(prompt_dir.glob("*.yaml")):
                    store.put_prompt(prompt_path.stem, yaml.safe_load(prompt_path.read_text(encoding="utf-8")) or {})
            for event in load_yaml(config_dir, "opening_events.yaml", {}).get("opening_events", []):
                minute = int(event.get("world_minute", event.get("minute", 0)))
                store.append_event(event["type"], None, minute, event.get("payload", {}))
            store.set_meta("bootstrap_report", {"status": "complete", "config_dir": str(config_dir), "validation_errors": []})
        return db_path
    except Exception:
        store.close()
        shutil.rmtree(db_path.parent, ignore_errors=True)
        raise
    finally:
        try:
            store.close()
        except Exception:
            pass


def write_world_package_from_template(
    template: dict[str, Any],
    output_dir: Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Compile a WorldBuilder template into an editable YAML content package."""

    validation = validate_world_template(template)
    if not validation["valid"]:
        raise ValueError("Invalid world template: " + "; ".join(validation["errors"]))
    output_dir = Path(output_dir).resolve()
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise FileExistsError(f"world package directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    world = dict(template.get("world") or {})
    files: list[str] = []

    def values(key: str) -> list[dict[str, Any]]:
        raw = template.get(key, [])
        return [dict(item) for item in raw] if isinstance(raw, list) else []

    entities = values("entities")
    collections = {
        "locations": values("locations") or [item for item in entities if item.get("type") == "location"],
        "objects": values("objects") or [item for item in entities if item.get("type") not in {"location", "agent", "faction", "resource"}],
        "agents": values("agents") or [item for item in entities if item.get("type") == "agent"],
        "factions": values("factions") or [item for item in entities if item.get("type") == "faction"],
        "actions": values("actions"),
        "behavior_trees": values("behavior_trees"),
        "recipes": values("recipes"),
        "capabilities": values("capabilities"),
        "attributes": values("attributes"),
    }

    def write(name: str, payload: dict[str, Any]) -> None:
        (output_dir / name).write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        files.append(name)

    setting = {
        "name": world.get("title", world.get("id")),
        "premise": world.get("premise", ""),
        "genre": world.get("genre", ""),
        "locale": world.get("locale", "en"),
    }
    for optional in ("public_history", "secrets", "calendar", "world_clock"):
        if world.get(optional) is not None:
            setting[optional] = world[optional]
    write("setting.yaml", setting)
    write("world_state.yaml", {"world_state": {"properties": dict(world.get("properties") or {})}})
    write("locations.yaml", {"locations": collections["locations"], "resources": values("resources")})
    write("objects.yaml", {"objects": collections["objects"]})
    write("factions.yaml", {"factions": collections["factions"], "relationships": values("relationships")})
    for filename, key in (
        ("agents.yaml", "agents"),
        ("actions.yaml", "actions"),
        ("behavior_trees.yaml", "behavior_trees"),
        ("recipes.yaml", "recipes"),
        ("capabilities.yaml", "capabilities"),
        ("attributes.yaml", "attributes"),
    ):
        write(filename, {key: collections[key]})
    rules = template.get("rules", {})
    write("rules.yaml", dict(rules) if isinstance(rules, dict) else {"world_rules": list(rules or [])})
    write("opening_events.yaml", {"opening_events": values("initial_events") or values("opening_events")})
    write("evolution.yaml", dict(template.get("evolution") or {}))
    write("story.yaml", dict(template.get("story") or {}))
    write("player_template.yaml", dict(template.get("player_template") or {}))
    prompts = template.get("prompts") if isinstance(template.get("prompts"), dict) else {}
    if prompts:
        prompt_dir = output_dir / "prompts"
        prompt_dir.mkdir(exist_ok=True)
        for name, payload in prompts.items():
            path = f"prompts/{name}.yaml"
            (output_dir / path).write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
            files.append(path)
    manifest = {
        "config_version": str(template.get("template_version", "template-1")),
        "world_id": str(world.get("id")),
        "name": str(world.get("title") or world.get("id")),
        "time_unit": str(world.get("time_unit", "minute")),
        "start_world_minute": int(world.get("start_world_minute", 0)),
        "random_seed": world.get("random_seed"),
        "files": sorted(set(files)),
    }
    (output_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    package_validation = validate_config(output_dir)
    if not package_validation["valid"]:
        if not overwrite:
            shutil.rmtree(output_dir, ignore_errors=True)
        raise ValueError("Generated world package is invalid: " + "; ".join(package_validation["errors"]))
    return output_dir


def create_instance_from_template(template: dict[str, Any], instance_id: str, runtime_root: Path | None = None) -> Path:
    """Compile a WorldBuilder draft and bootstrap one independent instance."""

    with tempfile.TemporaryDirectory(prefix="sim2-template-") as directory:
        config_dir = write_world_package_from_template(template, Path(directory), overwrite=True)
        return create_instance(config_dir, instance_id, runtime_root)
