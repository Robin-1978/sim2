"""Verify frontier growth in a fresh save with the configured remote models.

Run with: python scripts/verify_frontier_growth.py --provider remote --runtime-dir artifacts/frontier-growth
The world only seeds a starting mooring, a distress signal and generic actions.
The player accepts the generated route through the regular player API.
"""

from __future__ import annotations

import argparse
import json
from contextlib import closing
from pathlib import Path

from sim2.api.service import ControlService
from sim2.bootstrap.service import create_instance_from_template
from sim2.llm.config import LLMConfig
from sim2.llm.gateway import LLMRouter
from sim2.persistence.store import WorldStore
from verify_automatic_growth import _probe_template as relay_template


ROLES = ("director", "god", "story", "agent_decision", "agent_reflection")


def frontier_template() -> dict:
    world = relay_template()
    world["world"].update(
        id="frontier_probe",
        title="Aster Frontier",
        premise=("Surveyors on an airborne relay have intercepted a live distress signal beyond their registered map. "
                 "The relay has one airship and a crew berth. Two parties need access to viable mooring "
                 "and disagree about who should manage it."),
    )
    world["locations"] = [{
        "id": "relay_lab", "name": "Aster Mooring", "public": True, "safe": True, "tags": ["mooring"], "routes": [],
    }]
    world.update(objects=[], resources=[], capabilities=[])
    travel = world["actions"][0]
    travel["description"] = "Fly a survey airship along a known connection between moorings."
    survey = {
        "id": "check_mooring", "version": 1,
        "description": "Inspect practical access to a berth and transmit what is observed.",
        "parameters": {}, "preconditions": ["actor.alive"], "duration": {"minutes": 5},
        "costs": {"reserve": {}, "per_minute": {"charge": 0.1}, "on_success": {}, "on_failure": {}, "refund": {}},
        "effects": [{"type": "need_delta", "need": "focus", "delta": 1}],
        "information": {
            "event_type": "mooring.inspected", "summary": "A resident inspected the mooring and filed a berthing report.",
            "wake": ["story"], "source_type": "inspection", "confidence": 0.9,
            "result": "mooring_inspected", "record_object_discovery": False,
        },
        "success": {"condition": "actor.alive", "result": "mooring_inspected"},
        "failure": {"condition": "actor.incapacitated", "result": "failed"},
        "kind": "atomic", "executor": "effect_pipeline",
    }
    world["actions"] = [travel, survey]
    world["behavior_trees"] = [{
        "id": "survey_mooring", "agent_label": "Survey Mooring", "summary": "Inspect the mooring at the actor's current location.",
        "entry_conditions": ["actor.alive"], "estimated_minutes": 5,
        "nodes": [{"type": "action", "action_id": "check_mooring", "parameters": {}}],
        "success_results": ["mooring_inspected"], "failure_results": ["failed", "interrupted"],
    }]
    world["agents"] = [{
        "id": "pilot", "name": "Aster Pilot", "role": "player", "controller": "bt", "inventory": {},
        "ultimate_goal": "Explore this frontier and decide who to trust.", "needs": {"focus": 70}, "initial_bt_ids": [],
        "initial_state": {"location_id": "relay_lab", "status": "idle", "integrity": 100, "charge": 90},
    }]
    world["rules"].update(fallback_behavior_ids=[], world_rules=[], resource_property_projections={}, capability_progression={})
    world["evolution"] = {
        "cadence": {"reflection": "5m", "innovation": "20m"},
        "director": {
            "policy": "llm", "cadence": "10m", "window": "90m", "targets": ["place"],
            "context_events": ["frontier.distress", "world.location.created", "mooring.inspected"], "narrative_direction": True,
        },
        "contracts": {
            "god_structural": {
                "tools": ["propose_entity", "wait"], "max_proposals": 2, "required_content": {"growth_cell": True},
                "purpose": "Turn the Director's current exploration priority into a real, reachable mooring or landmark.",
                "result": ("Create one public location, connecting it to an existing mooring with a positive route distance. "
                           "Invent the name, description and physical affordances from the Director brief and observed distress signal. "
                           "Set public: true and safe explicitly inside the state object. Inside state set routes with the exact visible "
                           "Aster Mooring name and a distance of 1-3. Let actual arrivals supply the proof of use."),
            },
            "frontier_resident": {
                "tools": ["propose_entity", "wait"], "max_proposals": 2,
                "purpose": "A new place exists, so give it an autonomous resident with an independent stake in keeping it habitable.",
                "result": ('Create exactly one autonomous agent at the location named by the source event, not the original mooring. '
                           'In state supply role, ultimate_goal, traits, and initial_bt_ids ["Survey Mooring"]. Use controller llm. '
                           'Invent the person, objective and a plausible source of friction. Submit an agent entity, not an object.'),
            },
        },
        "wakes": [
            {"id": "frontier_populate", "on": "world.location.created", "for": "god", "task": "frontier_resident"},
            {"on": "mooring.inspected", "for": "story"},
        ],
    }
    world["story"] = {"emergent_source_event_types": ["mooring.inspected", "world.location.created", "entity.created"], "beats": []}
    world["initial_events"] = [{
        "id": "frontier_signal", "type": "frontier.distress", "world_minute": 0,
        "payload": {"summary": "A distress signal confirms a viable mooring outside the map. One operator asks for repairs. "
                               "A second insists that fuel rationing must be settled before their crew helps."},
    }]
    return world


def events(store: WorldStore, kind: str, keys: tuple[str, ...]) -> list[dict]:
    """Project committed events without recording prompts or model output."""
    result = []
    rows = store.connection.execute(
        "SELECT event_id,world_minute,actor_id,data_json FROM event_log WHERE event_type=? ORDER BY event_id",
        (kind,),
    )
    for event_id, minute, actor, payload in rows:
        data = json.loads(payload)
        result.append({"id": event_id, "minute": minute, "actor": actor,
                       **{key: data[key] for key in keys if key in data}})
    return result


def proof(store: WorldStore) -> dict:
    locations = events(store, "world.location.created", ("entity_id", "name", "summary"))
    residents = [e for e in events(store, "entity.created", ("entity_id", "entity_type", "name"))
                 if e.get("entity_type") == "agent"]
    destination = locations[0]["entity_id"] if locations else None
    residents = [r for r in residents if
                 store.repositories.entities.get_agent(r["entity_id"])["initial_state"]["location_id"] == destination]
    resident_ids = {r["entity_id"] for r in residents}
    completed = events(store, "behavior.completed", ("behavior_id", "outcome"))
    player_arrival = next(({"id": e["id"], "minute": e["minute"], "destination": destination} for e in completed
                          if destination and e["actor"] == "pilot" and
                          e.get("outcome", {}).get("delta", {}).get("location", {}).get("after") == destination), {})
    resident_work = [{"id": e["id"], "minute": e["minute"], "actor": e["actor"], "behavior_id": e["behavior_id"]}
                     for e in completed if e["actor"] in resident_ids and e.get("behavior_id") == "survey_mooring"]
    inspections = {e["id"] for e in events(store, "mooring.inspected", ()) if e["actor"] in resident_ids}
    beats = [e for e in events(store, "story.beat.activated", ("beat_id", "title", "summary", "source_event_id"))
             if e.get("source_event_id") in inspections]
    plans = events(store, "director.plan.started", ("plan_id", "target_cell", "objective", "player_choice", "source_event_id"))
    completed_plan = store.repositories.metadata.get("last_completed_season_plan", {}) or {}
    return {"minute": store.repositories.metadata.get("world_minute", 0), "places": locations,
            "residents": residents, "resident_work": resident_work, "story_beats": beats,
            "director_plans": plans, "completed_plan": completed_plan.get("plan_id"),
            "arrival_closed_plan": bool(player_arrival and completed_plan.get("outcome_event_id") == player_arrival["id"]),
            "player_arrival": player_arrival}


def select_route(service: ControlService, destination: str) -> dict:
    view = service.player_view("pilot")
    for action in view.get("actions", []):
        parameters = action.get("parameters")
        if action.get("kind") == "travel" and isinstance(parameters, dict) and parameters.get("location_id") == destination:
            receipt = service.player_act({"player_id": "pilot", "behavior_ref": action["ref"],
                                          "parameters": parameters, "reason": "Follow the newly discovered mooring route."})
            return {"choice": action["choice"], "status": receipt["status"], "failed_check": receipt.get("failed_check")}
    return {"status": "route_missing"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="remote")
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--instance-id", default="frontier")
    parser.add_argument("--minutes", type=int, default=70)
    args = parser.parse_args()
    if args.minutes <= 0:
        parser.error("--minutes must be positive")

    router = LLMRouter(LLMConfig.load())
    providers = {role: router.new_provider_for_name(args.provider, role=role) for role in ROLES}
    path = create_instance_from_template(frontier_template(), args.instance_id, args.runtime_dir)
    route = {}
    with closing(ControlService(path)) as service:
        platform = service.platform
        store = platform.store
        for offset in range(0, args.minutes, 10):
            platform.run(min(10, args.minutes - offset), providers=providers)
            result = proof(store)
            if result["places"] and route.get("status") != "accepted":
                route = select_route(service, result["places"][0]["entity_id"])
            print(json.dumps({"minute": result["minute"], "places": len(result["places"]),
                              "residents": len(result["residents"]), "surveyed": bool(result["resident_work"]),
                              "story": bool(result["story_beats"]), "route": route, "arrived": bool(result["player_arrival"])},
                             ensure_ascii=False), flush=True)
            if result["arrival_closed_plan"] and result["resident_work"] and result["story_beats"] and len(result["director_plans"]) >= 2:
                break
        call_counts = {r["role"]: r["calls"] for r in store.connection.execute(
            "SELECT role,COUNT(*) AS calls FROM llm_call WHERE status='response' GROUP BY role"
        )}
        result.update(provider=args.provider, route=route, model_calls=[
            {"role": role, "model": providers[role].config.model, "calls": count}
            for role, count in call_counts.items() if role in providers
        ])
        resident_tasks = {e["entity_id"] for e in result["residents"]}
        result["resident_cadence"] = [
            dict(row) for row in store.connection.execute(
                "SELECT actor_id,task_type,due_minute,cadence_minutes FROM evolution_task WHERE role='agent_reflection'"
            )
            if row["actor_id"] in resident_tasks
        ]
    with closing(ControlService(path)) as service:
        view = service.player_view("pilot")
        result["after_reopen"] = {key: view.get(key) for key in ("location", "nearby_participants")}
    verified = (
        route.get("status") == "accepted"
        and result["arrival_closed_plan"]
        and bool(result["resident_work"] and result["story_beats"])
        and len(result["director_plans"]) >= 2
        and result["after_reopen"]["location"]["id"] == result["player_arrival"]["destination"]
        and {"director", "god", "story", "agent_decision"}.issubset(call_counts)
    )
    result["verified"] = verified
    report = args.runtime_dir / f"{args.instance_id}-summary.json"
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verified": verified, "report": str(report)}, ensure_ascii=False))
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
