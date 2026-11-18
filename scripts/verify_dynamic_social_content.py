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


STAGES = (
    "Create a connected water location and an empty water resource there. Do not stock the resource; a later behavior will fill it.",
    "Create a stewardship relationship from Mira to the new water resource.",
    "Create a controlled withdrawal action and its behavior for the water resource, so a steward or an active delegation can withdraw one unit.",
    "Create a covert take action and its behavior for the water resource.",
    "Create a violation review action, its behavior, and the authorization rule that only the water steward may review an observed violation.",
    "Create the request, approve, reject and revoke administrative behaviors and the authorization rules they need.",
    "Use propose_world_change with change_type rule to add two rules: a resource_access authorization rule for the controlled withdrawal behavior, and a violation_review authorization rule for the review behavior.",
)


def _run_turn(
    platform: Platform,
    router: LLMRouter,
    provider: str,
    model: str | None,
    task_type: str,
    source_events: list[dict[str, Any]],
    instruction: str,
) -> Any:
    return AgentEngine(
        platform,
        router.new_provider_for_name(provider, model=model, role="god"),
        max_iterations=10,
        max_tool_calls=20,
        max_read_tool_calls=4,
    ).run(
        role="god",
        actor_id="god",
        task_type=task_type,
        interaction={"from": "director", "topic": "god_structural", "message": instruction},
        source_events=source_events,
    )


def run_probe(platform: Platform, router: LLMRouter, provider: str, model: str | None, run_index: int) -> dict[str, Any]:
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
    all_calls: list[dict[str, Any]] = []
    for stage_index, instruction in enumerate(STAGES, start=1):
        try:
            turn = _run_turn(
                platform,
                router,
                provider,
                model,
                str(task["task_type"]),
                task.get("source_events") or [],
                instruction,
            )
        except Exception as exc:
            report["notes"].append(f"stage {stage_index} provider error: {type(exc).__name__}: {exc}")
            break
        calls = _tool_calls(turn)
        for call in calls:
            call["stage"] = stage_index
        all_calls.extend(calls)
        report["god_status"] = str((turn.result or {}).get("status") or report["god_status"])
        if stage_index == 1 and report["god_status"] in {"accepted", "wait"}:
            platform.complete_evolution_task(str(task["task_id"]))
        if report["god_status"] not in {"accepted", "wait"}:
            report["notes"].append(f"stage {stage_index} ended with {report['god_status']}")
            break
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
    with tempfile.TemporaryDirectory(prefix="sim2-dynamic-social-") as runtime:
        for index in range(runs):
            platform = Platform(create_instance(WORLD, f"dynamic-social-probe-{index}", Path(runtime)))
            try:
                reports.append(run_probe(platform, router, args.provider, args.model, index + 1))
            finally:
                platform.close()
    valid = [item for item in reports if item.get("valid_probe")]
    summary = {
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
