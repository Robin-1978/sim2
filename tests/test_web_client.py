from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import yaml

from sim2.api.http import ControlHTTPServer
from sim2.api.authoring import AuthoringService
from sim2.bootstrap.service import create_instance


def _operator_headers(server: ControlHTTPServer) -> dict[str, str]:
    return {"x-sim2-session": server.issue_session("operator")}


def _json_request(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json", **(headers or {})},
        method="POST",
    )
    with urlopen(request) as response:
        return json.loads(response.read())


def test_http_serves_runtime_and_authoring_workbench(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "web-world", tmp_path / "runtime")
    server = ControlHTTPServer(("127.0.0.1", 0), db_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    template = yaml.safe_load((Path(__file__).parents[1] / "examples" / "orbital-world-template.yaml").read_text(encoding="utf-8"))
    operator = _operator_headers(server)
    try:
        with urlopen(base + "/") as response:
            html = response.read().decode("utf-8")
        assert "设计世界" in html
        assert "开始游戏" in html
        assert "现在，你在这里" in html
        assert "现在可以做什么" in html
        assert "scene-inspector" in html
        assert "创建并打开游戏" in html
        assert "开始自动运行" in html
        assert "解锁管理" in html
        assert 'id="instance-select"' in html
        with urlopen(base + "/app.css") as response:
            assert b"--cyan" in response.read()
        with urlopen(base + "/authoring/schema") as response:
            schema = json.loads(response.read())
        assert schema["status"] == "ok"
        assert "behavior_trees" in schema["schema"]["properties"]
        with urlopen(base + "/authoring/catalog") as response:
            catalog = json.loads(response.read())
        assert catalog["status"] == "ok"
        assert any(item["id"] == "starter_village" for item in catalog["packages"])

        opened = _json_request(base + "/authoring/open", {}, headers=operator)
        assert opened["status"] == "passed"
        assert opened["template"]["world"]["id"] == "starter_village"
        opened_compile = _json_request(base + "/authoring/compile", {"template": opened["template"], "source_path": opened["source_path"]}, headers=operator)
        assert opened_compile["status"] == "passed"

        compiled = _json_request(base + "/authoring/compile", {"template": template, "draft_revision": 4}, headers=operator)
        assert compiled["status"] == "passed"
        assert compiled["draft_revision"] == 4
        assert "manifest.yaml" in compiled["files"]
        assert compiled["diagnostics"] == []

        rejected = _json_request(base + "/authoring/compile", {"template": {"world": {}}, "draft_revision": 5}, headers=operator)
        assert rejected["status"] == "rejected"
        assert rejected["diagnostics"]
        assert rejected["diagnostics"][0]["code"] == "template_validation_error"

        smoke = _json_request(base + "/authoring/smoke", {"template": template, "minutes": 1}, headers=operator)
        assert smoke["status"] == "passed"
        assert smoke["smoke"]["world_minute"] == 1

        exported = _json_request(base + "/authoring/export", {"template": template}, headers=operator)
        assert exported["status"] == "passed"
        assert exported["package_format"] == "yaml-files"
        assert "manifest.yaml" in exported["files"]

        build_rejected = _json_request(base + "/authoring/build", {"brief": ""}, headers=operator)
        assert build_rejected["status"] == "rejected"
        assert "brief" in build_rejected["errors"][0]
        try:
            _json_request(base + "/dialogue", {"speaker_id": "god", "listener_id": "nobody", "message": "test"})
        except HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError("God-identity dialogue must require an operator session")
    finally:
        server.close()
        thread.join(timeout=2)


def test_authoring_path_is_limited_to_configured_root(tmp_path: Path):
    root = tmp_path / "worlds"
    root.mkdir()
    service = AuthoringService(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    result = service.open_package(outside)
    assert result["status"] == "rejected"
    assert "authoring_root" in result["errors"][0]


def test_web_catalog_can_create_an_independent_instance_from_a_template(tmp_path: Path):
    config_root = Path(__file__).parents[1] / "worlds"
    db_path = create_instance(Path(__file__).parents[1] / "worlds" / "starter_village", "catalog-host", tmp_path / "runtime")
    server = ControlHTTPServer(("127.0.0.1", 0), db_path, authoring_root=config_root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    operator = _operator_headers(server)
    try:
        try:
            _json_request(base + "/authoring/create", {"package_id": "starter_village", "instance_id": "unauthorized-child"})
        except HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError("authoring create must require an operator session")
        with urlopen(base + "/authoring/catalog") as response:
            catalog = json.loads(response.read())
        assert catalog["status"] == "ok"
        starter = next(item for item in catalog["packages"] if item["id"] == "starter_village")
        assert starter["status"] == "passed"
        created = _json_request(base + "/authoring/create", {"package_id": "starter_village", "instance_id": "catalog-child"}, headers=operator)
        assert created["status"] == "passed"
        assert Path(created["db_path"]).is_file()
        assert created["instance_id"] == "catalog-child"
    finally:
        server.close()
        thread.join(timeout=2)


def test_web_player_loop_exposes_actions_and_submits_through_platform(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "player-web", tmp_path / "runtime")
    server = ControlHTTPServer(("127.0.0.1", 0), db_path, authoring_root=Path(__file__).parents[1] / "worlds")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/player/entry") as response:
            entry = json.loads(response.read())
        assert entry["status"] == "ok"
        assert entry["origins"]
        entered = _json_request(base + "/players", {"player_id": "web_player", "origin_id": entry["origins"][0]["id"], "name": "Web Player"})
        assert entered["status"] == "accepted"
        try:
            urlopen(base + "/player/view?player_id=web_player")
        except HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError("player view must require a local player session")
        player_headers = {"x-sim2-session": entered["session_token"]}
        with urlopen(Request(base + "/player/view?player_id=web_player", headers=player_headers)) as response:
            view = json.loads(response.read())
        assert view["status"] == "ok"
        assert view["player"]["name"] == "Web Player"
        assert view["actions"]
        travel = next(item for item in view["actions"] if item.get("kind") == "travel")
        acted = _json_request(base + "/player/act", {"player_id": "web_player", "behavior_ref": travel["ref"], "parameters": travel["parameters"], "confidence": 1, "reason": "Player chose the route."}, headers=player_headers)
        assert acted["status"] == "accepted"
        with urlopen(Request(base + "/player/view?player_id=web_player", headers=player_headers)) as response:
            active_view = json.loads(response.read())
        assert active_view["active_task"]["action"] or active_view["active_task"]["bt"]
        assert not any(item.get("kind") == "travel" for item in active_view["actions"])
    finally:
        server.close()
        thread.join(timeout=2)


class _SlowWaitProvider:
    cooldown_remaining = 0.0

    def complete_json(self, *, system, context, tools=None, messages=None):
        time.sleep(0.02)
        return {"intent_type": "wait", "reason": "Test runtime advances without model side effects."}


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition was not reached before timeout")


def test_web_console_covers_all_cli_commands(tmp_path: Path):
    import shutil

    worlds_root = tmp_path / "console-worlds"
    shutil.copytree(Path(__file__).parents[1] / "worlds" / "starter_village", worlds_root / "starter_village")
    runtime_root = tmp_path / "runtime"
    db_path = create_instance(worlds_root / "starter_village", "console-all", runtime_root)

    def providers(_name):
        return {role: _SlowWaitProvider() for role in ("agent_decision", "agent_reflection", "god", "story")}

    server = ControlHTTPServer(("127.0.0.1", 0), db_path, authoring_root=worlds_root, runtime_provider_factory=providers)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    operator = _operator_headers(server)
    try:
        with urlopen(base + "/") as response:
            html = response.read().decode("utf-8")
        assert "命令" in html
        assert "console-result" in html
        assert "console-init" in html

        validated = _json_request(base + "/authoring/validate", {"package_id": "starter_village"}, headers=operator)
        assert validated["status"] == "passed", validated
        described = _json_request(base + "/authoring/describe", {"package_id": "starter_village"}, headers=operator)
        assert described["status"] == "passed", described
        assert described["world_id"] == "starter_village"

        with urlopen(base + "/authoring/schema") as response:
            assert json.loads(response.read())["status"] == "ok"

        opened = _json_request(base + "/authoring/open", {}, headers=operator)
        assert opened["status"] == "passed"
        built = _json_request(
            base + "/authoring/build-world",
            {"template": opened["template"], "source_path": opened["source_path"], "output": "console_built", "overwrite": True},
            headers=operator,
        )
        assert built["status"] == "passed", built
        assert (worlds_root / "console_built" / "manifest.yaml").is_file()

        inited = _json_request(
            base + "/instances/init",
            {"package_id": "starter_village", "instance_id": "console-init-child"},
            headers=operator,
        )
        assert inited["status"] == "passed", inited

        observer = json.loads(urlopen(base + "/observer?limit=5").read())
        agent_id = observer["agents"][0]["id"]
        decided = _json_request(base + "/decide", {"agent_id": agent_id, "role": "agent_decision"}, headers=operator)
        assert decided["result"]["status"] in {"accepted", "wait", "skipped", "rejected"}, decided

        simulated = _json_request(base + "/simulate", {"minutes": 2}, headers=operator)
        assert simulated["status"] == "ok", simulated
        assert simulated["report"]["end_minute"] >= simulated["report"]["start_minute"] + 2

        created = _json_request(base + "/snapshots", {"label": "console"}, headers=operator)
        assert created["status"] == "ok", created
        listed = json.loads(Request(base + "/snapshots", headers=operator).get_full_url() and urlopen(Request(base + "/snapshots", headers=operator)).read())
        assert any(item["snapshot_id"] == created["snapshot_id"] for item in listed["snapshots"])
        restored = _json_request(base + "/restore", {"snapshot_id": created["snapshot_id"]}, headers=operator)
        assert restored["status"] == "ok", restored

        with urlopen(base + "/player/entry") as response:
            entry = json.loads(response.read())
        player_headers = {}
        entered = _json_request(base + "/players", {"player_id": "console_p1", "origin_id": entry["origins"][0]["id"]}, headers=None)
        assert entered["status"] == "accepted"
        player_headers = {"x-sim2-session": entered["session_token"]}
        with urlopen(Request(base + "/trade/offers?agent_id=console_p1", headers=player_headers)) as response:
            assert json.loads(response.read())["status"] == "ok"
        # Invalid offer still goes through Platform validation instead of 500.
        bad_counter = _json_request(
            base + "/trade/counter",
            {"counterparty_id": "console_p1", "offer_id": "missing", "give": {}, "want": {}},
            headers=player_headers,
        )
        assert bad_counter["status"] == "rejected"
        bad_reject = _json_request(
            base + "/trade/reject", {"recipient_id": "console_p1", "offer_id": "missing"}, headers=player_headers
        )
        assert bad_reject["status"] == "rejected"
        injected = _json_request(base + "/events", {"event_type": "story.note", "payload": {"text": "console"}}, headers=operator)
        assert injected["status"] in {"accepted", "rejected"}, injected
    finally:
        server.close()
        thread.join(timeout=3)


def test_web_runtime_controls_and_instance_hot_switch(tmp_path: Path):
    world = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    runtime_root = tmp_path / "runtime"
    first = create_instance(world, "web-first", runtime_root)
    create_instance(world, "web-second", runtime_root)

    def providers(_name):
        return {
            role: _SlowWaitProvider()
            for role in ("agent_decision", "agent_reflection", "god", "story")
        }

    server = ControlHTTPServer(("127.0.0.1", 0), first, runtime_provider_factory=providers)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    operator = _operator_headers(server)
    try:
        try:
            _json_request(base + "/runtime/start", {"max_world_minutes": 2, "max_llm_calls": 20})
        except HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError("runtime start must require an operator session")

        with urlopen(base + "/instances") as response:
            instances = json.loads(response.read())
        assert instances["status"] == "ok"
        assert {item["instance_id"] for item in instances["instances"]} >= {"web-first", "web-second"}
        assert next(item for item in instances["instances"] if item["instance_id"] == "web-first")["active"]

        stale_player_token = server.issue_session("player", player_id="han_qiu")

        switched = _json_request(base + "/instances/activate", {"instance_id": "web-second"}, headers=operator)
        assert switched["status"] == "accepted", switched
        assert server.database_path == runtime_root.resolve() / "instances" / "web-second" / "world.sqlite3"
        assert server.registry.latest() == server.database_path
        try:
            urlopen(Request(base + "/player/view?player_id=han_qiu", headers={"x-sim2-session": stale_player_token}))
        except HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError("player sessions must be scoped to one world instance")

        stopped_without_worker = _json_request(base + "/runtime/stop", {"reason": "test-idle-stop"}, headers=operator)
        assert stopped_without_worker["state"] == "stopped", stopped_without_worker
        assert json.loads(urlopen(base + "/status").read())["status"] == "paused"

        budget_run = _json_request(
            base + "/runtime/start",
            {"max_world_minutes": 100, "max_llm_calls": 1},
            headers=operator,
        )
        assert budget_run["status"] == "accepted", budget_run
        _wait_until(lambda: not json.loads(urlopen(base + "/runtime").read())["running"])
        assert json.loads(urlopen(base + "/runtime").read())["state"] == "paused_budget"

        started = _json_request(
            base + "/runtime/start",
            {"max_world_minutes": 100, "max_llm_calls": 200},
            headers=operator,
        )
        assert started["status"] == "accepted", started
        duplicate = _json_request(
            base + "/runtime/start",
            {"max_world_minutes": 100, "max_llm_calls": 200},
            headers=operator,
        )
        assert duplicate["status"] == "rejected"
        assert duplicate["failed_check"] == "already_running"
        manual_wait = _json_request(
            base + "/player/wait",
            {"player_id": "han_qiu"},
            headers=operator,
        )
        assert manual_wait["status"] == "rejected"
        assert manual_wait["failed_check"] == "runtime_running"
        _wait_until(lambda: json.loads(urlopen(base + "/runtime").read())["state"] in {"running", "pausing", "paused"})

        paused = _json_request(base + "/runtime/pause", {"reason": "test"}, headers=operator)
        assert paused["status"] in {"pausing", "paused"}, paused
        _wait_until(lambda: not json.loads(urlopen(base + "/runtime").read())["running"])
        runtime = json.loads(urlopen(base + "/runtime").read())
        assert runtime["state"] in {"paused", "cooldown", "paused_budget"}

        resumed = _json_request(base + "/runtime/resume", {"reason": "test"}, headers=operator)
        assert resumed["status"] == "accepted", resumed
        assert resumed["run_id"] == started["run_id"]
        assert resumed["llm_calls_this_run"] >= runtime["llm_calls_this_run"]
        _wait_until(lambda: json.loads(urlopen(base + "/runtime").read())["running"])

        stopped = _json_request(base + "/runtime/stop", {"reason": "test-stop"}, headers=operator)
        assert stopped["status"] == "accepted", stopped
        _wait_until(lambda: not json.loads(urlopen(base + "/runtime").read())["running"])
        assert json.loads(urlopen(base + "/runtime").read())["state"] == "stopped"

        before_step = json.loads(urlopen(base + "/runtime").read())["world_minute"]
        stepped = _json_request(base + "/runtime/step", {}, headers=operator)
        assert stepped["to_minute"] == before_step + 1
        assert stepped["runtime"]["state"] == "paused"
    finally:
        server.close()
        thread.join(timeout=3)


def test_web_dialogue_accepts_names_and_reports_missing_participant(tmp_path: Path):
    from sim2.bootstrap.service import create_instance as _create

    config = Path(__file__).parents[1] / "worlds" / "tidebound_commons"
    db_path = _create(config, "dialogue-names", tmp_path / "runtime")
    server = ControlHTTPServer(("127.0.0.1", 0), db_path, authoring_root=Path(__file__).parents[1] / "worlds")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/player/entry") as response:
            entry = json.loads(response.read())
        entered = _json_request(base + "/players", {"player_id": "player_one", "origin_id": entry["origins"][0]["id"], "name": "Ari"})
        assert entered["status"] == "accepted"
        player_headers = {"x-sim2-session": entered["session_token"]}
        by_names = _json_request(
            base + "/dialogue",
            {"speaker_id": "Ari", "listener_id": "Mara", "topic": "general", "message": "hi by names"},
            headers=player_headers,
        )
        assert by_names["status"] == "accepted", by_names
        missing = _json_request(
            base + "/dialogue",
            {"speaker_id": "player_one", "listener_id": "nobody_xyz", "topic": "general", "message": "hi"},
            headers=player_headers,
        )
        assert missing["status"] == "rejected", missing
        assert missing["missing"] == "nobody_xyz"
        assert "mara" in missing["allowed_values"]
    finally:
        server.close()
        thread.join(timeout=3)


def test_observer_feeds_show_npc_dialogue_and_decisions(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "tidebound_commons"
    db_path = create_instance(config, "observer-feeds", tmp_path / "runtime")
    server = ControlHTTPServer(("127.0.0.1", 0), db_path, authoring_root=Path(__file__).parents[1] / "worlds")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/") as response:
            html = response.read().decode("utf-8")
        assert "NPC 对话" in html
        assert "决策动态" in html
        with urlopen(base + "/observer/dialogue?limit=10") as response:
            assert json.loads(response.read())["exchanges"] == []
        assert server.control.platform.submit_dialogue("mara", "selene", "general", "The tide is turning.")["status"] == "accepted"
        with urlopen(base + "/observer/dialogue?limit=10") as response:
            exchanges = json.loads(response.read())["exchanges"]
        assert exchanges[-1]["message"] == "The tide is turning."
        assert exchanges[-1]["speaker"] == "Mara"
        server.control.platform.inject_event("behavior.completed", {"behavior_id": "travel", "result": "arrived safely"}, actor_id="mara")
        with urlopen(base + "/observer/decisions?limit=10") as response:
            decisions = json.loads(response.read())["decisions"]
        assert decisions[-1]["actor"] == "Mara"
        assert decisions[-1]["event_type"] == "behavior.completed"
    finally:
        server.close()
        thread.join(timeout=3)


def _sse_read_event(fp, timeout_at):
    import time as _time

    event_name, data_lines = None, []
    while _time.monotonic() < timeout_at:
        line = fp.readline().decode("utf-8", "replace")
        if not line:
            break
        line = line.rstrip("\n").rstrip("\r")
        if line == "":
            if event_name:
                return event_name, "\n".join(data_lines)
            event_name, data_lines = None, []
        elif line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())
        # comment/heartbeat lines (": ...") are ignored
    raise AssertionError("timed out waiting for SSE event")


def test_web_sse_pushes_timeline_deltas(tmp_path: Path):
    import http.client
    import time as _time

    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "web-sse", tmp_path / "runtime")
    server = ControlHTTPServer(("127.0.0.1", 0), db_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    operator = _operator_headers(server)
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=12)
    try:
        conn.request("GET", "/events/stream?after=0")
        response = conn.getresponse()
        assert response.status == 200
        assert response.getheader("content-type", "").startswith("text/event-stream")
        name, payload = _sse_read_event(response.fp, _time.monotonic() + 8)
        assert name == "ready", payload
        assert json.loads(payload)["after"] == 0
        injected = _json_request(base + "/events", {"event_type": "story.note", "payload": {"text": "sse-ping"}}, headers=operator)
        assert injected["status"] == "accepted"
        deadline = _time.monotonic() + 10
        seen = False
        while _time.monotonic() < deadline and not seen:
            name, payload = _sse_read_event(response.fp, deadline)
            if name == "timeline":
                seen = any(item.get("event_id") == injected["event_id"] for item in json.loads(payload).get("events", []))
        assert seen, "SSE did not push the injected event"
    finally:
        conn.close()
        server.close()
        thread.join(timeout=3)


def test_web_idempotency_key_dedupes_retries(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "web-idem", tmp_path / "runtime")
    server = ControlHTTPServer(("127.0.0.1", 0), db_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    operator = _operator_headers(server)
    try:
        before = json.loads(urlopen(base + "/timeline?limit=500").read())
        before_ids = {item["event_id"] for item in before["events"]}
        first = _json_request(base + "/events", {"event_type": "story.note", "payload": {"n": 1}, "idempotency_key": "retry-1"}, headers=operator)
        second = _json_request(base + "/events", {"event_type": "story.note", "payload": {"n": 2}, "idempotency_key": "retry-1"}, headers=operator)
        assert first["event_id"] == second["event_id"]
        third = _json_request(base + "/events", {"event_type": "story.note", "payload": {"n": 3}, "idempotency_key": "retry-2"}, headers=operator)
        assert third["event_id"] != first["event_id"]
        after = json.loads(urlopen(base + "/timeline?limit=500").read())
        new_ids = {item["event_id"] for item in after["events"]} - before_ids
        assert new_ids == {first["event_id"], third["event_id"]}
    finally:
        server.close()
        thread.join(timeout=3)


def test_web_event_related_traces_causal_chain(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "web-related", tmp_path / "runtime")
    server = ControlHTTPServer(("127.0.0.1", 0), db_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    operator = _operator_headers(server)
    try:
        first = _json_request(base + "/events", {"event_type": "behavior.started", "payload": {"behavior_id": "travel", "correlation_id": "chain-9"}}, headers=operator)
        second = _json_request(base + "/events", {"event_type": "behavior.completed", "payload": {"behavior_id": "travel", "result": "ok", "correlation_id": "chain-9"}}, headers=operator)
        with urlopen(base + f"/events/related?event_id={second['event_id']}") as response:
            chain = json.loads(response.read())
        assert chain["status"] == "ok"
        assert chain["event"]["event_id"] == second["event_id"]
        assert first["event_id"] in [item["event_id"] for item in chain["related"]]
        with urlopen(base + "/events/related?event_id=999999999") as response:
            missing = json.loads(response.read())
        assert missing["status"] == "rejected"
    finally:
        server.close()
        thread.join(timeout=3)


def test_web_runtime_history_and_snapshot_download(tmp_path: Path):
    world = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    runtime_root = tmp_path / "runtime"
    first = create_instance(world, "web-hist", runtime_root)

    def providers(_name):
        return {role: _SlowWaitProvider() for role in ("agent_decision", "agent_reflection", "god", "story")}

    server = ControlHTTPServer(("127.0.0.1", 0), first, runtime_provider_factory=providers)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    operator = _operator_headers(server)
    try:
        started = _json_request(base + "/runtime/start", {"max_world_minutes": 50, "max_llm_calls": 1}, headers=operator)
        assert started["status"] == "accepted", started
        _wait_until(lambda: not json.loads(urlopen(base + "/runtime").read())["running"])
        stopped = _json_request(base + "/runtime/stop", {"reason": "hist-stop"}, headers=operator)
        assert stopped["status"] == "accepted", stopped
        history = json.loads(urlopen(base + "/runtime").read()).get("history", [])
        assert history, "runtime history should record finished segments"
        assert history[0]["run_id"] == started["run_id"]
        assert "llm_calls" in history[0] and "prompt_tokens" in history[0]

        created = _json_request(base + "/snapshots", {"label": "hist-dl"}, headers=operator)
        assert created["status"] == "ok", created
        download = Request(base + f"/snapshots/download?snapshot_id={created['snapshot_id']}", headers=operator)
        with urlopen(download) as response:
            assert response.status == 200
            assert response.headers.get("content-type") == "application/octet-stream"
            assert "attachment" in (response.headers.get("content-disposition") or "")
            assert response.read()[:16] == b"SQLite format 3\x00"
        try:
            urlopen(base + f"/snapshots/download?snapshot_id={created['snapshot_id']}")
        except HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError("snapshot download must require an operator session")
    finally:
        server.close()
        thread.join(timeout=3)


def test_web_full_client_surfaces_new_panels(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "web-panels", tmp_path / "runtime")
    server = ControlHTTPServer(("127.0.0.1", 0), db_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/") as response:
            html = response.read().decode("utf-8")
        for marker in (
            'class="skip-link"',
            'id="player-trade-target"',
            'id="player-send-trade"',
            'id="runtime-history-list"',
            'id="npc-decisions-list"',
            'id="console-result"',
            'aria-labelledby="npc-decisions-title"',
        ):
            assert marker in html, marker
    finally:
        server.close()
        thread.join(timeout=2)


def test_web_client_keeps_session_header_when_posting_with_idempotency_key():
    """Idempotent writes must not drop the operator session header.

    ``write()`` passes ``Idempotency-Key`` as per-request headers. Spreading
    the fetch options after ``headers`` replaced the auth and content-type
    headers, so every runtime command returned 403 and reopened the unlock
    dialog even though unlocking had succeeded.
    """

    source = (Path(__file__).parents[1] / "src" / "sim2" / "web" / "app.js").read_text(encoding="utf-8")
    request_body = source.split("async function request(", 1)[1].split("function newIdempotencyKey", 1)[0]
    fetch_body = request_body.split("await fetch(path, {", 1)[1].split("});", 1)[0]
    assert "x-sim2-session" in fetch_body
    assert fetch_body.index("...options") < fetch_body.index("headers:")
    assert "rememberOperatorToken(result.session_token)" in source


def test_operator_session_exchange_and_dialog_wiring(tmp_path: Path):
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "web-session", tmp_path / "runtime")
    server = ControlHTTPServer(("127.0.0.1", 0), db_path, operator_token="fixed-test-token")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        ok = _json_request(base + "/session", {"role": "operator", "operator_token": "fixed-test-token"})
        assert ok["status"] == "ok" and ok["role"] == "operator" and ok["session_token"]
        try:
            _json_request(base + "/session", {"role": "operator", "operator_token": "wrong-token"})
        except HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError("wrong operator token must be rejected")
        with urlopen(base + "/") as response:
            html = response.read().decode("utf-8")
        # The unlock form must route through submit (covers Enter/autofill),
        # and cancel must not submit the dialog form.
        assert 'id="operator-cancel" class="button ghost" type="button"' in html
        assert 'id="operator-submit" class="button primary" type="submit"' in html
    finally:
        server.close()
        thread.join(timeout=3)
