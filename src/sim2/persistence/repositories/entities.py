from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..store import json_dumps


class EntitiesRepository:
    """Persistence owner for the configured entities domain."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def sync_world_property_from_resource(self, entity_id: str, amount: Any) -> dict[str, Any] | None:
        """Keep a shared resource and configured world-property projections equal."""

        if not isinstance(amount, (int, float)) or isinstance(amount, bool):
            return None
        properties = dict(self.store.get_meta("world_properties", {}) or {})
        rules = self.store.get_meta("rules", {}) or {}
        mappings = rules.get("resource_property_projections", {}) if isinstance(rules, dict) else {}
        keys = [str(entity_id)]
        if isinstance(mappings, dict):
            configured = mappings.get(str(entity_id))
            if isinstance(configured, str):
                keys.append(configured)
            elif isinstance(configured, list):
                keys.extend(str(item) for item in configured if str(item))
        changes = []
        for key in dict.fromkeys(keys):
            if key not in properties or properties.get(key) == amount:
                continue
            before = properties.get(key)
            properties[key] = amount
            changes.append({"world_property": key, "before": before, "after": amount})
        if not changes:
            return None
        self.store.set_meta("world_properties", properties)
        return changes[0] if len(changes) == 1 else {"projections": changes}

    def put_entity(self, entity_id: str, entity_type: str, name: str, data: dict[str, Any]) -> None:
        self.store.connection.execute(
            "INSERT OR REPLACE INTO entity(id,entity_type,name,data_json) VALUES (?,?,?,?)",
            (entity_id, entity_type, name, json_dumps(data)),
        )

    def put_agent(self, agent_id: str, data: dict[str, Any], name: str) -> None:
        data = dict(data)
        data.setdefault("agent_revision", 0)
        self.put_entity(agent_id, "agent", name, data)
        self.store.connection.execute(
            "INSERT OR REPLACE INTO agent_state(id,data_json) VALUES (?,?)",
            (agent_id, json_dumps(data)),
        )

    def put_definition(self, table: str, definition_id: str, data: dict[str, Any]) -> None:
        if table not in {"action_def", "bt_def", "recipe_def", "capability_def"}:
            raise ValueError(f"unsupported definition table: {table}")
        data = dict(data)
        definition_version = int(data.get("definition_version", data.get("version", 1)) or 1)
        data.setdefault("definition_version", definition_version)
        data.setdefault("status", "active")
        data.setdefault("created_revision", int(self.store.get_meta("world_revision", 0) or 0))
        self.store.connection.execute(
            f"INSERT OR REPLACE INTO {table}(id,data_json) VALUES (?,?)",
            (definition_id, json_dumps(data)),
        )

    def put_relationship(self, from_id: str, to_id: str, relation_type: str, data: dict[str, Any]) -> None:
        self.store.connection.execute(
            "INSERT OR REPLACE INTO relationship(from_id,to_id,relation_type,data_json) VALUES (?,?,?,?)",
            (from_id, to_id, relation_type, json_dumps(data)),
        )

    def get_relationships(self, from_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            "SELECT to_id,relation_type,data_json FROM relationship WHERE from_id=? ORDER BY to_id LIMIT ?",
            (from_id, max(1, min(int(limit), 50))),
        ).fetchall()
        return [{"person": row["to_id"], "relation": row["relation_type"], **json.loads(row["data_json"])} for row in rows]

    def get_related_relationships(self, entity_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            "SELECT from_id,to_id,relation_type,data_json FROM relationship WHERE from_id=? OR to_id=? ORDER BY from_id,to_id LIMIT ?",
            (entity_id, entity_id, max(1, min(int(limit), 50))),
        ).fetchall()
        result = []
        for row in rows:
            other = row["to_id"] if row["from_id"] == entity_id else row["from_id"]
            relation = json.loads(row["data_json"])
            result.append({"person": other, "relation": row["relation_type"], "direction": "outgoing" if row["from_id"] == entity_id else "incoming", **relation})
        return result

    def list_relationships(self) -> list[dict[str, Any]]:
        rows = self.store.connection.execute("SELECT from_id,to_id,relation_type,data_json FROM relationship ORDER BY from_id,to_id").fetchall()
        return [{"from": row["from_id"], "to": row["to_id"], "type": row["relation_type"], **json.loads(row["data_json"])} for row in rows]

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        row = self.store.connection.execute("SELECT data_json FROM agent_state WHERE id=?", (agent_id,)).fetchone()
        if row is None:
            raise KeyError(f"agent not found: {agent_id}")
        return json.loads(row["data_json"])

    def list_agents(self) -> list[dict[str, Any]]:
        rows = self.store.connection.execute("SELECT id,data_json FROM agent_state ORDER BY id").fetchall()
        return [{"id": row["id"], **json.loads(row["data_json"])} for row in rows]

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        row = self.store.connection.execute("SELECT entity_type,name,data_json FROM entity WHERE id=?", (entity_id,)).fetchone()
        if row is None:
            raise KeyError(f"entity not found: {entity_id}")
        data = json.loads(row["data_json"])
        data.update({"id": entity_id, "entity_type": row["entity_type"], "name": row["name"]})
        return data

    def entities_at_location(self, location_id: str) -> list[dict[str, Any]]:
        rows = self.store.connection.execute("SELECT id,entity_type,name,data_json FROM entity").fetchall()
        result = []
        for row in rows:
            data = json.loads(row["data_json"])
            state = data.get("initial_state", {})
            if state.get("location_id") == location_id:
                result.append({
                    "id": row["id"],
                    "type": row["entity_type"],
                    "entity_type": row["entity_type"],
                    "name": row["name"],
                    "summary": data.get("role", data.get("type", row["entity_type"])),
                    "initial_state": state,
                    "amount": data.get("amount"),
                })
        return result

    def entities_of_type(self, entity_type: str) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            "SELECT id,entity_type,name,data_json FROM entity WHERE entity_type=? ORDER BY id",
            (entity_type,),
        ).fetchall()
        result = []
        for row in rows:
            data = json.loads(row["data_json"])
            result.append({"id": row["id"], "entity_type": row["entity_type"], "name": row["name"], "summary": data.get("description", data.get("role", entity_type))})
        return result

    def get_definitions(self, table: str) -> list[dict[str, Any]]:
        if table not in {"action_def", "bt_def", "recipe_def", "capability_def"}:
            raise ValueError(f"unsupported definition table: {table}")
        rows = self.store.connection.execute(f"SELECT data_json FROM {table} ORDER BY id").fetchall()
        return [json.loads(row["data_json"]) for row in rows]

    def update_agent(self, agent_id: str, data: dict[str, Any]) -> None:
        data = dict(data)
        data["agent_revision"] = int(data.get("agent_revision", 0) or 0) + 1
        self.store.connection.execute("UPDATE agent_state SET data_json=? WHERE id=?", (json_dumps(data), agent_id))
        self.store.connection.execute("UPDATE entity SET data_json=? WHERE id=?", (json_dumps(data), agent_id))

    def update_agents_bulk(self, agents: list[dict[str, Any]]) -> None:
        rows = []
        for agent in agents:
            item = dict(agent)
            item["agent_revision"] = int(item.get("agent_revision", 0) or 0) + 1
            agent["agent_revision"] = item["agent_revision"]
            rows.append((json_dumps(item), item["id"]))
        self.store.connection.executemany("UPDATE agent_state SET data_json=? WHERE id=?", rows)
        self.store.connection.executemany("UPDATE entity SET data_json=? WHERE id=?", rows)

