from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from ..persistence.store import WorldStore, config_hash


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
            default_needs = {
                str(item["id"]): float(item.get("target", 100))
                for item in (rules.get("needs", []) or [])
                if isinstance(item, dict) and item.get("id")
            }
            evolution = load_yaml(config_dir, "evolution.yaml", {})
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
                store.put_entity(resource["id"], "resource", resource.get("id", "resource"), resource)
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
                store.put_agent(agent["id"], state, agent.get("name", agent["id"]))
                for knowledge in agent.get("knowledge_seed", []):
                    fact_key = str(knowledge)
                    store.put_knowledge_fact(agent["id"], fact_key, {"fact": fact_key, "source_type": "config_seed", "confidence": 1.0})
            schedule = evolution.get("evolution", {}).get("schedule", {}) if isinstance(evolution, dict) else {}
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


def create_instance_from_template(template: dict[str, Any], instance_id: str, runtime_root: Path | None = None) -> Path:
    """Normalize a WorldBuilder draft into a temporary package and bootstrap it."""

    if not isinstance(template, dict):
        raise ValueError("world template must be an object")
    world = template.get("world", {}) if isinstance(template.get("world", {}), dict) else {}
    with tempfile.TemporaryDirectory(prefix="sim2-template-") as directory:
        config_dir = Path(directory)
        collections: dict[str, list[dict[str, Any]]] = {}

        def values(key: str) -> list[dict[str, Any]]:
            raw = template.get(key, [])
            return list(raw) if isinstance(raw, list) else []

        entities = values("entities")
        collections["locations"] = values("locations") or [item for item in entities if item.get("type") == "location"]
        collections["objects"] = values("objects") or [item for item in entities if item.get("type") not in {"location", "agent", "faction"}]
        collections["agents"] = values("agents") or [item for item in entities if item.get("type") == "agent"]
        collections["factions"] = values("factions") or [item for item in entities if item.get("type") == "faction"]
        collections["actions"] = values("actions")
        collections["behavior_trees"] = values("behavior_trees")
        collections["recipes"] = values("recipes")
        collections["capabilities"] = values("capabilities")
        files: list[str] = []

        def write(name: str, payload: dict[str, Any]) -> None:
            (config_dir / name).write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
            files.append(name)

        write("setting.yaml", {"name": world.get("title", world.get("id", instance_id)), "premise": world.get("premise", "")})
        write("world_state.yaml", {"world_state": {"properties": world.get("properties", {})}})
        for filename, key in CONFIG_COLLECTIONS.items():
            if key in collections:
                write(filename, {key: collections[key]})
        faction_payload = {"factions": collections["factions"], "relationships": template.get("relationships", [])}
        write("factions.yaml", faction_payload)
        rules = template.get("rules", {})
        write("rules.yaml", rules if isinstance(rules, dict) else {"world_rules": rules})
        write("opening_events.yaml", {"opening_events": template.get("initial_events", template.get("opening_events", []))})
        write("evolution.yaml", template.get("evolution", {}))
        write("player_template.yaml", template.get("player_template", {}))
        manifest = {
            "config_version": str(template.get("template_version", "template-1")),
            "world_id": str(world.get("id", instance_id)),
            "time_unit": world.get("time_unit", "minute"),
            "start_world_minute": int(world.get("start_world_minute", 0)),
            "files": sorted(set(files)),
        }
        (config_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return create_instance(config_dir, instance_id, runtime_root)
