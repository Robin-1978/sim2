"""Replacing internal identifiers with the names a character can see."""

from __future__ import annotations

import re
from typing import Any

from .action_text import label


class VisibleTextMixin:
    """Methods of ``AgentViewCompiler`` for this concern; they use the attributes its constructor creates."""

    def _humanize_fact(self, fact: Any) -> Any:
        """Replace compact config references in facts with visible names."""

        if isinstance(fact, str) and ":" in fact:
            prefix, raw_value = fact.split(":", 1)
            visible_parts: list[str] = []
            for value in raw_value.split(":"):
                value = value.strip()
                try:
                    entity_name = self.entities_repo.get_entity(value).get("name")
                except KeyError:
                    entity_name = None
                visible_parts.append(str(entity_name if entity_name and entity_name != value else value.replace("_", " ")))
            return self._visible_text(f"{prefix.replace('_', ' ').title()}: {' — '.join(visible_parts)}")
        if isinstance(fact, dict) and "fact" in fact:
            item = dict(fact)
            item["fact"] = self._humanize_fact(item["fact"])
            return item
        return self._visible_text(fact) if isinstance(fact, str) else fact

    @staticmethod
    def _merge_visible_facts(facts: list[Any]) -> list[Any]:
        """Merge duplicate evidence while preserving every source in the prompt."""

        merged: list[Any] = []
        positions: dict[str, int] = {}
        sources: dict[str, list[str]] = {}
        for fact in facts:
            if isinstance(fact, dict):
                text = str(fact.get("fact") or fact.get("statement") or "")
                visible = dict(fact)
            else:
                text = str(fact)
                visible = fact
            key = " ".join(text.split()).casefold()
            if not key:
                continue
            source = ""
            if isinstance(fact, dict):
                source = str(fact.get("source_type") or fact.get("source") or "").strip()
            if key not in positions:
                positions[key] = len(merged)
                merged.append(visible)
                sources[key] = [source] if source else []
                continue
            if source and source not in sources[key]:
                sources[key].append(source)
            current = merged[positions[key]]
            if isinstance(current, dict) and isinstance(fact, dict):
                current["confidence"] = max(
                    float(current.get("confidence", 0) or 0),
                    float(fact.get("confidence", 0) or 0),
                )
                if len(sources[key]) > 1:
                    current["source_type"] = ", ".join(sorted(sources[key]))
        return merged

    @staticmethod
    def _with_behavior_choice(record: Any, bt_defs: dict[str, dict[str, Any]]) -> Any:
        """Attach the readable chosen behavior to outcome feedback."""

        if not isinstance(record, dict):
            return record
        item = dict(record)
        reference = str(item.get("behavior_ref") or item.get("behavior_id") or "").removeprefix("behavior:")
        behavior = bt_defs.get(reference) or {}
        if behavior:
            summary = str(behavior.get("summary") or "")
            item["behavior_choice"] = behavior.get("agent_label") or (
                summary if summary and summary != reference else label(reference)
            )
        return item

    def _visible_text(self, value: Any) -> str:
        """Replace configured entity keys in prose shown to an Agent.

        Event and discovery facts are authored as ordinary text, so a package
        can mention a location or object by its stable key.  Those keys are
        useful to SQLite but are not readable Agent choices.  Resolve exact
        entity tokens here without changing event types, fact keys, or
        free-form text that merely contains a similar word.
        """

        text = str(value)
        revision = int(self.store.repositories.metadata.get("world_revision", 0) or 0)
        if revision != self._visible_entity_revision:
            entities: list[tuple[str, str]] = []
            for entity in self.entities_repo.entities_of_type("location") + self.entities_repo.entities_of_type("agent") + self.entities_repo.entities_of_type("object"):
                key = str(entity.get("id") or "")
                name = str(entity.get("name") or key)
                if key and name:
                    entities.append((key, name if name != key else key.replace("_", " ")))
            # Include resource/material/artifact/document keys without assuming
            # a particular world vocabulary. The table is rebuilt only after a
            # committed world revision changes, instead of once per field in
            # every AgentView record.
            for entity_type in ("resource", "material", "artifact", "document", "tool", "consumable", "shared_stock", "market_stock"):
                for entity in self.entities_repo.entities_of_type(entity_type):
                    key = str(entity.get("id") or "")
                    name = str(entity.get("name") or key)
                    if key and name:
                        entities.append((key, name if name != key else key.replace("_", " ")))
            self._visible_entity_replacements = sorted(set(entities), key=lambda item: len(item[0]), reverse=True)
            self._visible_entity_revision = revision
        for key, name in self._visible_entity_replacements:
            # Entity identifiers are snake_case tokens.  Treat underscore as
            # part of a token so ``village_food`` cannot rewrite the prefix
            # of a different configured identifier such as
            # ``village_food_store``.  This keeps humanization exact without
            # adding world-specific names to the compiler.
            text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", name, text)
        return text

    def _humanize_record(self, value: Any, *, field: str = "") -> Any:
        """Apply visible entity names to nested outcome/event projections."""

        if isinstance(value, dict):
            return {str(key): self._humanize_record(item, field=str(key)) for key, item in value.items()}
        if isinstance(value, list):
            return [self._humanize_record(item, field=field) for item in value]
        if isinstance(value, str):
            return self._humanize_fact(value) if field in {"fact", "summary", "message", "reason"} else self._visible_text(value)
        return value
