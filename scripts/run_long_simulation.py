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
                "change_type": "world_property",
                "summary": "The weekly review updates fog pressure from measured world signals.",
                "scope": "world",
                "property": "fog_level",
                "value": 0.2,
                "expected_effects": ["Fog pressure is recorded at 0.2."],
            }
        behavior_section = context.split("<executable_behaviors>", 1)[-1].split("</executable_behaviors>", 1)[0]
        choices = re.findall(r"^\s+choice: (.+)$", behavior_section, flags=re.MULTILINE)
        if not choices:
            return {"intent_type": "wait", "reason": "No executable behavior."}
        choice = choices[(self.calls - 1) % len(choices)]
        return {"choice": choice, "reason": "Choose an offered behavior for the current state.", "confidence": 0.7}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=10080)
    args = parser.parse_args()
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    provider = DeterministicProvider()
    with tempfile.TemporaryDirectory(prefix="sim2-long-run-") as runtime:
        platform = Platform(create_instance(config, "long-run", Path(runtime)))
        try:
            report = platform.run(args.minutes, providers={"agent_decision": provider, "agent_reflection": provider, "god": provider})
            output = {"provider_calls": provider.calls, "report": report.as_dict(), "metrics": platform.metrics()}
            print(json.dumps(output, ensure_ascii=False, indent=2))
        finally:
            platform.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
