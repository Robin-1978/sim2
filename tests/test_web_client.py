from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import yaml

from sim2.api.http import ControlHTTPServer
from sim2.api.authoring import AuthoringService
from sim2.bootstrap.service import create_instance


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
    try:
        with urlopen(base + "/") as response:
            html = response.read().decode("utf-8")
        assert "设计世界" in html
        assert "开始游戏" in html
        assert "现在，你在这里" in html
        assert "现在可以做什么" in html
        assert "scene-inspector" in html
        assert "创建新游戏" in html
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

        opened = _json_request(base + "/authoring/open", {})
        assert opened["status"] == "passed"
        assert opened["template"]["world"]["id"] == "starter_village"
        opened_compile = _json_request(base + "/authoring/compile", {"template": opened["template"], "source_path": opened["source_path"]})
        assert opened_compile["status"] == "passed"

        compiled = _json_request(base + "/authoring/compile", {"template": template, "draft_revision": 4})
        assert compiled["status"] == "passed"
        assert compiled["draft_revision"] == 4
        assert "manifest.yaml" in compiled["files"]
        assert compiled["diagnostics"] == []

        rejected = _json_request(base + "/authoring/compile", {"template": {"world": {}}, "draft_revision": 5})
        assert rejected["status"] == "rejected"
        assert rejected["diagnostics"]
        assert rejected["diagnostics"][0]["code"] == "template_validation_error"

        smoke = _json_request(base + "/authoring/smoke", {"template": template, "minutes": 1})
        assert smoke["status"] == "passed"
        assert smoke["smoke"]["world_minute"] == 1

        exported = _json_request(base + "/authoring/export", {"template": template})
        assert exported["status"] == "passed"
        assert exported["package_format"] == "yaml-files"
        assert "manifest.yaml" in exported["files"]

        build_rejected = _json_request(base + "/authoring/build", {"brief": ""})
        assert build_rejected["status"] == "rejected"
        assert "brief" in build_rejected["errors"][0]
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
    try:
        with urlopen(base + "/authoring/catalog") as response:
            catalog = json.loads(response.read())
        assert catalog["status"] == "ok"
        starter = next(item for item in catalog["packages"] if item["id"] == "starter_village")
        assert starter["status"] == "passed"
        created = _json_request(base + "/authoring/create", {"package_id": "starter_village", "instance_id": "catalog-child"})
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

