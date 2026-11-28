"""Player-facing checks at actual desktop and phone viewport sizes."""

from contextlib import closing, contextmanager
from pathlib import Path
from shutil import which
from threading import Thread
from urllib.parse import urlsplit

import pytest

from sim2.api.http import ControlHTTPServer
from sim2.api.runtime_supervisor import RuntimeSupervisor
from sim2.api.service import ControlService
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


def test_first_freight_enables_each_next_choice_after_the_previous_update(tmp_path, browser):
    database = create_instance(WORLDS / "gate_cities", "web-first-freight", tmp_path)
    with server_url(tmp_path, database) as url:
        page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
        try:
            page.goto(url, wait_until="domcontentloaded")
            pw.expect(page.locator("#entry-title")).to_have_text("诸城之门", timeout=20000)
            page.locator("#entry-name").fill("林晚")
            page.locator("#entry-submit").click()
            choices = page.locator("#opportunity-choices")
            pw.expect(choices.get_by_role("button", name="优先送零件")).to_be_visible(timeout=20000)
            pw.expect(page.locator("#scene-countdown")).to_contain_text("1 个货运名额")

            pending_trades = []
            trade_route = "**/trades?**"
            hold_trades = True

            def hold_trade_response(route):
                if hold_trades:
                    pending_trades.append(route)
                else:
                    route.continue_()

            page.route(trade_route, hold_trade_response)
            with page.expect_request(trade_route):
                choices.get_by_role("button", name="优先送水").click()
            load_water = choices.get_by_role("button", name="装两份净水")
            pw.expect(load_water).to_be_disabled()
            hold_trades = False
            for route in pending_trades:
                route.continue_()
            page.unroute(trade_route)

            load_water.click()
            choices.get_by_role("button", name="把净水送到低城").click()
            pw.expect(page.locator("#scene-title")).to_have_text("低城水庭", timeout=15000)
            choices.get_by_role("button", name="通过低城节水公约").click()
            pw.expect(page.get_by_text("通过低城节水公约：行动完成", exact=False)).to_be_visible(timeout=15000)
        finally:
            page.close()


def test_gate_construction_stays_visible_when_the_crew_can_appoint_a_foreman(tmp_path, browser):
    database = create_instance(WORLDS / "gate_cities_sandbox", "web-gate-priority", tmp_path)
    with closing(ControlService(database)) as service:
        store = service.platform.store
        with store.transaction():
            properties = store.repositories.metadata.get("world_properties")
            properties.update(site_surveyed=1, market_permit=1, market_city=1, market_mine=1,
                              merit_count=3, service_votes=2)
            store.repositories.metadata.set("world_properties", properties)
    with server_url(tmp_path, database) as url:
        page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.locator("#entry-name").fill("林晚")
            page.locator("#entry-submit").click()
            choices = page.locator("#opportunity-choices")
            pair = choices.get_by_role("button", name="开启灰脊—低城坊市门线")
            pw.expect(pair).to_be_visible(timeout=20000)
            pw.expect(page.locator("#venture-title")).to_have_text("经营你的高地工坊")
            assert pair.bounding_box()["y"] < 620
            goals = page.evaluate("async () => (await import('/js/core.js')).state.view.goals")
            assert any("behavior:authorize_appointment" in goal["ready_behavior_refs"] for goal in goals)
        finally:
            page.close()


def test_gate_can_open_a_workshop_before_building_its_first_gate(tmp_path, browser):
    database = create_instance(WORLDS / "gate_cities_sandbox", "shop-first", tmp_path)
    with server_url(tmp_path, database) as url:
        page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.locator("#entry-name").fill("林晚")
            page.locator("#entry-submit").click()
            choices = page.locator("#opportunity-choices")
            choices.get_by_role("button", name="投入两枚钱开工坊").click(timeout=20000)
            pw.expect(page.locator("#opportunity-title")).to_have_text("交付工坊的第一批路石", timeout=60000)
            pw.expect(choices.get_by_role("button", name="委托坊市班组")).to_be_visible()
            pw.expect(page.locator("#venture-metrics")).to_contain_text("工坊周转金2 枚")
            pw.expect(page.locator("#recap-card")).to_be_hidden()
            view = page.evaluate("async () => (await import('/js/core.js')).state.view")
            assert view["world_properties"]["connected_count"] == 0
            assert view["inventory"]["gate_core"] == 1
        finally:
            page.close()


def test_gate_mobile_updates_for_public_changes_without_rendering_resident_routines(tmp_path, browser):
    database = create_instance(WORLDS / "gate_cities_sandbox", "mobile-chronicle", tmp_path)
    with server_url(tmp_path, database) as url:
        page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
        view_requests = []
        def is_view_request(request):
            return urlsplit(request.url).path.endswith("/view")

        page.on("request", lambda request: view_requests.append(request.url) if is_view_request(request) else None)
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.locator("#entry-name").fill("林晚")
            page.locator("#entry-submit").click()
            pw.expect(page.locator("#summary-name")).to_have_text("林晚")
            page.wait_for_function("async () => (await import('/js/core.js')).state.streamConnected")
            before = len(view_requests)
            with closing(ControlService(database)) as service:
                store = service.platform.store
                with store.transaction():
                    for _ in range(60):
                        store.repositories.events.append_event("behavior.completed", "porter_bai", 0, {
                            "summary": "北门试送货", "behavior_id": "routine_resident", "correlation_id": "bt:porter_bai:0",
                        })
                page.wait_for_timeout(1400)
                assert len(view_requests) == before
                with page.expect_request(is_view_request):
                    with store.transaction():
                        store.repositories.events.append_event("gatebuild.agreement", "warden", 0, {"summary": "北门同意联合维护"})
                feed = page.locator("#world-feed")
                pw.expect(feed.get_by_text("北门同意联合维护", exact=False)).to_have_count(1)
                pw.expect(feed.get_by_text("北门试送货", exact=False)).to_have_count(0)
        finally:
            page.close()


def test_copper_pick_bonus_appears_on_the_mining_card(tmp_path, browser):
    db = create_instance(WORLDS / "windward_skyfall", "tool-card", tmp_path)
    with closing(ControlService(db)) as service:
        platform = service.platform
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        with platform.store.transaction():
            entities = platform.store.repositories.entities
            player = entities.get_agent("hero")
            player["initial_state"]["location_id"] = "copper_tide"
            player["inventory"]["copper_pick"] = 1
            entities.put_agent("hero", player, "林晚")
    with server_url(tmp_path, db) as url:
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.locator("#entry-continue button").click()
            pw.expect(page.get_by_text("铜镐省 36 分钟", exact=True)).to_be_visible()
            card = page.locator(".action-row, .recommended-card").filter(
                has=page.get_by_text("铜镐省 36 分钟", exact=True),
            )
            pw.expect(card).to_contain_text("采铜")
            pw.expect(card).to_contain_text("54 分钟")
        finally:
            page.close()


def test_open_voyage_recommends_rest_for_the_actual_low_stat(tmp_path, browser):
    db = create_instance(WORLDS / "windward_skyfall", "open-voyage-low-energy", tmp_path)
    with closing(ControlService(db)) as service:
        platform = service.platform
        platform.enter_player("hero", "mechanic", name="林晚")
        with platform.store.transaction():
            store = platform.store
            player = store.repositories.entities.get_agent("hero")
            player["initial_state"]["stamina"] = 6
            store.repositories.entities.update_agent("hero", player)
            world = store.repositories.metadata.get("world_properties")
            world.update(dawn_salvage=1, ending_trigger=1, voyage_phase=2)
            store.repositories.metadata.set("world_properties", world)
            store.repositories.events.append_event("skyfall.launched", "hero", 0, {})
    with server_url(tmp_path, db) as url:
        page = browser.new_page(viewport={"width": 390, "height": 844})
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.locator("#entry-continue button").click()
            page.get_by_role("button", name="继续航行").click()
            pw.expect(page.locator("#ending-overlay")).to_be_hidden()
            stats = page.locator("#summary-stats")
            pw.expect(stats.locator(".low")).to_have_text("精力6%")
            pw.expect(stats).to_contain_text("健康")
            pw.expect(stats).to_contain_text("饱食")
            pw.expect(page.locator("#recommended-action")).to_contain_text("在营地休息")
        finally:
            page.close()


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
            page.evaluate("""async () => {
                const {state} = await import('/js/core.js');
                const {renderCharacter} = await import('/js/render_character.js');
                state.view.ui.player_summary_attributes = ["hydration", "integrity"];
                renderCharacter();
            }""")
            assert page.locator("#summary-stats dt").all_text_contents() == ["水分", "健康"]
        finally:
            page.close()


@pytest.mark.parametrize(("width", "height"), [(375, 667), (390, 844), (1366, 900)])
def test_game_navigation_keeps_the_player_and_current_choice(tmp_path, browser, width, height):
    db = create_instance(WORLDS / "windward_skyfall", "field-book", tmp_path)
    with server_url(tmp_path, db) as url:
        page = browser.new_page(viewport={"width": width, "height": height})
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.locator("#entry-name").fill("岚")
            page.locator(".origin-card", has_text="制图师").click()
            page.locator("#entry-submit").click()
            nav = page.get_by_role("navigation", name="游戏画面")
            choice = page.locator("#opportunity-choices button").first
            pw.expect(choice).to_be_visible()
            summary = page.get_by_role("region", name="我的角色")
            pw.expect(summary.locator("#summary-name")).to_have_text("岚")
            pw.expect(summary.locator("#summary-origin")).to_have_text("制图师 · 灯岬")
            pw.expect(summary.locator("#summary-goal")).to_contain_text("旧灯塔")
            assert summary.locator("dt").all_text_contents() == ["健康", "精力", "水分", "饱食"]
            assert all(summary.locator("dd").all_text_contents())
            first_choice = choice.bounding_box()
            footer = page.locator(".mobile-action-bar").bounding_box()
            assert first_choice["y"] + first_choice["height"] <= (footer["y"] if footer else height)
            bag = summary.get_by_role("button", name="行囊")
            bag.click()
            pw.expect(page.locator("#player-sheet-name")).to_have_text("岚")
            pw.expect(page.locator("#inventory-grid .inventory-item").first).to_be_visible()
            page.keyboard.press("Escape")
            pw.expect(bag).to_be_focused()
            nav.get_by_role("button", name="角色").click()
            pw.expect(page.locator("#player-name")).to_be_visible()
            pw.expect(page.locator("#player-role")).to_have_text("制图师")
            pw.expect(choice).to_be_hidden()
            nav.get_by_role("button", name="纪事").click()
            page.locator("#right-tabs [data-tab=talk]").click()
            pw.expect(page.locator("#tab-talk")).to_be_visible()
            nav.get_by_role("button", name="探索").focus()
            page.keyboard.press("Enter")
            pw.expect(choice).to_be_visible()
            pw.expect(nav.get_by_role("button", name="探索")).to_have_attribute("aria-pressed", "true")
            map_requests = []
            page.route("**/api/v1/map?**", lambda route: map_requests.append(route))
            page.locator("#open-map").click()
            pw.expect(page.locator("#map-dialog")).to_be_visible()
            page.keyboard.press("Escape")
            pw.expect(page.locator("#map-dialog")).to_be_hidden()
            with page.expect_response(lambda response: "/api/v1/map?" in response.url):
                map_requests[0].continue_()
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            pw.expect(page.locator("#map-dialog")).to_be_hidden()
            pw.expect(choice).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth") == width
            page.unroute("**/api/v1/map?**")
            bag_before = bag.inner_text()
            page.locator("#opportunity-choices").get_by_role("button", name="吊起铜料").click()
            pw.expect(bag).not_to_have_text(bag_before, timeout=15000)
            updated_bag = bag.inner_text()
            page.reload(wait_until="domcontentloaded")
            pw.expect(summary.locator("#summary-origin")).to_have_text("制图师 · 灯岬", timeout=30000)
            pw.expect(bag).to_have_text(updated_bag)
        finally:
            page.close()


def test_saved_player_renews_before_protected_reads_after_server_restart(tmp_path, browser):
    database = create_instance(WORLDS / "tidebound_commons", "web-restart", tmp_path)
    page = browser.new_page()
    protected = []

    @contextmanager
    def running(port=0):
        with ControlHTTPServer(("127.0.0.1", port), database, runtime_root=tmp_path) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield server
            finally:
                page.goto("about:blank")
                server.shutdown()
                thread.join()

    def on_response(response):
        path = urlsplit(response.url).path
        if path.endswith("/view") or path == "/api/v1/observer/timeline":
            protected.append(response.status)

    try:
        page.on("response", on_response)
        with running() as server:
            port = server.server_port
            url = f"http://127.0.0.1:{port}"
            page.goto(url)
            page.locator("#entry-name").fill("林晚")
            page.locator("#entry-submit").click()
            pw.expect(page.locator("#summary-name")).to_have_text("林晚", timeout=20000)
            saved = page.evaluate("JSON.parse(localStorage.getItem('sim2.game.player'))")
            assert saved["sessionGeneration"] == server.session_generation
            page.evaluate("sessionStorage.setItem('sim2.game.operator', 'old-operator-session')")

        protected.clear()
        with running(port) as server:
            assert server.session_generation != saved["sessionGeneration"]
            assert not server.authorize_session(saved["token"], "player", player_id=saved["id"])[0]
            with page.expect_response(lambda response: urlsplit(response.url).path == "/api/v1/observer/timeline") as chronicle:
                page.goto(url)
                pw.expect(page.locator("#summary-name")).to_have_text("林晚", timeout=20000)
            assert chronicle.value.status == 200
            page.wait_for_function("""async () => {
                const {state} = await import('/js/core.js');
                return state.view?.player?.id && !document.querySelector('#game').classList.contains('hidden');
            }""")
            refreshed = page.evaluate("JSON.parse(localStorage.getItem('sim2.game.player'))")
            assert refreshed["id"] == saved["id"] and refreshed["instanceId"] == saved["instanceId"]
            assert refreshed["sessionGeneration"] == server.session_generation
            assert refreshed["token"] != saved["token"]
            assert protected and set(protected) == {200}
    finally:
        page.close()


def test_holdings_open_from_normal_nav_and_appear_on_the_map(tmp_path, browser):
    database = create_instance(WORLDS / "gate_cities_sandbox", "web-holdings", tmp_path)
    with closing(ControlService(database)) as service:
        assert service.enter_player("hero", "builder", "旅人")["status"] == "accepted"
        store = service.platform.store
        with store.transaction():
            player = store.repositories.entities.get_agent("hero")
            player["initial_state"]["location_id"] = "low_fields"
            store.repositories.entities.update_agent("hero", player)
    with server_url(tmp_path, database) as url:
        page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
        try:
            page.goto(url)
            page.get_by_role("button", name="继续 旅人").first.click(timeout=20000)
            tab = page.get_by_role("button", name="产业", exact=True)
            tab.click(timeout=20000)
            panel = page.locator("#holdings-screen")
            plot = panel.locator('[data-plot="farm"]')
            for title in ("开办河滩炊坊", "开办旧站修具台", "开办高地驿舍"):
                pw.expect(panel.get_by_role("button", name=title)).to_be_enabled()
            plot.get_by_role("button", name="开办河滩炊坊").click()
            pw.expect(plot.get_by_text("经营中")).to_be_visible(timeout=30000)
            plot.get_by_role("button", name="开一炉饭").click()
            pw.expect(plot.locator(".holding-metrics")).to_contain_text("3 份", timeout=30000)
            pw.expect(panel.locator("#holdings-treasury details")).not_to_have_attribute("open", "")
            hire = plot.get_by_role("button", name="委托江遥经营炊坊")
            pw.expect(hire).to_be_disabled()
            pw.expect(hire).to_contain_text("两枚五十分")
            panel.get_by_text("存取经营金", exact=True).click()
            deposit = panel.get_by_role("button", name="存一枚进经营账")
            for _ in range(2):
                deposit.click()
                pw.expect(deposit).to_be_enabled(timeout=30000)
            pw.expect(hire).to_be_enabled()
            hire.click()
            pw.expect(plot).to_contain_text("江遥代管中", timeout=30000)
            pw.expect(plot.get_by_role("button", name="暂停江遥接单")).to_be_enabled()
            page.get_by_role("button", name="探索", exact=True).click()
            pw.expect(page.locator("#scene-objects")).to_contain_text("河滩炊坊 · 你的产业")
            page.locator("#open-map").click()
            pw.expect(page.locator("#map-canvas .map-owned")).to_contain_text("河滩炊坊")
        finally:
            page.close()


def test_north_bridge_goal_shows_the_shortage_and_starts_preparation(tmp_path, browser):
    database = create_instance(WORLDS / "gate_cities_sandbox", "web-bridge-preparation", tmp_path)
    with closing(ControlService(database)) as service:
        assert service.enter_player("hero", "builder", "旅人")["status"] == "accepted"
        result = service.player_act({"player_id": "hero", "behavior_ref": "behavior:survey_bridge"})
        assert result["status"] == "accepted", result
        RuntimeSupervisor(service).advance_manual(60, until_actor_idle="hero")
        assert service.player_view("hero")["world_properties"]["bridge_surveyed"] == 1
        store = service.platform.store
        with store.transaction():
            player = store.repositories.entities.get_agent("hero")
            player["inventory"].update(frame=0, metal=2)
            player["initial_state"]["location_id"] = "high_gate"
            store.repositories.entities.update_agent("hero", player)

    with server_url(tmp_path, database) as url:
        page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True)
        try:
            page.goto(url)
            page.get_by_role("button", name="继续 旅人").first.click(timeout=20000)
            goals = page.locator("#goal-list")
            bridge = goals.locator(".chapter-card").filter(has_text="让北栈桥能走重货")
            pw.expect(bridge).to_contain_text("框架件还差 2（现有 0 / 需要 2）")
            bridge.get_by_role("button", name="制作三份框架件").click()
            pw.expect(bridge.get_by_role("button", name="加固北栈桥货路")).to_be_enabled(timeout=30000)
            pw.expect(bridge).not_to_contain_text("框架件还差")
            bridge.get_by_role("button", name="加固北栈桥货路").click()
            pw.expect(bridge).to_have_count(0, timeout=30000)
            assert page.evaluate("async () => (await import('/js/core.js')).state.view.world_properties.bridge_improved") == 1
        finally:
            page.close()
