from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sim2.bootstrap.service import create_instance, validate_config
from sim2.evolution.config import normalize_evolution_config
from sim2.persistence.store import WorldStore


def test_compact_evolution_compiles_to_runtime_policy() -> None:
    compiled = normalize_evolution_config({
        "cadence": {"reflection": "1d", "god": "1w"},
        "wakes": [
            {
                "on": "cistern.reviewed",
                "for": "god",
                "count": 3,
                "every": "1w",
                "tools": ["propose_institution", "wait"],
                "definition_type": "institution",
                "purpose": "Decide whether the repeated review needs a public institution.",
            },
            {"on": "institution.activated", "for": "story"},
        ],
    })

    assert compiled["schedule"] == {
        "agent_reflection_minutes": 1440,
        "god_local_minutes": 10080,
    }
    god = compiled["event_wake_policy"]["consumers"]["god"]
    group = god["event_groups"][0]
    assert group["event_types"] == ["cistern.reviewed"]
    assert group["minimum_events"] == 3
    assert group["cooldown_minutes"] == 10080
    assert group["agent_contract"]["evidence_sources"] == [{"event_types": ["cistern.reviewed"]}]
    assert group["agent_contract"]["required_definition_type"] == "institution"
    assert compiled["event_wake_policy"]["consumers"]["story"]["direct_event_types"] == ["institution.activated"]


def test_compact_evolution_rejects_ambiguous_fields_and_durations() -> None:
    with pytest.raises(ValueError, match="retired fields"):
        normalize_evolution_config({"schedule": {"god_local_minutes": 10}})
    with pytest.raises(ValueError, match="invalid duration"):
        normalize_evolution_config({"cadence": {"god": "soon"}})
    with pytest.raises(ValueError, match="unknown field"):
        normalize_evolution_config({"wakes": [{"on": "x", "for": "god", "threshold": 3}]})
    with pytest.raises(ValueError, match="unknown field"):
        normalize_evolution_config({"wakes": [{"on": "x", "for": "god", "evidence": [{"event_types": ["x"]}]}]})


def test_compact_evolution_is_valid_and_stored_compiled(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    package = tmp_path / "compact-town"
    package.mkdir()
    for path in source.iterdir():
        if path.name == "__pycache__":
            continue
        target = package / path.name
        if path.is_file():
            target.write_bytes(path.read_bytes())
    (package / "evolution.yaml").write_text(yaml.safe_dump({
        "cadence": {"reflection": "1d", "god": "1w"},
        "wakes": [
            {"on": "cistern.reviewed", "for": "god", "count": 3, "tools": ["propose_institution", "wait"], "external": True},
            {"on": "institution.activated", "for": "story"},
            {"on": "cistern.reviewed", "for": "story"},
            {"on": "research.completed", "for": "god", "count": 2, "every": "1w"},
            {"on": "civilization.milestone", "for": "god"},
        ],
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")

    report = validate_config(package)
    assert report["valid"] is True, report["errors"]
    db_path = create_instance(package, "compact-town", tmp_path / "runtime")
    store = WorldStore(db_path)
    try:
        evolution = store.get_meta("evolution")
        assert "wakes" not in evolution
        assert evolution["schedule"]["god_local_minutes"] == 10080
        assert evolution["event_wake_policy"]["consumers"]["god"]["event_groups"]
    finally:
        store.close()

