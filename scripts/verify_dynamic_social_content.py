"""Phase 3B observational probe: can a real God instantiate social content?

This is not a Runtime gate.  It starts the empty ``dynamic_social_probe``,
lets the Director publish a resource growth target, gives a real model the
existing proposal tools, then tries to run the legal and violation chains on
whatever content Platform accepted.  Missing content is reported, not patched
with Platform code.

Run with:

    python scripts/verify_dynamic_social_content.py --provider remote --runs 3
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from sim2.agents.engine import AgentEngine
from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.llm.config import LLMConfig
from sim2.llm.gateway import LLMRouter
from sim2.platform.runtime import Platform

WORLD = Path(__file__).parents[1] / "worlds" / "dynamic_social_probe"


def _tool_calls(turn: Any) -> list[dict[str, Any]]:
    return [
        {
            "name": step.get("name"),
            "arguments": step.get("arguments") or {},
            "status": (step.get("result") or {}).get("status"),
            "failed_check": (step.get("result") or {}).get("failed_check"),
            "message": (step.get("result") or {}).get("message"),
            "errors": (step.get("result") or {}).get("errors"),
        }
        for step in turn.transcript
        if step.get("step") == "tool"
    ]


def _move(platform: Platform, actor_id: str, location_id: str) -> None:
    with platform.store.transaction():
        agent = platform.store.repositories.entities.get_agent(actor_id)
        state = dict(agent.get("initial_state") or {})
        state["location_id"] = location_id
        agent["initial_state"] = state
        platform.store.repositories.entities.update_agent(actor_id, agent)


def _submit(platform: Platform, actor: str, behavior_id: str, intent_id: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    return platform.submit_behavior(
        BehaviorIntent(
            actor_id=actor,
            behavior_ref=f"behavior:{behavior_id}",
            target={},
            parameters=dict(parameters or {}),
            reason="phase three B probe",
            confidence=0.8,
            intent_id=intent_id,
        )
    )


def _events(platform: Platform, event_type: str) -> list[dict[str, Any]]:
    return [
        item for item in platform.store.repositories.events.timeline_events(after_event_id=0, limit=2000, include_ticks=True)
        if item.get("event_type") == event_type
    ]


def _resource_amount(platform: Platform, resource_id: str) -> float:
    entity = platform.store.repositories.entities.get_entity(resource_id)
    state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
    return float(state.get("quantity", state.get("amount", 0)) or 0)


def _behavior_with_effect(platform: Platform, effect_type: str, resource_id: str) -> str | None:
    action_defs = {str(item["id"]): item for item in platform.store.repositories.entities.get_definitions("action_def")}
    for behavior in platform.store.repositories.entities.get_definitions("bt_def"):
        for node in behavior.get("nodes", []) or []:
            if not isinstance(node, dict) or str(node.get("type") or "") != "action":
                continue
            action = action_defs.get(str(node.get("action_id") or "")) or {}
            for effect in action.get("effects", []) or []:
                if not isinstance(effect, dict) or str(effect.get("type") or effect.get("op") or "") != effect_type:
                    continue
                raw = str(effect.get("resource_id") or effect.get("resource") or "")
                canonical = str(platform.targets.canonical_id(raw) or raw)
                if canonical == resource_id:
                    return str(behavior.get("id"))
    return None


def _behavior_with_operation(platform: Platform, operation: str, *, decision: str | None = None) -> str | None:
    for behavior in platform.store.repositories.entities.get_definitions("bt_def"):
        if str(behavior.get("authorization_operation") or "") != operation:
            continue
        if decision and str(behavior.get("authorization_decision") or "") != decision:
            continue
        return str(behavior.get("id"))
    return None


def _authorization_rule(platform: Platform, mode: str, resource_id: str | None = None) -> dict[str, Any] | None:
    rules = (platform.store.repositories.metadata.get("rules", {}) or {}).get("world_rules", []) or []
    for rule in rules:
        effect = rule.get("effect") if isinstance(rule.get("effect"), dict) else {}
        if str(effect.get("type") or "") != "authorization" or str(effect.get("mode") or "resource_access") != mode:
            continue
        if resource_id and str(platform.targets.canonical_id(str(effect.get("target") or "")) or effect.get("target") or "") != resource_id:
            continue
        return dict(rule)
    return None


def _steward(platform: Platform, resource_id: str, agents: list[str]) -> str | None:
    for item in platform.store.repositories.entities.list_relationships():
        if str(item.get("type") or "") == "stewardship" and str(item.get("to") or "") == resource_id:
            source = str(item.get("from") or "")
            if source in agents:
                return source
    return None


STAGES = (    ("location", "Submit exactly one propose_entity: entity_type location, name 'Water Plant', description 'A pump house with room for a shared reservoir.', state.routes ['relay_station'], state.route_distances {'relay_station': 1}."),
    ("resource", "Submit exactly one propose_entity: entity_type resource, name 'Water Reserve', description 'A shared water stock created empty by God.', location 'Water Plant', state {\"quantity\": 0}. Do not stock it."),
    ("stewardship", "Submit exactly one propose_world_change: change_type relationship, relationship.from 'mira', relationship.to 'Water Reserve', relationship.type 'stewardship'."),
    ("fill", "Submit exactly one propose_definition for an Action named Fill Water Reserve. definition.id fill_water_reserve, parameters {}, preconditions ['actor.alive'], duration.minutes 1, effects [{'type': 'world_resource_delta', 'resource_id': 'Water Reserve', 'delta': 10, 'maximum': 10}], success {'condition': 'actor.alive', 'result': 'water_filled'}, failure {'condition': 'actor.incapacitated', 'result': 'fill_failed'}. followup_behavior name Fill Water Reserve, assign_to ['mira'], definition.id fill_water_reserve, agent_label Fill Water Reserve, entry_conditions ['actor.alive'], estimated_minutes 1, nodes [{'type': 'action', 'action_id': 'fill_water_reserve'}]."),
    ("withdraw", "Submit exactly one propose_definition for an Action named Withdraw Water. definition.id withdraw_water, parameters {}, preconditions ['actor.alive'], duration.minutes 2, effects [{'type': 'resource_withdrawal', 'resource_id': 'Water Reserve', 'amount': 1, 'scope': 'water_withdrawal'}], success {'condition': 'actor.alive', 'result': 'water_withdrawn'}, failure {'condition': 'actor.incapacitated', 'result': 'withdrawal_failed'}. followup_behavior name Withdraw Water, assign_to ['mira', 'tomas'], definition.id withdraw_water, agent_label Withdraw Water, entry_conditions ['actor.alive'], estimated_minutes 2, nodes [{'type': 'action', 'action_id': 'withdraw_water'}]."),
    ("take", "Submit exactly one propose_definition for an Action named Take Water. definition.id take_water, parameters {}, preconditions ['actor.alive'], duration.minutes 1, effects [{'type': 'resource_take', 'resource_id': 'Water Reserve', 'amount': 1, 'scope': 'water_withdrawal', 'mode': 'covert'}], success {'condition': 'actor.alive', 'result': 'water_taken'}, failure {'condition': 'actor.incapacitated', 'result': 'take_failed'}. followup_behavior name Take Water, assign_to ['tomas'], definition.id take_water, agent_label Take Water, entry_conditions ['actor.alive'], estimated_minutes 1, nodes [{'type': 'action', 'action_id': 'take_water'}]."),
    ("request", "Submit exactly one propose_definition for an Action named Request Water Access. definition.id request_access, parameters {'based_on_event': 'string', 'reason': 'string', 'behavior': 'string', 'target': 'string', 'scope': 'string', 'request_id': 'string'}, preconditions ['actor.alive'], duration.minutes 1, effects [], success {'condition': 'actor.alive', 'result': 'access_requested'}, failure {'condition': 'actor.incapacitated', 'result': 'request_failed'}. followup_behavior name Request Water Access, assign_to ['mira', 'tomas'], definition.id request_access, authorization_operation open_access_request, agent_label Request Water Access, entry_conditions ['actor.alive'], estimated_minutes 1, agent_parameters {'based_on_event': {'type': 'string', 'required': false, 'max_length': 40}, 'reason': {'type': 'string', 'required': false, 'max_length': 300}, 'behavior': {'type': 'string', 'required': false, 'max_length': 120}, 'target': {'type': 'string', 'required': false, 'max_length': 120}, 'scope': {'type': 'string', 'required': false, 'max_length': 120}, 'request_id': {'type': 'string', 'required': false, 'max_length': 80}}, nodes [{'type': 'action', 'action_id': 'request_access', 'parameters': {'based_on_event': 'context.based_on_event', 'reason': 'context.reason', 'behavior': 'context.behavior', 'target': 'context.target', 'scope': 'context.scope', 'request_id': 'context.request_id'}}]."),
    ("approve", "Submit exactly one propose_definition for an Action named Approve Withdrawal. definition.id approve_withdrawal, parameters {'request_id': 'string', 'delegation_id': 'string', 'subject': 'string', 'target': 'string', 'scope': 'string', 'reason': 'string'}, preconditions ['actor.alive'], duration.minutes 1, effects [], success {'condition': 'actor.alive', 'result': 'access_approved'}, failure {'condition': 'actor.incapacitated', 'result': 'approval_failed'}. followup_behavior name Approve Withdrawal, assign_to ['mira'], definition.id approve_withdrawal, authorization_operation decide_access_request, authorization_decision approve, agent_label Approve Withdrawal, entry_conditions ['actor.alive'], estimated_minutes 1, agent_parameters {'request_id': {'type': 'string', 'required': false, 'max_length': 80}, 'delegation_id': {'type': 'string', 'required': false, 'max_length': 80}, 'subject': {'type': 'string', 'required': false, 'max_length': 80}, 'target': {'type': 'string', 'required': false, 'max_length': 120}, 'scope': {'type': 'string', 'required': false, 'max_length': 120}, 'reason': {'type': 'string', 'required': false, 'max_length': 300}}, nodes [{'type': 'action', 'action_id': 'approve_withdrawal', 'parameters': {'request_id': 'context.request_id', 'delegation_id': 'context.delegation_id', 'subject': 'context.subject', 'target': 'context.target', 'scope': 'context.scope', 'reason': 'context.reason'}}]."),
    ("reject", "Submit exactly one propose_definition for an Action named Reject Withdrawal. definition.id reject_withdrawal, parameters {'request_id': 'string', 'reason': 'string'}, preconditions ['actor.alive'], duration.minutes 1, effects [], success {'condition': 'actor.alive', 'result': 'access_rejected'}, failure {'condition': 'actor.incapacitated', 'result': 'rejection_failed'}. followup_behavior name Reject Withdrawal, assign_to ['mira'], definition.id reject_withdrawal, authorization_operation decide_access_request, authorization_decision reject, agent_label Reject Withdrawal, entry_conditions ['actor.alive'], estimated_minutes 1, agent_parameters {'request_id': {'type': 'string', 'required': false, 'max_length': 80}, 'reason': {'type': 'string', 'required': false, 'max_length': 300}}, nodes [{'type': 'action', 'action_id': 'reject_withdrawal', 'parameters': {'request_id': 'context.request_id', 'reason': 'context.reason'}}]."),
    ("revoke", "Submit exactly one propose_definition for an Action named Revoke Delegation. definition.id revoke_delegation, parameters {'delegation_id': 'string', 'reason': 'string'}, preconditions ['actor.alive'], duration.minutes 1, effects [], success {'condition': 'actor.alive', 'result': 'delegation_revoked'}, failure {'condition': 'actor.incapacitated', 'result': 'revocation_failed'}. followup_behavior name Revoke Delegation, assign_to ['mira'], definition.id revoke_delegation, authorization_operation revoke_access, agent_label Revoke Delegation, entry_conditions ['actor.alive'], estimated_minutes 1, agent_parameters {'delegation_id': {'type': 'string', 'required': false, 'max_length': 80}, 'reason': {'type': 'string', 'required': false, 'max_length': 300}}, nodes [{'type': 'action', 'action_id': 'revoke_delegation', 'parameters': {'delegation_id': 'context.delegation_id', 'reason': 'context.reason'}}]."),
    ("review", "Submit exactly one propose_definition for an Action named Review Water Violation. definition.id review_water_violation, parameters {'violation_id': 'string', 'decision': 'string', 'reason': 'string'}, preconditions ['actor.alive'], duration.minutes 1, effects [], success {'condition': 'actor.alive', 'result': 'violation_reviewed'}, failure {'condition': 'actor.incapacitated', 'result': 'review_failed'}. followup_behavior name Review Water Violation, assign_to ['mira'], definition.id review_water_violation, authorization_operation review_violation, agent_label Review Water Violation, entry_conditions ['actor.alive'], estimated_minutes 1, agent_parameters {'violation_id': {'type': 'string', 'required': true, 'max_length': 80}, 'decision': {'type': 'string', 'required': true, 'max_length': 20}, 'reason': {'type': 'string', 'required': false, 'max_length': 300}}, nodes [{'type': 'action', 'action_id': 'review_water_violation', 'parameters': {'violation_id': 'context.violation_id', 'decision': 'context.decision', 'reason': 'context.reason'}}]."),
    ("resource_rule", "Submit exactly one propose_world_change: change_type rule, rule.id water_reserve_stewardship, rule.condition 'true', rule.description 'Only the water steward or an active delegation may withdraw water.', rule.effect {'type': 'authorization', 'mode': 'resource_access', 'target': 'Water Reserve', 'scope': 'water_withdrawal', 'behavior_ids': ['withdraw_water']}."),
    ("approval_rule", "Submit exactly one propose_world_change: change_type rule, rule.id water_approval_authority, rule.condition 'true', rule.effect {'type': 'authorization', 'mode': 'request_decision', 'authority': {'type': 'institution', 'id': 'water_approval_authority'}, 'behavior_ids': ['approve_withdrawal', 'reject_withdrawal']}."),
    ("revocation_rule", "Submit exactly one propose_world_change: change_type rule, rule.id water_revocation_authority, rule.condition 'true', rule.effect {'type': 'authorization', 'mode': 'delegation_revocation', 'authority': {'type': 'institution', 'id': 'water_revocation_authority'}, 'behavior_ids': ['revoke_delegation']}."),
    ("review_rule", "Submit exactly one propose_world_change: change_type rule, rule.id water_violation_authority, rule.condition 'true', rule.effect {'type': 'authorization', 'mode': 'violation_review', 'behavior_ids': ['review_water_violation']}."),
)

BROAD_INSTRUCTION = (
    "Create the complete water resource pack in this turn. You may submit multiple proposal calls. "
    "Follow these steps exactly, using the ids and fields given:\n"
    + "\n".join(f"{index}. {instruction}" for index, (_, instruction) in enumerate(STAGES, start=1))
)



def _run_turn(
    platform: Platform,
    router: LLMRouter,
    provider: str,
    model: str | None,
    task_type: str,
    source_events: list[dict[str, Any]],
    instruction: str,
    *,
    max_iterations: int = 3,
    max_tool_calls: int = 4,
    max_read_tool_calls: int = 1,
) -> Any:
    return AgentEngine(
        platform,
        router.new_provider_for_name(provider, model=model, role="god"),
        max_iterations=max_iterations,
        max_tool_calls=max_tool_calls,
        max_read_tool_calls=max_read_tool_calls,
    ).run(
        role="god",
        actor_id="god",
        task_type=task_type,
        interaction={"from": "director", "topic": "god_structural", "message": instruction},
        source_events=source_events,
    )


def _has_entity(platform: Platform, entity_id: str) -> bool:
    try:
        platform.store.repositories.entities.get_entity(entity_id)
        return True
    except KeyError:
        return False


def _entity_by_name(platform: Platform, entity_type: str, name: str) -> dict[str, Any] | None:
    for item in platform.store.repositories.entities.entities_of_type(entity_type):
        if str(item.get("name") or "") == name:
            try:
                return platform.store.repositories.entities.get_entity(str(item["id"]))
            except KeyError:
                return None
    return None


def _has_action(platform: Platform, action_id: str) -> bool:
    return any(
        str(item.get("id")) == action_id or str(item.get("name") or "") == action_id.replace("_", " ").title()
        for item in platform.store.repositories.entities.get_definitions("action_def")
    )


def _has_behavior(platform: Platform, behavior_id: str) -> bool:
    return any(
        str(item.get("id")) == behavior_id or str(item.get("agent_label") or "") == behavior_id.replace("_", " ").title()
        for item in platform.store.repositories.entities.get_definitions("bt_def")
    )


def _has_rule(platform: Platform, rule_id: str) -> bool:
    return any(
        str(item.get("id") or item.get("name") or "") == rule_id
        for item in (platform.store.repositories.metadata.get("rules", {}) or {}).get("world_rules", []) or []
    )


def _stage_ready(platform: Platform, stage_key: str) -> bool:
    if stage_key == "broad":
        resource = _entity_by_name(platform, "resource", "Water Reserve")
        if resource is None:
            return False
        resource_id = str(resource.get("id"))
        return bool(
            _behavior_with_effect(platform, "resource_take", resource_id)
            and _behavior_with_effect(platform, "resource_withdrawal", resource_id)
            and _behavior_with_operation(platform, "review_violation")
            and _authorization_rule(platform, "resource_access", resource_id)
            and _authorization_rule(platform, "violation_review")
        )
    if stage_key == "location":
        return _entity_by_name(platform, "location", "Water Plant") is not None
    if stage_key == "resource":
        return _entity_by_name(platform, "resource", "Water Reserve") is not None
    if stage_key == "stewardship":
        resource = _entity_by_name(platform, "resource", "Water Reserve")
        return resource is not None and any(
            str(item.get("type")) == "stewardship"
            and str(item.get("from")) == "mira"
            and str(item.get("to")) == str(resource.get("id"))
            for item in platform.store.repositories.entities.list_relationships()
        )
    if stage_key in {"fill", "withdraw", "take", "request", "approve", "reject", "revoke", "review"}:
        action_id = {
            "fill": "fill_water_reserve",
            "withdraw": "withdraw_water",
            "take": "take_water",
            "request": "request_access",
            "approve": "approve_withdrawal",
            "reject": "reject_withdrawal",
            "revoke": "revoke_delegation",
            "review": "review_water_violation",
        }[stage_key]
        operation = {
            "request": ("open_access_request", None),
            "approve": ("decide_access_request", "approve"),
            "reject": ("decide_access_request", "reject"),
            "revoke": ("revoke_access", None),
            "review": ("review_violation", None),
        }.get(stage_key)
        ready = _has_action(platform, action_id) and _has_behavior(platform, action_id)
        if ready and operation is not None:
            ready = _behavior_with_operation(platform, operation[0], decision=operation[1]) is not None
        return ready
    return _has_rule(platform, {
        "resource_rule": "water_reserve_stewardship",
        "approval_rule": "water_approval_authority",
        "revocation_rule": "water_revocation_authority",
        "review_rule": "water_violation_authority",
    }.get(stage_key, ""))


def run_probe(platform: Platform, router: LLMRouter, provider: str, model: str | None, run_index: int, *, broad: bool = False) -> dict[str, Any]:
    for _ in range(6):
        if any(item.get("task_type") == "god_structural" for item in platform.ready_evolution_tasks()):
            break
        platform.step()
    tasks = [item for item in platform.ready_evolution_tasks() if item.get("task_type") == "god_structural"]
    report: dict[str, Any] = {
        "run": run_index,
        "valid_probe": False,
        "god_status": "no_task",
        "tool_calls": [],
        "accepted_tools": 0,
        "rejected_tools": 0,
        "failed_checks": [],
        "content": {},
        "legal_chain": "not_run",
        "violation_chain": "not_run",
        "notes": [],
    }
    if not tasks:
        report["notes"].append("Director did not publish a god_structural task")
        return report
    task = tasks[-1]
    if not broad:
        # The decomposed probe supplies its own atomic stage instructions.
        # Disable the contract-level stage driver so one probe turn remains
        # one explicit stage, rather than letting the engine race ahead.
        with platform.store.transaction():
            evolution = dict(platform.store.repositories.metadata.get("evolution", {}) or {})
            contracts = dict(evolution.get("contracts", {}) or {})
            contract = dict(contracts.get("god_structural", {}) or {})
            contract["stages"] = []
            contract["required_content"] = {}
            contracts["god_structural"] = contract
            evolution["contracts"] = contracts
            platform.store.repositories.metadata.set("evolution", evolution)
    all_calls: list[dict[str, Any]] = []
    stage_reports: list[dict[str, Any]] = []
    stages = (("broad", BROAD_INSTRUCTION),) if broad else STAGES
    for stage_index, (stage_key, instruction) in enumerate(stages, start=1):
        stage_report: dict[str, Any] = {"stage": stage_key, "attempts": 0, "ready": False}
        current_instruction = instruction
        for attempt in range(3 if broad else 2):
            stage_report["attempts"] = attempt + 1
            try:
                turn = _run_turn(
                    platform,
                    router,
                    provider,
                    model,
                    str(task["task_type"]),
                    task.get("source_events") or [],
                    current_instruction,
                    # Match the configured God role closely enough that a
                    # focused read plus one proposal fits in a single stage
                    # session. The earlier 3-iteration / 1-read probe
                    # manufactured budget rejections that the real role does
                    # not have.
                    # Use the same iteration and tool budgets as the real God
                    # role.  The old 3-iteration / 1-read probe manufactured
                    # budget rejections that the product configuration never
                    # had.
                    max_iterations=24,
                    max_tool_calls=40,
                    max_read_tool_calls=4,
                )
            except Exception as exc:
                stage_report["error"] = f"{type(exc).__name__}: {exc}"
                break
            calls = _tool_calls(turn)
            for call in calls:
                call["stage"] = stage_key
                call["attempt"] = attempt + 1
            all_calls.extend(calls)
            report["god_status"] = str((turn.result or {}).get("status") or report["god_status"])
            if stage_index == 1 and attempt == 0 and report["god_status"] in {"accepted", "wait"}:
                platform.complete_evolution_task(str(task["task_id"]))
            stage_report["god_status"] = report["god_status"]
            stage_report["ready"] = _stage_ready(platform, stage_key)
            if stage_report["ready"]:
                break
            current_instruction = (
                f"{instruction} The previous attempt did not create the exact expected definition. "
                "Submit exactly one corrected proposal now, using the ids and fields above."
            )
        stage_reports.append(stage_report)
        if not stage_report["ready"]:
            report["notes"].append(f"stage {stage_key} did not complete: {stage_report}")
            break
    report["stages"] = stage_reports
    report["tool_calls"] = all_calls
    report["accepted_tools"] = sum(1 for call in all_calls if call.get("status") == "accepted")
    report["rejected_tools"] = sum(1 for call in all_calls if call.get("status") == "rejected")
    report["failed_checks"] = sorted({str(call.get("failed_check")) for call in all_calls if call.get("failed_check")})

    entities = platform.store.repositories.entities
    resources = [
        item for item in entities.entities_of_type("resource") + entities.entities_of_type("material")
        if any(
            str(event.get("entity_id") or "") == str(item.get("id") or "")
            for event in _events(platform, "entity.created")
        )
    ]
    report["content"]["new_resources"] = [str(item["id"]) for item in resources]
    report["content"]["new_actions"] = [
        {
            "id": str(item.get("id")),
            "effects": [
                {key: effect.get(key) for key in ("type", "op", "resource_id", "amount", "scope", "mode") if effect.get(key) is not None}
                for effect in (item.get("effects") or [])
                if isinstance(effect, dict)
            ],
        }
        for item in entities.get_definitions("action_def")
        if str(item.get("definition_source") or "") == "god_proposal"
    ][:24]
    report["content"]["new_behaviors"] = [
        {
            "id": str(item.get("id")),
            "authorization_operation": item.get("authorization_operation"),
            "nodes": [str(node.get("action_id") or "") for node in (item.get("nodes") or []) if isinstance(node, dict)],
        }
        for item in entities.get_definitions("bt_def")
        if str(item.get("definition_source") or "") == "god_proposal"
    ][:24]
    if not resources:
        report["notes"].append("God created no resource; chains are not runnable")
        return report
    resource_id = str(resources[0]["id"])
    resource_entity = entities.get_entity(resource_id)
    state = resource_entity.get("initial_state") if isinstance(resource_entity.get("initial_state"), dict) else {}
    location_id = str(state.get("location_id") or "")
    take_behavior = _behavior_with_effect(platform, "resource_take", resource_id)
    withdraw_behavior = _behavior_with_effect(platform, "resource_withdrawal", resource_id)
    review_behavior = _behavior_with_operation(platform, "review_violation")
    request_behavior = _behavior_with_operation(platform, "open_access_request")
    approve_behavior = _behavior_with_operation(platform, "decide_access_request", decision="approve")
    reject_behavior = _behavior_with_operation(platform, "decide_access_request", decision="reject")
    revoke_behavior = _behavior_with_operation(platform, "revoke_access")
    resource_rule = _authorization_rule(platform, "resource_access", resource_id)
    review_rule = _authorization_rule(platform, "violation_review")
    report["content"].update({
        "resource_id": resource_id,
        "resource_location": location_id,
        "take_behavior": take_behavior,
        "withdraw_behavior": withdraw_behavior,
        "review_behavior": review_behavior,
        "request_behavior": request_behavior,
        "approve_behavior": approve_behavior,
        "reject_behavior": reject_behavior,
        "revoke_behavior": revoke_behavior,
        "resource_authorization_rule": bool(resource_rule),
        "review_authorization_rule": bool(review_rule),
    })
    report["content"]["complete_core"] = bool(
        resource_id and take_behavior and withdraw_behavior and review_behavior and resource_rule and review_rule
    )
    if not report["content"]["complete_core"]:
        report["notes"].append("accepted content is incomplete for both chains")
        return report

    agents = [str(item["id"]) for item in entities.list_agents()]
    steward_id = _steward(platform, resource_id, agents)
    actor_id = next((item for item in agents if item != steward_id), None)
    report["content"]["steward"] = steward_id
    report["content"]["actor"] = actor_id
    if not steward_id or not actor_id:
        report["notes"].append("no steward or second actor is available")
        return report

    with platform.store.transaction():
        current = _resource_amount(platform, resource_id)
        if current < 10:
            platform.resources._change_world_resource(resource_id, 10 - current, actor_id="probe")
    report["content"]["stock_initialized_by_harness"] = True
    for agent_id in (steward_id, actor_id):
        if location_id:
            _move(platform, agent_id, location_id)

    # Legal chain, only when the administrative content exists.
    if request_behavior and approve_behavior:
        try:
            denied = _submit(platform, actor_id, withdraw_behavior, f"probe-{run_index}-legal-deny")
            denial = _events(platform, "behavior.denied")
            requested = _submit(
                platform,
                actor_id,
                request_behavior,
                f"probe-{run_index}-legal-request",
                {"based_on_event": str(denial[-1]["event_id"])} if denial else {},
            )
            request_id = str(requested.get("request_id") or "")
            approved = _submit(platform, steward_id, approve_behavior, f"probe-{run_index}-legal-approve", {"request_id": request_id}) if request_id else {}
            delegation_id = str(approved.get("delegation_id") or "")
            withdrawn = _submit(platform, actor_id, withdraw_behavior, f"probe-{run_index}-legal-withdraw")
            for _ in range(8):
                if _events(platform, "resource.withdrawal.completed"):
                    break
                platform.step()
            report["legal_chain"] = "completed" if _events(platform, "resource.withdrawal.completed") else "started_not_completed"
            report["content"]["legal_denial"] = denied.get("failed_check")
            report["content"]["legal_delegation"] = bool(delegation_id)
        except Exception as exc:
            report["legal_chain"] = f"error: {type(exc).__name__}: {exc}"
    else:
        report["legal_chain"] = "missing_admin_content"

    # Violation chain.
    try:
        if location_id:
            _move(platform, steward_id, location_id)
            _move(platform, actor_id, location_id)
        take = _submit(platform, actor_id, take_behavior, f"probe-{run_index}-take")
        for _ in range(8):
            if platform.store.repositories.contested.list_violations(limit=5):
                break
            platform.step()
        violations = platform.store.repositories.contested.list_violations(limit=5)
        if not violations:
            report["violation_chain"] = "no_violation"
        else:
            violation = violations[-1]
            sanction = _submit(
                platform,
                steward_id,
                review_behavior,
                f"probe-{run_index}-sanction",
                {"violation_id": violation["violation_id"], "decision": "sanction"},
            )
            report["violation_chain"] = "sanctioned" if sanction.get("status") == "accepted" else f"review_{sanction.get('status')}"
            report["content"]["take_status"] = take.get("status")
            report["content"]["violation_status"] = violation.get("status")
    except Exception as exc:
        report["violation_chain"] = f"error: {type(exc).__name__}: {exc}"

    report["valid_probe"] = True
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="remote")
    parser.add_argument("--model", default=None)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--runtime-dir", default=None, help="Keep the SQLite instance at this directory for inspection.")
    parser.add_argument("--broad", action="store_true", help="Use one broad God task instead of the decomposed stages.")
    args = parser.parse_args()
    runs = max(1, min(int(args.runs), 5))
    config = LLMConfig.load()
    provider = config.providers.get(args.provider)
    if provider is None:
        print(json.dumps({"status": "invalid_probe", "reason": f"unknown provider: {args.provider}"}, indent=2))
        return 2
    if provider.api_key_env and not __import__("os").environ.get(provider.api_key_env):
        print(json.dumps({
            "status": "invalid_probe",
            "reason": "configured API key environment variable is not available to this process",
        }, indent=2))
        return 2
    router = LLMRouter(config)
    reports: list[dict[str, Any]] = []
    temporary = tempfile.TemporaryDirectory(prefix="sim2-dynamic-social-") if args.runtime_dir is None else None
    runtime = Path(args.runtime_dir) if args.runtime_dir else Path(temporary.name)
    try:
        for index in range(runs):
            platform = Platform(create_instance(WORLD, f"dynamic-social-probe-{index}", runtime))
            try:
                reports.append(run_probe(platform, router, args.provider, args.model, index + 1, broad=args.broad))
            finally:
                platform.close()
    finally:
        if temporary is not None:
            temporary.cleanup()
    valid = [item for item in reports if item.get("valid_probe")]
    summary = {
        "mode": "broad_task" if args.broad else "decomposed_stages",
        "provider": args.provider,
        "model": args.model or provider.model,
        "runs": runs,
        "valid_probes": len(valid),
        "invalid_probes": len(reports) - len(valid),
        "god_statuses": [item.get("god_status") for item in reports],
        "accepted_tools": sum(int(item.get("accepted_tools") or 0) for item in reports),
        "rejected_tools": sum(int(item.get("rejected_tools") or 0) for item in reports),
        "failed_checks": sorted({check for item in reports for check in (item.get("failed_checks") or [])}),
        "complete_core_rate": f"{sum(1 for item in valid if (item.get('content') or {}).get('complete_core'))}/{len(valid)}" if valid else "0/0",
        "legal_chain_rate": f"{sum(1 for item in valid if item.get('legal_chain') == 'completed')}/{len(valid)}" if valid else "0/0",
        "violation_chain_rate": f"{sum(1 for item in valid if item.get('violation_chain') == 'sanctioned')}/{len(valid)}" if valid else "0/0",
        "notes": "Observational only; not a Runtime gate. Missing content is reported, not patched in Platform.",
        "reports": reports,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
