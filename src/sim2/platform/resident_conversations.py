"""Package-authored conversation topics for behavior-tree residents."""

from __future__ import annotations

from typing import Any


def resident_conversation(rules: dict[str, Any], resident_id: str) -> dict[str, Any]:
    conversations = rules.get("resident_conversations") or {}
    if not isinstance(conversations, dict) or not conversations:
        return {}
    default = conversations.get("default") or {}
    profile = (conversations.get("residents") or {}).get(resident_id) or {}
    return {**default, **profile, "topics": {**(default.get("topics") or {}), **(profile.get("topics") or {})}}


def authored_reply(store: Any, conditions: Any, resident: dict[str, Any], topic: str) -> str:
    rules = store.repositories.metadata.get("rules", {}) or {}
    profile = resident_conversation(rules, str(resident["id"]))
    entry = profile.get("topics", {}).get(topic)
    if not isinstance(entry, dict):
        return ""
    world = store.repositories.metadata.get("world_properties", {}) or {}
    for line in entry.get("lines") or []:
        condition = line.get("when")
        if not condition or conditions.evaluate(str(condition), agent=resident, world=world):
            return str(line.get("text") or "")
    return ""
