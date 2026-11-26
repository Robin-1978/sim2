from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
from shutil import copytree
from typing import Any

import pytest
import yaml

from sim2.bootstrap.service import create_instance, upgrade_instance, validate_config
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform


WORLD = Path(__file__).parents[1] / "worlds" / "windward_skyfall"
RESCUE = "rescue_kite_scout"


def read_rows(package: Path, section: str) -> list[dict[str, Any]]:
    return yaml.safe_load((package / f"{section}.yaml").read_text())[section]


def write_rows(package: Path, section: str, rows: list[dict[str, Any]]) -> None:
    (package / f"{section}.yaml").write_text(yaml.safe_dump({section: rows}, allow_unicode=True))


def start_rescue(platform: Platform) -> str:
    assert platform.enter_player("hero", "mechanic", name="领航员")["status"] == "accepted"
    result = platform.submit_behavior(BehaviorIntent(
        actor_id="hero", behavior_ref=f"behavior:{RESCUE}", target={}, parameters={},
        reason="Resolve the rescue.", confidence=1.0, intent_id="dsl-rescue",
    ))
    assert result["status"] == "accepted", result
    return result["execution_id"]


@pytest.mark.parametrize("field", ["success", "failure"])
def test_package_and_definition_proposal_diagnose_malformed_outcome(tmp_path: Path, field: str) -> None:
    package = tmp_path / "world"
    copytree(WORLD, package)
    actions = read_rows(package, "actions")
    rescue = next(row for row in actions if row["id"] == RESCUE)
    rescue["preconditions"].append("actor.role != 'world.absent_property'")
    write_rows(package, "actions", actions)
    assert validate_config(package)["valid"]

    db = create_instance(package, "dsl-proposal", tmp_path)
    broken = "actor.alive and ("
    rescue[field]["condition"] = broken
    write_rows(package, "actions", actions)
    errors = validate_config(package)["errors"]
    assert any(f"{RESCUE}.{field}.condition has invalid expression" in error for error in errors)
    with closing(Platform(db)) as platform:
        report = platform.submit_definition_proposal("god", {
            "definition_type": "action", "name": "Broken resupply", "definition": {
                "duration": {"minutes": 1},
                "success": {"condition": broken if field == "success" else "actor.alive", "result": "resupplied"},
                "failure": {"condition": broken if field == "failure" else "actor.incapacitated", "result": "failed"},
                "effects": [{"type": "world_property_delta", "property": "kite_rep", "delta": 0.1}],
            },
        })
        assert report["status"] == "rejected"
        assert f"{field}.condition has invalid expression" in json.dumps(report)


@pytest.mark.parametrize("parallel", [False, True])
def test_action_projection_and_commit_use_the_same_calendar_fields(tmp_path: Path, parallel: bool) -> None:
    package = tmp_path / "world"
    copytree(WORLD, package)
    actions = read_rows(package, "actions")
    rescue = next(row for row in actions if row["id"] == RESCUE)
    rescue["preconditions"].append("context.calendar_day == 1 and context.minute_of_day < 426")
    rescue["success"]["condition"] = (
        "context.day_phase == 'dawn' and context.calendar_day == 1 and context.minute_of_day == 426"
    )
    write_rows(package, "actions", actions)
    if parallel:
        behaviors = read_rows(package, "behavior_trees")
        behavior = next(row for row in behaviors if row["id"] == RESCUE)
        behavior["nodes"] = [{"type": "parallel", "children": behavior["nodes"]}]
        write_rows(package, "behavior_trees", behaviors)
    with closing(Platform(create_instance(package, "dsl-calendar", tmp_path))) as platform:
        execution_id = start_rescue(platform)
        for _ in range(6):
            platform.step()
        event, _ = platform.store.repositories.events.behavior_outcome_evidence("hero", execution_id)
        assert event["event_type"] == "behavior.completed", event
        assert platform.store.repositories.metadata.get("world_properties")["kite_rep"] == pytest.approx(0.35)


@pytest.mark.parametrize("legacy", [False, True])
def test_upgrade_pins_pending_actions_and_behavior_outcome(tmp_path: Path, legacy: bool) -> None:
    package = tmp_path / "world"
    copytree(WORLD, package)
    actions = read_rows(package, "actions")
    next_action = {
        "id": "record_rescue_ledger", "verb": "work", "executor": "effect_pipeline", "duration": {"minutes": 1},
        "costs": {"reserve": {}, "per_minute": {}, "on_success": {}, "on_failure": {}, "refund": {}},
        "effects": [{"type": "world_property_set", "property": "foundry_rep", "value": 0.2}],
        "success": {"condition": "actor.alive", "result": "ledger_recorded"},
        "failure": {"condition": "actor.incapacitated", "result": "failed"},
    }
    actions.append(next_action)
    write_rows(package, "actions", actions)
    behaviors = read_rows(package, "behavior_trees")
    rescue = next(row for row in behaviors if row["id"] == RESCUE)
    rescue["nodes"].append({"type": "action", "action_id": next_action["id"]})
    rescue["outcome_effects"] = [{"type": "world_property_set", "property": "choir_rep", "value": 0.3}]
    rescue["outcome_events"] = [{"event_type": "rescue.ledger_v1", "summary": "首航已登记。"}]
    write_rows(package, "behavior_trees", behaviors)
    db = create_instance(package, "dsl-snapshot", tmp_path)
    with closing(Platform(db)) as platform:
        execution_id = start_rescue(platform)
        platform.step()
        if legacy:
            with platform.store.transaction() as connection:
                data = json.loads(connection.execute("SELECT data_json FROM bt_instance WHERE execution_id=?", (execution_id,)).fetchone()["data_json"])
                data.pop("behavior_snapshot")
                data.pop("recipe_snapshots")
                for action in data["actions"]:
                    action.pop("definition_snapshot")
                connection.execute("UPDATE bt_instance SET data_json=? WHERE execution_id=?", (json.dumps(data), execution_id))

    next_action["effects"][0]["value"] = 0.8
    rescue["outcome_effects"][0]["value"] = 0.9
    rescue["outcome_events"][0]["event_type"] = "rescue.ledger_v2"
    write_rows(package, "actions", actions)
    write_rows(package, "behavior_trees", behaviors)
    upgrade_instance(package, db)

    with closing(Platform(db)) as platform:
        for _ in range(6):
            platform.step()
        repository = platform.store.repositories
        props = repository.metadata.get("world_properties")
        assert (props["foundry_rep"], props["choir_rep"]) == (0.2, 0.3)
        counts = repository.events.event_type_counts()
        assert counts["rescue.ledger_v1"] == 1
        assert "rescue.ledger_v2" not in counts


def test_in_progress_craft_uses_the_admitted_recipe_after_upgrade(tmp_path: Path) -> None:
    package = tmp_path / "world"
    copytree(WORLD, package)
    recipes = read_rows(package, "recipes")
    rope = next(row for row in recipes if row["id"] == "fiber_rope")
    rope["duration_minutes"] = 2
    write_rows(package, "recipes", recipes)
    db = create_instance(package, "dsl-recipe", tmp_path)

    with closing(Platform(db)) as platform:
        assert platform.enter_player("hero", "cartographer", name="领航员")["status"] == "accepted"
        repository = platform.store.repositories.entities
        before = repository.get_agent("hero")["inventory"].get("rope", 0)
        result = platform.submit_behavior(BehaviorIntent(
            actor_id="hero", behavior_ref="behavior:craft", target={}, parameters={"recipe_id": "fiber_rope"},
            reason="Make rope.", confidence=1.0, intent_id="dsl-rope",
        ))
        assert result["status"] == "accepted", result
        platform.step()
        assert repository.get_agent("hero")["inventory"].get("rope", 0) == before
    rope["outputs"][0]["quantity"] = 7
    write_rows(package, "recipes", recipes)
    upgrade_instance(package, db)

    with closing(Platform(db)) as platform:
        platform.step()
        actor = platform.store.repositories.entities.get_agent("hero")
        assert actor["inventory"]["rope"] == before + 1
