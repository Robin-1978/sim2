from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import yaml

from sim2.api.http import ControlHTTPServer
from sim2.api.authoring import AuthoringService
from sim2.bootstrap.service import create_instance


def _json_request(url: str, payload: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
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
        with urlopen(base + "/app.css") as response:
            assert b"--cyan" in response.read()
        with urlopen(base + "/authoring/schema") as response:
            schema = json.loads(response.read())
        assert schema["status"] == "ok"
        assert "behavior_trees" in schema["schema"]["properties"]

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

