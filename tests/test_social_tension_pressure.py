"""The G-04 pressure probe changes conditions, never the model's choice."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform

ROOT = Path(__file__).parents[1]


def _script():
    spec = importlib.util.spec_from_file_location("verify_social_tension", ROOT / "scripts" / "verify_social_tension.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_pressure_changes_scarcity_and_claimants_without_directing_a_choice(tmp_path: Path) -> None:
    module = _script()
    platform = Platform(create_instance(module.WORLD, "pressure-probe", tmp_path / "runtime"))
    try:
        assert module._reserve(platform) == 10
        state = module._apply_pressure(platform)
        assert module._reserve(platform) == 2
        assert platform.store.repositories.metadata.get("world_properties", {}).get("energy_reserve") == 2
        assert state["third_claimant"] == "ilya"
        assert platform.store.repositories.entities.get_agent("ilya")["initial_state"]["location_id"] == "relay_yard"
        text = module._pressure_lines().casefold()
        assert "only two units" in text
        assert "ilya" in text
        assert "thirty minutes" in text
        assert "you have decided" not in text
        assert "you must take" not in text
    finally:
        platform.close()
