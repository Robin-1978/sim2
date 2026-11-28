"""Run identical Windward openings in independent saves using the configured remote roles.

python scripts/verify_windward_divergence.py --runtime-dir artifacts/windward-playtest --minutes 2880
Use --resume to extend the same saves. A nonzero result means evidence is missing.
No model responses, reasoning, prompts or credentials are included in the report.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from itertools import combinations
from pathlib import Path
from tempfile import TemporaryDirectory

from sim2.api.service import ControlService
from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.llm.config import LLMConfig
from sim2.llm.gateway import LLMRouter
from sim2.platform.runtime import Platform
from worlds import world_path

ROLES = ("god", "story", "director", "agent_decision", "agent_reflection")
OPENING = "rescue_kite_scout"


def try_player_choice(path: Path) -> dict:
    """Try the first currently available generated choice in a disposable copy."""
    with TemporaryDirectory() as directory:
        copy = Path(directory) / "play.sqlite3"
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(copy)) as destination:
                source.backup(destination)
        with closing(ControlService(copy)) as service:
            store = service.platform.store
            generated = {row[0] for row in store.connection.execute("""
                SELECT json_extract(data_json,'$.definition_id') FROM event_log
                WHERE actor_id='god' AND event_type='definition.registered'
                  AND json_extract(data_json,'$.definition_type')='behavior'
            """)}
            view = service.player_view("hero")
            actions = [item for item in view["actions"]
                       if item["ref"].removeprefix("behavior:") in generated]
            trial = {"available": [item["choice"] for item in actions], "chapter": view.get("chapter"), "completed": False}
            if not actions:
                return trial
            selected = actions[0]
            cursor = store.repositories.events.latest_event_id()
            result = service.player_act({"player_id": "hero", "behavior_ref": selected["ref"],
                                         "parameters": selected["parameters"] if isinstance(selected.get("parameters"), dict) else {},
                                         "reason": "试玩存档中居民提出的新行动"})
            trial.update(choice=selected["choice"], admission=result["status"])
            if result["status"] != "accepted":
                return trial
            for _ in range(240):
                service.platform.step()
                actor = store.repositories.entities.get_agent("hero")
                if not (actor.get("initial_state") or {}).get("active_bt_instance"):
                    break
            for row in store.connection.execute("""
                SELECT event_id,event_type,data_json FROM event_log WHERE event_id>? AND actor_id='hero'
                  AND event_type IN ('behavior.completed','behavior.failed','relationship.changed','entity.state_changed','resource.changed','world.property_changed')
                ORDER BY event_id
            """, (cursor,)):
                data = json.loads(row["data_json"])
                if row["event_type"] == "behavior.failed":
                    if data.get("behavior_id") == selected["ref"].removeprefix("behavior:"):
                        trial.update(failure=data.get("result"), outcome_event_id=row["event_id"])
                elif row["event_type"] == "behavior.completed":
                    if data.get("behavior_id") == selected["ref"].removeprefix("behavior:"):
                        trial.update(completed=True, outcome_event_id=row["event_id"],
                                     delta=(data.get("outcome") or {}).get("delta"))
                else:
                    fields = {key: data[key] for key in
                              ("target_id", "relation", "actual_delta", "state", "before", "after", "resource_id", "property")
                              if key in data}
                    trial.setdefault("effects", []).append({"event": row["event_type"], **fields})
            return trial


def run_save(root: Path, name: str, minutes: int, checkpoint: int, resume: bool) -> dict:
    path = root / "instances" / name / "world.sqlite3"
    if not resume:
        path = create_instance(world_path("windward_skyfall"), name, root)
        with closing(Platform(path)) as platform:
            entry = platform.enter_player("hero", "mechanic", name="林晚")
            if entry.get("status") != "accepted":
                raise RuntimeError(f"Player entry failed in {name}")
            intent = BehaviorIntent("hero", OPENING, {}, {}, "救回斥候", 1.0, "opening-rescue")
            if platform.submit_behavior(intent).get("status") != "accepted":
                raise RuntimeError(f"Opening action failed in {name}")
    elif not path.is_file():
        raise FileNotFoundError(path)
    config = LLMConfig.load()
    router = LLMRouter(config)
    providers = {role: router.provider_for_role(role) for role in ROLES}
    while True:
        with closing(Platform(path)) as platform:
            meta = platform.store.repositories.metadata
            if meta.get("world_id") != "windward_skyfall" or meta.get("instance_id") != name:
                raise ValueError(f"Unexpected save at {path}")
            start = int(meta.get("world_minute", 0))
            if start >= minutes:
                result = evidence(platform, checkpoint)
                result["models"] = {role: provider.config.model for role, provider in providers.items()}
                result["providers"] = {role: config.provider_name_for(role) for role in ROLES}
                result["player_trial_on_copy"] = try_player_choice(path)
                if not result["player_trial_on_copy"]["completed"]:
                    result["flags"].append("no_verified_playable_generated_choice")
                return result
            report = platform.run(min(checkpoint, minutes - start), providers=providers)
            end = int(meta.get("world_minute", 0))
            print(f"{name} {start} → {end}", flush=True)
            if report.stop_reason or end <= start:
                raise RuntimeError(f"{name} stopped at {end}: {report.stop_reason}")


def evidence(platform: Platform, checkpoint: int) -> dict:
    store = platform.store
    meta = store.repositories.metadata
    events = store.repositories.events
    rows = store.connection.execute(
        "SELECT role,status,COUNT(*) AS n FROM llm_call GROUP BY role,status"
    )
    calls = {role: {} for role in ROLES}
    for role, status, count in rows:
        calls.setdefault(role, {})[status] = count
    registrations = [dict(row) for row in store.connection.execute("""
        SELECT event_id,world_minute,event_type,COALESCE(
               json_extract(data_json,'$.name'),json_extract(data_json,'$.summary')
        ) AS label, json_extract(data_json,'$.entity_id') AS entity_id,
          json_extract(data_json,'$.entity_type') AS entity_type FROM event_log WHERE actor_id='god' AND event_type IN (
            'definition.registered','entity.created','institution.activated','world.change.relationship',
            'world.change.faction','world.change.rule','world.change.route'
        ) ORDER BY event_id
    """)]
    plans = list((meta.get("season_plan_history") or {}).values())
    npc_ids = {row[0] for row in store.connection.execute(
        "SELECT id FROM agent_state WHERE json_extract(data_json,'$.role') != 'player'"
    )}
    director_choices = {
        (minute, decision.get("target_cell"), decision.get("reason"))
        for minute, body in store.connection.execute(
            "SELECT world_minute,response_json FROM llm_call WHERE role='director' AND status='response'"
        )
        if isinstance(decision := json.loads(body), dict)
    }
    model_plans = [plan["plan_id"] for plan in plans
                   if (plan.get("created_minute"), plan.get("target_cell"), plan.get("reason")) in director_choices]
    uses = []
    for plan in plans:
        if plan.get("status") != "completed" or not plan.get("outcome_event_id"):
            continue
        outcome, = events.events_by_ids([plan["outcome_event_id"]])
        if outcome["event_type"] == "world.proposal.evaluated":
            observed_id = (outcome.get("observed") or {}).get("outcome_event_id")
            if not observed_id:
                continue
            outcome, = events.events_by_ids([observed_id])
        if outcome.get("actor_id") not in npc_ids:
            continue
        delta = (outcome.get("outcome") or {}).get("delta") or outcome.get("observed_delta") or {}
        uses.append({"cell": plan["target_cell"], "event": outcome["event_type"],
                     "actor": outcome.get("actor_id"), "minute": outcome["world_minute"],
                     "state": outcome.get("state"), "relation": outcome.get("relation"),
                     "behavior": outcome.get("behavior_id"),
                     "inventory": sorted(delta.get("inventory", {})), "destination": delta.get("location", {}).get("after"),
                     "event_id": outcome["event_id"], "plan_id": plan["plan_id"]})
    # A resident may first adopt a generated behavior after the plan expires.
    adoptions = []
    for row in store.connection.execute("""
        SELECT created.event_id AS registration, used.event_id, used.world_minute, used.actor_id,
               json_extract(created.data_json,'$.name') AS label, used.data_json
        FROM event_log AS created JOIN event_log AS used ON used.event_type='behavior.completed'
          AND used.event_id>created.event_id AND used.actor_id IN (
              SELECT id FROM agent_state WHERE json_extract(data_json,'$.role') != 'player')
          AND json_extract(used.data_json,'$.behavior_id')=json_extract(created.data_json,'$.definition_id')
        WHERE created.event_type='definition.registered' AND created.actor_id='god'
          AND json_extract(created.data_json,'$.definition_type')='behavior'
        ORDER BY used.event_id
    """):
        data = json.loads(row["data_json"])
        delta = (data.get("outcome") or {}).get("delta") or {}
        # Older saves include the action id on world effects, but not the execution id.
        # Match the completed action, actor and minute inside this behavior execution.
        properties = [dict(item) for item in store.connection.execute("""
            SELECT DISTINCT json_extract(effect.data_json,'$.property') AS property,
                   json_extract(effect.data_json,'$.before') AS before, json_extract(effect.data_json,'$.after') AS after
            FROM event_log AS action JOIN event_log AS effect ON effect.actor_id=action.actor_id
              AND effect.world_minute=action.world_minute AND effect.event_id<action.event_id
              AND json_extract(effect.data_json,'$.source')=json_extract(action.data_json,'$.action_id')
            WHERE action.event_type='action.completed' AND action.actor_id=?
              AND json_extract(action.data_json,'$.execution_id')=? AND action.event_id<?
              AND effect.event_type='world.property_changed'
              AND json_extract(effect.data_json,'$.before') IS NOT json_extract(effect.data_json,'$.after')
        """, (row["actor_id"], data.get("execution_id"), row["event_id"]))]
        adoptions.append({"registration": row["registration"], "event_id": row["event_id"],
                          "minute": row["world_minute"], "actor": row["actor_id"], "behavior": row["label"],
                          "properties": properties, "inventory": sorted(delta.get("inventory", {})),
                          "destination": delta.get("location", {}).get("after")})
    chapters = []
    closures = {row["beat"]: dict(row) for row in store.connection.execute("""
        SELECT json_extract(data_json,'$.beat_id') AS beat, event_id, world_minute, actor_id,
               json_extract(data_json,'$.behavior_id') AS behavior
        FROM event_log WHERE event_type='story.beat.completed'
    """)}
    for beat in (meta.get("story_state") or {}).get("emergent_beats", []):
        source = events.events_by_ids([beat["source_event_id"]])
        if source:
            closed = closures.get(beat["id"])
            chapters.append({"chapter": beat.get("summary"), "audience": beat.get("audience"),
                             "completion": closed, "id": beat["id"],
                             "previous_chapter": source[0].get("beat_id") if source[0]["event_type"] == "story.beat.completed" else None,
                             "previous_behavior_event": source[0].get("behavior_event_id"),
                             "next": beat.get("unlock_behavior_ids"), "source": source[0]["event_type"],
                             "event_id": source[0]["event_id"], "source_minute": source[0]["world_minute"]})
    opening = events.recent_events_by_types(["skyfall.dawn_salvage"], limit=1)
    flags = []
    if len(opening) != 1 or opening[0].get("actor_id") != "hero" or opening[0].get("action_id") != OPENING:
        flags.append("opening_not_verified")
    for role in ("god", "story", "director"):
        if not calls[role].get("response"):
            flags.append(f"no_remote_response:{role}")
    if not model_plans:
        flags.append("no_committed_model_director_choice")
    if not registrations:
        flags.append("no_committed_god_change")
    if not uses and not adoptions:
        flags.append("no_verified_use_of_generated_structure")
    if not any(item["source_minute"] > checkpoint for item in chapters):
        flags.append("no_new_grounded_chapter_after_reopening")
    if not any(item["completion"] for item in chapters):
        flags.append("no_completed_story_followup")
    if not any(item["previous_chapter"] and item["previous_behavior_event"] for item in chapters):
        flags.append("no_chapter_continues_a_completed_followup")
    return {"save": meta.get("instance_id"), "minute": meta.get("world_minute"),
            "package_hash": meta.get("config_hash"), "seed": meta.get("random_seed"),
            "properties": {key: {field: spec[field] for field in ("unit", "min", "max") if field in spec}
                           for key, spec in (meta.get("rules") or {}).get("world_property_labels", {}).items()
                           if isinstance(spec, dict)},
            "calls": calls, "registrations": registrations,
            "plans": [{key: plan.get(key) for key in ("plan_id", "target_cell", "status", "closure_reason", "outcome_event_id", "mainline")}
                                      for plan in plans],
            "recent_generated_resident_actions": store.repositories.activity.recent_generated_resident_actions(
                max(0, int(meta.get("world_minute", 0)) - checkpoint)),
            "model_plans": model_plans, "uses": uses, "adoptions": adoptions,
            "chapters": chapters, "flags": flags}


def witnessed_effects(save: dict) -> set[tuple]:
    dynamic = {item["entity_id"]: "generated:" + str(item["entity_type"]) for item in save["registrations"]
               if item["event_type"] == "entity.created" and item["entity_id"]}
    effects = set()
    for use in save["uses"] + save["adoptions"]:
        if dest := use["destination"]:
            effects.add(("location", dynamic.get(dest, dest)))
        for item in use["inventory"]:
            effects.add(("inventory", dynamic.get(item, item)))
        if state := use.get("state"):
            effects.add(("entity.state_changed", state))
        if relation := use.get("relation"):
            effects.add(("relationship.changed", relation))
        for item in use.get("properties", []):
            spec = save["properties"].get(item["property"])
            if spec is None:
                continue
            if spec.get("unit") == "percent" and any(
                isinstance(value, (float, int)) and not spec.get("min", float("-inf")) <= value <= spec.get("max", float("inf"))
                for value in (item["before"], item["after"])
            ):
                continue
            effects.add(("world.property_changed", item["property"]))
    return effects


def compare(saves: list[dict]) -> list[str]:
    flags = []
    if len({(save["package_hash"], save["seed"]) for save in saves}) != 1:
        flags.append("different_packages_or_seeds")
    for left, right in combinations(saves, 2):
        # Random identifiers and alternative prose cannot establish gameplay divergence.
        left_effects, right_effects = witnessed_effects(left), witnessed_effects(right)
        if not left_effects or not right_effects or left_effects == right_effects:
            flags.append(f"distinct_used_mechanics_unproven:{left['save']}:{right['save']}")
    return flags


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--minutes", type=int, default=2880)
    parser.add_argument("--checkpoint", type=int, default=120)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.checkpoint < 1 or args.minutes <= args.checkpoint:
        parser.error("minutes must exceed a positive checkpoint")
    names = ("voyage-a", "voyage-b", "voyage-c")
    root = args.runtime_dir.resolve()
    for name in names:
        path = root / "instances" / name / "world.sqlite3"
        if path.exists() != args.resume:
            parser.error(f"Unexpected save state at {path}. Use a fresh directory or resume an existing run.")
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        results = [pool.submit(run_save, root, name, args.minutes, args.checkpoint, args.resume) for name in names]
        saves = [result.result() for result in results]
    flags = compare(saves)
    report = {"saves": saves, "comparison_flags": flags}
    output = root / "divergence.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output, {save["save"]: save["flags"] for save in saves}, flags, flush=True)
    return int(bool(flags or any(save["flags"] for save in saves)))


if __name__ == "__main__":
    raise SystemExit(main())
