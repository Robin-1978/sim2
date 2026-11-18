"""Verify the production growth chain: stagnation -> place -> typed follow-ups.

Unlike ``verify_world_growth.py`` (which drives the three steps with explicit
synthetic events), this check uses only the events Platform actually emits:

* ``world.stagnation`` asks God for a new place;
* ``world.location.created`` wakes two follow-ups: add an activity and create
  an empty local stock;
* ``world.resource.created`` wakes the action that changes that stock;
* Mira then travels and executes the generated work.

    python scripts/verify_world_growth_chain.py --provider remote --cycles 1
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
    spec = importlib.util.spec_from_file_location("sim2_evolution_chain_probe", PROBE_SCRIPT)
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
            "id": "place_review",
            "on": "world.stagnation",
            "for": "god",
            "tools": ["query_history", "propose_entity", "wait"],
            "purpose": "The relay has no new place. Build one reachable place.",
            "result": (
                "Create exactly one new location connected to Relay Lab with a positive route "
                "distance. Give it a clear non-research purpose. Do not create an Action, "
                "behavior, resource, object, faction or research source in this turn. If no "
                "grounded place is possible, wait."
            ),
        },
        {
            "id": "after_place_verb",
            "on": "world.location.created",
            "for": "god",
            "external": True,
            "tools": ["query_history", "propose_definition", "wait"],
            "purpose": "A new place exists; give it one executable non-research activity.",
            "result": (
                "Read the new location name from the triggering evidence. Add exactly one "
                "non-research Action and its bound behavior tree at that location, executable by "
                "Mira now. Include a Travel node to that exact location and set assign_to ['Mira']. "
                "Use ONLY these exact effect forms and configured names: "
                "effects=[{type:'world_property_delta', property:'relay_stability', delta:0.03, maximum:1.0}] "
                "or effects=[{type:'need_delta', need:'focus', delta:-1.0}]; capability xp may use "
                "{type:'capability.add_xp', capability:'stewardship', amount:1}. Never invent a need, "
                "capability, resource or world property. The Action must have a new success result "
                "and one outcome_events entry with a new event_type and wake ['story']. Do not "
                "create another location or a research source. If no grounded action is possible, wait."
            ),
        },
        {
            "id": "after_place_resource",
            "on": "world.location.created",
            "for": "god",
            "external": True,
            "tools": ["query_history", "propose_entity", "wait"],
            "purpose": "A new place exists; give it an empty local stock that real work can fill.",
            "result": (
                "Read the new location name from the triggering evidence. Create exactly one empty "
                "resource entity at that location. Use propose_entity with entity_type 'resource', "
                "a short unique stock name, location set to that exact location, and state "
                "{quantity: 0}. Do not create an Action, location, object or research source in this "
                "turn. If no grounded stock is possible, wait."
            ),
        },
        {
            "id": "after_resource_action",
            "on": "world.resource.created",
            "for": "god",
            "external": True,
            "tools": ["query_history", "propose_definition", "wait"],
            "purpose": "A new stock exists; add the activity that actually changes it.",
            "result": (
                "Read the new resource and location names from the triggering evidence. Add exactly "
                "one Action and its bound behavior tree at that location that increases the resource "
                "by 1 with effects=[{type:'world_resource_delta', resource_id:'<exact resource name "
                "from the evidence>', delta:1, maximum:50}]. Include a Travel node to that exact "
                "location, set assign_to ['Mira'], and use a new success result plus one "
                "outcome_events entry with a new event_type and wake ['story']. Do not create another "
                "location, resource or research source. If no grounded action is possible, wait."
            ),
        },
        {
            "id": "story_after_growth",
            "on": "definition.registered",
            "for": "story",
            "every": "1h",
            "tools": ["query_history", "propose_story_beat", "wait"],
            "purpose": "Turn a newly registered capability into a grounded chapter.",
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


def _locations(platform: Platform) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in platform.store.connection.execute("SELECT id,name,data_json FROM entity WHERE entity_type='location'").fetchall()
    ]


def _resources(platform: Platform) -> list[dict[str, Any]]:
    rows = []
    for row in platform.store.connection.execute("SELECT id,name,data_json FROM entity WHERE entity_type IN ('resource','material')").fetchall():
        try:
            data = json.loads(row["data_json"] or "{}")
        except json.JSONDecodeError:
            data = {}
        rows.append({"id": row["id"], "name": row["name"], "state": data.get("initial_state", {}), "routes": data.get("routes")})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="remote")
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--max-completion-tokens", type=int, default=None)
    args = parser.parse_args()

    router = LLMRouter(LLMConfig.load())
    providers = {
        role: router.new_provider_for_name(args.provider, max_completion_tokens=args.max_completion_tokens, role=role)
        for role in ("god", "agent_decision", "story")
    }
    temporary = tempfile.TemporaryDirectory(prefix="sim2-world-growth-chain-") if not args.runtime_dir else None
    root = Path(args.runtime_dir) if args.runtime_dir else Path(temporary.name)
    root.mkdir(parents=True, exist_ok=True)
    db = create_instance_from_template(_template(), f"growth-chain-{uuid4().hex[:8]}", root)
    platform = Platform(db)
    rows: list[dict[str, Any]] = []
    try:
        before_locations = {item["id"] for item in _locations(platform)}
        for cycle in range(1, max(1, args.cycles) + 1):
            # Let the derived stagnation signal ask for a new place.
            for _ in range(240):
                if any(item.get("role") == "god" for item in platform.ready_evolution_tasks()):
                    break
                platform.step()
            god_turns: list[dict[str, Any]] = []
            seen: set[str] = set()
            # Run the whole follow-up chain: place, activity, stock, producer.
            for _ in range(8):
                tasks = [item for item in platform.ready_evolution_tasks() if item.get("role") == "god"]
                if not tasks:
                    break
                task = tasks[-1]
                if str(task["task_id"]) in seen:
                    break
                seen.add(str(task["task_id"]))
                turn = AgentEngine(platform, providers["god"], max_iterations=10).run(
                    role="god",
                    actor_id="god",
                    task_type=str(task["task_type"]),
                    source_events=task.get("source_events") or [],
                )
                if turn.result.get("status") in {"accepted", "wait"}:
                    platform.complete_evolution_task(str(task["task_id"]))
                god_turns.append({
                    "task": task["task_type"],
                    "status": turn.result.get("status"),
                    "check": turn.result.get("failed_check"),
                    "calls": _call_names(turn),
                })
            new_locations = [item for item in _locations(platform) if item["id"] not in before_locations]
            if not new_locations:
                rows.append({"cycle": cycle, "status": "no_place", "god": god_turns})
                break
            location = new_locations[-1]
            before_locations.add(location["id"])
            resource_candidates = [
                item for item in _resources(platform)
                if str(item["state"].get("location_id") or "") == str(location["id"])
            ]
            choices: list[str] = []
            outcomes: list[str] = []
            before_quantity = 0.0
            resource_name = ""
            if resource_candidates:
                resource = resource_candidates[-1]
                resource_name = resource["name"]
                before_quantity = float(resource["state"].get("quantity", 0) or 0)
            for _ in range(4):
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
                    for _ in range(int(turn.result.get("remaining_minutes", 1) or 1) + 6):
                        platform.step()
                    state = platform.store.repositories.entities.get_agent("mira").get("initial_state", {})
                    outcomes.append(str((state.get("last_outcome") or {}).get("result") or ""))
            after_quantity = before_quantity
            if resource_candidates:
                after = platform.store.repositories.entities.get_entity(str(resource_candidates[-1]["id"]))
                after_quantity = float((after.get("initial_state") or {}).get("quantity", 0) or 0)
            behaviors = [
                item.get("agent_label") or item.get("name")
                for item in platform.store.repositories.entities.get_definitions("bt_def")
            ]
            location_after = platform.store.repositories.entities.get_entity(str(location["id"]))
            targeted = any(
                str(node.get("parameters", {}).get("location_id") or "") in {str(location["id"]), location["name"]}
                for behavior in platform.store.repositories.entities.get_definitions("bt_def")
                for node in behavior.get("nodes") or []
                if isinstance(node, dict)
            )
            row = {
                "cycle": cycle,
                "minute": int(platform.store.repositories.metadata.get("world_minute", 0) or 0),
                "location": location["name"],
                "resource": resource_name,
                "quantity": [before_quantity, after_quantity],
                "god": god_turns,
                "god_accepted": sum(1 for item in god_turns if item["status"] == "accepted"),
                "god_rejected": [item for item in god_turns if item["status"] == "rejected"],
                "behaviors": behaviors,
                "npc_choices": choices,
                "outcomes": outcomes,
                "reachable": bool(location_after.get("routes")),
                "targeted": targeted,
            }
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)

        passed = bool(rows) and all(
            row.get("god_accepted", 0) >= 3
            and not row.get("god_rejected")
            and row.get("resource")
            and row.get("quantity", [0, 0])[1] > row.get("quantity", [0, 0])[0]
            and row.get("npc_choices")
            and row.get("reachable")
            and row.get("targeted")
            for row in rows
        )
        print(json.dumps({"chain": {"status": "passed" if passed else "failed", "rows": rows}}, ensure_ascii=False), flush=True)
        return 0 if passed else 2
    finally:
        platform.close()
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
