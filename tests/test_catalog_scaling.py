"""Bounds for a world that keeps creating new content.

The catalog trim keeps one character's prompt bounded, the query tool keeps
retired definitions discoverable, the Director stops at a configured cell cap,
and definition validation rejects a reskinned duplicate.
"""

from __future__ import annotations

import json
from pathlib import Path

from sim2.agents.tool_executor import AgentToolExecutor
from sim2.bootstrap.service import create_instance
from sim2.evolution.config import normalize_evolution_config
from sim2.platform.runtime import Platform
from sim2.prompting.renderer import render_context
from sim2.world.growth import build_growth_ledger
from sim2.world.lifecycle import trim_unlocked_behaviors, unlocked_behavior_limit

WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"


def _platform(tmp_path: Path, name: str) -> Platform:
    return Platform(create_instance(WORLD, name, tmp_path / "runtime"))


def test_trim_unlocked_behaviors_keeps_active_and_newest() -> None:
    agent = {
        "unlocked_bt_ids": ["old_1", "old_2", "active", "new_1", "new_2"],
        "initial_state": {"active_behavior_id": "active"},
    }
    trimmed = trim_unlocked_behaviors(agent, 3)
    assert trimmed == ["active", "new_1", "new_2"]
    assert trim_unlocked_behaviors(agent, 0) == agent["unlocked_bt_ids"]


def test_unlocked_behavior_limit_reads_the_decision_policy() -> None:
    assert unlocked_behavior_limit({}) == 24
    assert unlocked_behavior_limit({"decision_policy": {"max_unlocked_behaviors": 8}}) == 8
    assert unlocked_behavior_limit({"decision_policy": {"max_unlocked_behaviors": 0}}) == 0
    assert unlocked_behavior_limit({"decision_policy": {"max_unlocked_behaviors": "bad"}}) == 24


def test_generic_verb_is_preferred_over_a_new_specific_action(tmp_path: Path) -> None:
    platform = _platform(tmp_path, "generic-verb")
    try:
        generic = {
            "definition_type": "action",
            "name": "Transfer Stored Resource",
            "definition": {
                "summary": "Move one unit of a visible shared resource.",
                "parameters": {"resource_id": "string"},
                "duration": {"minutes": 1},
                "effects": [{
                    "type": "resource_withdrawal",
                    "resource_id": "context.resource_id",
                    "amount": 1,
                    "scope": "resource_withdrawal",
                }],
                "success": {"condition": "actor.alive", "result": "resource_moved"},
                "failure": {"condition": "actor.incapacitated", "result": "move_failed"},
            },
            "followup_behavior": {
                "name": "Transfer Stored Resource Behavior",
                "assign_to": ["梅岚"],
                "definition": {
                    "entry_conditions": ["actor.alive"],
                    "estimated_minutes": 1,
                    "agent_parameters": {"resource_id": {"type": "string", "required": True, "max_length": 120}},
                    "nodes": [{
                        "type": "action",
                        "action_id": "Transfer Stored Resource",
                        "parameters": {"resource_id": "context.resource_id"},
                    }],
                    "success_results": ["resource_moved"],
                    "failure_results": ["move_failed"],
                },
            },
        }
        first = platform.submit_definition_proposal("god", generic)
        assert first["status"] == "accepted", first

        specific = {
            "definition_type": "action",
            "name": "Withdraw Cistern Water",
            "definition": {
                "summary": "Withdraw water from the cistern.",
                "parameters": {},
                "duration": {"minutes": 1},
                "effects": [{
                    "type": "resource_withdrawal",
                    "resource_id": "cistern_water",
                    "amount": 1,
                    "scope": "resource_withdrawal",
                }],
                "success": {"condition": "actor.alive", "result": "water_moved"},
                "failure": {"condition": "actor.incapacitated", "result": "move_failed"},
            },
        }
        rejected = platform.submit_definition_proposal("god", specific)
        assert rejected["status"] == "rejected", rejected
        assert any("generic transfer_resource Action already exists" in str(item) for item in rejected.get("errors") or []), rejected

        view = platform.decision_view("mei_lan")
        entry = next(item for item in view["executable_behaviors"] if item.get("ref") == "behavior:transfer_stored_resource_behavior")
        names = {item["name"] for item in entry["parameter_options"]["resource_id"]}
        assert "蓄水" in names
    finally:
        platform.close()


def test_assigning_behaviors_keeps_the_catalog_cap(tmp_path: Path) -> None:
    platform = _platform(tmp_path, "catalog-trim")
    try:
        with platform.store.transaction():
            rules = dict(platform.store.repositories.metadata.get("rules", {}) or {})
            policy = dict(rules.get("decision_policy", {}) or {})
            policy["max_unlocked_behaviors"] = 1
            rules["decision_policy"] = policy
            platform.store.repositories.metadata.set("rules", rules)

        def proposal(name: str, delta: int) -> dict:
            return {
                "definition_type": "action",
                "name": name,
                "definition": {
                    "summary": f"Catalog trim action {name}.",
                    "parameters": {},
                    "duration": {"minutes": 5 + delta},
                    "effects": [{"type": "need_delta", "need": "purpose", "delta": delta}],
                    "success": {"condition": "actor.alive", "result": f"trim_{delta}_done"},
                    "failure": {"condition": "actor.incapacitated", "result": "trim_failed"},
                },
                "followup_behavior": {
                    "name": f"{name} Behavior",
                    "assign_to": ["梅岚"],
                    "definition": {
                        "entry_conditions": ["actor.alive"],
                        "estimated_minutes": 5 + delta,
                        "nodes": [{"type": "action", "action_id": name, "parameters": {}}],
                        "success_results": [f"trim_{delta}_done"],
                        "failure_results": ["trim_failed"],
                    },
                },
            }

        first = platform.submit_definition_proposal("god", proposal("Catalog Trim One", 1))
        second = platform.submit_definition_proposal("god", proposal("Catalog Trim Two", 2))
        assert first["status"] == "accepted", first
        assert second["status"] == "accepted", second
        agent = platform.store.repositories.entities.get_agent("mei_lan")
        assert len(agent.get("unlocked_bt_ids") or []) <= 1
        assert "catalog_trim_two_behavior" in (agent.get("unlocked_bt_ids") or [])
    finally:
        platform.close()


def test_definition_duplicate_shape_is_rejected(tmp_path: Path) -> None:
    platform = _platform(tmp_path, "definition-dedup")
    try:
        payload = {
            "definition_type": "action",
            "name": "Catalog Dedup Alpha",
            "definition": {
                "summary": "Raise purpose once while the character is alive.",
                "parameters": {},
                "duration": {"minutes": 5},
                "effects": [{"type": "need_delta", "need": "purpose", "delta": 1}],
                "success": {"condition": "actor.alive", "result": "dedup_done"},
                "failure": {"condition": "actor.incapacitated", "result": "dedup_failed"},
            },
            "followup_behavior": {
                "name": "Catalog Dedup Alpha Behavior",
                "assign_to": ["梅岚"],
                "definition": {
                    "entry_conditions": ["actor.alive"],
                    "estimated_minutes": 5,
                    "nodes": [{"type": "action", "action_id": "Catalog Dedup Alpha", "parameters": {}}],
                    "success_results": ["dedup_done"],
                    "failure_results": ["dedup_failed"],
                },
            },
        }
        first = platform.submit_definition_proposal("god", payload)
        assert first["status"] == "accepted", first

        reskinned = json.loads(json.dumps(payload))
        reskinned["name"] = "Catalog Dedup Beta"
        duplicate = platform.submit_definition_proposal("god", reskinned)
        assert duplicate["status"] == "rejected", duplicate
        assert any("duplicates existing action" in str(item) for item in duplicate.get("errors") or []), duplicate
    finally:
        platform.close()


def test_behavior_query_finds_a_retired_definition(tmp_path: Path) -> None:
    platform = _platform(tmp_path, "catalog-query")
    try:
        template = next(
            item for item in platform.store.repositories.entities.get_definitions("bt_def")
            if item.get("id") == "inspect_cistern"
        )
        retired = dict(template)
        retired["id"] = "retired_catalog_probe"
        retired["agent_label"] = "Retired Catalog Probe"
        retired["name"] = "Retired Catalog Probe"
        with platform.store.transaction():
            platform.store.repositories.entities.put_definition("bt_def", retired["id"], retired)

        executor = AgentToolExecutor(platform, role="agent_decision", actor_id="mei_lan")
        found = executor.execute("query_behavior_catalog", {"query": "retired", "limit": 5})
        assert found["status"] == "ok", found
        assert found["matched"] == 1
        assert found["behaviors"][0]["choice"] == "Retired Catalog Probe"
        assert found["behaviors"][0]["offered"] is False

        offered = executor.execute("query_behavior_catalog", {"query": "Inspect Cistern"})
        assert offered["status"] == "ok"
        assert any(item["offered"] for item in offered["behaviors"]), offered
    finally:
        platform.close()


def test_prompt_keeps_five_full_choices_and_compacts_the_rest() -> None:
    view = {
        "drives": [{"id": "purpose", "urgency": 0.5}],
        "behavior_full_choices": 5,
        "executable_behaviors": [
            {
                "choice": f"Choice {index}",
                "time": "1 minute",
                "when": "actor.alive",
                "drive_tags": ["purpose"],
            }
            for index in range(8)
        ],
    }
    prompt = render_context(view, "Choose one behavior.")

    assert prompt.count("    <behavior>") == 5
    assert "<more_behaviors>" in prompt
    assert "Choice 7" in prompt
    assert "query_behavior_catalog" in prompt


def test_director_saturation_stops_new_plans(tmp_path: Path) -> None:
    platform = _platform(tmp_path, "director-saturation")
    try:
        place_count = int(build_growth_ledger(platform.store)["cells"]["place"]["count"])
        with platform.store.transaction():
            platform.store.repositories.metadata.set("evolution", normalize_evolution_config({
                "director": {
                    "cadence": "1m",
                    "window": "10m",
                    "targets": ["place"],
                    "max_cell_units": place_count,
                },
            }))
        with platform.store.transaction():
            plan = platform._services["director"].tick(1)
        assert plan is None
        saturated = platform.store.repositories.metadata.get("season_plan")
        assert saturated["status"] == "saturated"
        assert not [item for item in platform.ready_evolution_tasks() if item["task_type"] == "god_structural"]

        with platform.store.transaction():
            platform.store.repositories.metadata.set("evolution", normalize_evolution_config({
                "director": {
                    "cadence": "1m",
                    "window": "10m",
                    "targets": ["place"],
                    "max_cell_units": place_count + 1,
                },
            }))
        with platform.store.transaction():
            plan = platform._services["director"].tick(2)
        assert plan and plan["status"] == "active"
        assert plan["target_cell"] == "place"
    finally:
        platform.close()
