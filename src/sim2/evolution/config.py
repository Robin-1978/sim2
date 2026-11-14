"""Author friendly evolution configuration.

World authors should describe *when* a world-level consumer is needed and
what it may submit.  The runtime still uses the more explicit event-wake
policy internally, but that representation is compiled at bootstrap time.
"""

from __future__ import annotations

import copy
import re
from typing import Any


_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([mhdw])\s*$", re.IGNORECASE)
_UNIT_MINUTES = {"m": 1, "h": 60, "d": 1440, "w": 10080}
# One decision view per evaluated character is the per-window cost, so the
# configurable cohort is capped to keep a window evaluation bounded.
STAGNATION_CHARACTER_CAP = 64
_RUNTIME_EVENTS = {
    "civilization.milestone",
    "entity.created",
    "definition.registered",
    "behavior.registered",
    "institution.proposed",
    "institution.activated",
    "institution.revised",
    "institution.ledger_entry",
    "institution.allocation_completed",
    "resource.safety_low",
    "world.stagnation",
}


def duration_minutes(value: Any) -> int:
    """Parse a strict duration such as ``7d`` or an integer minute count."""

    if value is None or value == "":
        raise ValueError("duration is required")
    if isinstance(value, bool):
        raise ValueError("duration must be a number of minutes or include m/h/d/w")
    if isinstance(value, (int, float)):
        return max(0, int(value))
    match = _DURATION.match(str(value))
    if not match:
        raise ValueError(f"invalid duration {value!r}; use minutes or m/h/d/w such as 7d")
    return max(0, int(float(match.group(1)) * _UNIT_MINUTES[match.group(2).lower()]))


def _event_name(value: Any) -> str:
    return str(value or "").strip()


def _group_id(event_types: list[str], role: str, index: int) -> str:
    base = next(iter(event_types), f"wake_{index}")
    safe = re.sub(r"[^a-z0-9]+", "_", base.casefold()).strip("_") or f"wake_{index}"
    return f"{safe}_{role}"


def _stagnation(value: Any) -> dict[str, Any]:
    """Compile the author-facing ``stagnation`` section.

    ``window`` is how long the whole world may go without any character
    gaining a new executable option before Platform publishes a
    ``world.stagnation`` event.  ``max_interval`` caps how rarely the event is
    repeated while the same stagnation persists (the spacing doubles from one
    window up to this cap).  ``max_characters`` bounds how many characters are
    evaluated per window; building a decision view per character is the cost
    that grows with the population, so a larger roster is covered by a
    rotating cohort instead of a full sweep.
    """

    if not isinstance(value, dict):
        raise ValueError("stagnation must be an object with a window")
    unknown = sorted(str(key) for key in value if key not in {"window", "max_interval", "max_characters", "progress_event_types"})
    if unknown:
        raise ValueError("unknown stagnation field(s): " + ", ".join(unknown) + "; use window, max_interval, max_characters, progress_event_types")
    if value.get("window") in (None, ""):
        raise ValueError("stagnation.window is required, for example 1d")
    window = duration_minutes(value["window"])
    if window <= 0:
        raise ValueError("stagnation.window must be positive")
    max_interval = duration_minutes(value["max_interval"]) if value.get("max_interval") not in (None, "") else max(window, _UNIT_MINUTES["w"])
    if max_interval < window:
        raise ValueError("stagnation.max_interval must not be shorter than stagnation.window")
    compiled: dict[str, Any] = {"window_minutes": window, "max_interval_minutes": max_interval}
    if value.get("max_characters") not in (None, ""):
        count = value["max_characters"]
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= STAGNATION_CHARACTER_CAP:
            raise ValueError(f"stagnation.max_characters must be an integer from 1 to {STAGNATION_CHARACTER_CAP}")
        compiled["max_characters"] = count
    progress_event_types = value.get("progress_event_types")
    if progress_event_types is not None:
        if isinstance(progress_event_types, str):
            progress_event_types = [progress_event_types]
        if not isinstance(progress_event_types, (list, tuple)) or not progress_event_types:
            raise ValueError("stagnation.progress_event_types must be one event name or a non-empty list")
        names = [str(item).strip() for item in progress_event_types]
        if any(not name or name.endswith("*") for name in names):
            raise ValueError("stagnation.progress_event_types entries must be exact non-empty event names")
        compiled["progress_event_types"] = list(dict.fromkeys(names))
    return compiled


def _distinct_keys(value: Any, index: int) -> list[str]:
    """Normalize ``distinct_by`` into a list of event payload keys."""

    if value is None or value == "":
        return []
    keys = [value] if isinstance(value, str) else value
    if not isinstance(keys, (list, tuple)) or not all(isinstance(item, str) and item.strip() for item in keys):
        raise ValueError(f"wake {index + 1} distinct_by must be an event field name or a list of field names")
    return list(dict.fromkeys(item.strip() for item in keys))


def _contract(spec: dict[str, Any], event_types: list[str], minimum: int) -> dict[str, Any]:
    """Compile the small author-facing fields into the internal contract."""

    result: dict[str, Any] = {}
    tools = spec.get("tools", spec.get("allowed_tools"))
    if isinstance(tools, str):
        tools = [tools]
    if isinstance(tools, list):
        result["allowed_tools"] = [str(item) for item in tools if str(item).strip()]
    for source, target in (
        ("purpose", "purpose"),
        ("result", "required_result"),
        ("required_result", "required_result"),
        ("definition_type", "required_definition_type"),
        ("assign_to", "assign_to"),
    ):
        if spec.get(source) and target not in result:
            result[target] = (
                [str(item) for item in spec[source]]
                if source == "assign_to" and isinstance(spec[source], list)
                else str(spec[source])
            )
    if spec.get("require_story_outcome") is not None:
        result["require_story_outcome"] = bool(spec.get("require_story_outcome"))
    evidence = spec.get("evidence", spec.get("evidence_sources"))
    if evidence is None:
        # An event in a compact wake is already a persisted fact.  The
        # platform counts it; authors do not need to repeat this selector.
        evidence = [{"event_types": event_types}]
    elif isinstance(evidence, dict):
        evidence = [evidence]
    if isinstance(evidence, list):
        result["evidence_sources"] = [dict(item) for item in evidence if isinstance(item, dict)]
    result["minimum_evidence"] = max(1, int(spec.get("count", spec.get("minimum_evidence", minimum)) or minimum))
    return result


def normalize_evolution_config(value: dict[str, Any] | None) -> dict[str, Any]:
    """Compile the one supported ``cadence``/``wakes`` authoring format.

    Explicit runtime ``schedule`` and ``event_wake_policy`` fields are rejected
    until every world has moved to the smaller authoring contract.  Keeping a
    second accepted format would make documentation and model guidance drift.
    """

    raw = copy.deepcopy(value or {})
    if not isinstance(raw, dict):
        raise ValueError("evolution configuration must be an object")
    nested = raw.get("evolution")
    if isinstance(nested, dict):
        root = nested
        wrapper = raw
    else:
        root = raw
        wrapper = None
    legacy_keys = {"schedule", "reflection_cadence_minutes", "event_wake_policy"}
    legacy = sorted(legacy_keys.intersection(root))
    if legacy:
        raise ValueError(
            "evolution uses retired fields " + ", ".join(legacy)
            + "; use top-level cadence and wakes instead"
        )
    known = {"cadence", "wakes", "triggers", "limits", "stagnation"}
    unknown = sorted(str(key) for key in root if key not in known)
    if unknown:
        raise ValueError("unknown evolution field(s): " + ", ".join(unknown))
    if "stagnation" in root:
        root["stagnation"] = _stagnation(root["stagnation"])

    cadence = root.get("cadence") if isinstance(root.get("cadence"), dict) else {}
    cadence_known = {"reflection", "innovation", "god", "god_structural"}
    unknown_cadence = sorted(str(key) for key in cadence if key not in cadence_known)
    if unknown_cadence:
        raise ValueError("unknown cadence field(s): " + ", ".join(unknown_cadence) + "; use reflection, innovation, god, god_structural")
    schedule: dict[str, int] = {}
    if cadence:
        reflection = cadence.get("reflection")
        innovation = cadence.get("innovation")
        god = cadence.get("god")
        structural = cadence.get("god_structural")
        if reflection is not None:
            schedule["agent_reflection_minutes"] = duration_minutes(reflection)
        if innovation is not None:
            schedule["agent_innovation_minutes"] = duration_minutes(innovation)
        if god is not None:
            schedule["god_local_minutes"] = duration_minutes(god)
        if structural is not None:
            schedule["god_structural_minutes"] = duration_minutes(structural)
    if cadence.get("reflection") is not None:
        root["reflection_cadence_minutes"] = schedule["agent_reflection_minutes"]

    wakes = root.get("wakes") or []
    if isinstance(wakes, dict):
        wakes = [wakes]
    if isinstance(wakes, list):
        policy = dict(root.get("event_wake_policy") or {})
        consumers = dict(policy.get("consumers") or {})
        for index, raw_spec in enumerate(wakes):
            if not isinstance(raw_spec, dict):
                continue
            known_wake = {"id", "on", "for", "count", "every", "tools", "purpose", "result", "evidence", "external", "definition_type", "assign_to", "manual_only", "distinct_by", "require_story_outcome"}
            unknown_wake = sorted(str(key) for key in raw_spec if key not in known_wake)
            if unknown_wake:
                raise ValueError(
                    f"wake {index + 1} has unknown field(s): " + ", ".join(unknown_wake)
                    + "; use id, on, for, count, every, distinct_by, tools, purpose, result, evidence, manual_only, require_story_outcome"
                )
            role = str(raw_spec.get("for", "") or "").strip()
            # YAML 1.1 parsers (including PyYAML's safe loader) treat an
            # unquoted ``on`` mapping key as boolean ``True``. Accept that
            # spelling so a compact world file stays portable.
            event = raw_spec.get("on", raw_spec.get(True))
            events = [event] if isinstance(event, str) else list(event or [])
            event_types = [_event_name(item) for item in events if _event_name(item)]
            if not role:
                raise ValueError(f"wake {index + 1} is missing for")
            if not event_types:
                raise ValueError(f"wake {index + 1} is missing on")
            consumer = dict(consumers.get(role) or {})
            direct = list(consumer.get("direct_event_types") or [])
            groups = list(consumer.get("event_groups") or consumer.get("groups") or [])
            manual_events = list(consumer.get("manual_event_types") or [])
            count = max(1, int(raw_spec.get("count", 1) or 1))
            cooldown = duration_minutes(raw_spec["every"]) if raw_spec.get("every") is not None else 0
            contract_fields = {"tools", "purpose", "result", "evidence", "definition_type", "assign_to", "require_story_outcome"}
            prefixes = [item[:-1] for item in event_types if item.endswith("*")]
            exact_events = [item for item in event_types if not item.endswith("*")]
            if prefixes:
                consumer["direct_task_type_prefixes"] = list(dict.fromkeys(
                    [str(item) for item in (consumer.get("direct_task_type_prefixes") or [])] + prefixes
                ))
            distinct_by = _distinct_keys(raw_spec.get("distinct_by"), index)
            has_contract = any(key in raw_spec for key in contract_fields) or count > 1 or cooldown > 0 or bool(distinct_by)
            manual_only = bool(raw_spec.get("manual_only"))
            if has_contract and exact_events:
                group = {
                    "id": str(raw_spec.get("id") or _group_id(event_types, role, index)),
                    "event_types": exact_events,
                    "minimum_events": count,
                    "cooldown_minutes": cooldown,
                    "agent_contract": _contract(raw_spec, event_types, count),
                }
                if distinct_by:
                    group["distinct_by"] = distinct_by
                groups.append(group)
            elif not manual_only:
                # A manual-only event is admitted by explicit producers
                # (see ``manual_event_types``); listing it as a direct event
                # would also let the automatic fan-out wake the consumer.
                direct.extend(exact_events)
            if manual_only:
                manual_events.extend(exact_events)
            consumer["direct_event_types"] = list(dict.fromkeys(str(item) for item in direct))
            consumer["manual_event_types"] = list(dict.fromkeys(str(item) for item in manual_events))
            if groups:
                consumer["event_groups"] = groups
            # Compact wakes name their source event directly.  It may be
            # emitted by an Action, a rule, or a runtime subsystem; the
            # explicit event name is the author's source declaration.
            external = list(consumer.get("external_event_types") or [])
            external.extend(
                item for item in exact_events
                if bool(raw_spec.get("external")) or item in _RUNTIME_EVENTS
            )
            consumer["external_event_types"] = list(dict.fromkeys(str(item) for item in external))
            consumers[role] = consumer
        policy["consumers"] = consumers
        root["event_wake_policy"] = policy
    if schedule:
        root["schedule"] = schedule
    else:
        root.pop("schedule", None)
    root.pop("cadence", None)
    root.pop("wakes", None)
    if wrapper is not None:
        wrapper["evolution"] = root
        return wrapper
    return root

