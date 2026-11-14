from __future__ import annotations

from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform


WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"


def test_platform_is_a_facade_over_explicit_services(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "platform-composition", tmp_path / "runtime"))
    try:
        assert Platform.__bases__ == (object,)
        assert set(platform._services) >= {
            "api",
            "intent",
            "execution",
            "tick",
            "evolution",
            "activity",
            "definitions",
            "entities",
            "story",
            "world_changes",
        }
        assert platform.status()["world_minute"] == 0
        assert platform.tick_snapshot()["world_minute"] == 0
        assert platform._services["intent"].submit_behavior.__self__ is platform._services["intent"]
    finally:
        platform.close()
