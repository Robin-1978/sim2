from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform


WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"


def test_concurrent_ticks_commit_one_next_minute(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "concurrent-ticks", tmp_path / "runtime"))
    try:
        original_snapshot = platform.store.world_snapshot
        snapshot_barrier = threading.Barrier(2)

        def snapshot(*, include_agent_revisions: bool = True):
            result = original_snapshot(include_agent_revisions=include_agent_revisions)
            # The old implementation read the clock before taking the write
            # transaction. Force both callers to observe that race. The new
            # implementation reads while holding BEGIN IMMEDIATE, so the
            # first caller is already in a transaction and does not wait.
            if not platform.store.connection.in_transaction:
                snapshot_barrier.wait(timeout=2)
            return result

        platform.store.world_snapshot = snapshot
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _item: platform.step(), range(2)))

        assert all(item["status"] == "advanced" for item in results)
        assert platform.store.get_meta("world_minute") == 2
        ticks = [
            item
            for item in platform.store.repositories.events.recent_events(limit=20)
            if item["event_type"] == "world_minute.advanced"
        ]
        assert sorted(int(item["world_minute"]) for item in ticks) == [1, 2]
    finally:
        platform.close()
