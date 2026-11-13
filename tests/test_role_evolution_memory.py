from __future__ import annotations

from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform


def _town(tmp_path: Path, instance: str) -> Platform:
    return Platform(create_instance(Path(__file__).parents[1] / "worlds" / "three_lanterns_town", instance, tmp_path / "runtime"))


def test_story_and_god_keep_bounded_role_memories(tmp_path: Path):
    platform = _town(tmp_path, "role-memory")
    try:
        source_event = platform.store.append_event(
            "research.completed", "luo_shan", 10,
            {"source_id": "boundary_stone", "fact": "The boundary inscription was verified.", "story_source": True},
        )
        story = platform.submit_story_beat("story", {
            "beat": "记录界碑研究结果并决定下一步核验",
            "summary": "界碑读数已核实，下一步核对北岗刻文。",
            "fact": "The boundary inscription was verified.",
            "goal_hint": "Continue the configured boundary research.",
            "audience": ["罗杉", "韩秋"],
        }, source_event_ids=[source_event])
        assert story["status"] == "accepted"
        god = platform.submit_entity_proposal("god", {
            "entity_type": "document",
            "entity_id": "role_memory_source",
            "name": "后续核验记录",
            "description": "A role-memory test source.",
            "location_id": "north_watch",
            "state": {
                "research_discoveries": [{"id": "role_memory_fact", "fact": "The later record confirms the same boundary."}],
                "research_assignees": ["罗杉"],
            },
        })
        assert god["status"] == "accepted"
        counts = {
            role: platform.store.connection.execute("SELECT COUNT(*) AS n FROM memory_item WHERE agent_id=?", (role,)).fetchone()["n"]
            for role in ("story", "god")
        }
        assert counts["story"] == 1
        assert counts["god"] == 1
        story_view = platform.view.build_decision_view("story", role="story", source_event_id=source_event)
        god_view = platform.view.build_decision_view("god", role="god")
        assert story_view["long_term_memories"]
        assert god_view["long_term_memories"]
        assert "world_signals" in god_view["world_trends"]
        assert set(god_view["world_trends"]["world_signals"]) >= {"uncertainty", "disorder", "coherence", "novelty"}
    finally:
        platform.close()


def test_role_memory_write_is_idempotent(tmp_path: Path):
    platform = _town(tmp_path, "role-memory-idempotent")
    try:
        from sim2.evolution.memory import remember_role

        first = remember_role(platform.store, "god", "diagnosis", {"summary": "A bounded diagnosis."}, memory_key="god:test:1", source_event_id=1)
        second = remember_role(platform.store, "god", "diagnosis", {"summary": "A different retry wording."}, memory_key="god:test:1", source_event_id=2)
        assert first == second
        assert platform.store.connection.execute("SELECT COUNT(*) AS n FROM memory_item WHERE agent_id='god'").fetchone()["n"] == 1
    finally:
        platform.close()
