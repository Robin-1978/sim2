"""God proposal payloads for the dynamic social probe.

These payloads are world content, not Platform primitives.  They use only the
existing proposal services: a location, an empty resource, a stewardship
relationship, four administrative authorization behaviors, one controlled
withdrawal, one covert take, one violation review, and four authorization
rules.
"""

from __future__ import annotations

from typing import Any


def _action(
    name: str,
    action_id: str,
    assign_to: list[str],
    *,
    action: dict[str, Any] | None = None,
    behavior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    definition = {
        "id": action_id,
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

FILL = _action(
    "Fill Water Reserve",
    "fill_water_reserve",
    ["mira"],
    action={
        "description": "Fill the shared water reserve from the plant.",
        "effects": [{"type": "world_resource_delta", "resource_id": "water_reserve", "delta": 10, "maximum": 10}],
        "success": {"condition": "actor.alive", "result": "water_filled"},
        "failure": {"condition": "actor.incapacitated", "result": "fill_failed"},
    },
    behavior={"summary": "Fill the shared water reserve."},
)

WITHDRAW = _action(
    "Withdraw Water",
    "withdraw_water",
    ["mira", "tomas"],
    action={
        "description": "Withdraw one unit of shared water with authorization.",
        "duration": {"minutes": 2},
        "effects": [{"type": "resource_withdrawal", "resource_id": "water_reserve", "amount": 1, "scope": "water_withdrawal"}],
        "success": {"condition": "actor.alive", "result": "water_withdrawn"},
        "failure": {"condition": "actor.incapacitated", "result": "withdrawal_failed"},
    },
    behavior={"summary": "Withdraw one unit of shared water with authorization."},
)

TAKE = _action(
    "Take Water",
    "take_water",
    ["tomas"],
    action={
        "description": "Take one unit of shared water without authorization.",
        "effects": [{"type": "resource_take", "resource_id": "water_reserve", "amount": 1, "scope": "water_withdrawal", "mode": "covert"}],
        "success": {"condition": "actor.alive", "result": "water_taken"},
        "failure": {"condition": "actor.incapacitated", "result": "take_failed"},
    },
    behavior={"summary": "Take one unit of shared water without authorization."},
)

REQUEST = _action(
    "Request Water Access",
    "request_access",
    ["mira", "tomas"],
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
    "Review Water Violation",
    "review_water_violation",
    ["mira"],
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
            "action_id": "review_water_violation",
            "parameters": {"violation_id": "context.violation_id", "decision": "context.decision", "reason": "context.reason"},
        }],
    },
)

RULES = (
    _rule(
        "water_reserve_stewardship",
        "Only the water steward or an active delegation may withdraw water.",
        {"type": "authorization", "mode": "resource_access", "target": "water_reserve", "scope": "water_withdrawal", "behavior_ids": ["withdraw_water"]},
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
        {"type": "authorization", "mode": "violation_review", "behavior_ids": ["review_water_violation"]},
    ),
)

# -- second resource instance ------------------------------------------------
# The same proposal shapes and the same administrative behaviors are reused.
# Only the resource, location, action effects and two authorization rules differ.

FUEL_DEPOT = {
    "entity_type": "location",
    "entity_id": "fuel_depot",
    "name": "Fuel Depot",
    "description": "A shed with a shared fuel tank.",
    "state": {"routes": ["relay_station"], "route_distances": {"relay_station": 1}},
}

FUEL_RESERVE = {
    "entity_type": "resource",
    "entity_id": "fuel_reserve",
    "name": "Fuel Reserve",
    "description": "A shared fuel stock created empty by God; a behavior must fill it.",
    "location_id": "fuel_depot",
    "state": {"quantity": 0},
}

FUEL_STEWARDSHIP = {
    "change_type": "relationship",
    "summary": "Mira is accountable for the fuel reserve too.",
    "relationship": {"participants": ["mira", "fuel_reserve"], "type": "stewardship"},
}

FILL_FUEL = _action(
    "Fill Fuel Reserve",
    "fill_fuel_reserve",
    ["mira"],
    action={
        "description": "Fill the shared fuel reserve from the depot.",
        "effects": [{"type": "world_resource_delta", "resource_id": "fuel_reserve", "delta": 10, "maximum": 10}],
        "success": {"condition": "actor.alive", "result": "fuel_filled"},
        "failure": {"condition": "actor.incapacitated", "result": "fill_fuel_failed"},
    },
    behavior={"summary": "Fill the shared fuel reserve."},
)

WITHDRAW_FUEL = _action(
    "Withdraw Fuel",
    "withdraw_fuel",
    ["mira", "tomas"],
    action={
        "description": "Withdraw one unit of shared fuel with authorization.",
        "duration": {"minutes": 2},
        "effects": [{"type": "resource_withdrawal", "resource_id": "fuel_reserve", "amount": 1, "scope": "fuel_withdrawal"}],
        "success": {"condition": "actor.alive", "result": "fuel_withdrawn"},
        "failure": {"condition": "actor.incapacitated", "result": "fuel_withdrawal_failed"},
    },
    behavior={"summary": "Withdraw one unit of shared fuel with authorization."},
)

TAKE_FUEL = _action(
    "Take Fuel",
    "take_fuel",
    ["tomas"],
    action={
        "description": "Take one unit of shared fuel without authorization.",
        "effects": [{"type": "resource_take", "resource_id": "fuel_reserve", "amount": 1, "scope": "fuel_withdrawal", "mode": "covert"}],
        "success": {"condition": "actor.alive", "result": "fuel_taken"},
        "failure": {"condition": "actor.incapacitated", "result": "fuel_take_failed"},
    },
    behavior={"summary": "Take one unit of shared fuel without authorization."},
)

REVIEW_FUEL = _action(
    "Review Fuel Violation",
    "review_fuel_violation",
    ["mira"],
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
            "action_id": "review_fuel_violation",
            "parameters": {"violation_id": "context.violation_id", "decision": "context.decision", "reason": "context.reason"},
        }],
    },
)

FUEL_RULES = (
    _rule(
        "fuel_reserve_stewardship",
        "Only the fuel steward or an active delegation may withdraw fuel.",
        {"type": "authorization", "mode": "resource_access", "target": "fuel_reserve", "scope": "fuel_withdrawal", "behavior_ids": ["withdraw_fuel"]},
    ),
    _rule(
        "fuel_violation_authority",
        "Only the fuel steward may review an observed fuel violation.",
        {"type": "authorization", "mode": "violation_review", "behavior_ids": ["review_fuel_violation"]},
    ),
)

# -- third resource instance -------------------------------------------------
# Same shape again; only the world content changes.

COOLANT_SHED = {
    "entity_type": "location",
    "entity_id": "coolant_shed",
    "name": "Coolant Shed",
    "description": "A shed with a shared coolant tank.",
    "state": {"routes": ["relay_station"], "route_distances": {"relay_station": 1}},
}

COOLANT_RESERVE = {
    "entity_type": "resource",
    "entity_id": "coolant_reserve",
    "name": "Coolant Reserve",
    "description": "A shared coolant stock created empty by God; a behavior must fill it.",
    "location_id": "coolant_shed",
    "state": {"quantity": 0},
}

COOLANT_STEWARDSHIP = {
    "change_type": "relationship",
    "summary": "Mira is accountable for the coolant reserve too.",
    "relationship": {"participants": ["mira", "coolant_reserve"], "type": "stewardship"},
}

FILL_COOLANT = _action(
    "Fill Coolant Reserve",
    "fill_coolant_reserve",
    ["mira"],
    action={
        "description": "Fill the shared coolant reserve from the shed.",
        "effects": [{"type": "world_resource_delta", "resource_id": "coolant_reserve", "delta": 10, "maximum": 10}],
        "success": {"condition": "actor.alive", "result": "coolant_filled"},
        "failure": {"condition": "actor.incapacitated", "result": "fill_coolant_failed"},
    },
    behavior={"summary": "Fill the shared coolant reserve."},
)

WITHDRAW_COOLANT = _action(
    "Withdraw Coolant",
    "withdraw_coolant",
    ["mira", "tomas"],
    action={
        "description": "Withdraw one unit of shared coolant with authorization.",
        "duration": {"minutes": 2},
        "effects": [{"type": "resource_withdrawal", "resource_id": "coolant_reserve", "amount": 1, "scope": "coolant_withdrawal"}],
        "success": {"condition": "actor.alive", "result": "coolant_withdrawn"},
        "failure": {"condition": "actor.incapacitated", "result": "coolant_withdrawal_failed"},
    },
    behavior={"summary": "Withdraw one unit of shared coolant with authorization."},
)

TAKE_COOLANT = _action(
    "Take Coolant",
    "take_coolant",
    ["tomas"],
    action={
        "description": "Take one unit of shared coolant without authorization.",
        "effects": [{"type": "resource_take", "resource_id": "coolant_reserve", "amount": 1, "scope": "coolant_withdrawal", "mode": "covert"}],
        "success": {"condition": "actor.alive", "result": "coolant_taken"},
        "failure": {"condition": "actor.incapacitated", "result": "coolant_take_failed"},
    },
    behavior={"summary": "Take one unit of shared coolant without authorization."},
)

REVIEW_COOLANT = _action(
    "Review Coolant Violation",
    "review_coolant_violation",
    ["mira"],
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
            "action_id": "review_coolant_violation",
            "parameters": {"violation_id": "context.violation_id", "decision": "context.decision", "reason": "context.reason"},
        }],
    },
)

COOLANT_RULES = (
    _rule(
        "coolant_reserve_stewardship",
        "Only the coolant steward or an active delegation may withdraw coolant.",
        {"type": "authorization", "mode": "resource_access", "target": "coolant_reserve", "scope": "coolant_withdrawal", "behavior_ids": ["withdraw_coolant"]},
    ),
    _rule(
        "coolant_violation_authority",
        "Only the coolant steward may review an observed coolant violation.",
        {"type": "authorization", "mode": "violation_review", "behavior_ids": ["review_coolant_violation"]},
    ),
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


def install_second_resource(platform: Any) -> dict[str, Any]:
    """Instantiate a second resource with the same primitives and admin content."""

    results: dict[str, Any] = {
        "fuel_depot": platform.submit_entity_proposal("god", FUEL_DEPOT),
        "fuel_reserve": platform.submit_entity_proposal("god", FUEL_RESERVE),
        "fuel_stewardship": platform.submit_world_change("god", FUEL_STEWARDSHIP),
        "fill_fuel": platform.submit_definition_proposal("god", FILL_FUEL),
        "withdraw_fuel": platform.submit_definition_proposal("god", WITHDRAW_FUEL),
        "take_fuel": platform.submit_definition_proposal("god", TAKE_FUEL),
        "review_fuel": platform.submit_definition_proposal("god", REVIEW_FUEL),
    }
    for payload in FUEL_RULES:
        results[payload["rule"]["id"]] = platform.submit_world_change("god", payload)
    return results


def install_third_resource(platform: Any) -> dict[str, Any]:
    """Instantiate a third resource with the same primitives and admin content."""

    results: dict[str, Any] = {
        "coolant_shed": platform.submit_entity_proposal("god", COOLANT_SHED),
        "coolant_reserve": platform.submit_entity_proposal("god", COOLANT_RESERVE),
        "coolant_stewardship": platform.submit_world_change("god", COOLANT_STEWARDSHIP),
        "fill_coolant": platform.submit_definition_proposal("god", FILL_COOLANT),
        "withdraw_coolant": platform.submit_definition_proposal("god", WITHDRAW_COOLANT),
        "take_coolant": platform.submit_definition_proposal("god", TAKE_COOLANT),
        "review_coolant": platform.submit_definition_proposal("god", REVIEW_COOLANT),
    }
    for payload in COOLANT_RULES:
        results[payload["rule"]["id"]] = platform.submit_world_change("god", payload)
    return results
