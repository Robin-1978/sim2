from __future__ import annotations

import json
from pathlib import Path

from sim2.agents.tools import AgentToolExecutor
from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform
from sim2.prompting.renderer import render_context


WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"


def _town(tmp_path: Path, name: str) -> Platform:
    return Platform(create_instance(WORLD, name, tmp_path / "runtime"))


def test_world_history_and_current_knowledge_gaps_are_separate_reads(tmp_path: Path) -> None:
    platform = _town(tmp_path, "knowledge-gap-read")
    try:
        npc = platform.perception.query_knowledge_gaps("luo_shan", global_scope=False)
        assert npc["status"] == "rejected"
        assert npc["failed_check"] == "knowledge_gap_scope"

        god = AgentToolExecutor(platform, role="god", actor_id="god").execute("query_knowledge_gaps", {})
        assert god["status"] == "ok"
        assert god["gaps"][0]["status"] == "active"
        assert god["gaps"][0]["research_source"] == "褪色界碑"
        assert "gap_id" not in json.dumps(god, ensure_ascii=False)
        assert "boundary_stone" not in json.dumps(god, ensure_ascii=False)

        view = platform.view.build_decision_view("god", role="god")
        prompt = render_context(view, "Review the next unresolved research question.")
        assert "<world_history>" in prompt
        assert "<knowledge_gaps>" in prompt
        assert "query_knowledge_gaps" in prompt
    finally:
        platform.close()


def test_completed_research_resolves_its_current_gap(tmp_path: Path) -> None:
    platform = _town(tmp_path, "knowledge-gap-resolution")
    try:
        with platform.store.transaction():
            event_id = platform.store.repositories.events.append_event(
                "research.completed",
                "luo_shan",
                12,
                {"source_id": "boundary_stone", "stage_id": "researched:boundary_stone:markings", "fact": "The boundary markings were verified."},
            )
            updated = platform.store.repositories.memory.update_research_gap(
                "boundary_stone",
                stage_id="researched:boundary_stone:markings",
                completed=True,
                fact="The boundary markings were verified.",
                event_id=event_id,
                world_minute=12,
            )
        assert updated[0]["status"] == "resolved"
        resolved = AgentToolExecutor(platform, role="god", actor_id="god").execute("query_knowledge_gaps", {"status": "resolved"})
        assert resolved["matched"] == 1
        assert resolved["gaps"][0]["last_finding"] == "The boundary markings were verified."
    finally:
        platform.close()


def test_world_view_marks_resolved_sources_separately_from_history(tmp_path: Path) -> None:
    platform = _town(tmp_path, "research-source-status-view")
    try:
        with platform.store.transaction():
            event_id = platform.store.repositories.events.append_event(
                "research.completed",
                "luo_shan",
                12,
                {
                    "source_id": "boundary_stone",
                    "stage_id": "researched:boundary_stone:markings",
                    "fact": "The boundary markings were verified.",
                },
            )
            platform.store.repositories.memory.update_research_gap(
                "boundary_stone",
                stage_id="researched:boundary_stone:markings",
                completed=True,
                fact="The boundary markings were verified.",
                event_id=event_id,
                world_minute=12,
            )
        view = platform.view.build_decision_view("god", role="god")
        source = next(item for item in view["research_sources"] if item["name"] == "褪色界碑")
        assert source["status"] == "resolved"
        assert source["remaining_stages"] == 0
        prompt = render_context(view, "Review the next world continuation.")
        assert "<research_sources>" in prompt
        assert "status=resolved" in prompt
        assert "old historical sentence does not reopen it" in prompt
    finally:
        platform.close()


def test_multi_stage_research_gap_stays_active_until_all_stages_finish(tmp_path: Path) -> None:
    platform = _town(tmp_path, "knowledge-gap-stages")
    try:
        proposal = platform.submit_entity_proposal(
            "god",
            {
                "entity_type": "document",
                "entity_id": "two_stage_gap_source",
                "name": "两阶段水样记录",
                "description": "A two-stage research source for the gap projection test.",
                "state": {
                    "location_id": "north_watch",
                    "research_assignees": ["luo_shan"],
                    "research_discoveries": [
                        {"id": "water:sample", "fact": "The sample has a measurable trace."},
                        {"id": "water:source", "fact": "The upstream source is the likely origin."},
                    ],
                },
            },
        )
        assert proposal["status"] == "accepted"
        with platform.store.transaction():
            first = platform.store.repositories.events.append_event("research.completed", "luo_shan", 20, {"source_id": "two_stage_gap_source", "stage_id": "water:sample", "fact": "A trace was measured."})
            active = platform.store.repositories.memory.update_research_gap("two_stage_gap_source", stage_id="water:sample", completed=True, event_id=first, world_minute=20)
        assert active[0]["status"] == "active"
        assert active[0]["completed_stages"] == ["water:sample"]
        with platform.store.transaction():
            second = platform.store.repositories.events.append_event("research.completed", "luo_shan", 30, {"source_id": "two_stage_gap_source", "stage_id": "water:source", "fact": "The upstream source was identified."})
            resolved = platform.store.repositories.memory.update_research_gap("two_stage_gap_source", stage_id="water:source", completed=True, event_id=second, world_minute=30)
        assert resolved[0]["status"] == "resolved"
        assert resolved[0]["completed_stages"] == ["water:sample", "water:source"]
    finally:
        platform.close()


def test_dynamic_research_source_requires_an_executable_assignee(tmp_path: Path) -> None:
    platform = _town(tmp_path, "knowledge-gap-assignee")
    try:
        result = platform.submit_entity_proposal(
            "god",
            {
                "entity_type": "document",
                "name": "无人负责的研究页",
                "state": {
                    "location_id": "north_watch",
                    "research_discoveries": [{"id": "unassigned:fact", "fact": "A fact with no executor."}],
                },
            },
        )
        assert result["status"] == "rejected"
        assert result["failed_check"] == "entity_schema"
        assert any("research_assignee" in error for error in result["errors"])
    finally:
        platform.close()
