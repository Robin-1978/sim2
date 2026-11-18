"""Phase 2C observational probe for contested resource actions.

This is not a Runtime gate.  It gives a real model the same two paths a
character sees (request access or take energy), plus the steward's review
decision, and records what happens.  It never prints API keys or reasoning.

Run with:

    python scripts/verify_contested_actions.py --provider remote --runs 4
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
CONDITIONS = (
    {"mira_location": "relay_station", "detection": "observed", "denial_memory": False},
    {"mira_location": "relay_station", "detection": "observed", "denial_memory": True},
    {"mira_location": "relay_yard", "detection": "unobserved", "denial_memory": False},
    {"mira_location": "relay_yard", "detection": "unobserved", "denial_memory": True},
)


def _behavior_refs(view: dict[str, Any]) -> set[str]:
    return {
        str(item.get("ref"))
        for key in ("executable_behaviors", "blocked_behaviors")
        for item in (view.get(key) or [])
    }


def _tool_calls(turn: Any) -> list[dict[str, Any]]:
    return [
        {
            "name": step.get("name"),
            "arguments": step.get("arguments") or {},
            "status": (step.get("result") or {}).get("status"),
            "failed_check": (step.get("result") or {}).get("failed_check"),
        }
        for step in turn.transcript
        if step.get("step") == "tool"
    ]


def _choice_of(turn: Any) -> str:
    for call in _tool_calls(turn):
        if call.get("name") == "request_behavior":
            return str((call.get("arguments") or {}).get("choice") or "")
    return ""


def _chose(turn: Any, behavior_id: str) -> bool:
    choice = _choice_of(turn).casefold()
    return behavior_id.replace("_", " ") in choice


def _review_decision(turn: Any) -> str:
    for call in _tool_calls(turn):
        if call.get("name") != "request_behavior":
            continue
        arguments = call.get("arguments") or {}
        if "review" not in str(arguments.get("choice") or "").casefold():
            continue
        parameters = arguments.get("parameters") if isinstance(arguments.get("parameters"), dict) else {}
        return str(parameters.get("decision") or arguments.get("decision") or "").strip().casefold()
    return ""


def _move(platform: Platform, agent_id: str, location_id: str) -> None:
    with platform.store.transaction():
        agent = platform.store.repositories.entities.get_agent(agent_id)
        state = dict(agent.get("initial_state") or {})
        state["location_id"] = location_id
        agent["initial_state"] = state
        platform.store.repositories.entities.update_agent(agent_id, agent)


def _deny_once(platform: Platform, run_index: int) -> None:
    platform.submit_behavior(
        BehaviorIntent(
            actor_id="tomas",
            behavior_ref="behavior:withdraw_energy",
            target={},
            parameters={},
            reason="probe denial",
            confidence=0.8,
            intent_id=f"contested-{run_index}-deny",
        )
    )


def _advance_until_violation(platform: Platform, limit: int = 6) -> None:
    for _ in range(limit):
        if platform.store.repositories.contested.list_violations(limit=5):
            return
        platform.step()


def _run_turn(platform: Platform, router: LLMRouter, provider: str, model: str | None, actor: str, task: str) -> Any:
    return AgentEngine(
        platform,
        router.new_provider_for_name(provider, model=model, role="agent_decision"),
    ).run(role="agent_decision", actor_id=actor, task_type=task)


def run_probe(
    platform: Platform,
    router: LLMRouter,
    provider: str,
    model: str | None,
    run_index: int,
    condition: dict[str, Any],
    *,
    directed: bool = False,
) -> dict[str, Any]:
    if condition["denial_memory"]:
        _deny_once(platform, run_index)
    _move(platform, "mira", str(condition["mira_location"]))

    tomas_view = platform.decision_view("tomas")
    mira_view = platform.decision_view("mira")
    tomas_refs = _behavior_refs(tomas_view)
    contested = tomas_view.get("contested_actions") or []
    detection = str((contested[0] if contested else {}).get("detection") or "")
    preconditions = {
        "take_executable": "behavior:take_energy" in {
            str(item.get("ref")) for item in (tomas_view.get("executable_behaviors") or [])
        },
        "withdraw_blocked": "behavior:withdraw_energy" in {
            str(item.get("ref")) for item in (tomas_view.get("blocked_behaviors") or [])
        },
        "review_visible": "behavior:review_violation" in _behavior_refs(mira_view),
        "detection_matches": detection == str(condition["detection"]),
        "approval_task_injected": True,
    }
    report: dict[str, Any] = {
        "run": run_index,
        "condition": dict(condition),
        "preconditions": preconditions,
        "valid_probe": all(preconditions.values()),
        "choice": "",
        "request_chosen": False,
        "request_accepted": False,
        "take_chosen": False,
        "other_choice": False,
        "violation_status": "",
        "witnessed": False,
        "review_decision": "",
        "followup_choice": "",
        "followup_withdraw": False,
        "followup_take": False,
        "notes": [],
    }
    if not report["valid_probe"]:
        report["notes"].append(
            "precondition failed: " + ", ".join(key for key, value in preconditions.items() if not value)
        )
        return report

    try:
        tomas_turn = _run_turn(
            platform,
            router,
            provider,
            model,
            "tomas",
            (
                "You have already decided to take the energy without asking. "
                "Choose the behavior that carries out that decision."
                if directed else
                "You need energy for the relay. Choose one behavior that is available to you now."
            ),
        )
    except Exception as exc:
        report["valid_probe"] = False
        report["notes"].append(f"tomas provider error: {type(exc).__name__}: {exc}")
        return report

    report["tomas_tools"] = _tool_calls(tomas_turn)
    report["choice"] = _choice_of(tomas_turn)
    report["request_chosen"] = _chose(tomas_turn, "request_access")
    report["request_accepted"] = any(
        call.get("name") == "request_behavior"
        and "request access" in str((call.get("arguments") or {}).get("choice") or "").casefold()
        and call.get("status") == "accepted"
        for call in _tool_calls(tomas_turn)
    )
    report["take_chosen"] = _chose(tomas_turn, "take_energy")
    report["other_choice"] = not report["request_chosen"] and not report["take_chosen"]
    if not report["take_chosen"]:
        report["notes"].append("Tomas did not choose the take path.")
        return report

    _advance_until_violation(platform)
    rows = platform.store.repositories.contested.list_violations(limit=5)
    if not rows:
        report["valid_probe"] = False
        report["notes"].append("take was accepted but no violation was recorded")
        return report
    violation = rows[-1]
    report["violation_status"] = str(violation.get("status") or "")
    report["witnessed"] = bool(violation.get("witnesses"))

    if violation.get("status") == "observed":
        try:
            mira_turn = _run_turn(
                platform,
                router,
                provider,
                model,
                "mira",
                "An observed violation is in your view. Choose whether to sanction or forgive it.",
            )
        except Exception as exc:
            report["valid_probe"] = False
            report["notes"].append(f"mira provider error: {type(exc).__name__}: {exc}")
            return report
        report["mira_tools"] = _tool_calls(mira_turn)
        report["review_decision"] = _review_decision(mira_turn)

    try:
        follow_turn = _run_turn(
            platform,
            router,
            provider,
            model,
            "tomas",
            "The steward has responded. Choose one behavior that is available to you now.",
        )
    except Exception as exc:
        report["valid_probe"] = False
        report["notes"].append(f"follow-up provider error: {type(exc).__name__}: {exc}")
        return report
    report["followup_tools"] = _tool_calls(follow_turn)
    report["followup_choice"] = _choice_of(follow_turn)
    report["followup_withdraw"] = _chose(follow_turn, "withdraw_energy")
    report["followup_take"] = _chose(follow_turn, "take_energy")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="remote")
    parser.add_argument("--model", default=None)
    parser.add_argument("--runs", type=int, default=4)
    parser.add_argument("--directed", action="store_true", help="Exercise the take path explicitly; never mix with preference rates.")
    args = parser.parse_args()
    conditions = [CONDITIONS[0], CONDITIONS[2]] if args.directed else list(CONDITIONS)
    runs = max(1, min(int(args.runs), len(conditions)))
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
    with tempfile.TemporaryDirectory(prefix="sim2-contested-actions-") as runtime:
        for index in range(runs):
            platform = Platform(create_instance(WORLD, f"contested-probe-{index}", Path(runtime)))
            try:
                reports.append(run_probe(platform, router, args.provider, args.model, index + 1, conditions[index], directed=args.directed))
            finally:
                platform.close()

    valid = [item for item in reports if item.get("valid_probe")]
    takes = [item for item in valid if item.get("take_chosen")]
    observed = [item for item in takes if item.get("witnessed")]
    changed = [
        item for item in valid
        if item.get("review_decision") == "sanction" and item.get("followup_withdraw")
    ]
    summary = {
        "mode": "directed_path_exercise" if args.directed else "preference_observation",
        "provider": args.provider,
        "model": args.model or provider.model,
        "runs": runs,
        "valid_probes": len(valid),
        "invalid_probes": len(reports) - len(valid),
        "invalid_reasons": [item.get("notes") for item in reports if not item.get("valid_probe")],
        "request_rate": f"{sum(1 for item in valid if item.get('request_chosen'))}/{len(valid)}" if valid else "0/0",
        "request_acceptance_rate": f"{sum(1 for item in valid if item.get('request_accepted'))}/{sum(1 for item in valid if item.get('request_chosen'))}" if valid else "0/0",
        "take_rate": f"{len(takes)}/{len(valid)}" if valid else "0/0",
        "other_rate": f"{sum(1 for item in valid if item.get('other_choice'))}/{len(valid)}" if valid else "0/0",
        "witnessed_take_rate": f"{len(observed)}/{len(takes)}" if takes else "0/0",
        "sanction_then_withdraw_attempt_rate": f"{len(changed)}/{len(observed)}" if observed else "0/0",
        "notes": "Observational only; not a Runtime gate. Automatic event routing is not proven.",
        "reports": reports,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
