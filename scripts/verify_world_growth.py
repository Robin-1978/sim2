"""Verify real world growth without the tidebound world or a long simulation.

Each cycle creates a new place, an empty local stock and one non-research
Action that changes that stock.  A real provider must pass every step, and
Mira must travel to the new place and execute the new behavior.  The harness
never creates the place, stock, action, behavior or resource delta itself.

    python scripts/verify_world_growth.py --provider remote --cycles 2

The probe world is a temporary declarative package.  It intentionally has no
research source, so the model cannot satisfy the contract with another archive.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from sim2.agents.engine import AgentEngine
from sim2.bootstrap.service import create_instance_from_template
from sim2.llm.config import LLMConfig
from sim2.llm.gateway import LLMRouter
from sim2.platform.runtime import Platform

PROBE_SCRIPT = Path(__file__).with_name("audit_dynamic_evolution_loop.py")


def _probe_module():
    spec = importlib.util.spec_from_file_location("sim2_evolution_probe", PROBE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _template() -> dict[str, Any]:
    template = _probe_module()._probe_template(fallback=True)
    template["actions"] = [item for item in template["actions"] if item["id"] != "research_source"]
    template["behavior_trees"] = [item for item in template["behavior_trees"] if item["id"] != "study_source"]
    for obj in template["objects"]:
        for key in ("research_required", "research_discoveries", "research_focus", "research_assignees"):
            obj.pop(key, None)
    template["capabilities"] = [item for item in template["capabilities"] if item["id"] != "research"]
    mira = template["agents"][0]
    mira["capabilities"].pop("research", None)
    mira["initial_bt_ids"] = ["inspect_archive"]
    mira.pop("research_targets", None)
    template["rules"]["fallback_behavior_ids"] = ["inspect_archive"]
    template["rules"].pop("capability_progression", None)
    template["rules"]["world_rules"] = []
    template["resources"][0]["initial_state"]["quantity"] = 6
    template["evolution"]["stagnation"] = {"window": "1h", "max_interval": "2h"}
    template["evolution"]["wakes"] = [
        {
            "id": "growth_place",
            "on": "growth.place.requested",
            "for": "god",
            "external": True,
            "tools": ["query_history", "propose_entity", "wait"],
            "purpose": "Build one reachable place for the relay.",
            "result": (
                "Create exactly one new location connected to Relay Lab with a positive route "
                "distance. Give it a clear non-research purpose. Do not create an Action, "
                "behavior, resource, object, faction or research source in this turn. If no "
                "grounded place is possible, wait."
            ),
        },
        {
            "id": "growth_resource",
            "on": "growth.resource.requested",
            "for": "god",
            "external": True,
            "tools": ["query_history", "propose_entity", "wait"],
            "purpose": "Give the new place an empty local stock that only real work can fill.",
            "result": (
                "Create exactly one empty resource entity at the location named in the "
                "triggering evidence. Use propose_entity with entity_type 'resource', a short "
                "unique stock name, location set to that exact location, and state "
                "{quantity: 0}. Do not create an Action, behavior, location, object or research "
                "source in this turn. If no grounded stock is possible, wait."
            ),
        },
        {
            "id": "growth_verb",
            "on": "growth.verb.requested",
            "for": "god",
            "external": True,
            "tools": ["query_history", "propose_definition", "wait"],
            "purpose": "Add the activity that actually changes the new local stock.",
            "result": (
                "Add exactly one Action and its bound behavior tree at the location named in the "
                "triggering evidence. The Action must increase the named resource by 1 with "
                "effects=[{type:'world_resource_delta', resource_id:'<exact resource name from the evidence>', "
                "delta:1, maximum:50}]. Include a Travel node to that exact location, set assign_to "
                "['Mira'], and use a new success result plus one outcome_events entry with a new "
                "event_type and wake ['story']. Do not create another location or resource and do "
                "not create a research source. If no grounded action is possible, wait."
            ),
        },
        {
            "id": "story_after_growth",
            "on": "definition.registered",
            "for": "story",
            "every": "1h",
            "tools": ["query_history", "propose_story_beat", "wait"],
            "purpose": "Turn the new local production capability into a grounded chapter.",
        },
    ]
    return template


def _call_names(turn: Any) -> list[str]:
    names = []
    for item in (turn.response or {}).get("tool_calls") or []:
        function = item.get("function") if isinstance(item, dict) else {}
        if isinstance(function, dict):
            names.append(str(function.get("name")))
    return names


def _behavior_choice(turn: Any) -> str:
    for item in (turn.response or {}).get("tool_calls") or []:
        function = item.get("function") if isinstance(item, dict) else {}
        if not isinstance(function, dict) or function.get("name") != "request_behavior":
            continue
        args = function.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        return str((args or {}).get("choice") or "")
    return ""


def _run_ready(platform: Platform, role: str, provider: Any, *, actor: str = "god", iterations: int = 10):
    tasks = [item for item in platform.ready_evolution_tasks() if item.get("role") == role]
    if not tasks:
        return None
    task = tasks[-1]
    turn = AgentEngine(platform, provider, max_iterations=iterations).run(
        role=role,
        actor_id=str(task.get("actor_id") or actor),
        task_type=str(task["task_type"]),
        source_events=task.get("source_events") or [],
    )
    if turn.result.get("status") in {"accepted", "wait"}:
        platform.complete_evolution_task(str(task["task_id"]))
    return turn


def _locations(platform: Platform) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in platform.store.connection.execute("SELECT id,name,data_json FROM entity WHERE entity_type='location'").fetchall()
    ]


def _resources(platform: Platform) -> list[dict[str, Any]]:
    rows = []
    for row in platform.store.connection.execute("SELECT id,name,data_json FROM entity WHERE entity_type IN ('resource','material')").fetchall():
        try:
            state = json.loads(row["data_json"] or "{}")
        except json.JSONDecodeError:
            state = {}
        rows.append({"id": row["id"], "name": row["name"], "state": state, "initial_state": state.get("initial_state", {})})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="remote")
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--max-completion-tokens", type=int, default=None)
    args = parser.parse_args()

    router = LLMRouter(LLMConfig.load())
    providers = {
        role: router.new_provider_for_name(
            args.provider,
            max_completion_tokens=args.max_completion_tokens,
            role=role,
        )
        for role in ("god", "agent_decision", "story")
    }
    temporary = tempfile.TemporaryDirectory(prefix="sim2-world-growth-") if not args.runtime_dir else None
    root = Path(args.runtime_dir) if args.runtime_dir else Path(temporary.name)
    root.mkdir(parents=True, exist_ok=True)
    db = create_instance_from_template(_template(), f"growth-{uuid4().hex[:8]}", root)
    platform = Platform(db)
    cycles: list[dict[str, Any]] = []
    try:
        before_locations = {item["id"] for item in _locations(platform)}
        for cycle in range(1, max(1, args.cycles) + 1):
            minute = int(platform.store.repositories.metadata.get("world_minute", 0) or 0)
            with platform.store.transaction():
                platform._services["events"].publish(
                    "growth.place.requested",
                    "god",
                    minute + 1,
                    {"summary": "The relay needs one new reachable place for the next cycle of work."},
                    wake="configured",
                )
            place_turn = _run_ready(platform, "god", providers["god"])
            new_locations = [item for item in _locations(platform) if item["id"] not in before_locations]
            if not new_locations:
                cycles.append({"cycle": cycle, "status": "no_place", "calls": _call_names(place_turn) if place_turn else []})
                break
            location = new_locations[-1]
            before_locations.add(location["id"])

            minute = int(platform.store.repositories.metadata.get("world_minute", 0) or 0)
            with platform.store.transaction():
                platform._services["events"].publish(
                    "growth.resource.requested",
                    "god",
                    minute + 1,
                    {"summary": f"Location {location['name']} now exists. Create one empty resource stock there; only real work may fill it."},
                    wake="configured",
                )
            resource_turn = _run_ready(platform, "god", providers["god"])
            candidates = [
                item for item in _resources(platform)
                if str(item["initial_state"].get("location_id") or "") == str(location["id"])
            ]
            if not candidates:
                cycles.append({"cycle": cycle, "location": location["name"], "status": "no_resource", "calls": _call_names(resource_turn) if resource_turn else []})
                break
            resource = candidates[-1]
            before_quantity = float(resource["initial_state"].get("quantity", 0) or 0)

            minute = int(platform.store.repositories.metadata.get("world_minute", 0) or 0)
            with platform.store.transaction():
                platform._services["events"].publish(
                    "growth.verb.requested",
                    "god",
                    minute + 1,
                    {"summary": f"Location {location['name']} has empty stock {resource['name']}. Add one non-research action there that increases {resource['name']} by 1."},
                    wake="configured",
                )
            verb_turn = _run_ready(platform, "god", providers["god"])
            behaviors = [
                item.get("agent_label") or item.get("name")
                for item in platform.store.repositories.entities.get_definitions("bt_def")
            ]

            choices: list[str] = []
            outcomes: list[str] = []
            for _ in range(2):
                decisions = [item for item in platform.ready_evolution_tasks() if item.get("role") == "agent_decision"]
                if not decisions:
                    break
                task = decisions[-1]
                turn = AgentEngine(platform, providers["agent_decision"], max_iterations=6).run(
                    role="agent_decision",
                    actor_id=str(task.get("actor_id") or "mira"),
                    task_type=str(task["task_type"]),
                )
                if turn.result.get("status") in {"accepted", "wait"}:
                    platform.complete_evolution_task(str(task["task_id"]))
                choices.append(_behavior_choice(turn))
                if turn.result.get("status") == "accepted":
                    for _ in range(int(turn.result.get("remaining_minutes", 1) or 1) + 5):
                        platform.step()
                    state = platform.store.repositories.entities.get_agent("mira").get("initial_state", {})
                    outcomes.append(str((state.get("last_outcome") or {}).get("result") or ""))

            after = platform.store.repositories.entities.get_entity(str(resource["id"]))
            after_quantity = float((after.get("initial_state") or {}).get("quantity", 0) or 0)
            location_after = platform.store.repositories.entities.get_entity(str(location["id"]))
            row = {
                "cycle": cycle,
                "minute": int(platform.store.repositories.metadata.get("world_minute", 0) or 0),
                "location": location["name"],
                "resource": resource["name"],
                "quantity": [before_quantity, after_quantity],
                "god": {
                    "place": place_turn.result.get("status") if place_turn else None,
                    "resource": resource_turn.result.get("status") if resource_turn else None,
                    "verb": verb_turn.result.get("status") if verb_turn else None,
                },
                "god_calls": {
                    "place": _call_names(place_turn) if place_turn else [],
                    "resource": _call_names(resource_turn) if resource_turn else [],
                    "verb": _call_names(verb_turn) if verb_turn else [],
                },
                "behaviors": behaviors,
                "npc_choices": choices,
                "outcomes": outcomes,
                "reachable": bool(location_after.get("routes")),
                "dead": not any(
                    str(node.get("parameters", {}).get("location_id") or "") in {str(location["id"]), location["name"]}
                    for behavior in platform.store.repositories.entities.get_definitions("bt_def")
                    for node in behavior.get("nodes") or []
                    if isinstance(node, dict)
                ),
            }
            cycles.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)

        passed = bool(cycles) and all(
            row.get("god", {}).get("verb") == "accepted"
            and row.get("quantity", [0, 0])[1] > row.get("quantity", [0, 0])[0]
            and row.get("npc_choices")
            and not row.get("dead")
            for row in cycles
        )
        summary = {"status": "passed" if passed else "failed", "cycles": len(cycles), "results": cycles}
        print(json.dumps({"growth": summary}, ensure_ascii=False), flush=True)
        return 0 if passed else 2
    finally:
        platform.close()
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
