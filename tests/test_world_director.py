"""The deterministic Director chooses structural targets; it never writes content."""

from __future__ import annotations

from pathlib import Path
from shutil import copytree

import pytest
import yaml

from sim2.agents.engine import AgentEngine
from sim2.bootstrap.service import create_instance
from sim2.evolution.config import normalize_evolution_config
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform
from sim2.prompting.renderer import render_context
from sim2.world.growth import build_growth_ledger

WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"


def _town(tmp_path: Path, name: str) -> Platform:
    return Platform(create_instance(WORLD, name, tmp_path / "runtime"))


def _director_config(platform: Platform, *, targets: list[str], cadence: str = "1m", window: str = "10m") -> None:
    with platform.store.transaction():
        platform.store.repositories.metadata.set("evolution", normalize_evolution_config({
            "director": {"cadence": cadence, "window": window, "targets": targets},
        }))


def test_director_selects_a_target_and_queues_a_structural_task(tmp_path: Path) -> None:
    platform = _town(tmp_path, "director-plan")
    try:
        _director_config(platform, targets=["place"])
        before = build_growth_ledger(platform.store)["cells"]["place"]["count"]
        with platform.store.transaction():
            plan = platform._services["director"].tick(1)
        assert plan and plan["status"] == "active"
        assert plan["target_cell"] == "place"
        assert plan["baseline"] == before
        assert plan["reason"].startswith("place=")
        tasks = [item for item in platform.ready_evolution_tasks() if item["task_type"] == "god_structural"]
        assert tasks, platform.ready_evolution_tasks()
    finally:
        platform.close()


def test_director_keeps_the_plan_open_until_a_new_place_is_used(tmp_path: Path) -> None:
    platform = _town(tmp_path, "director-advance")
    try:
        _director_config(platform, targets=["place"])
        with platform.store.transaction():
            first = platform._services["director"].tick(1)
        assert first and first["status"] == "active"
        place = platform.submit_entity_proposal("god", {
            "entity_type": "location",
            "name": "Director Test Place",
            "description": "A place created after the Director asked for one.",
            "state": {"routes": ["lantern_square"], "route_distances": {"lantern_square": 2}},
        })
        assert place["status"] == "accepted", place
        with platform.store.transaction():
            second = platform._services["director"].tick(2)
        assert second and second["status"] == "active"
        assert second["plan_id"] == first["plan_id"]
        assert second["baseline"] == first["baseline"]
        ledger = build_growth_ledger(platform.store)
        assert ledger["cells"]["place"]["count"] == first["baseline"] + 1
        assert any("Director Test Place" in entry for entry in ledger["dead_content"])
        assert not platform.store.repositories.metadata.get("last_completed_season_plan")
    finally:
        platform.close()


def test_director_does_not_count_a_stock_reservation_until_the_behavior_completes(tmp_path: Path) -> None:
    platform = _town(tmp_path, "director-resource-proof")
    try:
        _director_config(platform, targets=["resource"], window="6h")
        platform.step()
        proposal = platform.submit_entity_proposal("god", {
            "entity_type": "resource",
            "name": "Director Test Reeds",
            "description": "A new batch of reeds on the old road.",
            "location_id": "old_road",
            "state": {"quantity": 0},
        })
        assert proposal["status"] == "accepted", proposal
        resource_id = proposal["entity_id"]
        entities = platform.store.repositories.entities
        with platform.store.transaction():
            stock = entities.get_entity(resource_id)
            stock["initial_state"]["quantity"] = 6
            entities.put_entity(resource_id, "resource", stock["name"], {"initial_state": stock["initial_state"]})
            actor = entities.get_agent("luo_shan")
            actor["initial_state"]["location_id"] = "old_road"
            entities.update_agent("luo_shan", actor)
            gathering = next(item for item in entities.get_definitions("bt_def") if item["id"] == "gather_reeds")
            gathering["nodes"] = [gathering["nodes"][1]]
            gathering["nodes"][0]["parameters"]["resource_tag"] = resource_id
            entities.put_definition("bt_def", "gather_reeds", gathering)

        for intent_id in ("cancelled-gathering", "completed-gathering"):
            submitted = platform.submit_behavior(BehaviorIntent(
                actor_id="luo_shan", behavior_ref="behavior:gather_reeds", target={}, parameters={},
                reason="Gather reeds from the new stock.", confidence=1.0, intent_id=intent_id,
            ))
            assert submitted["status"] == "accepted", submitted
            platform.step()
            assert entities.get_entity(resource_id)["initial_state"]["quantity"] == 4
            assert not platform.store.repositories.metadata.get("last_completed_season_plan")
            if intent_id == "cancelled-gathering":
                platform.interrupt_behavior("luo_shan", "abandon the collection", hard=True)
                assert entities.get_entity(resource_id)["initial_state"]["quantity"] == 6

        for _ in range(120):
            platform.step()
        plan = platform.store.repositories.metadata.get("last_completed_season_plan")
        event, = platform.store.repositories.events.events_by_ids([plan["outcome_event_id"]])
        assert event["event_type"] == "behavior.completed"
        assert event["execution_id"] == "completed-gathering"
    finally:
        platform.close()


def test_social_plan_requires_a_character_to_change_the_new_bond(tmp_path: Path) -> None:
    platform = _town(tmp_path, "director-bond")
    try:
        _director_config(platform, targets=["social"])
        platform.step()
        meta = platform.store.repositories.metadata
        plan = meta.get("season_plan")
        with platform.store.transaction():
            rules = meta.get("rules")
            rules["relationship_effects"] = {"dialogue.completed": {"relation": "supply_partner", "delta": 0.25}}
            meta.set("rules", rules)
        bond = {"from": "mei_lan", "to": "luo_shan", "type": "supply_partner", "relation_strength": 1.0}
        change = {"change_type": "relationship", "summary": "Keep the cistern together.",
                  "relationship": bond, "evaluation_window_minutes": 1}
        assert platform.submit_world_change("god", change)["status"] == "accepted"
        platform.step()
        assert platform.store.repositories.evolution.list_world_proposals()[0]["status"] == "observed"
        assert meta.get("season_plan")["plan_id"] == plan["plan_id"]
        # The reverse direction is a different bond. The forward one is already at its maximum.
        for source, target in [("luo_shan", "mei_lan"), ("mei_lan", "luo_shan")]:
            assert platform.submit_dialogue(source, target, "Cistern", "Let us check the water.")["status"] == "accepted"
        platform.step()
        assert not meta.get("last_completed_season_plan")
        change["relationship"] = {**bond, "relation_strength": 0.4}
        assert platform.submit_world_change("god", change)["status"] == "accepted"
        assert platform.submit_dialogue("mei_lan", "luo_shan", "Cistern", "I checked the water.")["status"] == "accepted"
        platform.step()
        closed = meta.get("last_completed_season_plan")
        assert closed["plan_id"] == plan["plan_id"]
        event, = platform.store.repositories.events.events_by_ids([closed["outcome_event_id"]])
        assert (event["event_type"], event["actor_id"], event["target_id"], event["actual_delta"]) == (
            "relationship.changed", "mei_lan", "luo_shan", 0.25,
        )
    finally:
        platform.close()


def test_character_use_of_a_new_faction_closes_its_social_plan(tmp_path: Path) -> None:
    platform = _town(tmp_path, "director-faction")
    try:
        _director_config(platform, targets=["social"], window="3h")
        platform.step()
        faction = platform.submit_entity_proposal("god", {
            "entity_type": "faction", "name": "Cistern keepers", "description": "Residents who keep the cistern open.",
            "state": {"goals": ["Keep the cistern open"], "norms": ["Record each inspection"], "strategy": "cooperate"},
        })
        assert faction["status"] == "accepted", faction
        entities = platform.store.repositories.entities
        with platform.store.transaction():
            inspect = next(item for item in entities.get_definitions("bt_def") if item["id"] == "inspect_cistern")
            inspect["outcome_effects"] = [{"type": "entity_state_set", "entity_id": "Cistern keepers",
                                          "state": "last_inspection", "value": "complete"}]
            entities.put_definition("bt_def", inspect["id"], inspect)
        accepted = platform.submit_behavior(BehaviorIntent(
            "mei_lan", "behavior:inspect_cistern", {}, {}, "Inspect for the keepers.", 1.0, "keepers-inspection",
        ))
        assert accepted["status"] == "accepted", accepted
        platform.step()
        assert not platform.store.repositories.metadata.get("last_completed_season_plan")
        for _ in range(accepted["remaining_minutes"]):
            platform.step()
        closed = platform.store.repositories.metadata.get("last_completed_season_plan")
        event, = platform.store.repositories.events.events_by_ids([closed["outcome_event_id"]])
        assert (event["event_type"], event["entity_id"], event["after"]) == (
            "entity.state_changed", faction["entity_id"], "complete",
        )
    finally:
        platform.close()


def test_active_season_plan_is_visible_to_god_and_story(tmp_path: Path) -> None:
    platform = _town(tmp_path, "director-prompt")
    try:
        with platform.store.transaction():
            platform.store.repositories.metadata.set("season_plan", {
                "plan_id": "plan:1:social",
                "target_cell": "social",
                "baseline": 0,
                "created_minute": 1,
                "expires_minute": 100,
                "reason": "place=4, verb=6, social=0, rule=2",
                "status": "active",
            })
        god_prompt = render_context(platform.view.build_decision_view("god", role="god"), "Review structural growth.")
        story_prompt = render_context(platform.view.build_decision_view("story", role="story"), "Continue the story.")
        for prompt in (god_prompt, story_prompt):
            assert "<season_plan>" in prompt
            assert "target cell: social" in prompt
        assert "Fill this cell with one reachable, executable growth unit" in god_prompt
        assert "do not claim a structural change" in story_prompt
    finally:
        platform.close()


@pytest.mark.parametrize("reviewer", [None, "story", "god"])
def test_world_package_owns_growth_evidence_and_optional_followup(tmp_path, reviewer) -> None:
    package = tmp_path / "town"
    copytree(WORLD, package)
    director = {"targets": ["social"], "cadence": "1m", "window": "2h", "evidence": {"social": [{
        "registered": [{"event": "entity.created", "key": ["name"], "label": "name",
                        "when": "event.entity_type == 'faction'"}],
        "used": [{"event": "audit.signed", "key": ["council"], "when": "event.certified and event.quantity >= 2"}],
    }]}}
    config = {"director": director}
    if reviewer:
        director["followups"] = {"social": {"after": "3m", "for": reviewer, "task": "cistern_review"}}
        config["contracts"] = {"cistern_review": {"tools": ["wait"], "result": "Read the signed cistern report."}}
    (package / "evolution.yaml").write_text(yaml.safe_dump(config))
    path = package / "behavior_trees.yaml"
    trees = yaml.safe_load(path.read_text())
    inspect = next(item for item in trees["behavior_trees"] if item["id"] == "inspect_cistern")
    inspect.setdefault("outcome_events", []).append({
        "event_type": "audit.signed", "data": {"council": "Cistern council", "certified": True, "quantity": 2},
    })
    path.write_text(yaml.safe_dump(trees, allow_unicode=True))
    platform = Platform(create_instance(package, "custom-growth", tmp_path / "runtime"))
    try:
        platform.step()
        repo = platform.store.repositories
        initial = repo.metadata.get("season_plan")
        new = platform.submit_entity_proposal("god", {
            "entity_type": "faction", "name": "Cistern council", "description": "Keeps signed water reports.",
            "state": {"goals": ["keep reports"], "norms": ["sign reports"], "strategy": "cooperate"},
        })
        assert new["status"] == "accepted", new
        with platform.store.transaction():
            for actor, council, quantity in [(None, "Cistern council", 2), ("mei_lan", "Other", 2), ("mei_lan", "Cistern council", 1)]:
                repo.events.append_event("audit.signed", actor, 1, {"council": council, "quantity": quantity, "certified": True})
        platform.step()
        assert not any(t["task_type"] == "cistern_review" for t in platform.ready_evolution_tasks())
        for _ in range(4):
            platform.step()
        assert not repo.metadata.get("last_completed_season_plan")
        tasks = [t for t in platform.ready_evolution_tasks() if t["task_type"] == "cistern_review"]
        if reviewer:
            task, = tasks
            assert (task["role"], task["due_minute"], task["source_event_id"]) == (reviewer, 4, new["event_id"])
            assert task["season_plan"]["use_review_subject"] == "Cistern council"
            class WaitForUse:
                def complete_json(self, **kwargs):
                    return {"tool_call": {"name": "wait", "arguments": {"reason": "Waiting for the cistern report."}}}

            turn = AgentEngine(platform, WaitForUse()).run(role=reviewer, actor_id=reviewer, task_type=task["task_type"],
                                                           source_event_id=task["source_event_id"])
            assert turn.result["status"] == "wait"
            assert "Read the signed cistern report. Registered unit: Cistern council" in turn.prompt_history[0]
            assert [tool["name"] for tool in turn.initial_context["tools"]] == ["wait"]
        else:
            assert not tasks
        accepted = platform.submit_behavior(BehaviorIntent("mei_lan", "behavior:inspect_cistern", {}, {}, "Sign the report.", 1.0, "visit"))
        assert accepted["status"] == "accepted", accepted
        for _ in range(accepted["remaining_minutes"] + 1):
            platform.step()
        closed = repo.metadata.get("last_completed_season_plan")
        assert closed["plan_id"] == initial["plan_id"]
        event, = repo.events.events_by_ids([closed["outcome_event_id"]])
        assert (event["event_type"], event["execution_id"]) == ("audit.signed", accepted["execution_id"])
        if reviewer:
            platform.run(1, providers={})
            assert task["task_id"] not in {item["task_id"] for item in platform.ready_evolution_tasks()}
    finally:
        platform.close()


def test_director_config_is_validated() -> None:
    compiled = normalize_evolution_config({"director": {"cadence": "1d", "window": "3d", "targets": ["place", "rule"]}})
    assert compiled["director"]["cadence_minutes"] == 1440
    assert compiled["director"]["window_minutes"] == 4320
    assert compiled["director"]["targets"] == ["place", "rule"]
    assert compiled["director"]["policy"] == "deterministic"
    assert compiled["director"]["max_cell_units"] == 0
    assert compiled["director"]["saturation"] == "idle"
    assert normalize_evolution_config({"director": {"policy": "llm"}})["director"]["policy"] == "llm"
    assert normalize_evolution_config({"director": {"max_cell_units": 8}})["director"]["max_cell_units"] == 8
    with pytest.raises(ValueError, match="max_cell_units must be a non-negative integer"):
        normalize_evolution_config({"director": {"max_cell_units": -1}})
    with pytest.raises(ValueError, match="saturation must be idle"):
        normalize_evolution_config({"director": {"saturation": "delete"}})
    with pytest.raises(ValueError, match="policy must be deterministic or llm"):
        normalize_evolution_config({"director": {"policy": "auto"}})
    with pytest.raises(ValueError, match="unknown targets"):
        normalize_evolution_config({"director": {"targets": ["economy"]}})
    with pytest.raises(ValueError, match="window must be at least one cadence"):
        normalize_evolution_config({"director": {"cadence": "2d", "window": "1d"}})

def test_world_change_dispatches_the_configured_story_wake(tmp_path: Path) -> None:
    platform = _town(tmp_path, "director-world-change-wake")
    try:
        with platform.store.transaction():
            platform.store.repositories.metadata.set("evolution", normalize_evolution_config({
                "wakes": [{
                    "id": "story_growth",
                    "on": "world.change.rule",
                    "for": "story",
                    "external": True,
                    "tools": ["propose_story_beat", "wait"],
                    "result": "Narrate the new rule.",
                }],
            }))
        result = platform.submit_world_change("god", {
            "change_type": "rule",
            "summary": "A test rule for the growth wake.",
            "rule": {
                "id": "director_test_rule",
                "condition": "event.recent.cistern.reviewed == true",
                "effect": "policy.director_test",
                "behavior_names": ["Inspect Cistern"],
                "wake": ["story"],
            },
        })
        assert result["status"] == "accepted", result
        tasks = [item for item in platform.ready_evolution_tasks() if item["role"] == "story"]
        assert any(item["task_type"] == "event_group:story_growth" for item in tasks), tasks
    finally:
        platform.close()


class _DirectorProvider:
    def __init__(self, target: str) -> None:
        self.target = target
        self.calls = 0
        self.system = ""
        self.context = ""
        self.tools = None

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.calls += 1
        self.system = system
        self.context = context
        self.tools = tools
        return {"target_cell": self.target, "reason": "The model chose the least developed cell."}


def test_llm_director_writes_the_same_plan_and_structural_task(tmp_path: Path) -> None:
    platform = _town(tmp_path, "director-llm")
    try:
        _director_config(platform, targets=["place", "resource"])
        provider = _DirectorProvider("resource")
        plan = platform._services["director"].plan_with_provider(provider, 1)
        assert plan and plan["status"] == "active"
        assert plan["target_cell"] == "resource"
        assert plan["reason"] == "The model chose the least developed cell."
        tasks = [item for item in platform.ready_evolution_tasks() if item["task_type"] == "god_structural"]
        assert tasks and tasks[0]["season_plan"]["target_cell"] == "resource"
        assert provider.calls == 1
        # The Director is a one-call policy chooser, not a ReAct agent.
        assert provider.tools is None
        assert "place=" in provider.context and "resource=" in provider.context
        assert len(provider.context) < 500 and len(provider.system) < 100
        assert "entity" not in provider.context.casefold()
    finally:
        platform.close()
