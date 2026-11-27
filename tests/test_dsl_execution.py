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
from sim2.world.definition_compiler import compile_definition_shape


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


@pytest.mark.parametrize(("broken", "correction", "message"), [
    ({"type": "entity_state_set", "entity_id": "airship", "state": "petition"}, {"value": False},
     "entity_state_set requires entity_id, state and value"),
    ({"type": "public_event", "summary": "Petition heard."}, {"event": "petition.heard"},
     "public_event requires event"),
])
def test_package_and_god_reject_incomplete_effects(
    tmp_path: Path, broken: dict[str, Any], correction: dict[str, Any], message: str,
) -> None:
    package = tmp_path / "package"
    copytree(WORLD, package)
    db = create_instance(package, "effect-contract", tmp_path)
    actions = read_rows(package, "actions")
    action = next(row for row in actions if row["id"] == RESCUE)
    action["effects"] = [broken]
    write_rows(package, "actions", actions)
    assert any(message in error for error in validate_config(package)["errors"])
    with closing(Platform(db)) as platform:
        proposal = platform.submit_definition_proposal("god", {
            "definition_type": "action", "name": "Petition recording", "definition": action,
        })
        assert proposal["status"] == "rejected"
        assert message in json.dumps(proposal)

    for kind, field in [("action", "effects"), ("action", "progress_effects"),
                        ("behavior", "outcome_effects"), ("recipe", "effects")]:
        _, errors = compile_definition_shape(kind, "Petition", {field: [broken]})
        assert any(message in error for error in errors)
        _, errors = compile_definition_shape(kind, "Petition", {field: [{**broken, **correction}]})
        assert all(message not in error for error in errors)
        assert any("requires completion" in error for error in errors) == (field == "progress_effects")
        assert any("declare it on the action" in error for error in errors) == (
            kind == "recipe" and broken["type"] == "public_event"
        )
    action["effects"] = [{**broken, **correction}]
    write_rows(package, "actions", actions)
    assert validate_config(package)["valid"]
    action["progress_effects"] = action["effects"]
    write_rows(package, "actions", actions)
    assert any("requires completion" in error for error in validate_config(package)["errors"])


def test_village_progress_changes_the_configured_character_attribute(tmp_path: Path) -> None:
    with closing(Platform(create_instance(WORLD.parent / "starter_village", "progress-state", tmp_path))) as platform:
        entities = platform.store.repositories.entities
        actor = entities.get_agent("he_yan")
        action = next(row for row in entities.get_definitions("action_def") if row["id"] == "attack")
        before = actor["initial_state"]["threat_level"]
        platform._services["execution"].apply_action_progress(actor, action)
        assert actor["initial_state"]["threat_level"] == pytest.approx(before + 0.2)


def test_incomplete_stored_effect_rolls_back_prior_effects(tmp_path: Path) -> None:
    with closing(Platform(create_instance(WORLD, "legacy-null", tmp_path))) as platform:
        repository = platform.store.repositories
        before = repository.metadata.get("world_properties")["kite_rep"]
        action = flow_action("legacy_petition", "kite_rep")
        action["effects"].append({"type": "entity_state_set", "entity_id": "airship", "state": "petition"})
        install_flow(platform, [action_node(action["id"])], [action])
        submit_flow(platform)
        platform.step()
        event, _ = repository.events.behavior_outcome_evidence("hero", "control-flow")
        assert event["event_type"] == "behavior.failed"
        assert repository.metadata.get("world_properties")["kite_rep"] == before
        assert "entity.state_changed" not in repository.events.event_type_counts()


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


def install_flow(platform: Platform, nodes: list[dict[str, Any]], actions: list[dict[str, Any]]) -> None:
    assert platform.enter_player("hero", "mechanic", name="领航员")["status"] == "accepted"
    entities = platform.store.repositories.entities
    with platform.store.transaction():
        for action in actions:
            entities.put_definition("action_def", action["id"], action)
        entities.put_definition("bt_def", "flow", {
            "id": "flow", "version": 1, "nodes": nodes,
        })
        actor = entities.get_agent("hero")
        actor.setdefault("unlocked_bt_ids", []).append("flow")
        actor["inventory"]["wood"] = 1
        entities.update_agent("hero", actor)


def flow_action(name: str, prop: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": name, "duration": {"minutes": 1},
        "effects": [{"type": "world_property_set", "property": prop, "value": 0.5}],
        **fields,
    }


def action_node(name: str) -> dict[str, Any]:
    return {"type": "action", "action_id": name}


def submit_flow(platform: Platform) -> None:
    result = platform.submit_behavior(BehaviorIntent(
        actor_id="hero", behavior_ref="behavior:flow", target={}, parameters={},
        reason="Follow the route plan.", confidence=1.0, intent_id="control-flow",
    ))
    assert result["status"] == "accepted", result


def test_selector_waits_and_rechecks_live_state_after_reopening(tmp_path: Path) -> None:
    db = create_instance(WORLD, "flow-reopen", tmp_path)
    with closing(Platform(db)) as platform:
        install_flow(platform, [{
            "type": "selector",
            "children": [
                {"type": "selector", "children": [
                    {"type": "sequence", "children": [
                        {"type": "condition", "expression": "world.kite_rep >= 0.3"},
                        action_node("primary_route"),
                    ]},
                    {"type": "condition", "expression": "world.foundry_rep >= 0.9"},
                ]},
                {"type": "sequence", "children": [
                    {"type": "wait", "minutes": 2},
                    {"type": "condition", "expression": "world.kite_rep >= 0.3 and context.minute_of_day == 422"},
                    action_node("fallback_route"),
                ]},
            ],
        }], [flow_action("primary_route", "foundry_rep"), flow_action("fallback_route", "choir_rep")])
        submit_flow(platform)
        platform.step()
        state = platform.store.repositories.entities.get_agent("hero")["initial_state"]
        assert state["active_action"] == "wait"
        assert state["remaining_minutes"] == 1
        assert platform.interrupt_behavior("hero", "Pause the watch.", hard=False)["status"] == "paused"

    with closing(Platform(db)) as platform:
        assert platform.resume_behavior("hero")["status"] == "resumed"
        metadata = platform.store.repositories.metadata
        with platform.store.transaction():
            properties = metadata.get("world_properties")
            metadata.set("world_properties", {**properties, "kite_rep": 0.4})
            replacement = flow_action("fallback_route", "choir_rep")
            replacement["effects"][0]["value"] = 0.8
            platform.store.repositories.entities.put_definition("action_def", "fallback_route", replacement)
        platform.step()
        assert metadata.get("world_properties")["choir_rep"] != 0.5
        platform.step()
        assert metadata.get("world_properties")["choir_rep"] == 0.5
        assert metadata.get("world_properties")["foundry_rep"] != 0.5
        event, _ = platform.store.repositories.events.behavior_outcome_evidence("hero", "control-flow")
        assert event["event_type"] == "behavior.completed"


def test_selector_skips_remaining_branches_when_first_succeeds(tmp_path: Path) -> None:
    with closing(Platform(create_instance(WORLD, "flow-success", tmp_path))) as platform:
        install_flow(platform, [
            {"type": "selector", "children": [action_node("first"), action_node("fallback")]},
            {"type": "condition", "expression": "context.last_action_result == 'first_recorded'"},
            action_node("after_selector"),
        ], [
            flow_action("first", "foundry_rep", success={"result": "first_recorded"}),
            flow_action("fallback", "choir_rep"),
            flow_action("after_selector", "kite_rep"),
        ])
        submit_flow(platform)
        platform.step()
        platform.step()
        properties = platform.store.repositories.metadata.get("world_properties")
        assert properties["foundry_rep"] == properties["kite_rep"] == 0.5
        assert properties["choir_rep"] != 0.5


@pytest.mark.parametrize("parallel", [False, True])
def test_selector_refunds_failed_branch_and_tries_fallback(tmp_path: Path, parallel: bool) -> None:
    with closing(Platform(create_instance(WORLD, "flow-refund", tmp_path))) as platform:
        attempt = action_node("attempt")
        nodes = [
            {"type": "parallel", "children": [attempt, {"type": "wait", "minutes": 3}]} if parallel else attempt,
            action_node("fallback"),
        ]
        install_flow(platform, [{"type": "selector", "children": nodes}], [
            flow_action("attempt", "foundry_rep", costs={"reserve": {"wood": 1}}, success={"condition": "world.kite_rep > 0.9"}),
            flow_action("fallback", "choir_rep", costs={"reserve": {"wood": 1}}),
        ])
        submit_flow(platform)
        platform.step()
        assert platform.store.repositories.entities.get_agent("hero")["inventory"]["wood"] == 1
        assert platform.store.repositories.metadata.get("world_properties")["foundry_rep"] != 0.5
        platform.step()
        assert platform.store.repositories.entities.get_agent("hero")["inventory"]["wood"] == 0
        assert platform.store.repositories.metadata.get("world_properties")["choir_rep"] == 0.5
        statuses = {
            row["action_id"]: row["status"] for row in platform.store.connection.execute(
                "SELECT action_id,status FROM action_instance WHERE execution_id='control-flow'"
            )
        }
        expected = {"attempt": "failed", "fallback": "succeeded"}
        if parallel:
            expected["wait"] = "failed"
        assert statuses == expected


def test_parallel_group_cannot_reserve_the_same_carried_item_twice(tmp_path: Path) -> None:
    with closing(Platform(create_instance(WORLD, "flow-contested", tmp_path))) as platform:
        nodes = [{
            "type": "action", "action_id": name, "parameters": {"material": "wood"},
        } for name in ("left", "right", "fallback")]
        reserve = {"reserve": {"item_parameter": "material"}}
        install_flow(platform, [{"type": "selector", "children": [
            {"type": "parallel", "children": nodes[:2]}, nodes[2],
        ]}], [
            flow_action("left", "kite_rep", costs=reserve),
            flow_action("right", "foundry_rep", costs=reserve),
            flow_action("fallback", "choir_rep", costs=reserve),
        ])
        submit_flow(platform)
        platform.step()
        properties = platform.store.repositories.metadata.get("world_properties")
        assert properties["choir_rep"] == 0.5
        assert properties["kite_rep"] != 0.5 and properties["foundry_rep"] != 0.5
        assert platform.store.repositories.entities.get_agent("hero")["inventory"]["wood"] == 0


def test_failed_condition_keeps_completed_action_and_blocks_behavior_outcome(tmp_path: Path) -> None:
    with closing(Platform(create_instance(WORLD, "flow-condition", tmp_path))) as platform:
        install_flow(platform, [
            action_node("record"), {"type": "condition", "expression": "world.kite_rep >= 0.9"},
        ], [flow_action("record", "foundry_rep", costs={"reserve": {"wood": 1}})])
        entities = platform.store.repositories.entities
        with platform.store.transaction():
            definition = next(item for item in entities.get_definitions("bt_def") if item["id"] == "flow")
            definition["outcome_events"] = [{"event_type": "route.verified"}]
            entities.put_definition("bt_def", "flow", definition)
        submit_flow(platform)
        platform.step()
        event, _ = platform.store.repositories.events.behavior_outcome_evidence("hero", "control-flow")
        assert event["event_type"] == "behavior.failed"
        assert entities.get_agent("hero")["inventory"]["wood"] == 0
        assert platform.store.repositories.metadata.get("world_properties")["foundry_rep"] == 0.5
        assert "route.verified" not in platform.store.repositories.events.event_type_counts()


@pytest.mark.parametrize(("invalid", "message"), [
    ({"type": "wait", "minutes": 0}, "positive integer minutes"),
    ({"type": "condition", "expression": "conflict.resolved"}, "unsupported condition path"),
    ({"type": "selector", "children": []}, "requires children"),
    ({"type": "parallel", "children": [{"type": "condition", "expression": "actor.alive"}]}, "outside a parallel group"),
    ({"type": "parallel", "children": [
        {"type": "sequence", "children": [{"type": "wait", "minutes": 2}, {"type": "action", "action_id": RESCUE}]},
        {"type": "wait", "minutes": 1},
    ]}, "sequential steps must be outside a parallel group"),
])
def test_control_nodes_are_validated_in_world_packages(tmp_path: Path, invalid: dict[str, Any], message: str) -> None:
    package = tmp_path / "world"
    copytree(WORLD, package)
    behaviors = read_rows(package, "behavior_trees")
    rescue = next(row for row in behaviors if row["id"] == RESCUE)
    rescue["nodes"].append({"type": "wait", "minutes": 2})
    write_rows(package, "behavior_trees", behaviors)
    assert validate_config(package)["valid"]
    rescue["nodes"].append(invalid)
    write_rows(package, "behavior_trees", behaviors)
    assert any(message in error for error in validate_config(package)["errors"])


def test_referenced_parallel_branches_are_checked_at_proposal_and_admission(tmp_path: Path) -> None:
    with closing(Platform(create_instance(WORLD, "parallel-branch", tmp_path))) as platform:
        entities = platform.store.repositories.entities
        wait = {"type": "wait", "minutes": 2}
        parallel = {"type": "parallel", "children": [{"type": "subtree", "behavior_id": "delayed"}, wait]}
        with platform.store.transaction():
            entities.put_definition("bt_def", "delayed", {"id": "delayed", "nodes": [wait, wait]})
        proposal = {"definition_type": "behavior", "name": "Delayed rescue", "definition": {
            "deferred": True, "nodes": [parallel],
        }}
        result = platform.submit_definition_proposal("god", proposal)
        assert result["status"] == "rejected" and "outside a parallel group" in str(result)
        assert platform.enter_player("hero", "mechanic", name="领航员")["status"] == "accepted"
        rescue = next(item for item in entities.get_definitions("bt_def") if item["id"] == RESCUE)
        with platform.store.transaction():
            rescue["nodes"] = [parallel]
            entities.put_definition("bt_def", RESCUE, rescue)
        intent = BehaviorIntent("hero", f"behavior:{RESCUE}", {}, {}, "Start rescue.", 1.0, "legacy-parallel")
        assert platform.submit_behavior(intent)["status"] == "rejected"
        assert "behavior.started" not in platform.store.repositories.events.event_type_counts()
        # A reference expanding into concurrent waits still finishes at the longest duration.
        with platform.store.transaction():
            entities.put_definition("bt_def", "delayed", {"id": "delayed", "nodes": [
                {"type": "parallel", "children": [wait, {"type": "wait", "minutes": 3}]},
            ]})
        intent = BehaviorIntent("hero", f"behavior:{RESCUE}", {}, {}, "Start rescue.", 1.0, "valid-parallel")
        result = platform.submit_behavior(intent)
        assert result["status"] == "accepted" and result["remaining_minutes"] == 3, result
        for _ in range(3):
            platform.step()
        assert platform.store.repositories.events.event_type_counts()["behavior.completed"] == 1


def test_mediation_condition_uses_recorded_dialogue(tmp_path: Path) -> None:
    village = WORLD.with_name("starter_village")
    with closing(Platform(create_instance(village, "flow-mediation", tmp_path))) as platform:
        entities = platform.store.repositories.entities
        with platform.store.transaction():
            listener = entities.get_agent("lin_bo")
            listener["initial_state"]["location_id"] = "inn"
            entities.update_agent("lin_bo", listener)
            entities.put_relationship("yu_ge", "lin_bo", "conflict", {"relation_strength": -0.5})
        execution_id = "mediation"
        result = platform.submit_behavior(BehaviorIntent(
            actor_id="yu_ge", behavior_ref="behavior:mediate_conflict", target={"name": "林泊"},
            parameters={"topic": "Grain allocation", "message": "Let's agree on the next delivery."},
            reason="Discuss the dispute.", confidence=1.0, intent_id=execution_id,
        ))
        assert result["status"] == "accepted", result
        for _ in range(result["remaining_minutes"]):
            platform.step()
        events = platform.store.repositories.events
        outcome, _ = events.behavior_outcome_evidence("yu_ge", execution_id)
        assert outcome["event_type"] == "behavior.completed"
        assert events.event_type_counts()["conflict.mediation_completed"] == 1


def test_definition_proposal_rejects_non_executable_control_nodes(tmp_path: Path) -> None:
    with closing(Platform(create_instance(WORLD, "flow-proposal", tmp_path))) as platform:
        report = platform.submit_definition_proposal("god", {
            "definition_type": "behavior", "name": "Broken watch", "definition": {
                "deferred": True, "nodes": [
                    {"type": "wait", "minutes": -1},
                    {"type": "condition", "expression": "conflict.resolved"},
                ],
            },
        })
        assert report["status"] == "rejected"
        result = json.dumps(report)
        assert "positive integer minutes" in result
        assert "unsupported condition path" in result


def test_selector_branch_still_requires_authorization_metadata(tmp_path: Path) -> None:
    with closing(Platform(create_instance(WORLD, "flow-authority", tmp_path))) as platform:
        action = flow_action("open_access", "foundry_rep", parameters={"based_on_event": "string"})
        with platform.store.transaction():
            platform.store.repositories.entities.put_definition("action_def", action["id"], action)
        report = platform.submit_definition_proposal("god", {
            "definition_type": "behavior", "name": "Choose how to ask", "definition": {
                "deferred": True, "nodes": [{"type": "selector", "children": [
                    {"type": "wait", "minutes": 1},
                    {"type": "action", "action_id": action["id"], "parameters": {"based_on_event": "context.denial"}},
                ]}],
            },
        })
        assert report["status"] == "rejected"
        assert "requires authorization_operation" in json.dumps(report)
