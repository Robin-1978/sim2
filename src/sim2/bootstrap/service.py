from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from ..actions.conditions import ConditionEngine
from ..evolution.config import normalize_evolution_config
from ..persistence.store import WorldStore, config_hash
from ..world.lifecycle import validate_incapacitation_rule
from ..world.player_template import PlayerTemplateError, parse_player_template
from ..world.schema import AttributeSchema
from ..world.ui_text import validate_ui_text
from .catalog_validation import validate_action_catalogs
from .template_validation import validate_world_template


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
            "route_distances": {
                "type": "object",
                "description": "Optional positive distance per route neighbor; omitted neighbors use one distance unit.",
                "additionalProperties": {"type": "number", "exclusiveMinimum": 0},
            },
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
        "required": ["id", "description", "parameters", "preconditions", "duration", "costs", "success", "failure"],
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
        "required": ["id", "summary", "nodes"],
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
            "controller": {"type": "string", "enum": ["llm", "bt", "bt_only", "behavior_tree"]},
            "bt_priority": {"type": "array", "items": {"type": "string"}},
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
            "incapacitation": {
                "type": "object",
                "description": "Optional way out of unconsciousness; without it a character whose vital attribute reaches its minimum stays unconscious.",
                "properties": {
                    "recovery": {
                        "type": "object",
                        "properties": {
                            "after_minutes": {"type": "integer", "minimum": 1},
                            "vital_fraction": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                        },
                        "required": ["after_minutes", "vital_fraction"],
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            },
            "fallback_behavior_ids": {"type": "array", "items": {"type": "string"}},
            "route_minutes_per_hop": {"type": "number", "exclusiveMinimum": 0},
            "travel": {
                "type": "object",
                "description": "Generic movement conversion and optional actor/transport speed fields.",
                "properties": {
                    "minutes_per_distance": {"type": "number", "exclusiveMinimum": 0},
                    "default_speed": {"type": "number", "exclusiveMinimum": 0},
                    "agent_speed_attribute": {"type": "string"},
                    "transport_parameter": {"type": "string"},
                    "transport_state_key": {"type": "string"},
                    "transport_speed_field": {"type": "string"},
                    "transport_speed_mode": {"type": "string", "enum": ["absolute", "multiplier"]},
                },
                "additionalProperties": True,
            },
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


def world_package_template(config_dir: Path) -> dict[str, Any]:
    """Read a reviewed package back into the unified authoring template.

    This is the inverse of ``write_world_package_from_template`` for the
    browser workbench.  It intentionally reads only declarative YAML files and
    never opens a running SQLite instance.
    """

    config_dir = Path(config_dir).resolve()
    manifest = load_yaml(config_dir, "manifest.yaml", {}) or {}
    setting = load_yaml(config_dir, "setting.yaml", {}) or {}
    world_state = load_yaml(config_dir, "world_state.yaml", {}).get("world_state", {}) or {}
    locations_file = load_yaml(config_dir, "locations.yaml", {}) or {}
    factions_file = load_yaml(config_dir, "factions.yaml", {}) or {}
    initial_events = load_yaml(config_dir, "opening_events.yaml", {}).get("opening_events", []) or []
    world = {
        "id": manifest.get("world_id") or manifest.get("name") or config_dir.name,
        "title": setting.get("name") or manifest.get("name") or config_dir.name,
        "premise": setting.get("premise", ""),
        "genre": setting.get("genre", ""),
        "locale": setting.get("locale", "en"),
        "time_unit": manifest.get("time_unit", "minute"),
        "start_world_minute": manifest.get("start_world_minute", 0),
        "random_seed": manifest.get("random_seed"),
        "properties": dict(world_state.get("properties", {}) or {}),
    }
    for key in ("public_history", "secrets", "calendar", "world_clock", "ui_text"):
        if key in setting:
            world[key] = setting[key]

    def collection(filename: str, key: str) -> list[dict[str, Any]]:
        value = load_yaml(config_dir, filename, {}).get(key, [])
        return [dict(item) for item in value] if isinstance(value, list) else []

    prompts: dict[str, Any] = {}
    prompt_dir = config_dir / "prompts"
    if prompt_dir.is_dir():
        for prompt_path in sorted(prompt_dir.glob("*.yaml")):
            prompts[prompt_path.stem] = load_yaml(prompt_dir, prompt_path.name, {})
    behavior_trees = collection("behavior_trees.yaml", "behavior_trees")
    behavior_trees = collection("core_behavior_trees.yaml", "behavior_trees") + behavior_trees
    return {
        "template_version": str(manifest.get("config_version", "draft-1")),
        "world": world,
        "attributes": collection("attributes.yaml", "attributes"),
        "locations": collection("locations.yaml", "locations"),
        "resources": [dict(item) for item in (locations_file.get("resources", []) or []) if isinstance(item, dict)],
        "objects": collection("objects.yaml", "objects"),
        "factions": collection("factions.yaml", "factions"),
        "agents": collection("agents.yaml", "agents"),
        "capabilities": collection("capabilities.yaml", "capabilities"),
        "actions": collection("actions.yaml", "actions"),
        "behavior_trees": behavior_trees,
        "recipes": collection("recipes.yaml", "recipes"),
        "relationships": [dict(item) for item in (factions_file.get("relationships", []) or []) if isinstance(item, dict)],
        "rules": load_yaml(config_dir, "rules.yaml", {}) or {},
        "evolution": load_yaml(config_dir, "evolution.yaml", {}) or {},
        "story": load_yaml(config_dir, "story.yaml", {}) or {},
        "player_template": load_yaml(config_dir, "player_template.yaml", {}) or {},
        "initial_events": [dict(item) for item in initial_events if isinstance(item, dict)],
        "prompts": prompts,
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
        event_type = str(definition.get("event_type") or default_event).strip()
        if not event_type:
            if roles:
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
    for catalog_id, entries in (rules.get("action_catalogs", {}) or {}).items():
        for index, entry in enumerate(entries if isinstance(entries, list) else []):
            event_type = str((entry or {}).get("event_type") or "").strip() if isinstance(entry, dict) else ""
            if event_type:
                producers.append((f"catalog {catalog_id} entry {index}", event_type, [], f"catalog:{catalog_id}"))
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
                item for item in (policy.get("event_groups") or [])
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
            # A manual-only event is exactly the one an explicit producer
            # ``wake`` list may deliver, so it is a declared admission too.
            direct_events |= {str(value) for value in (policy.get("manual_event_types") or [])}
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
        for group in (policy.get("event_groups") or []):
            if not isinstance(group, dict):
                continue
            for event_type in {str(value) for value in (group.get("event_types") or [])}:
                if event_type not in produced_events:
                    errors.append(
                        f"event group {group.get('id', 'group')} for {role} has no producer for {event_type}; "
                        "add a matching wake declaration or list it as an explicit external event"
                    )

    # ``world.stagnation`` is derived from state and exists only when the
    # package configures a stagnation window; otherwise the wake can never fire.
    stagnation_consumers = sorted(
        str(role)
        for role, policy in consumers.items()
        if isinstance(policy, dict) and "world.stagnation" in (
            {str(value) for value in (policy.get("direct_event_types") or [])}
            | {str(value) for value in (policy.get("manual_event_types") or [])}
            | {
                str(value)
                for group in (policy.get("event_groups") or [])
                if isinstance(group, dict)
                for value in (group.get("event_types") or [])
            }
        )
    )
    if stagnation_consumers and not (isinstance(root, dict) and isinstance(root.get("stagnation"), dict)):
        errors.append(
            "wake on world.stagnation for " + ", ".join(stagnation_consumers)
            + " needs an evolution stagnation window, for example stagnation: {window: 1d}"
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

    rules = load_yaml(config_dir, "rules.yaml", {}) or {}
    resources = load_yaml(config_dir, "locations.yaml", {}).get("resources", [])
    if not isinstance(resources, list):
        errors.append("locations.yaml.resources must be a list")
        resources = []
    else:
        for item in resources:
            if not isinstance(item, dict):
                errors.append("locations.yaml.resources entries must be objects")
        resources = [item for item in resources if isinstance(item, dict)]
    resource_ids = _ids(resources, "resources", errors)

    location_ids = ids_by_key["locations"]
    action_ids = ids_by_key["actions"]
    behavior_ids = ids_by_key["behavior_trees"]
    agent_ids = ids_by_key["agents"]
    object_ids = ids_by_key["objects"]
    faction_ids = ids_by_key["factions"]
    known_entities = location_ids | object_ids | agent_ids | faction_ids | resource_ids

    attributes = [item for item in data["attributes"] if isinstance(item, dict)]
    state_ids = {str(item.get("id")) for item in attributes if item.get("kind") == "state" and item.get("id")}
    need_ids = {str(item.get("id")) for item in attributes if item.get("kind") == "need" and item.get("id")}
    need_ids.update(
        str(item.get("id"))
        for item in (rules.get("needs") or [])
        if isinstance(item, dict) and item.get("id")
    )
    world_state = load_yaml(config_dir, "world_state.yaml", {}) or {}
    world_state_root = world_state.get("world_state") if isinstance(world_state, dict) else {}
    world_properties = set(
        (((world_state_root or {}).get("properties") or {}) if isinstance(world_state_root, dict) else {}).keys()
    )
    errors.extend(validate_action_catalogs(
        rules,
        resources=resources,
        location_ids=location_ids,
        condition_engine=ConditionEngine(rules),
        state_ids=state_ids,
        need_ids=need_ids,
        world_properties={str(item) for item in world_properties},
        item_labels=rules.get("item_labels") if isinstance(rules.get("item_labels"), dict) else {},
    ))

    for location in data["locations"]:
        for route in location.get("routes", []):
            if route not in location_ids:
                errors.append(f"location {location.get('id')} references unknown route {route}")
        route_distances = location.get("route_distances", {})
        if route_distances is not None and not isinstance(route_distances, dict):
            errors.append(f"location {location.get('id')} route_distances must be an object")
        elif isinstance(route_distances, dict):
            route_ids = {str(item) for item in (location.get("routes", []) or [])}
            for route, distance in route_distances.items():
                if str(route) not in route_ids:
                    errors.append(f"location {location.get('id')} route_distances references non-route {route}")
                if isinstance(distance, bool) or not isinstance(distance, (int, float)) or float(distance) <= 0:
                    errors.append(f"location {location.get('id')} route distance for {route} must be positive")
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
    try:
        player_template = parse_player_template(load_yaml(config_dir, "player_template.yaml", {}))
    except PlayerTemplateError as exc:
        errors.append(f"player_template.yaml: {exc}")
        player_template = {}
    entry_location_id = str(player_template.get("entry_location_id") or "")
    if entry_location_id and entry_location_id not in location_ids:
        errors.append(f"player template references unknown entry location {entry_location_id}")
    for behavior_id in player_template.get("initial_bt_ids", []) or []:
        if behavior_id not in behavior_ids:
            errors.append(f"player template references unknown behavior {behavior_id}")
    for faction_id in player_template.get("initial_faction_ids", []) or []:
        if faction_id not in faction_ids:
            errors.append(f"player template references unknown faction {faction_id}")
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

    errors.extend(validate_incapacitation_rule(rules))
    setting = load_yaml(config_dir, "setting.yaml", {})
    errors.extend(validate_ui_text(setting.get("ui_text") if isinstance(setting, dict) else None))
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
            store.repositories.metadata.set("world_id", manifest.get("world_id", instance_id))
            store.repositories.metadata.set("instance_id", instance_id)
            store.repositories.metadata.set("config_version", manifest.get("config_version", "unknown"))
            store.repositories.metadata.set("config_hash", config_hash(config_dir))
            store.repositories.metadata.set("world_minute", manifest.get("start_world_minute", 0))
            store.repositories.metadata.set("time_unit", manifest.get("time_unit", "minute"))
            store.repositories.metadata.set("runtime_status", "running")
            store.repositories.metadata.set("random_seed", manifest.get("random_seed"))

            setting = load_yaml(config_dir, "setting.yaml", {})
            store.repositories.metadata.set("setting", setting)
            world_state = load_yaml(config_dir, "world_state.yaml", {}).get("world_state", {})
            store.repositories.metadata.set("world_properties", world_state.get("properties", {}))
            rules = load_yaml(config_dir, "rules.yaml", {})
            store.repositories.metadata.set("rules", rules)
            store.repositories.metadata.set("attributes", load_yaml(config_dir, "attributes.yaml", {}).get("attributes", []))
            attribute_schema = AttributeSchema(store.repositories.metadata.get("attributes", []) or [], rules)
            default_needs = attribute_schema.need_defaults()
            for item in (rules.get("needs", []) or []):
                if isinstance(item, dict) and item.get("id"):
                    default_needs.setdefault(str(item["id"]), float(item.get("target", 100)))
            evolution = normalize_evolution_config(load_yaml(config_dir, "evolution.yaml", {}) or {})
            store.repositories.metadata.set("evolution", evolution)
            story = load_yaml(config_dir, "story.yaml", {})
            store.repositories.metadata.set("story", story)
            store.repositories.metadata.set("story_state", {})
            store.repositories.metadata.set(
                "player_template",
                parse_player_template(load_yaml(config_dir, "player_template.yaml", {})),
            )

            for location in load_yaml(config_dir, "locations.yaml", {}).get("locations", []):
                store.repositories.entities.put_entity(location["id"], "location", location.get("name", location["id"]), location)
            for obj in load_yaml(config_dir, "objects.yaml", {}).get("objects", []):
                store.repositories.entities.put_entity(obj["id"], obj.get("type", "object"), obj.get("name", obj["id"]), obj)
            locations = load_yaml(config_dir, "locations.yaml", {})
            resource_baseline: dict[str, float] = {}
            for resource in locations.get("resources", []):
                store.repositories.entities.put_entity(
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
                    store.repositories.entities.sync_world_property_from_resource(str(resource["id"]), amount)
            store.repositories.metadata.set("resource_baseline", resource_baseline)
            # Seed one canonical knowledge gap per configured research source.
            # God reviews and advances these gaps instead of inventing a new
            # document for every completion event.
            for source in store.repositories.memory.research_source_catalog():
                source_entity = store.repositories.entities.get_entity(str(source["id"]))
                source_state = source_entity.get("initial_state") if isinstance(source_entity.get("initial_state"), dict) else {}
                discoveries = source.get("discoveries") or []
                question = str(source_state.get("research_question") or "")
                if not question:
                    question = str(next((item.get("fact") for item in discoveries if isinstance(item, dict) and item.get("fact")), source.get("name", "research source")))
                gap_key = str(source_state.get("research_novelty_key") or f"source:{source['id']}")
                store.repositories.memory.upsert_knowledge_gap(gap_key, "active", gap_key, question, source_id=str(source["id"]), data={"seeded": True})
            for faction in load_yaml(config_dir, "factions.yaml", {}).get("factions", []):
                store.repositories.entities.put_entity(faction["id"], "faction", faction.get("name", faction["id"]), faction)
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
                store.repositories.entities.put_agent(agent["id"], state, agent.get("name", agent["id"]))
                for target_id in agent.get("stewardship", []) or []:
                    store.repositories.entities.put_relationship(
                        agent["id"], str(target_id), "stewardship",
                        {"scope": "stewardship", "source": "configured"},
                    )
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
                    store.repositories.memory.put_knowledge_fact(agent["id"], fact_key, fact_data)
            schedule = evolution.get("evolution", evolution).get("schedule", {}) if isinstance(evolution, dict) else {}
            for agent in load_yaml(config_dir, "agents.yaml", {}).get("agents", []):
                agent_id = agent["id"]
                reflection_cadence = int(schedule.get("agent_reflection_minutes", 0) or 0)
                innovation_cadence = int(schedule.get("agent_innovation_minutes", 0) or 0)
                controller = str(agent.get("controller") or "llm").casefold()
                if controller in {"bt", "bt_only", "behavior_tree"}:
                    reflection_cadence = 0
                    innovation_cadence = 0
                if reflection_cadence > 0:
                    store.repositories.evolution.put_evolution_task(f"reflection:{agent_id}", "agent_reflection", agent_id, "daily_reflection", reflection_cadence, reflection_cadence)
                if innovation_cadence > 0:
                    store.repositories.evolution.put_evolution_task(f"innovation:{agent_id}", "agent_reflection", agent_id, "weekly_innovation", innovation_cadence, innovation_cadence)
            for task_type, role, key in [
                ("god_local", "god", "god_local_minutes"),
                ("god_structural", "god", "god_structural_minutes"),
            ]:
                cadence = int(schedule.get(key, 0) or 0)
                if cadence > 0:
                    store.repositories.evolution.put_evolution_task(task_type, role, None, task_type, cadence, cadence)
            for relation in load_yaml(config_dir, "factions.yaml", {}).get("relationships", []):
                relation_data = dict(relation)
                from_id = str(relation_data.pop("from"))
                to_id = str(relation_data.pop("to"))
                relation_type = str(relation_data.pop("type", "related"))
                store.repositories.entities.put_relationship(from_id, to_id, relation_type, relation_data)
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
                    store.repositories.entities.put_definition(table, definition["id"], definition)
            prompt_dir = config_dir / "prompts"
            if prompt_dir.is_dir():
                for prompt_path in sorted(prompt_dir.glob("*.yaml")):
                    store.repositories.memory.put_prompt(prompt_path.stem, yaml.safe_load(prompt_path.read_text(encoding="utf-8")) or {})
            for event in load_yaml(config_dir, "opening_events.yaml", {}).get("opening_events", []):
                minute = int(event.get("world_minute", event.get("minute", 0)))
                store.repositories.events.append_event(event["type"], None, minute, event.get("payload", {}))
            store.repositories.metadata.set("bootstrap_report", {"status": "complete", "config_dir": str(config_dir), "validation_errors": []})
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
    validate_template: bool = True,
) -> Path:
    """Compile a WorldBuilder template into an editable YAML content package."""

    if validate_template:
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
    for optional in ("public_history", "secrets", "calendar", "world_clock", "ui_text"):
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
