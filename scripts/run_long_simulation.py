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
            # The performance baseline must not manufacture a direct God
            # property mutation. A real God session may submit a typed,
            # evidence-backed proposal; this deterministic provider simply
            # declines when it has no semantic reviewer.
            return {"intent_type": "wait", "reason": "No evidence-backed world proposal in the baseline provider."}
        if "Story Agent" in system or "<story_state>" in context:
            choices = re.findall(r"^\s+choice: (.+)$", context, flags=re.MULTILINE)
            if choices:
                return {"intent_type": "propose_story_beat", "beat": choices[0], "reason": "Activate the configured beat after the validated discovery."}
            return {"intent_type": "wait", "reason": "No configured story continuation is available."}
        behavior_section = context.split("<executable_behaviors>", 1)[-1].split("</executable_behaviors>", 1)[0]
        blocks = re.findall(r"<behavior>(.*?)</behavior>", behavior_section, flags=re.DOTALL)
        # This provider has no semantic model and therefore cannot invent a
        # message, topic or explicit participant. Select only choices whose
        # inputs Platform can resolve from the validated view. Predictable
        # schema rejections are not useful long-run behavior evidence.
        choices = []
        for block in blocks:
            if re.search(r"^\s+parameters: .*\brequired\b", block, flags=re.MULTILINE):
                continue
            if "Choose one participant explicitly" in block:
                continue
            match = re.search(r"^\s+choice: (.+)$", block, flags=re.MULTILINE)
            if match:
                choices.append(match.group(1))
        if not choices:
            return {"intent_type": "wait", "reason": "No executable behavior without user-facing parameters."}
        choice = choices[(self.calls - 1) % len(choices)]
        return {"choice": choice, "reason": "Choose an offered behavior for the current state.", "confidence": 0.7}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, default=10080)
    parser.add_argument("--config", type=Path, help="world package to smoke-test; defaults to Starter Village")
    parser.add_argument("--instance-id", default="long-run")
    args = parser.parse_args()
    config = args.config or (Path(__file__).parents[1] / "worlds" / "starter_village")
    provider = DeterministicProvider()
    with tempfile.TemporaryDirectory(prefix="sim2-long-run-") as runtime:
        platform = Platform(create_instance(config, args.instance_id, Path(runtime)))
        try:
            report = platform.run(args.minutes, providers={"agent_decision": provider, "agent_reflection": provider, "god": provider, "story": provider})
            agents = []
            for agent in platform.store.repositories.entities.list_agents():
                state = dict(agent.get("initial_state", {}) or {})
                agents.append({
                    "name": agent.get("name", agent.get("id")),
                    "status": state.get("status"),
                    "location": state.get("location_id"),
                    "attributes": {
                        item["id"]: item["value"]
                        for item in platform.attribute_schema.values(state, agent.get("needs", {}) or {})
                    },
                    "inventory": agent.get("inventory", {}),
                })
            resources = []
            rules = platform.store.get_meta("rules", {}) or {}
            for entity_type in rules.get("resource_entity_types", ["resource", "material"]):
                for row in platform.store.repositories.entities.entities_of_type(str(entity_type)):
                    item = platform.store.repositories.entities.get_entity(str(row["id"]))
                    resources.append({
                        "name": item.get("name", item.get("id")),
                        "type": item.get("entity_type"),
                        "amount": item.get("amount", (item.get("initial_state") or {}).get("quantity", (item.get("initial_state") or {}).get("amount"))),
                        "state": item.get("initial_state", {}),
                    })
            output = {
                "provider_calls": provider.calls,
                "report": report.as_dict(),
                "metrics": platform.metrics(),
                "world_properties": platform.store.get_meta("world_properties", {}),
                "agents": agents,
                "resources": resources,
                "evolution_tasks": {
                    row["status"]: int(row["n"])
                    for row in platform.store.connection.execute("SELECT status,COUNT(*) AS n FROM evolution_task GROUP BY status")
                },
            }
            print(json.dumps(output, ensure_ascii=False, indent=2))
        finally:
            platform.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
