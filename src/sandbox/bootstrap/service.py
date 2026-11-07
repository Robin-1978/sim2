from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

from ..persistence.store import WorldStore, config_hash


def load_yaml(config_dir: Path, name: str, default: Any) -> Any:
    path = config_dir / name
    if not path.exists():
        return default
    return yaml.safe_load(path.read_text(encoding="utf-8")) or default


def create_instance(
    config_dir: Path,
    instance_id: str,
    runtime_root: Path | None = None,
) -> Path:
    config_dir = Path(config_dir).resolve()
    runtime_root = Path(runtime_root or (Path.home() / ".sim2"))
    db_path = runtime_root / "instances" / instance_id / "world.sqlite3"
    if db_path.exists():
        raise FileExistsError(f"world instance already exists: {instance_id}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = WorldStore(db_path)
    try:
        manifest = load_yaml(config_dir, "manifest.yaml", {})
        with store.transaction():
            store.set_meta("world_id", manifest.get("world_id", instance_id))
            store.set_meta("instance_id", instance_id)
            store.set_meta("config_version", manifest.get("config_version", "unknown"))
            store.set_meta("config_hash", config_hash(config_dir))
            store.set_meta("world_minute", manifest.get("start_world_minute", 0))
            store.set_meta("time_unit", manifest.get("time_unit", "minute"))

            setting = load_yaml(config_dir, "setting.yaml", {})
            store.set_meta("setting", setting)
            world_state = load_yaml(config_dir, "world_state.yaml", {}).get("world_state", {})
            store.set_meta("world_properties", world_state.get("properties", {}))
            store.set_meta("rules", load_yaml(config_dir, "rules.yaml", {}))

            for location in load_yaml(config_dir, "locations.yaml", {}).get("locations", []):
                store.put_entity(location["id"], "location", location.get("name", location["id"]), location)
            for obj in load_yaml(config_dir, "objects.yaml", {}).get("objects", []):
                store.put_entity(obj["id"], obj.get("type", "object"), obj.get("name", obj["id"]), obj)
            for npc in load_yaml(config_dir, "agents.yaml", {}).get("agents", []):
                state = dict(npc)
                store.put_agent(npc["id"], state, npc.get("name", npc["id"]))
            for table, filename, key in [
                ("action_def", "actions.yaml", "actions"),
                ("bt_def", "behavior_trees.yaml", "behavior_trees"),
                ("recipe_def", "recipes.yaml", "recipes"),
                ("capability_def", "capabilities.yaml", "capabilities"),
            ]:
                for definition in load_yaml(config_dir, filename, {}).get(key, []):
                    store.put_definition(table, definition["id"], definition)

            for event in load_yaml(config_dir, "opening_events.yaml", {}).get("opening_events", []):
                minute = int(event.get("world_minute", event.get("minute", 0)))
                store.append_event(event["type"], None, minute, event.get("payload", {}))
            store.set_meta("bootstrap_report", {"status": "complete", "config_dir": str(config_dir)})
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
