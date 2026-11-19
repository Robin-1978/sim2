"""Bounded structural context for a growing world (G-08)."""

from __future__ import annotations

import json
from pathlib import Path

from sim2.agents.tool_executor import AgentToolExecutor
from sim2.agent_view.tool_registry import ROLE_TOOLS, TOOL_DEFINITIONS
from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform
from sim2.prompting.renderer import render_context, render_context_with_metrics
from sim2.world.index import build_world_index, inspect_definition, query_world_index

WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
UNLIMITED = 10**7


def _context(action_count: int = 100) -> dict:
    actions = [
        {
            "kind": "action",
            "cell": "verb",
            "name": f"Indexed Action {index:03d}",
            "summary": "A representative registered Action.",
            "effects": ["world_resource_delta"],
            "parameters": ["location_id"],
        }
        for index in range(action_count)
    ]
    behaviors = [
        {
            "kind": "behavior",
            "cell": "verb",
            "name": f"Indexed Behavior {index:03d}",
            "summary": "A representative registered behavior.",
            "operations": [],
            "effects": [],
        }
        for index in range(action_count)
    ]
    return {
        "role": "god",
        "prompt_protocol": ["Platform owns validation and world time."],
        "platform_rules": ["Never invent an internal identifier."],
        "world_time": {"minute": 12, "label": "day 1, 00:12"},
        "world_history": [],
        "active_story_threads": [],
        "knowledge_gaps": [],
        "research_sources": [],
        "recent_history": [],
        "world_rules": [],
        "prompt_sections": [],
        "evolution_trigger": {"id": "director:growth:12", "purpose": "Grow the resource cell."},
        "season_plan": {"status": "active", "target_cell": "resource", "reason": "fill the empty cell", "expires_minute": 72},
        "world_growth_ledger": {"cells": {"place": {"count": 2}, "verb": {"count": 100}, "social": {"count": 3}, "rule": {"count": 1}, "resource": {"count": 1}}, "empty_cells": [], "dead_content": []},
        "world_index": {
            "target_cell": "resource",
            "counts": {"place": 2, "verb": 200, "social": 3, "rule": 1, "resource": 1},
            "totals": {"entities": 6, "actions": 100, "behaviors": 100, "rules": 1},
            "entities": [{"kind": "entity", "name": "Water Reserve", "summary": "Shared water."}],
            "actions": actions[:6],
            "behaviors": behaviors[:6],
            "rules": [{"kind": "rule", "name": "Water Access Rule", "summary": "Only the steward may approve."}],
        },
        "world_trends": {},
        "recent_world_changes": [],
        "world_proposals": [],
        "recent_agent_outcomes": [],
        "world_progress_candidates": [],
        "rule_condition_fields": [],
        "registered_actions": [{"name": f"Legacy Action {index:03d}"} for index in range(100)],
        "registered_behaviors": [{"name": f"Legacy Behavior {index:03d}"} for index in range(100)],
        "tools": [],
    }


def test_structural_prompt_indexes_a_large_catalog_instead_of_rendering_it() -> None:
    prompt, metrics = render_context_with_metrics(_context(), "Review structural growth.", max_chars=16000)

    assert "Indexed inventory:" in prompt
    assert "Water Reserve" in prompt
    assert "Water Access Rule" in prompt
    # Only the bounded target neighborhood is rendered; the other 94 entries
    # are reachable through query_world_index.
    assert "Indexed Action 000" in prompt
    assert "Indexed Action 010" not in prompt
    assert "Legacy Action 099" not in prompt
    assert "Legacy Behavior 099" not in prompt
    assert "query_world_index" in prompt
    assert "inspect_definition" in prompt
    assert len(prompt) <= 16000
    assert metrics["omitted_required"] == []


def test_staged_contract_replaces_the_full_free_form_contract() -> None:
    staged = _context()
    staged["evolution_trigger"] = {
        "id": "director:growth:12",
        "purpose": "Grow the resource cell.",
        "stages": [{"instruction": "Create the required resource.", "required_content": {"entities": ["Reserve"]}}],
    }
    compact_prompt = render_context(staged, "Review structural growth.", max_chars=UNLIMITED)
    assert "Follow the current stage instruction exactly" in compact_prompt
    assert "rule.behavior_names must use exact BT names" not in compact_prompt
    assert len(compact_prompt) < 7_000

    free_form = _context()
    full_prompt = render_context(free_form, "Review structural growth.", max_chars=UNLIMITED)
    assert "rule.behavior_names must use exact BT names" in full_prompt


def test_god_contract_schema_stays_compact() -> None:
    from sim2.agent_view.tool_registry import tool_schemas_for_role

    contract = tool_schemas_for_role(
        "god",
        ["query_history", "query_knowledge_gaps", "propose_entity", "propose_definition", "propose_world_change", "wait"],
    )
    text = json.dumps(contract, ensure_ascii=False)
    assert len(text) < 11_000
    propose = next(item for item in contract if item["function"]["name"] == "propose_definition")
    assert len(json.dumps(propose, ensure_ascii=False)) < 5_500


def test_all_tool_descriptions_are_short_and_complete() -> None:
    from sim2.agent_view.tool_registry import tool_schemas_for_role

    def descriptions(value, path=""):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"description", "purpose"} and isinstance(item, str):
                    yield path, item
                else:
                    yield from descriptions(item, f"{path}/{key}")
        elif isinstance(value, list):
            for item in value:
                yield from descriptions(item, path)

    for role in ("god", "agent_decision", "story", "agent_reflection"):
        for path, text in descriptions(tool_schemas_for_role(role)):
            assert len(text) <= 120, (role, path, text)
            assert not text.endswith("..."), (role, path, text)


def test_omission_telemetry_marks_required_sections_lost_to_compaction() -> None:
    _, tight = render_context_with_metrics(_context(), "Review structural growth.", max_chars=6000)

    assert "world_action_catalog" in tight["omitted_required"]
    assert "world_behavior_catalog" in tight["omitted_required"]


def test_structural_history_keeps_only_a_name_level_index_summary() -> None:
    from sim2.agents.engine import AgentEngine

    compact = AgentEngine._structural_history_result(
        "query_world_index",
        {
            "status": "ok",
            "cell": "verb",
            "total": 40,
            "items": [{"name": f"Behavior {index}", "summary": "x" * 500} for index in range(8)],
            "next_offset": 8,
        },
    )
    assert compact["names"] == [f"Behavior {index}" for index in range(6)]
    assert compact["total"] == 40
    assert "items" not in compact


def test_contract_tools_alias_filters_the_role_catalog(monkeypatch) -> None:
    from sim2.agents.engine import AgentEngine

    monkeypatch.setattr("sim2.agents.engine.configured_role_tools", lambda platform, role: None)
    engine = AgentEngine.__new__(AgentEngine)
    engine.platform = None
    context = {
        "tools": [
            {"name": "observe_nearby"},
            {"name": "query_world_index"},
            {"name": "propose_entity"},
            {"name": "propose_definition"},
            {"name": "wait"},
        ]
    }
    interaction, session = engine._select_session_tools(
        context,
        "god",
        "god_structural",
        {"tools": ["query_world_index", "propose_entity", "wait"]},
    )
    assert interaction is None
    assert session == ["query_world_index", "propose_entity", "wait"]
    assert [item["name"] for item in context["tools"]] == ["query_world_index", "propose_entity", "wait"]


def test_structural_history_summarizes_proposal_arguments() -> None:
    from sim2.agents.engine import AgentEngine

    compact = AgentEngine._structural_history_arguments(
        "propose_definition",
        {
            "definition_type": "action",
            "definition": {"name": "Fill Reserve", "effects": [{"type": "world_resource_delta"}] * 50},
            "followup_behavior": {"name": "Fill Behavior", "nodes": [{"type": "action"}] * 50},
            "summary": "A large proposal.",
        },
    )
    assert compact == {
        "definition_type": "action",
        "summary": "A large proposal.",
        "definition": {"name": "Fill Reserve"},
        "followup_behavior": {"name": "Fill Behavior"},
    }


def test_world_index_is_paged_and_bounded(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "index-paging", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            for index in range(25):
                platform.store.repositories.entities.put_definition(
                    "action_def",
                    f"indexed_action_{index:03d}",
                    {
                        "id": f"indexed_action_{index:03d}",
                        "agent_label": f"Indexed Action {index:03d}",
                        "description": "A representative Action.",
                        "parameters": {"location_id": {"type": "string"}},
                        "effects": [{"type": "location_set"}],
                    },
                )
            for index in range(25):
                platform.store.repositories.entities.put_definition(
                    "bt_def",
                    f"indexed_behavior_{index:03d}",
                    {
                        "id": f"indexed_behavior_{index:03d}",
                        "agent_label": f"Indexed Behavior {index:03d}",
                        "description": "A representative behavior.",
                        "nodes": [{"type": "action", "action_id": f"indexed_action_{index:03d}"}],
                    },
                )
        first = query_world_index(platform.store, cell="verb", limit=10, offset=0)
        assert first["status"] == "ok"
        assert first["total"] >= 50
        assert len(first["items"]) == 10
        assert first["next_offset"] == 10
        last = query_world_index(platform.store, cell="verb", query="Indexed Action 02", limit=10, offset=0)
        assert 0 < last["total"] <= 10
        assert all("Indexed Action 02" in item["name"] for item in last["items"])
        index = build_world_index(platform.store, target_cell="resource")
        assert index["counts"]["verb"] >= 50
        assert index["totals"]["actions"] >= 25
        assert len(index["behaviors"]) <= index["limit"]
        water = next(item for item in index["entities"] if item.get("name") == "蓄水")
        assert water["location"] == "蓄水屋"
    finally:
        platform.close()


def test_inspect_definition_returns_one_bounded_tree(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "inspect-definition", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            platform.store.repositories.entities.put_definition(
                "action_def",
                "bounded_action",
                {
                    "id": "bounded_action",
                    "agent_label": "Bounded Action",
                    "description": "A representative Action.",
                    "parameters": {"location_id": {"type": "string"}},
                    "effects": [{"type": "location_set"}],
                },
            )
            platform.store.repositories.entities.put_definition(
                "bt_def",
                "bounded_behavior",
                {
                    "id": "bounded_behavior",
                    "agent_label": "Bounded Behavior",
                    "description": "A representative behavior.",
                    "nodes": [{"type": "action", "action_id": "bounded_action"}],
                },
            )
        found = inspect_definition(platform.store, name="Bounded Behavior")
        assert found["status"] == "ok" and found["found"] is True
        assert found["definition"]["kind"] == "behavior"
        assert found["definition"]["nodes"][0]["action_id"] == "bounded_action"
        missing = inspect_definition(platform.store, name="Not Registered")
        assert missing["status"] == "ok" and missing["found"] is False
        executor = AgentToolExecutor(platform, role="god", actor_id="god")
        assert executor.execute("query_world_index", {"cell": "verb", "limit": 5})["status"] == "ok"
        assert executor.execute("inspect_definition", {"name": "Bounded Action"})["status"] == "ok"
        assert "query_world_index" in ROLE_TOOLS["god"]
        assert TOOL_DEFINITIONS["query_world_index"]["kind"] == "read"
    finally:
        platform.close()


def test_god_view_exposes_a_relevance_scoped_index(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "god-index-view", tmp_path / "runtime"))
    try:
        with platform.store.transaction():
            platform.store.repositories.metadata.set(
                "season_plan",
                {"plan_id": "plan:test:resource", "status": "active", "target_cell": "resource", "reason": "test", "expires_minute": 60},
            )
        view = platform.view.build_decision_view("god", role="god")
        index = view.get("world_index") or {}
        assert index.get("target_cell") == "resource"
        assert index.get("totals", {}).get("entities", 0) > 0
        prompt = render_context(view, "Review structural growth.", max_chars=UNLIMITED)
        assert "Indexed inventory:" in prompt
        assert "<world_behavior_catalog>" in prompt
        assert "<world_action_catalog>" in prompt
    finally:
        platform.close()
