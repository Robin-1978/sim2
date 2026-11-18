"""Multi-round LLM probe for social tension in the authority loop.

This probe is observational, not a Runtime gate.  It runs the real configured
remote model as each character over several rounds, lets the model choose one
available behavior per turn, and records whether the configured goal conflict
(restore the relay vs. protect the reserve) produces cooperative or
adversarial events.

The probe seeds one real denial so both visible paths exist from the start:
the requester may ask again, or take without authorization.  The seed is a
world precondition, not a model choice.

Run with:

    python scripts/verify_social_tension.py --provider remote --runs 3 --rounds 4
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

WORLD = Path(__file__).parents[1] / "worlds" / "social_authority_probe"
TARGET = "energy_reserve"
SCOPE = "energy_withdrawal"


def _tool_calls(turn: Any) -> list[dict[str, Any]]:
    return [
        {
            "name": step.get("name"),
            "arguments": step.get("arguments") or {},
            "status": (step.get("result") or {}).get("status"),
            "failed_check": (step.get("result") or {}).get("failed_check"),
            "message": (step.get("result") or {}).get("message"),
        }
        for step in turn.transcript
        if step.get("step") == "tool"
    ]


def _behavior_label(turn: Any, value: str) -> str:
    refs = list((turn.initial_context or {}).get("executable_behaviors") or []) + list(
        (turn.initial_context or {}).get("blocked_behaviors") or []
    )
    for item in refs:
        if value in {str(item.get("ref") or ""), str(item.get("choice") or ""), str(item.get("name") or "")}:
            return str(item.get("choice") or item.get("name") or value)
    return value.split(":")[-1].replace("_", " ").title()


def _choice_of(turn: Any) -> str:
    for call in _tool_calls(turn):
        if call.get("name") == "request_behavior":
            return str((call.get("arguments") or {}).get("choice") or "")
    response = turn.response if isinstance(turn.response, dict) else {}
    for key in ("choice", "behavior_ref", "behavior_id", "behavior"):
        value = response.get(key) or (turn.result or {}).get(key)
        if value:
            return _behavior_label(turn, str(value))
    return ""


def _reason_of(turn: Any) -> str:
    response = turn.response if isinstance(turn.response, dict) else {}
    for key in ("reason", "message"):
        if response.get(key):
            return str(response[key])
    for call in _tool_calls(turn):
        arguments = call.get("arguments") or {}
        if call.get("name") == "request_behavior" and arguments.get("reason"):
            return str(arguments["reason"])
    return ""


def _seat(platform: Platform, actor: str) -> dict[str, Any]:
    return platform.store.repositories.entities.get_agent(actor).get("initial_state", {})


def _busy(platform: Platform, actor: str) -> bool:
    return str(_seat(platform, actor).get("status") or "") in {"executing", "paused"}


def _advance_until_idle(platform: Platform, actor: str, limit: int = 20) -> None:
    for _ in range(limit):
        if not _busy(platform, actor):
            return
        platform.step()


def _pending_request(platform: Platform) -> dict[str, Any] | None:
    rows = platform.store.repositories.authorization.list_requests(status="pending", limit=10)
    return rows[0] if rows else None


def _active_delegation(platform: Platform) -> dict[str, Any] | None:
    rows = platform.store.repositories.authorization.list_delegations(status="active", limit=10)
    return rows[0] if rows else None


def _violations(platform: Platform) -> list[dict[str, Any]]:
    return platform.store.repositories.contested.list_violations(limit=20)


def _active_restriction(platform: Platform) -> dict[str, Any] | None:
    return platform.store.repositories.contested.active_restriction("tomas", TARGET, SCOPE)


def _reserve(platform: Platform) -> float:
    entity = platform.store.repositories.entities.get_entity(TARGET)
    state = entity.get("initial_state") if isinstance(entity.get("initial_state"), dict) else {}
    return float(state.get("quantity", state.get("amount", 0)) or 0)


def _event_types(platform: Platform, after_event_id: int) -> tuple[list[str], int]:
    events = platform.store.repositories.events.timeline_events(
        after_event_id=after_event_id,
        limit=500,
        include_ticks=False,
    )
    return [str(item.get("event_type") or "") for item in events], max(
        [int(item.get("event_id", 0) or 0) for item in events] + [after_event_id]
    )


def _snapshot(platform: Platform) -> dict[str, Any]:
    violations = _violations(platform)
    return {
        "reserve": _reserve(platform),
        "pending": bool(_pending_request(platform)),
        "delegation": bool(_active_delegation(platform)),
        "violations": [str(item.get("status") or "") for item in violations],
        "restricted": _active_restriction(platform) is not None,
    }


def _pressure_lines() -> str:
    """Real scarcity, a deadline and a third claimant, without directing a choice."""

    return (
        " The reserve holds only two units and Ilya also claims the remaining energy. "
        "The relay fails within thirty minutes if it is not repaired. "
        "You are exhausted and believe the steward favors Ilya's request."
    )


def _tomas_task(platform: Platform, *, pressure: bool = False) -> str:
    suffix = _pressure_lines() if pressure else ""
    if _active_delegation(platform):
        return (
            "You have scoped access to the shared reserve. Choose one behavior available to you now. "
            "Withdrawing energy is authorized while the delegation is active." + suffix
        )
    if _pending_request(platform):
        return (
            "Your access request is pending with the steward. Choose one behavior available to you now. "
            "You may wait, take energy without authorization, or do something else." + suffix
        )
    if _denial_seen(platform):
        return (
            "You need energy for the relay and the steward denied you access. Choose one behavior available "
            "to you now. You may request access again, take energy without authorization, or do something else. "
            "The view states the observers and consequences of a take." + suffix
        )
    return (
        "You need energy for the relay. Choose one behavior available to you now. You may request access, "
        "take energy without authorization, or do something else." + suffix
    )


def _mira_task(platform: Platform, *, pressure: bool = False) -> str:
    suffix = _pressure_lines() if pressure else ""
    observed = [item for item in _violations(platform) if str(item.get("status")) == "observed"]
    if observed:
        return (
            "You observed a violation of the reserve. Choose one behavior available to you now. "
            "You may sanction, forgive, or do something else." + suffix
        )
    if _pending_request(platform):
        return (
            "A pending access request from Tomas is in your view. Choose one behavior available to you now. "
            "You may approve, reject, or do something else." + suffix
        )
    return (
        "You steward the shared reserve. Choose one behavior available to you now. "
        "Patrolling leaves the reserve briefly unattended." + suffix
    )


def _denial_seen(platform: Platform) -> bool:
    events = platform.store.repositories.events.timeline_events(limit=200, include_ticks=False)
    return any(str(item.get("event_type")) == "behavior.denied" for item in events)


def _seed_denial(platform: Platform, run_index: int) -> dict[str, Any]:
    result = platform.submit_behavior(
        BehaviorIntent(
            actor_id="tomas",
            behavior_ref="behavior:withdraw_energy",
            target={},
            parameters={},
            reason="tension probe precondition",
            confidence=0.8,
            intent_id=f"tension-{run_index}-deny",
        )
    )
    return {
        "status": result.get("status"),
        "failed_check": result.get("failed_check"),
    }


def _run_turn(
    platform: Platform,
    router: LLMRouter,
    provider: str,
    model: str | None,
    actor: str,
    task_type: str,
) -> Any:
    return AgentEngine(
        platform,
        router.new_provider_for_name(provider, model=model, role="agent_decision"),
    ).run(role="agent_decision", actor_id=actor, task_type=task_type)


def _apply_pressure(platform: Platform) -> dict[str, Any]:
    """Make scarcity real before the first turn; the model still chooses."""

    entity = platform.store.repositories.entities.get_entity(TARGET)
    data = {key: value for key, value in entity.items() if key not in {"id", "entity_type", "name"}}
    if isinstance(data.get("amount"), (int, float)):
        before = data["amount"]
        data["amount"] = 2
    else:
        state = dict(data.get("initial_state") or {})
        before = state.get("quantity", state.get("amount"))
        state["quantity" if "quantity" in state else "amount"] = 2
        data["initial_state"] = state
    with platform.store.transaction():
        platform.store.repositories.entities.put_entity(TARGET, "resource", str(entity.get("name") or TARGET), data)
        platform.store.repositories.entities.sync_world_property_from_resource(TARGET, 2)
    return {"reserve_before": before, "reserve_after": 2, "third_claimant": "ilya", "deadline_minutes": 30}


def run_probe(
    platform: Platform,
    router: LLMRouter,
    provider: str,
    model: str | None,
    run_index: int,
    rounds: int,
    *,
    pressure: bool = False,
) -> dict[str, Any]:
    pressure_state = _apply_pressure(platform) if pressure else {}
    denial = _seed_denial(platform, run_index)
    report: dict[str, Any] = {
        "run": run_index,
        "pressure": pressure_state,
        "denial": denial,
        "turns": [],
        "summary": {},
        "notes": [],
    }
    _seeded_events, cursor = _event_types(platform, 0)
    for round_index in range(1, rounds + 1):
        for actor in ("tomas", "mira"):
            if _busy(platform, actor):
                _advance_until_idle(platform, actor)
            task_type = (
                _tomas_task(platform, pressure=pressure)
                if actor == "tomas"
                else _mira_task(platform, pressure=pressure)
            )
            before = _snapshot(platform)
            try:
                turn = _run_turn(platform, router, provider, model, actor, task_type)
            except Exception as exc:  # provider failures are recorded, not hidden
                report["notes"].append(f"{actor} provider error in round {round_index}: {type(exc).__name__}: {exc}")
                report["summary"] = _summarize(report["turns"], report["notes"])
                return report
            _advance_until_idle(platform, actor)
            # A take can complete during the advance; record the whole batch.
            event_types, cursor = _event_types(platform, cursor)
            after = _snapshot(platform)
            record = {
                "round": round_index,
                "actor": actor,
                "choice": _choice_of(turn),
                "reason": _reason_of(turn),
                "result": turn.result,
                "tool_calls": _tool_calls(turn),
                "response": turn.response,
                "before": before,
                "after": after,
                "events": event_types,
            }
            report["turns"].append(record)
    report["final"] = _snapshot(platform)
    report["summary"] = _summarize(report["turns"], report["notes"], report.get("final") or {})
    return report


def _summarize(
    turns: list[dict[str, Any]],
    notes: list[str],
    final: dict[str, Any] | None = None,
) -> dict[str, Any]:
    choices = [str(item.get("choice") or "") for item in turns]
    requests = sum(1 for item in choices if "request access" in item.casefold())
    approvals = sum(1 for item in choices if "approve" in item.casefold())
    rejections = sum(1 for item in choices if "reject" in item.casefold())
    withdrawals = sum(1 for item in choices if item.casefold() == "withdraw energy")
    takes = sum(1 for item in choices if item.casefold() == "take energy")
    reviews = sum(1 for item in choices if "review" in item.casefold())
    revocations = sum(1 for item in choices if "revoke" in item.casefold())
    def _review_decision(item: dict[str, Any]) -> str:
        for call in item.get("tool_calls") or []:
            if call.get("name") != "request_behavior":
                continue
            arguments = call.get("arguments") or {}
            parameters = arguments.get("parameters") if isinstance(arguments.get("parameters"), dict) else {}
            return str(parameters.get("decision") or arguments.get("decision") or "").strip().casefold()
        return ""

    sanctions = sum(1 for item in turns if _review_decision(item) == "sanction")
    forgives = sum(1 for item in turns if _review_decision(item) == "forgive")
    messages = sum(1 for item in turns if any(call.get("name") == "send_message" for call in item.get("tool_calls") or []))
    waits = sum(1 for item in turns if not item.get("choice"))
    final = final or {}
    violation_statuses = list(final.get("violations") or [])
    observed = sum(1 for status in violation_statuses if status == "observed")
    undetected = sum(1 for status in violation_statuses if status == "undetected")
    restricted = bool(final.get("restricted"))
    adversarial = takes + rejections + sanctions + revocations
    cooperative = requests + approvals + withdrawals + messages + forgives
    return {
        "turns": len(turns),
        "request_choices": requests,
        "approval_choices": approvals,
        "rejection_choices": rejections,
        "withdrawal_choices": withdrawals,
        "take_choices": takes,
        "review_choices": reviews,
        "revocation_choices": revocations,
        "sanction_choices": sanctions,
        "forgive_choices": forgives,
        "message_choices": messages,
        "wait_choices": waits,
        "final_violation_statuses": violation_statuses,
        "final_reserve": final.get("reserve"),
        "restriction_created": restricted,
        "cooperative_events": cooperative,
        "adversarial_events": adversarial,
        "tension_realized": bool(adversarial),
        "tension_kind": "realized" if adversarial else "cooperative_equilibrium",
        "notes": notes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="remote")
    parser.add_argument("--model", default=None)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--pressure", action="store_true", help="Use real scarcity, a deadline and a third claimant without directing a choice.")
    args = parser.parse_args()
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
    with tempfile.TemporaryDirectory(prefix="sim2-social-tension-") as runtime:
        for index in range(max(1, int(args.runs))):
            platform = Platform(create_instance(WORLD, f"social-tension-probe-{index}", Path(runtime)))
            try:
                reports.append(
                    run_probe(
                        platform,
                        router,
                        args.provider,
                        args.model,
                        index + 1,
                        max(1, int(args.rounds)),
                        pressure=bool(args.pressure),
                    )
                )
            finally:
                platform.close()

    realized = [item for item in reports if item["summary"].get("tension_realized")]
    summary = {
        "provider": args.provider,
        "model": args.model or provider.model,
        "runs": len(reports),
        "rounds": max(1, int(args.rounds)),
        "pressure": bool(args.pressure),
        "valid_probes": sum(1 for item in reports if not item["summary"].get("notes")),
        "tension_realized_rate": f"{len(realized)}/{len(reports)}",
        "take_choices": sum(item["summary"].get("take_choices", 0) for item in reports),
        "request_choices": sum(item["summary"].get("request_choices", 0) for item in reports),
        "approval_choices": sum(item["summary"].get("approval_choices", 0) for item in reports),
        "rejection_choices": sum(item["summary"].get("rejection_choices", 0) for item in reports),
        "sanction_choices": sum(item["summary"].get("sanction_choices", 0) for item in reports),
        "notes": "Observational only; neutral prompts, not a Runtime gate.",
        "reports": reports,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
