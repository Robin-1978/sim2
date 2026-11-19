"""Check that a world keeps evolving for seven world days, without model calls.

Scripted agents stand in for the roles: the character takes the first behavior
it is offered, God proposes a new research source unless the scenario tells it
to decline.  Every scenario runs through the ordinary Platform and
SimulationRunner path, so wake groups, the stagnation signal, cadence and the
Platform's own proposal checks are the real ones.

    python scripts/verify_week_liveness.py [--config worlds/aster_relay] [--minutes 10080]

Scenarios; the exit code is non-zero when any verdict fails:

* God accepts every ask: every day has a progress event.
* God declines the first two asks: the stagnation signal asks again, God's
  source is accepted, and the last day has a progress event.
* God declines every ask: the engine keeps asking, at intervals that never
  shrink and never exceed the configured ``max_interval``.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import re
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform
from sim2.platform.stagnation import DEFAULT_PROGRESS_EVENTS

DAY = 1440
SCENARIOS = (("accepts", "God accepts every ask", 0), ("declines_two", "God declines the first two asks", 2), ("declines_all", "God declines every ask", math.inf))
WORDS = ["amber", "basalt", "cinder", "dune", "ember", "fjord", "garnet", "harbor", "indigo", "juniper", "kelp", "lichen",
         "marble", "nectar", "obsidian", "pewter", "quartz", "russet", "saffron", "tundra"]


class ScriptedAgents:
    """Provider double for every role; deterministic and free of model calls."""

    def __init__(self, declines: float, assignee: str, location: str) -> None:
        self.declines, self.assignee, self.location = declines, assignee, location
        self.god_calls = 0
        self.created = 0
        self.roles: Counter[str] = Counter()

    def complete_json(self, *, system, context, tools=None, messages=None):
        if "<executable_behaviors>" in context:
            self.roles["character"] += 1
            return self._character(context)
        if "world-level evolution Agent" in system:
            self.roles["god"] += 1
            return self._god(tools)
        self.roles["other"] += 1
        return {"intent_type": "wait", "reason": "Scripted: nothing to add."}

    @staticmethod
    def _character(context: str) -> dict[str, Any]:
        section = context.split("<executable_behaviors>", 1)[-1].split("</executable_behaviors>", 1)[0]
        for block in re.findall(r"<behavior>(.*?)</behavior>", section, flags=re.DOTALL):
            choice = re.search(r"^\s+choice: (.+)$", block, flags=re.MULTILINE)
            if not choice:
                continue
            options_line = re.search(r"^\s+parameter_options: (.+)$", block, flags=re.MULTILINE)
            options: dict[str, list[Any]] = {}
            if options_line:
                text = options_line.group(1).strip()
                if text.startswith("{"):
                    options = ast.literal_eval(text)
                else:
                    for part in text.split("; "):
                        name, _, values = part.partition(": ")
                        if name and values:
                            options[name] = [{"name": value.strip()} for value in values.split(",") if value.strip()]
            parameters: dict[str, Any] = {}
            for name in re.findall(r"\b(\w+) \(required", block):
                first = (options.get(name) or [None])[0]
                value = first.get("name") if isinstance(first, dict) else first
                if not value:
                    break
                parameters[name] = value
            else:
                response: dict[str, Any] = {"choice": choice.group(1), "reason": "Scripted: first offered behavior.", "confidence": 0.7}
                if parameters:
                    response["parameters"] = parameters
                return response
        return {"intent_type": "wait", "reason": "Scripted: nothing executable."}

    def _god(self, tools: Any) -> dict[str, Any]:
        offered = {(item.get("function") or item).get("name") for item in tools or []}
        self.god_calls += 1
        if self.god_calls <= self.declines or (offered and "propose_entity" not in offered):
            return {"intent_type": "wait", "reason": "Scripted: decline."}
        number = self.created
        self.created += 1
        # The Platform rejects a source whose words overlap an existing one, so each source uses words of its own.
        w = [f"{WORDS[(number * 6 + i) % len(WORDS)]}{number}x{i}" for i in range(6)]
        return {"name": "propose_entity", "arguments": {
            "entity_type": "object", "name": f"{w[0].title()} Ledger", "location": self.location,
            "description": f"{w[1]} {w[3]} {w[5]}",
            "state": {
                "research_question": f"{w[0]} {w[1]} {w[2]}", "novelty_key": f"{w[0]}{w[1]}",
                # Two discoveries of one study each: two research outcomes, the count of the research_synthesis wake.
                "research_required": 1,
                "research_discoveries": [
                    {"id": f"fact:{number}:a", "fact": f"The {w[3]} record confirms the {w[4]} observation."},
                    {"id": f"fact:{number}:b", "fact": f"The {w[5]} record clarifies the {w[2]} observation."},
                ],
                "research_assignees": [self.assignee], "status": "unresolved",
            },
        }}


def run_week(config: Path, declines: float, minutes: int) -> dict[str, Any]:
    """Run one scenario and return its per-day tally and the minutes of the key events.

    ``asked`` counts God turns (the first model call of each), so the recurring
    cadence task is included; a completed-task count would miss it, because the
    Platform reschedules that task instead of completing it.
    """

    with tempfile.TemporaryDirectory(prefix="sim2-week-") as runtime:
        platform = Platform(create_instance(config, "week-liveness", Path(runtime)))
        try:
            entities = platform.store.repositories.entities
            agent = entities.list_agents()[0]
            location = entities.get_entity(str(agent["initial_state"]["location_id"]))["name"]
            provider = ScriptedAgents(declines, str(agent["name"]), str(location))
            report = platform.run(minutes, providers={role: provider for role in ("agent_decision", "agent_reflection", "god", "story")})
            root = platform.store.repositories.metadata.get("evolution", {}) or {}
            stagnation = (root.get("evolution", root) or {}).get("stagnation") or {}
            progress_types = list(stagnation.get("progress_event_types") or DEFAULT_PROGRESS_EVENTS)
            marks = ",".join("?" for _ in progress_types)
            con = platform.store.connection

            def count(sql: str, *args: Any) -> int:
                return int(con.execute(sql, args).fetchone()[0])

            days = []
            for day in range(math.ceil(minutes / DAY)):
                lo, hi = day * DAY, (day + 1) * DAY
                days.append({
                    "progress": count(f"SELECT COUNT(*) FROM event_log WHERE world_minute>=? AND world_minute<? AND event_type IN ({marks})", lo, hi, *progress_types),
                    "stagnation": count("SELECT COUNT(*) FROM event_log WHERE world_minute>=? AND world_minute<? AND event_type='world.stagnation'", lo, hi),
                    "asked": count("SELECT COUNT(*) FROM llm_call WHERE role='god' AND iteration=1 AND world_minute>=? AND world_minute<?", lo, hi),
                    "added": count("SELECT COUNT(*) FROM intent WHERE intent_type='propose_entity' AND status='accepted' AND created_minute>=? AND created_minute<?", lo, hi),
                    "rejected": count("SELECT COUNT(*) FROM intent WHERE intent_type='propose_entity' AND status='rejected' AND created_minute>=? AND created_minute<?", lo, hi),
                })
            summary = report.as_dict()
            return {
                "days": days,
                "stagnation": [int(r[0]) for r in con.execute("SELECT world_minute FROM event_log WHERE event_type='world.stagnation' ORDER BY event_id")],
                "accepted": [int(r[0]) for r in con.execute("SELECT created_minute FROM intent WHERE intent_type='propose_entity' AND status='accepted' ORDER BY created_minute")],
                "progress": [int(r[0]) for r in con.execute(f"SELECT world_minute FROM event_log WHERE event_type IN ({marks}) ORDER BY event_id", progress_types)],
                "max_interval": int(stagnation.get("max_interval_minutes", 0) or 0),
                "rejections": dict(Counter(
                    str(json.loads(row[0] or "{}").get("failed_check") or "unknown")
                    for row in con.execute("SELECT result_json FROM intent WHERE intent_type='propose_entity' AND status='rejected'")
                )),
                "roles": dict(provider.roles),
                "flags": summary.get("degeneration_flags"),
                "errors": {key: summary.get(key) for key in ("llm_errors", "rejected_turns")},
            }
        finally:
            platform.close()


def problems(kind: str, week: dict[str, Any]) -> list[str]:
    """Return what is wrong with one scenario's week; an empty list is a pass."""

    days, asks = week["days"], week["stagnation"]
    found: list[str] = []
    if kind == "accepts":
        found += [f"day {number + 1} has no progress event" for number, row in enumerate(days) if not row["progress"]]
    elif kind == "declines_two":
        if not asks:
            found.append("no world.stagnation event")
        elif not any(minute >= asks[0] for minute in week["accepted"]):
            found.append("God's source was not accepted after the first stagnation event")
        if not days[-1]["progress"]:
            found.append("the last day has no progress event")
    else:
        gaps = [later - earlier for earlier, later in zip(asks, asks[1:])]
        if len(asks) < 2:
            found.append("fewer than two stagnation events in the week")
        if any(later < earlier for earlier, later in zip(gaps, gaps[1:])):
            found.append("the asks come at shrinking intervals")
        if week["max_interval"] and any(gap > week["max_interval"] for gap in gaps):
            found.append(f"an interval exceeds max_interval {week['max_interval']}")
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(__file__).parents[1] / "worlds" / "aster_relay")
    parser.add_argument("--minutes", type=int, default=7 * DAY)
    args = parser.parse_args()
    failed = False
    for kind, label, declines in SCENARIOS:
        started = time.time()
        week = run_week(args.config, declines, args.minutes)
        print(f"\n== {label} ({time.time() - started:.0f}s) ==")
        print("day  progress  stagnation  god_asked  sources_added  rejected")
        for number, row in enumerate(week["days"], 1):
            print(f"{number:>3}  {row['progress']:>8}  {row['stagnation']:>10}  {row['asked']:>9}  {row['added']:>13}  {row['rejected']:>8}")
        print(f"calls by role {week['roles']}; errors {week['errors']}; flags {week['flags']}")
        print(f"Platform rejected proposals by reason {week['rejections']}")
        found = problems(kind, week)
        failed = failed or bool(found)
        print("PASS" if not found else "FAIL: " + "; ".join(found))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
