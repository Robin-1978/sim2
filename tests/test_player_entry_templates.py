from __future__ import annotations

from pathlib import Path

import pytest

from sim2.api.cli import _ensure_player
from sim2.api.service import ControlService
from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform
from sim2.world.player_template import DEFAULT_PLAYER_GOAL, PlayerTemplateError, parse_player_template

WORLDS = Path(__file__).parents[1] / "worlds"


def test_player_template_uses_one_canonical_shape():
    template = parse_player_template({
        "entry_location_id": "lantern_square",
        "origins": [{"id": "traveler"}, {"id": "resident"}],
    })

    assert template["entry_location_id"] == "lantern_square"
    assert [origin["id"] for origin in template["origins"]] == ["traveler", "resident"]
    assert all(origin["starting_goal"] == DEFAULT_PLAYER_GOAL for origin in template["origins"])

    with pytest.raises(PlayerTemplateError, match="legacy player template"):
        parse_player_template({"player": {"entry_location_id": "lantern_square", "origin_choices": []}})
    with pytest.raises(PlayerTemplateError, match="must be an object"):
        parse_player_template({"entry_location_id": "lantern_square", "origins": ["traveler"]})


def test_tidebound_canonical_origins_can_enter_the_world(tmp_path: Path):
    db_path = create_instance(WORLDS / "tidebound_commons", "tidebound-player-entry", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        result = platform.enter_player("player_one", "forager", name="Ari")
        assert result["status"] == "accepted"
        agent = platform.store.repositories.entities.get_agent("player_one")
        assert agent["initial_state"]["location_id"] == "commons_quay"
        assert agent["ultimate_goal"] == DEFAULT_PLAYER_GOAL
        assert agent["name"] == "Ari"
    finally:
        platform.close()


def test_three_lanterns_canonical_origins_can_enter_the_world(tmp_path: Path):
    db_path = create_instance(WORLDS / "three_lanterns_town", "three-lanterns-player-entry", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        result = platform.enter_player("player_one", "resident", name="Ari")
        assert result["status"] == "accepted"
        agent = platform.store.repositories.entities.get_agent("player_one")
        assert agent["initial_state"]["location_id"] == "lantern_square"
        assert agent["ultimate_goal"] == DEFAULT_PLAYER_GOAL
    finally:
        platform.close()


def test_player_entry_projection_lists_canonical_origins(tmp_path: Path):
    db_path = create_instance(WORLDS / "tidebound_commons", "tidebound-player-projection", tmp_path / "runtime")
    service = ControlService(db_path)
    try:
        entry = service.player_entry()
        assert entry["entry_location"] == {"id": "commons_quay", "name": "Commons Quay"}
        assert [origin["id"] for origin in entry["origins"]] == [
            "steward",
            "forager",
            "chart_keeper",
            "ferry_runner",
        ]
        assert all(origin["goal"] == DEFAULT_PLAYER_GOAL for origin in entry["origins"])
    finally:
        service.close()


def test_cli_entry_lists_valid_origins_after_a_bad_choice(tmp_path: Path):
    db_path = create_instance(WORLDS / "tidebound_commons", "tidebound-cli-guidance", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        with pytest.raises(SystemExit, match=r"steward, forager, chart_keeper, ferry_runner"):
            _ensure_player(platform, "player_bad", "traveler", "Ari")
        _ensure_player(platform, "player_good", "forager", "Ari")
        assert platform.store.repositories.entities.get_agent("player_good")["name"] == "Ari"
    finally:
        platform.close()
