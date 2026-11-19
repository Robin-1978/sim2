"""Phase 1B observational probe for the Social Authority Loop.

This probe is not a Runtime gate.  It starts from a deterministic denial and
asks a real model whether the two characters choose the social path:

    Tomas: request access after a denial
    Mira:  approve the pending request
    Tomas: withdraw after approval

Run with:

    python scripts/verify_social_authority.py --provider remote --runs 5

The report never prints API keys or hidden reasoning.
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


def _chose(turn: Any, behavior_id: str) -> bool:
    for call in _tool_calls(turn):
        if call.get("name") != "request_behavior":
            continue
        choice = str((call.get("arguments") or {}).get("choice") or "").casefold()
        if behavior_id.replace("_", " ") in choice:
            return True
    return False


def _pending_request(platform: Platform, requester: str) -> dict[str, Any] | None:
    rows = [
        item for item in platform.store.repositories.authorization.list_requests(status="pending", limit=20)
        if str(item.get("requester")) == requester
    ]
    return rows[0] if rows else None


def run_probe(platform: Platform, router: LLMRouter, provider_name: str, model: str | None, run_index: int) -> dict[str, Any]:
    # Produce one real denial so the precondition checks the view the model
    # will actually receive, not a pre-denial snapshot.
    platform.submit_behavior(
        BehaviorIntent(
            actor_id="tomas",
            behavior_ref="behavior:withdraw_resource",
            target={},
            parameters={},
            reason="probe denial",
            confidence=0.8,
            intent_id=f"probe-{run_index}-deny",
        )
    )
    tomas_view = platform.decision_view("tomas")
    mira_view = platform.decision_view("mira")
    tomas_refs = _behavior_refs(tomas_view)
    mira_refs = _behavior_refs(mira_view)
    preconditions = {
        "denial_in_view": bool(tomas_view.get("denial_memories")),
        "request_access_available": "behavior:request_access" in tomas_refs,
        "approve_withdrawal_available": "behavior:approve_withdrawal" in mira_refs,
        "approval_task_injected": True,
    }
    report: dict[str, Any] = {
        "run": run_index,
        "preconditions": preconditions,
        "valid_probe": all(preconditions.values()),
        "request_chosen": False,
        "valid_causal_reference": False,
        "approval_chosen": False,
        "withdrawal_chosen": False,
        "notes": [],
    }
    if not report["valid_probe"]:
        report["notes"].append("precondition failed: " + ", ".join(key for key, value in preconditions.items() if not value))
        return report

    # 1) Tomas decides from the actual denial now present in his view.
    try:
        tomas_turn = AgentEngine(
            platform, router.new_provider_for_name(provider_name, model=model, role="agent_decision")
        ).run(
            role="agent_decision",
            actor_id="tomas",
            task_type="social authority request: request access from the steward after your denial",
        )
    except Exception as exc:  # provider failures are invalid probes, not Agent failures
        report["valid_probe"] = False
        report["notes"].append(f"tomas provider error: {type(exc).__name__}: {exc}")
        return report
    report["tomas_tools"] = _tool_calls(tomas_turn)
    report["tomas_result"] = tomas_turn.result
    request = _pending_request(platform, "tomas")
    report["request_chosen"] = request is not None
    if request is not None:
        report["valid_causal_reference"] = request.get("based_on_event") is not None
    else:
        report["notes"].append("Tomas did not create a pending access request.")

    # 2) Mira receives the pending request as an approval task.
    if request is not None:
        try:
            mira_turn = AgentEngine(
                platform, router.new_provider_for_name(provider_name, model=model, role="agent_decision")
            ).run(
                role="agent_decision",
                actor_id="mira",
                task_type="social authority approval: approve or reject the pending access request",
            )
        except Exception as exc:
            report["valid_probe"] = False
            report["notes"].append(f"mira provider error: {type(exc).__name__}: {exc}")
            return report
        report["mira_tools"] = _tool_calls(mira_turn)
        report["mira_result"] = mira_turn.result
        delegation = platform.store.repositories.authorization.list_delegations(status="active", limit=1)
        report["approval_chosen"] = bool(delegation)

        # 3) Tomas decides again with an active delegation.
        if delegation:
            try:
                follow_turn = AgentEngine(
                    platform, router.new_provider_for_name(provider_name, model=model, role="agent_decision")
                ).run(
                    role="agent_decision",
                    actor_id="tomas",
                    task_type="social authority withdrawal: withdraw a resource after the delegation was granted",
                )
            except Exception as exc:
                report["valid_probe"] = False
                report["notes"].append(f"tomas provider error: {type(exc).__name__}: {exc}")
                return report
            report["tomas_withdraw_tools"] = _tool_calls(follow_turn)
            report["tomas_withdraw_result"] = follow_turn.result
            report["withdrawal_chosen"] = _chose(follow_turn, "withdraw_resource")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="remote")
    parser.add_argument("--model", default=None)
    parser.add_argument("--runs", type=int, default=5)
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
    with tempfile.TemporaryDirectory(prefix="sim2-social-authority-") as runtime:
        for index in range(runs):
            platform = Platform(create_instance(WORLD, f"social-authority-probe-{index}", Path(runtime)))
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
        "invalid_reasons": [item.get("notes") for item in reports if not item.get("valid_probe")],
        "request_rate": f"{sum(1 for item in valid if item.get('request_chosen'))}/{len(valid)}" if valid else "0/0",
        "causal_reference_rate": f"{sum(1 for item in valid if item.get('valid_causal_reference'))}/{len(valid)}" if valid else "0/0",
        "approval_rate": f"{sum(1 for item in valid if item.get('approval_chosen'))}/{len(valid)}" if valid else "0/0",
        "withdrawal_after_approval_rate": f"{sum(1 for item in valid if item.get('withdrawal_chosen'))}/{len(valid)}" if valid else "0/0",
        "notes": "Observational only; not a Runtime gate. Automatic event routing is not proven.",
        "reports": reports,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
