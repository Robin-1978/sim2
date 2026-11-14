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
        # The smoke provider is deliberately deterministic, but it still
        # needs to respect the configured motivation signal.  Cycling through
        # every offered BT made a merchant buy food at high satiety, exhaust
        # its coins, and then starve even though the market still had stock.
        # Read the rendered drive urgencies and each configured BT's ordered
        # drive tags; no world attribute or behavior name is special-cased.
        drive_block = context.split("<drives>", 1)[-1].split("</drives>", 1)[0]
        drive_urgencies = {
            str(name).strip(): float(value)
            for name, value in re.findall(r"^\s*-\s*([^:]+): urgency=([0-9.]+)", drive_block, flags=re.MULTILINE)
        }
        scored: list[tuple[float, str]] = []
        allowed_choices = set(choices)
        for block in blocks:
            match = re.search(r"^\s+choice: (.+)$", block, flags=re.MULTILINE)
            if not match:
                continue
            if match.group(1) not in allowed_choices:
                # The first pass intentionally removed behaviors that need
                # user-facing parameters. Keep the same executable surface
                # when scoring drive urgency; otherwise the smoke provider
                # can reintroduce a parameterized behavior and get rejected.
                continue
            drive_match = re.search(r"^\s+drive_tags: (.+)$", block, flags=re.MULTILINE)
            drive_tags = []
            if drive_match:
                drive_tags = [item.strip() for item in re.split(r"[,;]", drive_match.group(1)) if item.strip()]
            # The first matching tag is the behavior's primary drive.  The
            # order is declarative content, so a food behavior can prioritize
            # the configured satiety need over a general safety tag.
            score = next((drive_urgencies[tag] for tag in drive_tags if tag in drive_urgencies), 0.0)
            scored.append((score, match.group(1)))
        urgent = [choice for score, choice in scored if score >= 0.05]
        if not drive_urgencies:
            # Small unit fixtures and custom worlds may omit the optional
            # drive projection. Preserve the old deterministic fallback for
            # that contract; when drives are present, the urgency policy
            # below remains authoritative.
            choice = choices[(self.calls - 1) % len(choices)]
        elif urgent:
            choice = urgent[(self.calls - 1) % len(urgent)]
        else:
            # When no configured drive is urgent, do not spend a scarce
            # resource merely because the corresponding behavior is visible.
            # If every option is non-urgent, waiting is the deterministic
            # provider's truthful result.
            return {"intent_type": "wait", "reason": "No configured drive is urgent enough to justify a state-changing behavior."}
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
