"""Verify the compact Agent ↔ Platform interaction loop with a real provider.

    python scripts/verify_core_interaction.py --provider remote

The report contains model outputs and semantic checks, but never API keys or
hidden reasoning. The scenario is intentionally short and reviewable:
decision → Platform outcome → reflection memory → next decision → God effect.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from sim2.agents.engine import AgentEngine
from sim2.agents.quality import evaluate_turn
from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.llm.config import LLMConfig
from sim2.llm.gateway import LLMRouter
from sim2.platform.runtime import Platform


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="remote", help="Configured provider name; remote is the default real-model path.")
    parser.add_argument("--model", default=None, help="Override the configured model name for this verification run.")
    args = parser.parse_args()
    config = LLMConfig.load()
    router = LLMRouter(config)
    world_config = Path(__file__).parents[1] / "worlds" / "starter_village"
    with tempfile.TemporaryDirectory(prefix="sim2-core-interaction-") as runtime:
        platform = Platform(create_instance(world_config, "core-interaction", Path(runtime)))
        try:
            decision_provider = router.new_provider_for_name(
                args.provider, model=args.model, role="agent_decision"
            )
            decision_view = platform.decision_view("liang_he", role="agent_decision")
            decision = AgentEngine(platform, decision_provider).run(role="agent_decision", actor_id="liang_he", task_type="core_decision")
            decision_quality = evaluate_turn(decision, decision_view)

            platform.submit_behavior(BehaviorIntent("lin_bo", "behavior:rest_and_recover", {}, {}, "Recover before duty.", 0.8, "core-outcome"))
            for _ in range(49):
                platform.step()
            reflection_view = platform.decision_view("lin_bo", role="agent_reflection")
            reflection_provider = router.new_provider_for_name(
                args.provider, model=args.model, role="agent_reflection"
            )
            reflection = AgentEngine(platform, reflection_provider).run(role="agent_reflection", actor_id="lin_bo", task_type="core_reflection")
            reflection_quality = evaluate_turn(reflection, reflection_view)
            memory_count = len(platform.store.list_memories("lin_bo", limit=20))

            next_view = platform.decision_view("lin_bo", role="agent_decision")
            next_decision_provider = router.new_provider_for_name(
                args.provider, model=args.model, role="agent_decision"
            )
            next_decision = AgentEngine(platform, next_decision_provider).run(role="agent_decision", actor_id="lin_bo", task_type="core_next_decision")
            next_quality = evaluate_turn(next_decision, next_view)

            platform.submit_world_change("operator", {"change_type": "world_property", "summary": "Lower fog after a clear reading.", "property": "fog_level", "value": 0.2})
            god_view = platform.decision_view("god", role="god")
            god_provider = router.new_provider_for_name(
                args.provider, model=args.model, role="god"
            )
            god = AgentEngine(platform, god_provider).run(role="god", actor_id="god", task_type="core_god")
            god_quality = evaluate_turn(god, god_view)

            # Resolve the configured semantic backend even when Reflection
            # correctly chose wait and produced no memory candidate.
            platform.view.memory.similarity._load_model()
            report = {
                "provider": args.provider,
                "decision": {"response": decision.response, "result": decision.result, "quality": decision_quality},
                "reflection": {"outcomes": reflection_view.get("recent_outcomes"), "response": reflection.response, "result": reflection.result, "memory_count": memory_count, "quality": reflection_quality},
                "next_decision": {"memory_count": len(next_view.get("memories", [])), "memory_backend": platform.view.memory.similarity._method, "response": next_decision.response, "result": next_decision.result, "quality": next_quality},
                "god": {"world_changes": god_view.get("recent_world_changes"), "response": god.response, "result": god.result, "quality": god_quality},
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))
            reflection_declined = reflection.result.get("status") == "wait"
            memory_reused = bool(next_view.get("memories")) if memory_count else True
            passed = all(item["quality"].get("passed") for item in (report["decision"], report["reflection"], report["next_decision"], report["god"])) and (memory_count > 0 or reflection_declined) and memory_reused
            return 0 if passed else 1
        finally:
            platform.close()


if __name__ == "__main__":
    raise SystemExit(main())
