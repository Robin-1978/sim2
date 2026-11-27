"""Match a later character event to a registered unit using the world's evidence specification."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any

import yaml

from ..dsl.evaluation import UNKNOWN, evaluate_tree
from ..dsl.expressions import parse_expression, reference_paths


@lru_cache(maxsize=1)
def _profiles() -> dict:
    return yaml.safe_load(files(__package__).joinpath("growth_evidence.yaml").read_text(encoding="utf-8"))


def evidence_rules(spec: Any) -> list:
    if isinstance(spec, str):
        if spec not in _profiles():
            raise ValueError(f"unknown growth evidence profile: {spec}")
        return _profiles()[spec]
    return spec


def validate_evidence(spec: Any) -> None:
    rules = evidence_rules(spec)
    if not isinstance(rules, list) or not rules:
        raise ValueError("growth evidence requires at least one registered/used rule")
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"registered", "used"}:
            raise ValueError("growth evidence rule requires registered and used selectors")
        widths = set()
        for selectors in rule.values():
            if not isinstance(selectors, list) or not selectors:
                raise ValueError("growth evidence selectors must be a non-empty list")
            for selector in selectors:
                if not isinstance(selector, dict) or set(selector) - {"event", "key", "when", "label"}:
                    raise ValueError("unknown growth evidence selector field")
                if not isinstance(selector.get("event"), str) or not selector["event"].strip():
                    raise ValueError("growth evidence selector requires an event name")
                key = selector.get("key")
                if not isinstance(key, list) or not key:
                    raise ValueError("growth evidence key must list the identifying event fields")
                paths = key + ([selector["label"]] if "label" in selector else [])
                if any(not isinstance(p, str) or not re.fullmatch(r"\w+(?:\.\w+)*", p) for p in paths):
                    raise ValueError("invalid field path in growth evidence selector")
                widths.add(len(key))
                if "when" in selector:
                    if not isinstance(selector["when"], str):
                        raise ValueError("growth evidence when must be a DSL expression")
                    tree = parse_expression(selector["when"])
                    if any(not path.startswith("event.") for path in reference_paths(tree)):
                        raise ValueError("growth evidence conditions read event fields only")
        if len(widths) != 1:
            raise ValueError("registered and used evidence keys must have equal length")


def _field(data: dict, path: str) -> Any:
    value = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return UNKNOWN
        value = value[part]
    return value


def _key(selector: dict, event: dict) -> tuple | None:
    key = tuple(_field(event, path) for path in selector["key"])
    if any(value is None or not isinstance(value, (str, float, int)) for value in key):
        return None
    if selector.get("when"):
        def resolve(path: str) -> Any:
            return _field(event, path.removeprefix("event."))
        try:
            result = evaluate_tree(parse_expression(selector["when"]), resolve, lambda path, index: resolve(f"{path}.{index}"))
        except (TypeError, ValueError):
            return None
        if result is UNKNOWN or not result:
            return None
    return key


@dataclass
class GrowthEvidence:
    registration: int | None = None
    registered_minute: int = 0
    subject: str = ""
    outcome: int | None = None


def match_growth_evidence(store: Any, plan: dict, spec: Any) -> GrowthEvidence:
    # Use is matched before registration so an event cannot prove its own adoption.
    selectors: dict[str, list] = {}
    for index, rule in enumerate(evidence_rules(spec)):
        for phase in ("used", "registered"):
            for item in rule[phase]:
                selectors.setdefault(item["event"], []).append((phase, index, item))
    rows = store.connection.execute(
        """SELECT event_id, event_type, actor_id, world_minute, data_json,
                  actor_id IN (SELECT id FROM agent_state) AS character_action
           FROM event_log WHERE event_id>? AND world_minute BETWEEN ? AND ?
             AND event_type IN (SELECT value FROM json_each(?)) ORDER BY event_id""",
        (plan.get("event_cursor", 0), plan["created_minute"], plan["expires_minute"], json.dumps(list(selectors))),
    )
    evidence = GrowthEvidence()
    registered: set[tuple] = set()
    for row in rows:
        data = {**json.loads(row["data_json"]), "actor_id": row["actor_id"]}
        new = []
        for phase, index, selector in selectors[row["event_type"]]:
            key = _key(selector, data)
            if key is None:
                continue
            if phase == "registered":
                new.append((index, key))
                if evidence.registration is None:
                    evidence.registration = row["event_id"]
                    evidence.registered_minute = row["world_minute"]
                    value = _field(data, selector.get("label", "summary"))
                    evidence.subject = str(value) if isinstance(value, (str, int, float)) else ""
            elif row["character_action"] and (index, key) in registered:
                evidence.outcome = row["event_id"]
                return evidence
        registered.update(new)
    return evidence
