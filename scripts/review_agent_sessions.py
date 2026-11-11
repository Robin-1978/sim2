"""Review real local Agent sessions with full context and output artifacts.

This is intentionally a review tool, not a production loop.  It writes a
redacted session report under ``artifacts/`` and prints only quality summaries.

    python scripts/review_agent_sessions.py
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from sim2.agent_view.validator import validate_agent_view
from sim2.agents.engine import AgentEngine
from sim2.agents.quality import evaluate_turn
from sim2.bootstrap.service import create_instance
from sim2.llm.gateway import LLMRouter
from sim2.platform.runtime import Platform
from sim2.prompting.renderer import render_context


def discover_model() -> str:
    import httpx

    response = httpx.get("http://127.0.0.1:8192/v1/models", timeout=10, trust_env=False)
    response.raise_for_status()
    models = response.json().get("data") or response.json().get("models") or []
    return str(models[0].get("id") or models[0].get("model") or models[0].get("name"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents", nargs="+", default=["lu_xingzhou", "liang_he", "lin_bo"])
    parser.add_argument("--provider", default="local")
    args = parser.parse_args()
    model = discover_model()
    router = LLMRouter.from_default_config()
    provider = router.new_provider_for_name(args.provider, model=model, role="agent_decision")
    report = {"provider": "local", "model": model, "sessions": []}
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    with tempfile.TemporaryDirectory(prefix="sim2-review-") as runtime:
        db = create_instance(config, "agent-review", Path(runtime))
        platform = Platform(db)
        try:
            for actor_id in args.agents:
                view = platform.decision_view(actor_id, role="agent_decision")
                context_errors = validate_agent_view(view, role="agent_decision")
                prompt = render_context(view, "Choose one behavior for the current goal.")
                turn = AgentEngine(platform, provider).run(role="agent_decision", actor_id=actor_id, task_type="review")
                quality = evaluate_turn(turn, view)
                report["sessions"].append({
                    "actor_id": actor_id,
                    "candidate_refs": [item.get("ref") for item in view.get("executable_behaviors", [])],
                    "context_validation_errors": context_errors,
                    "prompt": prompt,
                    "response": turn.response,
                    "result": turn.result,
                    "transcript": turn.transcript,
                    "quality": quality,
                })
        finally:
            platform.close()
    output_dir = Path(__file__).parents[1] / "artifacts"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "agent-session-review.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"review_file": str(output_path), "model": model, "sessions": [{"actor_id": item["actor_id"], "candidates": len(item["candidate_refs"]), "result": item["result"].get("status"), "grade": item["quality"].get("grade"), "quality": item["quality"].get("passed")} for item in report["sessions"]]}, ensure_ascii=False, indent=2))
    return 0 if all(item["quality"].get("passed") for item in report["sessions"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
