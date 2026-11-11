from __future__ import annotations

import asyncio
from pathlib import Path

import yaml

from sim2.api.map_view import render_topology_map
from sim2.api.text import TextWorldSession
from sim2.api.tui import WorldTUI
from sim2.bootstrap.service import create_instance, write_world_package_from_template
from sim2.platform.runtime import Platform


WORLD = Path(__file__).parents[1] / "worlds" / "starter_village"


def test_map_projection_uses_sqlite_topology_and_current_occupants(tmp_path: Path):
    platform = Platform(create_instance(WORLD, "map-world", tmp_path / "runtime"))
    try:
        view = platform.map_view(observer=True)
        assert any(node["id"] == "village_square" for node in view["nodes"])
        assert any({edge["from"], edge["to"]} == {"river_bridge", "village_square"} for edge in view["edges"])
        inn = next(node for node in view["nodes"] if node["id"] == "inn")
        assert {item["id"] for item in inn["occupants"]} >= {"yu_ge", "fei_lan"}
        assert "根门" not in render_topology_map(view)
        text = render_topology_map(view)
        assert "村广场" in text
        assert "余歌旅店" in text
    finally:
        platform.close()


def test_player_map_hides_secret_locations_and_text_client_renders_map(tmp_path: Path):
    platform = Platform(create_instance(WORLD, "player-map-world", tmp_path / "runtime"))
    try:
        entered = platform.enter_player("player_map", "courier", name="Ari")
        assert entered["status"] == "accepted"
        session = TextWorldSession(platform, player_id="player_map")
        rendered = session.execute("map")
        assert "河桥" in rendered
        assert "根门" not in rendered
    finally:
        platform.close()


def test_tui_mounts_map_timeline_controls_and_character_table(tmp_path: Path):
    db_path = create_instance(WORLD, "tui-world", tmp_path / "runtime")

    async def exercise() -> None:
        app = WorldTUI(db_path, observer=True, refresh_seconds=60)
        async with app.run_test(size=(150, 45)) as pilot:
            await pilot.pause()
            assert "starter_village" in str(app.query_one("#status_bar").content)
            assert "07:00" in str(app.query_one("#status_bar").content)
            assert "村广场" in str(app.query_one("#map").content)
            assert app.query_one("#agents").row_count >= 9
            first = app.service.observer(1)["agents"][0]
            assert first["location"] != first["location_id"]
            app.action_toggle_pause()
            await pilot.pause()
            assert "paused" in str(app.query_one("#status_bar").content)
            before = int(app.service.status()["world_minute"])
            app.action_step_world()
            await pilot.pause()
            assert int(app.service.status()["world_minute"]) == before + 1

    asyncio.run(exercise())


def test_map_projection_works_for_a_non_village_world(tmp_path: Path):
    template = yaml.safe_load((Path(__file__).parents[1] / "examples" / "orbital-world-template.yaml").read_text(encoding="utf-8"))
    package = write_world_package_from_template(template, tmp_path / "orbital")
    platform = Platform(create_instance(package, "orbital-map", tmp_path / "runtime"))
    try:
        view = platform.map_view(observer=True)
        assert {node["name"] for node in view["nodes"]} == {"Docking Ring", "Reactor Bay"}
        reactor = next(node for node in view["nodes"] if node["id"] == "reactor_bay")
        assert [item["id"] for item in reactor["occupants"]] == ["unit_seven"]
        rendered = render_topology_map(view)
        assert "Reactor Bay" in rendered
        assert "Unit Seven" in rendered
    finally:
        platform.close()
