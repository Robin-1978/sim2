"""Availability of generated Actions from committed outcomes in this save."""

from __future__ import annotations

import math
from typing import Any

from ..world.properties import world_property_key
from .targets import TargetResolver

_SOURCE = "god_proposal"
SOCIAL_OUTCOME_DELIVERED = "this social outcome has already been delivered"


def generated_action_pacing(store: Any) -> dict[str, Any]:
    config = store.repositories.metadata.get("evolution", {}) or {}
    limits = config.get("evolution", config).get("limits") or {}
    return limits.get("generated_action_pacing") or {}


def _social_milestone(
    store: Any, action: dict[str, Any], targets: TargetResolver
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]] | None:
    """Identify a pure social reward by the world property and the named counterpart."""
    properties, relations = set(), set()
    costs = action.get("costs") or {}
    if action.get("progress_effects") or any(costs.get(key) for key in ("reserve", "per_minute", "on_success")):
        return None
    for effect in action.get("effects") or []:
        kind = effect.get("type") or effect.get("op")
        if kind == "world_property_delta":
            prop = str(effect.get("property") or effect.get("world_property") or "")
            if not prop or prop.startswith("context."):
                return None
            if sign := _direction(effect):
                properties.add(sign + world_property_key(store, prop))
        elif kind == "relationship_delta":
            counterpart = targets.canonical_id(effect.get("target_id") or effect.get("target"))
            if not counterpart:
                return None
            if sign := _direction(effect):
                relations.add((counterpart, sign + str(effect.get("relation") or "related")))
        elif kind != "public_event":
            return None
    return (tuple(sorted(properties)), tuple(sorted(relations))) if properties and relations else None


def _direction(effect: dict[str, Any]) -> str:
    try:
        delta = float(effect.get("delta", effect.get("value", 0)) or 0)
    except (TypeError, ValueError, OverflowError):
        return ""
    return ("-" if delta < 0 else "+") if delta and math.isfinite(delta) else ""


def generated_action_unavailable(
    store: Any, action: dict[str, Any], agent: dict[str, Any], minute: int | None = None,
) -> str | None:
    if action.get("definition_source") != _SOURCE:
        return None
    policy = generated_action_pacing(store)
    if not policy:
        return None
    player_id = agent["id"] if agent.get("role") == "player" else None
    cooldown = 0 if player_id else policy.get("resident_cooldown", 0)
    ids = {action["id"]}
    targets = TargetResolver(store)
    milestone = _social_milestone(store, action, targets) if policy.get("social_milestone_once") else None
    if milestone:
        ids.update(
            candidate["id"] for candidate in store.repositories.entities.get_generated_actions()
            if _social_milestone(store, candidate, targets) == milestone
        )
    if not (milestone or cooldown):
        return None
    last = store.repositories.activity.last_action_completion(ids, player_id=player_id)
    if last is None:
        return None
    if milestone:
        return SOCIAL_OUTCOME_DELIVERED
    if minute is None:
        minute = int(store.repositories.metadata.get("world_minute", 0))
    if minute < last + cooldown:
        return f"cooldown until world minute {last + cooldown} after the residents' last completion"
    return None
