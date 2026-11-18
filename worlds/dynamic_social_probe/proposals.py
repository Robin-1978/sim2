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
