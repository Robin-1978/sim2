from __future__ import annotations

from contextlib import closing
from pathlib import Path
from shutil import copytree

import yaml

from sim2.api.service import ControlService
from sim2.bootstrap.service import create_instance, upgrade_instance, validate_config
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform
from sim2.simulation.runner import SimulationRunner


WINDWARD = Path(__file__).parents[1] / "worlds" / "windward_skyfall"


def write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_scheduled_routine_projects_the_catalog_when_a_rule_governs_the_root(tmp_path: Path, monkeypatch) -> None:
    world = WINDWARD.parent / "gate_cities_sandbox"
    with closing(Platform(create_instance(world, "scheduled-routine-policy", tmp_path))) as platform:
        actor = "porter_bai"
        routine = platform.store.repositories.entities.get_agent(actor)["routine_bt_id"]
        build_view = platform.view.build_decision_view
        calls = []

        def track_view(agent_id, **kwargs):
            calls.append(agent_id)
            return build_view(agent_id, **kwargs)

        monkeypatch.setattr(platform.view, "build_decision_view", track_view)
        result = platform.submit_world_change("god", {
            "change_type": "rule", "summary": "Pause the named shift while the stock is reviewed.",
            "rule": {
                "id": "scheduled_shift_review", "condition": "world.mine_ore < 100",
                "effect": {"type": "behavior_constraint", "message": "Stock review in progress."},
                "behavior_ids": [routine],
            },
        })
        assert result["status"] == "accepted", result
        intent = BehaviorIntent(actor, f"behavior:{routine}", {}, {}, "Scheduled resident.", 1.0, "scheduled-review")
        result = platform.submit_behavior(intent, configured_routine=True)
        assert result["status"] == "rejected", result
        assert calls == [actor]
        assert not platform.store.repositories.entities.get_agent(actor)["initial_state"].get("active_bt_instance")


def test_resident_routine_recovers_works_waits_and_returns_without_model_calls(tmp_path: Path) -> None:
    package = tmp_path / "world"
    copytree(WINDWARD, package)
    agents_path = package / "agents.yaml"
    agents = yaml.safe_load(agents_path.read_text())
    for agent in agents["agents"]:
        agent.pop("controller_fallback", None)
    porter = agents["agents"][0]
    porter.update(controller="bt", routine_bt_id="porter_routine", initial_bt_ids=["porter_routine", "rest_at_camp"])
    porter["bt_priority"] = ["rest_at_camp"]
    porter["needs"]["hydration"] = 35
    porter["initial_state"]["stamina"] = 45
    write_yaml(agents_path, agents)

    world_path = package / "world_state.yaml"
    world = yaml.safe_load(world_path.read_text())
    world["world_state"]["properties"]["work_gate_closed"] = 1
    write_yaml(world_path, world)
    actions_path = package / "actions.yaml"
    actions = yaml.safe_load(actions_path.read_text())
    for action in actions["actions"]:
        if action["id"] in {"rest_at_camp", "gather_resource", "travel_to"}:
            action["duration"]["minutes"] = 2
    write_yaml(actions_path, actions)

    trees_path = package / "behavior_trees.yaml"
    trees = yaml.safe_load(trees_path.read_text())
    trees["behavior_trees"].extend([
        {
            "id": "forest_shift",
            "summary": "Walk to the woods and collect supplies.",
            "entry_conditions": ["world.work_gate_closed == 0", "actor.inventory.wood < 2"],
            "nodes": [
                {"type": "behavior", "behavior_id": "travel_windworn"},
                {"type": "behavior", "behavior_id": "gather_wood"},
            ],
        },
        {
            "id": "porter_routine", "summary": "Work, recover, or take shelter.",
            "entry_conditions": ["actor.alive"],
            "nodes": [{"type": "selector", "children": [
                {"type": "sequence", "children": [
                    {"type": "condition", "expression": "world.work_gate_closed == 1 and actor.state.location_id != 'lantern_cape'"},
                    {"type": "behavior", "behavior_id": "travel_lantern_cape"},
                ]},
                {"type": "sequence", "children": [
                    {"type": "condition", "expression": "actor.need.hydration < 50"},
                    {"type": "behavior", "behavior_id": "drink_at_camp"},
                ]},
                {"type": "sequence", "children": [
                    {"type": "condition", "expression": "actor.state.stamina < 60"},
                    {"type": "behavior", "behavior_id": "rest_at_camp"},
                ]},
                {"type": "behavior", "behavior_id": "forest_shift"},
                {"type": "wait", "minutes": 2},
            ]}],
        },
    ])
    write_yaml(trees_path, trees)

    db = create_instance(package, "bt-resident-routine", tmp_path / "runtime")
    with closing(Platform(db)) as platform:
        store = platform.store
        report = platform.run(12, providers={})
        completed = store.repositories.events.recent_events_by_types(["action.completed"], limit=20)
        actions_done = [event["action_id"] for event in reversed(completed) if event["actor_id"] == "alan"]
        assert actions_done[:2] == ["drink_at_camp", "rest_at_camp"], (actions_done, report.as_dict())
        assert "wait" in actions_done
        assert "travel_to" not in actions_done
        with store.transaction():
            properties = store.repositories.metadata.get("world_properties")
            store.repositories.metadata.set("world_properties", {**properties, "work_gate_closed": 0})

    # Reload while the actor is on the routine and observe the configured guard
    # reopen work, then shelter again after the public route becomes unsafe.
    with closing(Platform(db)) as platform:
        store = platform.store
        report = platform.run(16, providers={})
        actor = store.repositories.entities.get_agent("alan")
        assert actor["initial_state"]["location_id"] == "windworn_woods", report.as_dict()
        assert actor["inventory"]["wood"] == 2
        with store.transaction():
            properties = store.repositories.metadata.get("world_properties")
            store.repositories.metadata.set("world_properties", {**properties, "work_gate_closed": 1})
        platform.run(9, providers={})
        assert store.repositories.entities.get_agent("alan")["initial_state"]["location_id"] == "lantern_cape"
        assert store.connection.execute("SELECT COUNT(*) FROM llm_call").fetchone()[0] == 0

    porter["routine_bt_id"] = "forest_shift"
    write_yaml(agents_path, agents)
    assert any("routine_bt_id must be included in initial_bt_ids" in error for error in validate_config(package)["errors"])


def test_bt_residents_record_conversations_without_model_replies_or_reflections(tmp_path: Path) -> None:
    class NoCalls:
        def complete_json(self, **kwargs):
            raise AssertionError("A BT actor must not reach the language model")

    db = create_instance(WINDWARD, "bt-model-boundary", tmp_path)
    with closing(ControlService(db)) as service:
        platform = service.platform
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        proposed = platform.submit_entity_proposal("god", {
            "entity_type": "agent", "name": "Porter Lin", "location_id": "lantern_cape",
            "state": {"role": "porter", "controller": "bt", "ultimate_goal": "Maintain the camp",
                      "initial_bt_ids": ["rest_at_camp"], "routine_bt_id": "在营地休息"},
        })
        assert proposed["status"] == "accepted", proposed
        actor_id = proposed["entity_id"]
        store = platform.store
        assert store.repositories.entities.get_agent(actor_id)["routine_bt_id"] == "rest_at_camp"
        message = service.dialogue("hero", "Porter Lin", "route", "The woods route is closed.")
        assert message["status"] == "accepted" and message["reply"]["status"] == "recorded"
        report = SimulationRunner(platform, {"agent_decision": NoCalls()}).resolve_pending_interactions()
        assert not report.stop_reason and report.decisions == 0
        with store.transaction():
            store.repositories.evolution.put_evolution_task(
                "explicit-worker-reflection", "agent_reflection", actor_id, "inbound_news", 0, 0, status="ready",
            )
        report = platform.run(1, providers={"agent_reflection": NoCalls()})
        assert report.reflections == 0
        assert not any(task["task_id"] == "explicit-worker-reflection" for task in platform.ready_evolution_tasks())
        assert store.connection.execute("SELECT COUNT(*) FROM llm_call").fetchone()[0] == 0


def test_world_upgrade_can_set_and_clear_the_resident_routine(tmp_path: Path) -> None:
    db = create_instance(WINDWARD, "bt-routine-upgrade", tmp_path / "runtime")
    package = tmp_path / "world"
    copytree(WINDWARD, package)
    path = package / "agents.yaml"
    data = yaml.safe_load(path.read_text())
    porter = data["agents"][0]
    porter["routine_bt_id"] = "rest_at_camp"
    write_yaml(path, data)
    upgrade_instance(package, db)
    with closing(Platform(db)) as platform:
        assert platform.store.repositories.entities.get_agent("alan")["routine_bt_id"] == "rest_at_camp"
    porter["routine_bt_id"] = None
    write_yaml(path, data)
    upgrade_instance(package, db)
    with closing(Platform(db)) as platform:
        assert platform.store.repositories.entities.get_agent("alan")["routine_bt_id"] is None
