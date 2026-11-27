"""Player-facing checks at actual desktop and phone viewport sizes."""

from contextlib import contextmanager
from pathlib import Path
from shutil import which
from threading import Thread

import pytest

from sim2.api.http import ControlHTTPServer
from sim2.bootstrap.service import create_instance

pw = pytest.importorskip("playwright.sync_api")
WORLDS = Path(__file__).parents[1] / "worlds"


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as playwright:
        executable = which("google-chrome") or which("chromium") or playwright.chromium.executable_path
        if not Path(executable).exists():
            pytest.skip("A Chromium browser is required for viewport checks")
        browser = playwright.chromium.launch(executable_path=executable)
        yield browser
        browser.close()


@contextmanager
def server_url(tmp_path: Path, database: Path | None = None):
    with ControlHTTPServer(("127.0.0.1", 0), database, runtime_root=tmp_path, authoring_root=WORLDS) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


@pytest.mark.parametrize(("width", "height"), [(390, 844), (1366, 900)])
def test_empty_lobby_opens_on_cover_until_player_browses(tmp_path, browser, width, height):
    with server_url(tmp_path) as url:
        page = browser.new_page(viewport={"width": width, "height": height}, is_mobile=width < 600)
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_function("document.querySelector('#entry-cover img')?.naturalWidth > 0")
            pw.expect(page.locator("#entry-create-panel")).to_be_hidden()
            for selector in ("#entry-cover", "#entry-title", "#entry-browse-worlds"):
                box = page.locator(selector).bounding_box()
                assert 0 <= box["y"] < height, (selector, box)
            page.locator("#entry-browse-worlds").click()
            pw.expect(page.locator("#entry-create-panel")).to_be_focused()
            pw.expect(page.locator(".world-template-card.active")).to_contain_text("风脊号")
        finally:
            page.close()


def test_scene_badges_and_cards_share_the_world_thresholds(tmp_path, browser):
    database = create_instance(WORLDS / "windward_skyfall", "alerts", tmp_path)
    with server_url(tmp_path, database) as url:
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.locator("#entry-name").fill("林晚")
            page.locator("#entry-submit").click()
            pw.expect(page.locator("#player-name")).to_have_text("林晚")
            tags = page.locator("#scene-tags")
            pw.expect(tags.locator(".badge", has_text="船体")).to_be_visible()
            pw.expect(tags.locator(".danger, .warning")).to_have_count(0)
            for pressure, hull, storm_level, hull_level in [(35, 0.64, "warning", "warning"), (65, 0.34, "danger", "danger")]:
                page.evaluate("""async ([storm, hull]) => {
                    const {state} = await import('/js/core.js');
                    const scene = await import('/js/render_scene.js');
                    Object.assign(state.view.world_properties, {storm_pressure: storm, hull_integrity: hull});
                    scene.renderScene();
                    scene.renderCrisisStrip();
                }""", [pressure, hull])
                for label, level in [("风暴压力", storm_level), ("船体", hull_level)]:
                    pw.expect(tags.locator(f".badge.{level}", has_text=label)).to_have_count(1)
                    pw.expect(page.locator(f".crisis-metric.{level}", has_text=label)).to_have_count(1)
        finally:
            page.close()
