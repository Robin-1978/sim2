"""Run a deterministic long-world smoke test without network LLM calls.

    python scripts/run_long_simulation.py --minutes 10080

This checks Platform scheduling, persistence, outcome review inputs and
diversity/degeneration indicators. It is not a replacement for real-model
quality checks.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform
from sim2.simulation.runner import SimulationRunner


class DeterministicProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, *, system: str, context: str, tools=None, messages=None):
        self.calls += 1
        if "Reflection" in system or "reflection" in context.lower():
            return {"intent_type": "wait", "reason": "No new durable lesson in this smoke-test window."}
        if "God Agent" in system:
            return {
                "intent_type": "propose_world_change",
                "change_type": "event",
                "summary": f"A scheduled world signal at simulation call {self.calls}.",
                "scope": "world",
                "effects": ["record a new public signal"],
            }
        refs = re.findall(r'<behavior ref="([^"]+)">', context)
        if not refs:
            return {"intent_type": "wait", "reason": "No executable behavior."}
        ref = refs[(self.calls - 1) % len(refs)]
        return {"behavior_ref": ref, "reason": "Choose an offered behavior for the current state.", "confidence": 0.7}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=10080)
    args = parser.parse_args()
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    provider = DeterministicProvider()
    with tempfile.TemporaryDirectory(prefix="sim2-long-run-") as runtime:
        platform = Platform(create_instance(config, "long-run", Path(runtime)))
        try:
            report = SimulationRunner(platform, {"agent_decision": provider, "agent_reflection": provider, "god": provider}).run(args.minutes)
            output = {"provider_calls": provider.calls, "report": report.as_dict(), "metrics": platform.metrics()}
            print(json.dumps(output, ensure_ascii=False, indent=2))
        finally:
            platform.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
