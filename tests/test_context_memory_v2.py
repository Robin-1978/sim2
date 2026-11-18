from __future__ import annotations

from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform

WORLD = Path(__file__).parents[1] / "worlds" / "starter_village"


def _platform(tmp_path: Path, name: str) -> Platform:
    return Platform(create_instance(WORLD, name, tmp_path / "runtime"))


def test_memory_ranking_uses_importance_recency_and_access(tmp_path: Path) -> None:
    platform = _platform(tmp_path, "memory-ranking")
    try:
        with platform.store.transaction():
            platform.store.repositories.metadata.set("world_minute", 100)
            platform.store.repositories.memory.put_memory(
                "fei_lan", "experience",
                {"summary": "An old route note mentions the north gate.", "importance": 0.2},
                tier="long_term",
            )
            platform.store.repositories.metadata.set("world_minute", 200)
            platform.store.repositories.memory.put_memory(
                "fei_lan", "experience",
                {"summary": "A recent verified patrol found a safe north gate.", "importance": 0.9},
                tier="long_term",
            )
        ranked = platform.memory.retrieve("fei_lan", query="north gate", limit=2)
        assert ranked
        assert "recent verified patrol" in ranked[0]["summary"]
        assert platform.memory.ranking["importance"] > 0
        assert platform.memory.ranking["recency"] > 0
    finally:
        platform.close()


def test_compaction_keeps_structured_facts_lessons_and_open_loops(tmp_path: Path) -> None:
    from sim2.memory.service import MemoryService

    platform = _platform(tmp_path, "memory-compaction")
    try:
        with platform.store.transaction():
            for index in range(12):
                platform.store.repositories.memory.put_memory(
                    "fei_lan", "knowledge",
                    {"fact": f"Verified fact {index} about the northern fields.", "importance": 0.4},
                    tier="long_term",
                )
                platform.store.repositories.memory.put_memory(
                    "fei_lan", "experience",
                    {"summary": f"Lesson {index} from moving supplies before dusk.", "importance": 0.4},
                    tier="long_term",
                )
                platform.store.repositories.memory.put_memory(
                    "fei_lan", "goal",
                    {"goal": f"Open promise {index} to inspect the granary.", "importance": 0.4},
                    tier="long_term",
                )
        result = MemoryService(platform.store).compact("fei_lan", keep=4)
        assert result["status"] == "compacted"
        assert result["sections"]["facts"] > 0
        assert result["sections"]["lessons"] > 0
        assert result["sections"]["open_loops"] > 0
        summaries = [
            item for item in platform.store.repositories.memory.list_memories("fei_lan", limit=1000)
            if item.get("type") == "summary"
        ]
        assert summaries
        sections = summaries[0].get("sections") or {}
        assert set(sections) == {"facts", "lessons", "open_loops", "other"}
    finally:
        platform.close()
