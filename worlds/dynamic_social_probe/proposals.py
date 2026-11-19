"""God proposal payloads for the dynamic social probe.

These payloads are world content, not Platform primitives.  One generic
``transfer_resource`` family (fill/withdraw/take), one generic review and the
request/approval/revocation administration are reused for every stock.  Adding
a resource means adding an entity, a stewardship relationship and two
authorization rules; it does not register another Action or behavior tree.
"""

from __future__ import annotations

from typing import Any


def _action(
    name: str,
    action_id: str,
    assign_to: list[str],
    *,
    verb: str,
    action: dict[str, Any] | None = None,
    behavior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    definition = {
        "id": action_id,
        "verb": verb,
        "parameters": {},
        "preconditions": ["actor.alive"],
        "duration": {"minutes": 1},
        "effects": [],
        "success": {"condition": "actor.alive", "result": "completed"},
        "failure": {"condition": "actor.incapacitated", "result": "failed"},
    }
    definition.update(action or {})
    behavior_definition = {
        "id": action_id,
        "verb": verb,
        "agent_label": name,
        "entry_conditions": ["actor.alive"],
        "estimated_minutes": 1,
        "nodes": [{"type": "action", "action_id": action_id}],
    }
    behavior_definition.update(behavior or {})
    return {
        "definition_type": "action",
        "name": name,
        "definition": definition,
        "followup_behavior": {"name": name, "assign_to": assign_to, "definition": behavior_definition},
    }


def _resource_parameter() -> dict[str, Any]:
    return {
        "type": "string",
        "required": True,
        "max_length": 120,
        "description": "Exact visible resource name.",
    }


def _rule(rule_id: str, description: str, effect: dict[str, Any]) -> dict[str, Any]:
    return {
        "change_type": "rule",
        "summary": description,
        "rule": {
            "id": rule_id,
            "condition": "true",
            "description": description,
            "effect": effect,
        },
    }


WATER_PLANT = {
    "entity_type": "location",
    "entity_id": "water_plant",
    "name": "Water Plant",
    "description": "A pump house with room for a shared reservoir.",
    "state": {"routes": ["relay_station"], "route_distances": {"relay_station": 1}},
}

WATER_RESERVE = {
    "entity_type": "resource",
    "entity_id": "water_reserve",
    "name": "Water Reserve",
    "description": "A shared water stock created empty by God; a behavior must fill it.",
    "location_id": "water_plant",
    "state": {"quantity": 0},
}

STEWARDSHIP = {
    "change_type": "relationship",
    "summary": "Mira is accountable for the new water reserve.",
    "relationship": {"participants": ["mira", "water_reserve"], "type": "stewardship"},
}

# One capability family for every stock; the target is an Agent binding.
FILL = _action(
    "Fill Resource",
    "fill_resource",
    ["mira"],
    verb="transfer_resource",
    action={
        "description": "Top up a visible shared resource from its configured source.",
        "parameters": {"resource_id": "string"},
        "effects": [{
            "type": "world_resource_delta",
            "resource_id": "context.resource_id",
            "delta": 10,
            "maximum": 10,
        }],
        "success": {"condition": "actor.alive", "result": "resource_filled"},
        "failure": {"condition": "actor.incapacitated", "result": "fill_failed"},
    },
    behavior={
        "summary": "Fill a visible shared resource.",
        "agent_parameters": {"resource_id": _resource_parameter()},
        "nodes": [{
            "type": "action",
            "action_id": "fill_resource",
            "parameters": {"resource_id": "context.resource_id"},
        }],
    },
)

WITHDRAW = _action(
    "Withdraw Resource",
    "withdraw_resource",
    ["mira", "tomas"],
    verb="transfer_resource",
    action={
        "description": "Withdraw one unit of a shared resource with authorization.",
        "parameters": {"resource_id": "string"},
        "duration": {"minutes": 2},
        "effects": [{
            "type": "resource_withdrawal",
            "resource_id": "context.resource_id",
            "amount": 1,
            "scope": "resource_withdrawal",
        }],
        "success": {"condition": "actor.alive", "result": "resource_withdrawn"},
        "failure": {"condition": "actor.incapacitated", "result": "withdrawal_failed"},
    },
    behavior={
        "summary": "Withdraw one unit of a visible shared resource with authorization.",
        "agent_parameters": {"resource_id": _resource_parameter()},
        "nodes": [{
            "type": "action",
            "action_id": "withdraw_resource",
            "parameters": {"resource_id": "context.resource_id"},
        }],
    },
)

TAKE = _action(
    "Take Resource",
    "take_resource",
    ["tomas"],
    verb="transfer_resource",
    action={
        "description": "Take one unit of a shared resource without authorization.",
        "parameters": {"resource_id": "string"},
        "effects": [{
            "type": "resource_take",
            "resource_id": "context.resource_id",
            "amount": 1,
            "scope": "resource_withdrawal",
            "mode": "covert",
        }],
        "success": {"condition": "actor.alive", "result": "resource_taken"},
        "failure": {"condition": "actor.incapacitated", "result": "take_failed"},
    },
    behavior={
        "summary": "Take one unit of a visible shared resource without authorization.",
        "agent_parameters": {"resource_id": _resource_parameter()},
        "nodes": [{
            "type": "action",
            "action_id": "take_resource",
            "parameters": {"resource_id": "context.resource_id"},
        }],
    },
)

REQUEST = _action(
    "Request Access",
    "request_access",
    ["mira", "tomas"],
    verb="authorize",
    action={
        "parameters": {
            "based_on_event": "string",
            "reason": "string",
            "behavior": "string",
            "target": "string",
            "scope": "string",
            "request_id": "string",
        },
    },
    behavior={
        "authorization_operation": "open_access_request",
        "agent_parameters": {
            "based_on_event": {"type": "string", "required": False, "max_length": 40},
            "reason": {"type": "string", "required": False, "max_length": 300},
            "behavior": {"type": "string", "required": False, "max_length": 120},
            "target": {"type": "string", "required": False, "max_length": 120},
            "scope": {"type": "string", "required": False, "max_length": 120},
            "request_id": {"type": "string", "required": False, "max_length": 80},
        },
        "nodes": [{
            "type": "action",
            "action_id": "request_access",
            "parameters": {key: f"context.{key}" for key in ("based_on_event", "reason", "behavior", "target", "scope", "request_id")},
        }],
    },
)

APPROVE = _action(
    "Approve Withdrawal",
    "approve_withdrawal",
    ["mira"],
    verb="authorize",
    action={
        "parameters": {
            "request_id": "string",
            "delegation_id": "string",
            "subject": "string",
            "target": "string",
            "scope": "string",
            "reason": "string",
        },
    },
    behavior={
        "authorization_operation": "decide_access_request",
        "authorization_decision": "approve",
        "agent_parameters": {key: {"type": "string", "required": False, "max_length": 120} for key in ("request_id", "delegation_id", "subject", "target", "scope", "reason")},
        "nodes": [{
            "type": "action",
            "action_id": "approve_withdrawal",
            "parameters": {key: f"context.{key}" for key in ("request_id", "delegation_id", "subject", "target", "scope", "reason")},
        }],
    },
)

REJECT = _action(
    "Reject Withdrawal",
    "reject_withdrawal",
    ["mira"],
    verb="authorize",
    action={"parameters": {"request_id": "string", "reason": "string"}},
    behavior={
        "authorization_operation": "decide_access_request",
        "authorization_decision": "reject",
        "agent_parameters": {
            "request_id": {"type": "string", "required": False, "max_length": 80},
            "reason": {"type": "string", "required": False, "max_length": 300},
        },
        "nodes": [{
            "type": "action",
            "action_id": "reject_withdrawal",
            "parameters": {"request_id": "context.request_id", "reason": "context.reason"},
        }],
    },
)

REVOKE = _action(
    "Revoke Delegation",
    "revoke_delegation",
    ["mira"],
    verb="authorize",
    action={"parameters": {"delegation_id": "string", "reason": "string"}},
    behavior={
        "authorization_operation": "revoke_access",
        "agent_parameters": {
            "delegation_id": {"type": "string", "required": False, "max_length": 80},
            "reason": {"type": "string", "required": False, "max_length": 300},
        },
        "nodes": [{
            "type": "action",
            "action_id": "revoke_delegation",
            "parameters": {"delegation_id": "context.delegation_id", "reason": "context.reason"},
        }],
    },
)

REVIEW = _action(
    "Review Violation",
    "review_violation",
    ["mira"],
    verb="authorize",
    action={"parameters": {"violation_id": "string", "decision": "string", "reason": "string"}},
    behavior={
        "authorization_operation": "review_violation",
        "agent_parameters": {
            "violation_id": {"type": "string", "required": True, "max_length": 80},
            "decision": {"type": "string", "required": True, "max_length": 20},
            "reason": {"type": "string", "required": False, "max_length": 300},
        },
        "nodes": [{
            "type": "action",
            "action_id": "review_violation",
            "parameters": {"violation_id": "context.violation_id", "decision": "context.decision", "reason": "context.reason"},
        }],
    },
)

RULES = (
    _rule(
        "water_reserve_stewardship",
        "Only the water steward or an active delegation may withdraw water.",
        {"type": "authorization", "mode": "resource_access", "target": "water_reserve", "scope": "resource_withdrawal", "behavior_ids": ["withdraw_resource"]},
    ),
    _rule(
        "water_approval_authority",
        "Only the water steward may decide a pending water request, and never the requester.",
        {"type": "authorization", "mode": "request_decision", "authority": {"type": "institution", "id": "water_approval_authority"}, "behavior_ids": ["approve_withdrawal", "reject_withdrawal"]},
    ),
    _rule(
        "water_revocation_authority",
        "Only the water steward may revoke a water delegation.",
        {"type": "authorization", "mode": "delegation_revocation", "authority": {"type": "institution", "id": "water_revocation_authority"}, "behavior_ids": ["revoke_delegation"]},
    ),
    _rule(
        "water_violation_authority",
        "Only the water steward may review an observed water violation.",
        {"type": "authorization", "mode": "violation_review", "behavior_ids": ["review_violation"]},
    ),
)

# -- second and third resource instances ------------------------------------
# The same capability family and the same administrative behaviors are reused.
# Only the resource, location, stewardship and two authorization rules differ.


def _resource_instance(
    *,
    location_id: str,
    location_name: str,
    location_description: str,
    resource_id: str,
    resource_name: str,
    resource_description: str,
    rule_prefix: str,
    rule_description: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], tuple[dict[str, Any], ...]]:
    location = {
        "entity_type": "location",
        "entity_id": location_id,
        "name": location_name,
        "description": location_description,
        "state": {"routes": ["relay_station"], "route_distances": {"relay_station": 1}},
    }
    resource = {
        "entity_type": "resource",
        "entity_id": resource_id,
        "name": resource_name,
        "description": resource_description,
        "location_id": location_id,
        "state": {"quantity": 0},
    }
    stewardship = {
        "change_type": "relationship",
        "summary": f"Mira is accountable for the {resource_name.lower()} too.",
        "relationship": {"participants": ["mira", resource_id], "type": "stewardship"},
    }
    rules = (
        _rule(
            f"{rule_prefix}_reserve_stewardship",
            f"Only the {rule_description} steward or an active delegation may withdraw {rule_description}.",
            {"type": "authorization", "mode": "resource_access", "target": resource_id, "scope": "resource_withdrawal", "behavior_ids": ["withdraw_resource"]},
        ),
        _rule(
            f"{rule_prefix}_violation_authority",
            f"Only the {rule_description} steward may review an observed {rule_description} violation.",
            {"type": "authorization", "mode": "violation_review", "behavior_ids": ["review_violation"]},
        ),
    )
    return location, resource, stewardship, rules


FUEL_DEPOT, FUEL_RESERVE, FUEL_STEWARDSHIP, FUEL_RULES = _resource_instance(
    location_id="fuel_depot",
    location_name="Fuel Depot",
    location_description="A depot with room for a shared fuel stock.",
    resource_id="fuel_reserve",
    resource_name="Fuel Reserve",
    resource_description="A shared fuel stock created empty by God; a behavior must fill it.",
    rule_prefix="fuel",
    rule_description="fuel",
)

COOLANT_SHED, COOLANT_RESERVE, COOLANT_STEWARDSHIP, COOLANT_RULES = _resource_instance(
    location_id="coolant_shed",
    location_name="Coolant Shed",
    location_description="A shed with room for a shared coolant stock.",
    resource_id="coolant_reserve",
    resource_name="Coolant Reserve",
    resource_description="A shared coolant stock created empty by God; a behavior must fill it.",
    rule_prefix="coolant",
    rule_description="coolant",
)


def install_social_content(platform: Any) -> dict[str, Any]:
    """Submit the frozen Phase 3A content sequence through Platform services."""

    results: dict[str, Any] = {
        "water_plant": platform.submit_entity_proposal("god", WATER_PLANT),
        "water_reserve": platform.submit_entity_proposal("god", WATER_RESERVE),
        "stewardship": platform.submit_world_change("god", STEWARDSHIP),
    }
    for name, payload in (
        ("fill", FILL),
        ("withdraw", WITHDRAW),
        ("take", TAKE),
        ("request", REQUEST),
        ("approve", APPROVE),
        ("reject", REJECT),
        ("revoke", REVOKE),
        ("review", REVIEW),
    ):
        results[name] = platform.submit_definition_proposal("god", payload)
    for payload in RULES:
        results[payload["rule"]["id"]] = platform.submit_world_change("god", payload)
    return results


def _install_instance(
    platform: Any,
    *,
    location: dict[str, Any],
    resource: dict[str, Any],
    stewardship: dict[str, Any],
    rules: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    results: dict[str, Any] = {
        str(location["entity_id"]): platform.submit_entity_proposal("god", location),
        str(resource["entity_id"]): platform.submit_entity_proposal("god", resource),
        f"{location['entity_id']}_stewardship": platform.submit_world_change("god", stewardship),
    }
    for payload in rules:
        results[payload["rule"]["id"]] = platform.submit_world_change("god", payload)
    return results


def install_second_resource(platform: Any) -> dict[str, Any]:
    """Instantiate a second resource with the existing capability and admin content."""

    return _install_instance(
        platform,
        location=FUEL_DEPOT,
        resource=FUEL_RESERVE,
        stewardship=FUEL_STEWARDSHIP,
        rules=FUEL_RULES,
    )


def install_third_resource(platform: Any) -> dict[str, Any]:
    """Instantiate a third resource with the existing capability and admin content."""

    return _install_instance(
        platform,
        location=COOLANT_SHED,
        resource=COOLANT_RESERVE,
        stewardship=COOLANT_STEWARDSHIP,
        rules=COOLANT_RULES,
    )
