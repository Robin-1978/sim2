from __future__ import annotations

from pathlib import Path

import pytest

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform


WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"


def test_event_and_evolution_repositories_share_platform_transaction(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "uow-boundary", tmp_path / "runtime"))
    try:
        assert platform.uow.events.repository is platform.store.repositories.events
        assert platform.uow.evolution.repository is platform.store.repositories.evolution
        with platform.uow.transaction() as uow:
            event_id = uow.events.append("test.committed", "luo_shan", 0, {"value": 1})
            uow.evolution.put_task(
                "test-task",
                "story",
                "story",
                "test:event",
                0,
                0,
                status="ready",
                data={"source_event_id": event_id},
            )
        assert platform.store.events_by_ids([event_id])[0]["event_type"] == "test.committed"
        assert platform.store.ready_evolution_tasks()[0]["task_id"] == "test-task"

        with pytest.raises(RuntimeError):
            with platform.uow.transaction() as uow:
                uow.events.append("test.rolled_back", "luo_shan", 0, {})
                raise RuntimeError("rollback")
        assert not any(item["event_type"] == "test.rolled_back" for item in platform.store.recent_events(limit=20))
    finally:
        platform.close()
