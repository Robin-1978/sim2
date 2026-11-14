"""Run a long, world-neutral God → NPC → Story evolution loop.

The probe is a declarative temporary world, not a scenario package.  It has
one research source and one conserved public resource.  The real Agents must
create the next source or continuation from validated outcomes; the harness
never inserts a new story, Action, BT, or world fact after initialization.

    bash -ic 'source ~/.bashrc && python scripts/audit_dynamic_evolution_loop.py \
      --provider remote --rounds 10 --runtime-dir /tmp/sim2-evolution-loop'
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from sim2.agents.engine import AgentEngine, AgentTurn
from sim2.bootstrap.service import create_instance_from_template
from sim2.intents.models import BehaviorIntent
from sim2.llm.config import LLMConfig
from sim2.llm.gateway import LLMRouter
from sim2.platform.runtime import Platform


def _probe_template() -> dict[str, Any]:
    """Return a small declarative world with unrelated, non-village content."""

    return {
        "template_version": "evolution-probe-1",
        "world": {
            "id": "evolution_probe",
            "title": "Aster Relay",
            "premise": "A relay station must learn how to preserve power while its archive reveals new routes.",
            "genre": "science_fiction",
            "properties": {"energy_reserve": 0, "relay_stability": 0.45},
            "random_seed": 6021,
            "calendar": {"minutes_per_day": 1440, "day_phases": [{"id": "shift", "start_minute": 360, "end_minute": 1200}, {"id": "quiet", "start_minute": 1200, "end_minute": 360}]},
            "world_clock": {"start_day": 1, "start_minute": 480, "season": "ion_storm"},
        },
        "attributes": [
            {"id": "integrity", "kind": "state", "label": "Integrity", "unit": "%", "min": 0, "max": 100, "default": 90, "vital": True, "cost_mode": "decrease", "prompt": "Higher means the operator is physically well."},
            {"id": "charge", "kind": "state", "label": "Charge", "unit": "%", "min": 0, "max": 100, "default": 90, "capacity": True, "cost_mode": "decrease", "prompt": "Higher means more short-term operating capacity."},
            {"id": "focus", "kind": "need", "label": "Focus", "unit": "points", "min": 0, "max": 100, "default": 70, "prompt": "Higher means the operator can sustain a meaningful technical task."},
        ],
        "locations": [
            {"id": "relay_lab", "name": "Relay Lab", "public": True, "safe": True, "tags": ["work", "archive"], "routes": ["storage_ring"]},
            {"id": "storage_ring", "name": "Storage Ring", "public": True, "safe": True, "tags": ["resource"], "routes": ["relay_lab"]},
        ],
        "objects": [
            {"id": "signal_archive", "type": "archive", "name": "Signal Archive", "initial_state": {"location_id": "relay_lab", "readable": True, "status": "partially_decoded"}, "research_required": 2, "research_discovery": {"id": "researched:signal_archive:route", "fact": "The archive encodes a route to an unregistered relay, but its second checksum is still unresolved."}},
        ],
        "resources": [
            {"id": "energy_reserve", "type": "resource", "name": "Public Energy Reserve", "location_id": "relay_lab", "initial_state": {"location_id": "relay_lab", "quantity": 0}},
        ],
        "capabilities": [
            {"id": "research", "name": "Archive research", "description": "Turn repeated archive study into verified technical knowledge.", "uses": ["research_source"], "xp_rule": "one_verified_fact"},
            {"id": "stewardship", "name": "Power stewardship", "description": "Inspect and preserve the public energy reserve.", "uses": ["inspect_archive"], "xp_rule": "one_successful_inspection"},
        ],
        "actions": [
            {
                "id": "travel_to", "version": 1, "description": "Move along a configured route.", "parameters": {"location_id": "string"}, "preconditions": ["route.known", "actor.mobile", "destination.reachable"], "requires_destination": True, "completion": {"if_already_at_destination": True}, "duration": {"mode": "route_distance"}, "costs": {"reserve": {}, "per_minute": {"charge": 0.1}, "on_success": {}, "on_failure": {}, "refund": {}}, "success": {"condition": "actor.state.location_id == context.destination_id", "result": "arrived"}, "effects": [{"type": "location_set", "location_id": "context.location_id"}], "failure": {"condition": "actor.incapacitated", "result": "failed"}, "kind": "atomic", "executor": "effect_pipeline",
            },
            {
                "id": "research_source", "version": 1, "description": "Study one configured archive source.", "parameters": {"source_id": "string"}, "preconditions": ["actor.can_research", "research.new_fact_available"], "duration": {"minutes": 60}, "costs": {"reserve": {}, "per_minute": {"charge": 0.15}, "on_success": {}, "on_failure": {}, "refund": {}}, "effects": [{"type": "need_delta", "need": "focus", "delta": 0.5}], "progress": {"key": "research:{source_id}", "scope": "world", "amount": 1, "wake": ["story", "god"]}, "success": {"condition": "research.new_fact_found", "result": "research_completed"}, "failure": {"condition": "source.destroyed", "result": "failed"}, "kind": "atomic", "executor": "effect_pipeline",
            },
            {
                "id": "inspect_archive", "version": 1, "description": "Inspect the public reserve and archive console.", "parameters": {"object_id": "string"}, "preconditions": ["actor.can_inspect"], "duration": {"minutes": 15}, "costs": {"reserve": {}, "per_minute": {"charge": 0.1}, "on_success": {}, "on_failure": {}, "refund": {}}, "effects": [{"type": "world_property_delta", "property": "relay_stability", "delta": 0.02, "maximum": 1.0}], "success": {"condition": "actor.alive", "result": "archive_inspected"}, "failure": {"condition": "actor.incapacitated", "result": "failed"}, "information": {"event_type": "archive.inspected", "summary": "The relay archive and public reserve were inspected.", "source_type": "inspection", "confidence": 0.85, "result": "archive_inspected"}, "kind": "atomic", "executor": "effect_pipeline",
            },
        ],
        "behavior_trees": [
            {"id": "study_source", "agent_label": "Study Configured Source", "summary": "Study Configured Source", "description": "Use the reusable research behavior on one visible source.", "no_op_travel_minutes": 0, "agent_parameters": {"source_id": {"type": "string", "required": True, "max_length": 120, "description": "Exact visible source name."}}, "entry_conditions": ["actor.can_research", "actor.alive"], "estimated_minutes": 61, "nodes": [{"type": "action", "action_id": "travel_to", "parameters": {"location_id": "context.source_location_id"}}, {"type": "action", "action_id": "research_source", "parameters": {"source_id": "context.source_id"}}], "success_results": ["research_completed"], "failure_results": ["failed", "interrupted"]},
            {"id": "inspect_archive", "agent_label": "Inspect Archive", "summary": "Inspect Archive", "entry_conditions": ["actor.can_inspect", "actor.alive"], "estimated_minutes": 16, "nodes": [{"type": "action", "action_id": "travel_to", "parameters": {"location_id": "relay_lab"}}, {"type": "action", "action_id": "inspect_archive", "parameters": {"object_id": "signal_archive"}}], "success_results": ["archive_inspected"], "failure_results": ["failed", "interrupted"]},
        ],
        "agents": [
            {"id": "mira", "name": "Mira", "role": "relay_researcher", "ultimate_goal": "Keep the relay useful by turning verified archive facts into safer public systems.", "traits": {"curious": 0.9, "careful": 0.85}, "beliefs": [], "needs": {"focus": 70}, "capabilities": {"research": 0.95, "stewardship": 0.75}, "initial_bt_ids": ["study_source", "inspect_archive"], "initial_state": {"location_id": "relay_lab", "status": "idle", "integrity": 100, "charge": 90}, "inventory": {"energy_cell": 2}},
        ],
        "rules": {
            "vital_attribute": "integrity", "capacity_attribute": "charge", "research_default_required": 2, "fallback_behavior_ids": ["study_source", "inspect_archive"],
            "needs": [{"id": "focus", "target": 75, "minimum": 0, "gamma": 1.3, "critical_threshold": 0.4, "critical_gain": 1.0}],
            "motivation_model": {"family": "hybrid_need_utility_appraisal", "no_fixed_hierarchy": True, "dimensions": ["safety_and_uncertainty", "competence", "meaning_and_goal_progress"]},
            "minute_effects": [{"id": "charge_recovery", "scope": "agent", "every_minutes": 1, "effect": {"type": "attribute_delta", "attribute": "charge", "delta": 0.05, "maximum": 100}}],
            "resource_entity_types": ["resource", "material"],
            "resource_property_projections": {"energy_reserve": "energy_reserve"},
            "context_values": {},
            "decision_policy": {"wait_backoff_minutes": [5, 10, 20], "empty_catalog_retry_minutes": 15, "wake_events": ["definition.registered", "story.beat.activated"]},
            "capability_progression": {"research": {"minimum_completed_outcomes": 2, "max_delta": 0.08, "max_value": 1.0}, "stewardship": {"minimum_completed_outcomes": 2, "max_delta": 0.08, "max_value": 1.0}},
        },
        "evolution": {
            "wakes": [
                {"id": "research_synthesis", "on": ["research.progressed", "research.completed"], "for": "god", "count": 2, "every": 1, "external": True, "tools": ["propose_entity", "propose_definition", "wait"], "purpose": "Two verified research work outcomes justify one new unanswered relay question or a concrete structure that lets the next study happen.", "result": "Create at most one genuinely new source or executable continuation when the accumulated evidence leaves a distinct unanswered question; otherwise wait."},
                {"id": "resource_safety", "on": "resource.safety_low", "for": "god", "count": 1, "every": 1, "external": True, "require_story_outcome": True, "tools": ["propose_definition", "wait"], "purpose": "The public energy reserve is below its floor and Mira carries a real source item.", "result": "Create one conserved supply Action and bound behavior when a real carried source exists; the behavior must wake Story after execution. Never mint public stock."},
                {"on": "research.completed", "for": "story"},
                {"on": "resource.replenished", "for": "story"},
                {"on": "entity.created", "for": "story"},
            ],
        },
        "story": {"emergent_source_event_types": ["research.completed", "resource.replenished", "entity.created"], "beats": []},
        "factions": [],
        "recipes": [],
        "player_template": {},
        "initial_events": [{"id": "probe_started", "type": "probe.started", "world_minute": 0, "payload": {"summary": "The Aster Relay evolution probe begins with one unresolved archive and a depleted public reserve."}}],
    }


def _summary(turn: AgentTurn | None) -> dict[str, Any] | None:
    if turn is None:
        return None
    response = turn.response or {}
    calls = []
    for item in list(response.get("tool_calls") or []):
        function = item.get("function") if isinstance(item, dict) else {}
        if not isinstance(function, dict):
            continue
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                pass
        calls.append({"name": function.get("name"), "arguments": arguments})
    return {"role": turn.role, "task_type": turn.task_type, "status": turn.result.get("status"), "failed_check": turn.result.get("failed_check"), "reason": turn.result.get("reason"), "iterations": turn.iterations, "calls": calls, "prompt_chars": len(turn.prompt_history[0]) if turn.prompt_history else 0}


def _ready(platform: Platform, *, role: str, task_prefix: str | None = None) -> dict[str, Any] | None:
    tasks = [item for item in platform.ready_evolution_tasks() if item.get("role") == role and (not task_prefix or str(item.get("task_type", "")).startswith(task_prefix))]
    return max(tasks, key=lambda item: int(item.get("task_id", "0").split(":")[-1]) if str(item.get("task_id", "").split(":")[-1]).isdigit() else 0, default=None)


def _run_task(platform: Platform, provider: Any, task: dict[str, Any]) -> AgentTurn:
    turn = AgentEngine(platform, provider).run(role=str(task["role"]), actor_id=str(task.get("actor_id") or task["role"]), task_type=str(task["task_type"]), source_event_id=task.get("source_event_id"), source_events=task.get("source_events") or [])
    if turn.result.get("status") in {"accepted", "wait"}:
        platform.complete_evolution_task(str(task["task_id"]))
    return turn


def _complete_behavior(platform: Platform, behavior_id: str, provider: Any, rounds: list[dict[str, Any]]) -> dict[str, Any]:
    decision = AgentEngine(platform, provider).run(role="agent_decision", actor_id="mira", task_type="agent_plan_selection")
    if decision.result.get("status") != "accepted":
        return {"decision": _summary(decision), "status": "no_npc_behavior"}
    behavior_ref = str(decision.result.get("behavior_ref") or "")
    if behavior_id and behavior_ref and behavior_ref.casefold() != f"behavior:{behavior_id}".casefold():
        # The decision is authoritative; the expected source is recorded only
        # for audit, so a model may choose an inspect action instead.
        behavior_id = behavior_ref.removeprefix("behavior:")
    started = decision.result
    for _ in range(int(started.get("remaining_minutes", 1)) + 3):
        platform.step()
    return {"decision": _summary(decision), "status": "completed", "behavior": behavior_id, "remaining_minutes": started.get("remaining_minutes"), "last_outcome": platform.store.get_agent("mira").get("initial_state", {}).get("last_outcome")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="remote")
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--max-completion-tokens", type=int, default=None)
    args = parser.parse_args()

    router = LLMRouter(LLMConfig.load())
    providers = {
        role: router.new_provider_for_name(args.provider, max_completion_tokens=args.max_completion_tokens, role=role)
        for role in ("agent_decision", "god", "story")
    }
    temporary = tempfile.TemporaryDirectory(prefix="sim2-evolution-probe-") if args.runtime_dir is None else None
    runtime_root = Path(args.runtime_dir) if args.runtime_dir else Path(temporary.name)
    instance_id = f"evolution-probe-{uuid4().hex[:8]}"
    db_path = create_instance_from_template(_probe_template(), instance_id, runtime_root)
    platform = Platform(db_path)
    log_path = runtime_root / "evolution-audit.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with platform.store.transaction():
            event_id = platform.store.append_event("resource.safety_low", None, 1, {"resource_id": "energy_reserve", "quantity": 0, "minimum_reserve": 1, "summary": "公共能源储备为零，Mira 携带的 energy_cell 可作为真实补给来源。"})
        platform._wake_configured_evolution_consumers(event_id, "resource.safety_low", None, 1)
        rows: list[dict[str, Any]] = []
        for round_no in range(1, max(1, args.rounds) + 1):
            before_entity_ids = {
                str(row["id"])
                for row in platform.store.connection.execute("SELECT id FROM entity").fetchall()
            }
            before_action_ids = {str(item.get("id")) for item in platform.store.get_definitions("action_def")}
            before_behavior_ids = {str(item.get("id")) for item in platform.store.get_definitions("bt_def")}
            god_turn = None
            story_turns: list[AgentTurn] = []
            god_task = _ready(platform, role="god", task_prefix="event_group:resource_safety" if round_no == 1 else "event_group:research_synthesis")
            if god_task:
                god_turn = _run_task(platform, providers["god"], god_task)
            behavior_id = str((god_turn.result if god_turn else {}).get("bound_behavior_id") or "")
            npc = _complete_behavior(platform, behavior_id, providers["agent_decision"], rows)
            story_tasks = [item for item in platform.ready_evolution_tasks() if item.get("role") == "story"]
            for task in story_tasks[:3]:
                story_turns.append(_run_task(platform, providers["story"], task))
            new_entities = []
            for entity_id in sorted(
                {
                    str(row["id"])
                    for row in platform.store.connection.execute("SELECT id FROM entity").fetchall()
                }
                - before_entity_ids
            ):
                entity = platform.store.get_entity(entity_id)
                new_entities.append({"id": entity_id, "name": entity.get("name"), "type": entity.get("entity_type"), "state": entity.get("initial_state", {})})
            row = {"round": round_no, "world_minute": int(platform.store.get_meta("world_minute", 0)), "god": _summary(god_turn), "npc": npc, "story": [_summary(turn) for turn in story_turns], "new_objects": new_entities, "registered_actions": [item.get("name") for item in platform.store.get_definitions("action_def") if str(item.get("id")) not in before_action_ids], "registered_behaviors": [item.get("agent_label") for item in platform.store.get_definitions("bt_def") if str(item.get("id")) not in before_behavior_ids], "energy_reserve": platform.store.get_entity("energy_reserve").get("initial_state", {}).get("quantity"), "inventory": platform.store.get_agent("mira").get("inventory", {}), "story_state": platform.store.get_meta("story_state", {})}
            rows.append(row)
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            print(json.dumps(row, ensure_ascii=False, default=str), flush=True)
            if npc.get("status") == "no_npc_behavior":
                break
            # Every subsequent round is driven by the actual previous result.
            # If a source is exhausted without God creating a new one, the
            # audit stops and leaves the exact absorbing state in SQLite.
            if round_no < args.rounds:
                research_tasks = [item for item in platform.ready_evolution_tasks() if item.get("role") == "god" and str(item.get("task_type", "")).startswith("event_group:research_synthesis")]
                if research_tasks:
                    continue
        return 0
    finally:
        platform.close()
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
