"""Capability families for behavior trees and actions.

A behavior tree is a procedure, not a target.  ``Take Water`` and ``Take
Coolant`` are the same ``transfer_resource`` capability with different
bindings.  These helpers give definitions a stable ``verb`` family, infer one
for legacy definitions, and identify an Action whose target is a parameter
(``context.resource_id``) rather than a literal entity.
"""

from __future__ import annotations

from typing import Any

# Canonical families used by the growth ledger and generation policy.  Worlds
# may introduce another lower-case family; aliases below collapse the common
# near-duplicates so two names cannot pretend to be two capabilities.
VERB_ALIASES = {
    "take": "transfer_resource",
    "withdraw": "transfer_resource",
    "deposit": "transfer_resource",
    "give": "transfer_resource",
    "transfer": "transfer_resource",
    "resource_transfer": "transfer_resource",
    "resource_take": "transfer_resource",
    "resource_withdrawal": "transfer_resource",
    "eat": "consume",
    "drink": "consume",
    "consume_item": "consume",
    "move_to": "move",
    "travel": "move",
    "travel_to": "move",
    "study": "research",
    "craft": "craft",
    "talk": "communicate",
    "chat": "communicate",
    "authorize": "authorize",
    "coerce": "coerce",
    "inspect": "inspect",
    "observe": "inspect",
    "repair": "repair",
}

_TRANSFER_EFFECTS = {
    "resource_take", "resource_withdrawal", "resource_transfer",
    "world_resource_delta", "resource_deposit",
}
_CONSUME_EFFECTS = {"need_delta", "attribute_delta", "inventory.consume_item"}
_MOVE_EFFECTS = {"location_set"}
_SOCIAL_EFFECTS = {"relationship_delta", "institution_delta"}
_MODIFY_EFFECTS = {"entity_state_set", "state_delta", "status_add", "status_remove"}
_INVENTORY_EFFECTS = {"inventory_add", "inventory_remove"}
_LEARN_EFFECTS = {"capability.add_xp", "capability_xp"}


def normalize_verb(value: Any) -> str:
    """Lower-case a family and collapse common aliases."""

    text = " ".join(str(value or "").strip().casefold().replace("-", " ").split())
    if not text:
        return ""
    key = text.replace(" ", "_")
    return VERB_ALIASES.get(key, key)


def is_parameter_target(value: Any) -> bool:
    """Whether an effect target is supplied by a binding at execution time."""

    return str(value or "").strip().startswith("context.")


def _effect_types(definition: dict[str, Any]) -> set[str]:
    types: set[str] = set()
    for field in ("effects", "progress_effects"):
        for effect in definition.get(field) or []:
            if isinstance(effect, dict):
                types.add(str(effect.get("type") or effect.get("op") or "").casefold())
    return types


def infer_action_verb(definition: dict[str, Any]) -> str:
    """Infer one capability family from the Action's effect mix."""

    explicit = normalize_verb(definition.get("verb"))
    if explicit:
        return explicit
    types = _effect_types(definition)
    if types & _TRANSFER_EFFECTS:
        return "transfer_resource"
    if types & _CONSUME_EFFECTS:
        return "consume"
    if types & _MOVE_EFFECTS:
        return "move"
    if types & _SOCIAL_EFFECTS:
        return "social"
    if types & _LEARN_EFFECTS:
        return "learn"
    if types & _INVENTORY_EFFECTS:
        return "inventory"
    if types & _MODIFY_EFFECTS:
        return "modify"
    if "public_event" in types:
        return "signal"
    return "other"


def infer_behavior_verb(definition: dict[str, Any], action_defs: dict[str, dict[str, Any]]) -> str:
    """Infer a Behavior's family from its action nodes, or ``composite``."""

    explicit = normalize_verb(definition.get("verb"))
    if explicit:
        return explicit
    verbs: set[str] = set()
    for node in definition.get("nodes") or []:
        if not isinstance(node, dict) or str(node.get("type") or "") != "action":
            continue
        action = action_defs.get(str(node.get("action_id") or ""))
        if action:
            verbs.add(infer_action_verb(action))
    if len(verbs) == 1:
        return next(iter(verbs))
    if verbs:
        return "composite"
    return "other"


def generic_parameter_target(definition: dict[str, Any]) -> str:
    """Return the parameter name a generic Action binds, or ``""``.

    ``context.resource_id`` is a binding; ``Water Reserve`` is a specific
    target.  A generic Action may also declare the same name in ``parameters``.
    """

    for field in ("effects", "progress_effects"):
        for effect in definition.get(field) or []:
            if not isinstance(effect, dict):
                continue
            for key in ("resource_id", "resource", "property", "need", "attribute", "item_id", "location_id", "target"):
                value = effect.get(key)
                if is_parameter_target(value):
                    return str(value).removeprefix("context.")
    return ""
