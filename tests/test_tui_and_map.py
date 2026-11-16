from __future__ import annotations

import asyncio
import re
from pathlib import Path

import yaml
from textual.widgets import Input, RichLog

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


def test_text_player_scene_runs_numbered_action_through_platform(tmp_path: Path):
    db_path = create_instance(WORLD, "text-scene-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        assert platform.enter_player("player_text_scene", "courier", name="Ari")["status"] == "accepted"
        session = TextWorldSession(platform, player_id="player_text_scene")
        scene = session.execute("look")
        assert "World minute 0" in scene
        assert "Day 1, 07:00" in scene
        assert "Goal: 找到失踪车队的下落" in scene
        assert "河桥 (unsafe)" in scene
        assert "Needs: Satiety" in scene
        assert "前往 村广场" in session.execute("actions")
        assert "[do 1]" in scene

        started = session.execute("do 1")
        assert started.startswith("status=accepted")
        match = re.search(r"wait (\d+)", started)
        assert match is not None
        minutes = int(match.group(1))
        assert session.execute(f"wait {minutes}").startswith(f"Advanced {minutes} world minute")
        assert "村广场" in session.execute("status")
        assert platform.store.repositories.entities.get_agent("player_text_scene")["initial_state"]["location_id"] == "village_square"
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


def test_tui_player_command_log_preserves_text_scene_tokens(tmp_path: Path):
    db_path = create_instance(WORLD, "tui-command-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        assert platform.enter_player("tui_player", "courier", name="Ari")["status"] == "accepted"
    finally:
        platform.close()

    async def exercise() -> None:
        app = WorldTUI(db_path, observer=False, player_id="tui_player", refresh_seconds=60)
        async with app.run_test(size=(150, 45)) as pilot:
            await pilot.pause()
            command = app.query_one("#command", Input)
            command.focus()
            command.value = "look"
            await pilot.press("enter")
            await pilot.pause()
            rendered = "\n".join(line.text for line in app.query_one("#message", RichLog).lines)
            assert "[do 1]" in rendered
            assert "########" in rendered

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
