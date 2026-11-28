from __future__ import annotations

import base64
import hashlib
import json
from contextlib import closing
from pathlib import Path
from shutil import copytree
from threading import Thread
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

import httpx
import pytest
import yaml

from sim2.api.authoring import AuthoringService
from sim2.api.http import ControlHTTPServer
from sim2.api.service import ControlService
from sim2.api.v1.contracts import PlayerViewV1
from sim2.bootstrap.service import create_instance, upgrade_instance, validate_config
from sim2.llm.image_backend import ArtworkConfig, ImageBackend
from sim2.persistence.store import WorldStore
from sim2.platform.artwork import ArtworkWorker


WORLD = Path(__file__).parents[1] / "worlds" / "windward_skyfall"
ART = "assets/dawn.webp"
CAPE = "assets/lantern-cape.webp"


def test_standalone_artwork_worker_renders_encountered_content_and_retries(tmp_path: Path, monkeypatch):
    package = WORLD.parent / "starter_village"
    db = create_instance(package, "generated-art", tmp_path / "runtime")
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr("sim2.platform.artwork.time", SimpleNamespace(time=lambda: clock.now))
    config_file = tmp_path / "artwork.json"
    config_file.write_text(json.dumps({"endpoint": "http://127.0.0.1:9000/images", "model": "fixture",
                                       "quality": "high", "output_compression": 88}))
    image = (WORLD / ART).read_bytes()
    requests = []

    class Story:
        contexts: list[str] = []

        def complete_json(self, *, system, context):
            assert "Story Agent" in system
            assert "private goal" not in context
            self.contexts.append(context)
            return {"prompt": "Portrait of a watchkeeper" if "Square" in context else "A lookout on a basalt cliff"}

    def render(request):
        assert request.url.path == "/images"
        payload = json.loads(request.content)
        assert payload["model"] == "fixture" and payload["output_format"] == "webp"
        assert payload["quality"] == "high" and payload["output_compression"] == 88
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"not WebP").decode()}]})
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(image).decode()}]})

    with closing(ControlService(db)) as control, closing(WorldStore(db)) as worker_store, httpx.Client(transport=httpx.MockTransport(render)) as client:
        platform = control.platform
        assert platform.enter_player("hero", "courier", name="林晚")["status"] == "accepted"
        place = platform.submit_entity_proposal("god", {"entity_type": "location", "name": "Basalt Lookout",
            "description": "An open basalt cliff above the river.",
            "state": {"public": True, "safe": True, "routes": ["river_bridge"]}})
        assert place["status"] == "accepted", place
        resident = platform.submit_entity_proposal("god", {"entity_type": "agent", "name": "Aster Watchkeeper",
            "location_id": place["entity_id"], "state": {"role": "watchkeeper", "ultimate_goal": "A private goal.",
            "initial_bt_ids": ["travel"]}})
        assert resident["status"] == "accepted", resident
        story = Story()
        worker = ArtworkWorker(worker_store, story, ImageBackend(ArtworkConfig.load(config_file), client))
        assert worker.process_next() == "idle"
        travel = next(action for action in control.player_view("hero")["actions"]
                      if action.get("parameters", {}).get("location_id") == place["entity_id"])
        assert control.player_act({"player_id": "hero", "behavior_ref": travel["ref"],
                                   "parameters": travel["parameters"]})["status"] == "accepted"
        control.player_wait("hero", minutes=20)
        assert control.player_view("hero")["location"]["id"] == place["entity_id"]
        assert worker.process_next() == "retry"
        assert "artwork" not in control.player_view("hero")["location"]
        assert worker.process_next() == "ready"
        assert worker.process_next() == "idle"
        clock.now += 61
        assert worker.process_next() == "ready"
        assert worker.process_next() == "idle"
        view = PlayerViewV1.model_validate(control.player_view("hero")).model_dump()
        digest = hashlib.sha256(image).hexdigest()
        url = f"/world-art/{digest}.webp"
        assert view["location"]["artwork"] == url
        assert next(item for item in view["nearby_participants"] if item["id"] == resident["entity_id"])["portrait"] == url
        assert control.world_artwork(digest) == image
        assert len(story.contexts) == 2
        assert requests[0] == requests[2]
        assert [call["size"] for call in requests] == ["1536x1024", "1024x1024", "1536x1024"]
    upgrade_instance(package, db)
    with closing(ControlService(db)) as reloaded:
        assert reloaded.player_view("hero")["location"]["artwork"] == url
        assert reloaded.world_artwork(digest) == image


@pytest.mark.parametrize("options", [{"quality": "best"}, {"output_compression": 101}, {"output_compression": True}])
def test_image_backend_rejects_invalid_render_settings(tmp_path: Path, options: dict):
    config_file = tmp_path / "artwork.json"
    config_file.write_text(json.dumps({"endpoint": "http://127.0.0.1:9000/images", "model": "fixture", **options}))
    with pytest.raises(ValueError, match="Artwork (quality|output_compression)"):
        ArtworkConfig.load(config_file)


def test_artwork_worker_only_publishes_the_current_claim(tmp_path: Path) -> None:
    path = tmp_path / "artwork-claims.sqlite3"
    image = (WORLD / ART).read_bytes()
    digest = hashlib.sha256(image).hexdigest()
    with closing(WorldStore(path)) as first, closing(WorldStore(path)) as second:
        old = first.repositories.artwork
        current = second.repositories.artwork
        with first.transaction():
            assert old.claim("resident", 10, "old", 5, 8, 3)["attempts"] == 1
        with second.transaction():
            assert current.claim("resident", 10, "current", 7, 10, 3) is None
            assert current.claim("resident", 10, "current", 8, 11, 3)["attempts"] == 2
        with first.transaction():
            assert not old.finish("resident", "old", "stale", digest, image)
            assert old.get(digest) is None
        with second.transaction():
            assert current.finish("resident", "current", "approved", digest, image)
            assert current.claim("resident", 10, "duplicate", 20, 25, 3) is None
        assert old.generated_for("resident") == digest


def test_world_serves_deduplicated_artwork_from_a_portable_snapshot(tmp_path: Path) -> None:
    package = tmp_path / "package"
    copytree(WORLD, package)
    setting_path = package / "setting.yaml"
    setting = yaml.safe_load(setting_path.read_text())
    setting["ui"]["scene_art"]["windworn_woods"] = ART
    setting_path.write_text(yaml.safe_dump(setting, allow_unicode=True))
    artwork = (package / ART).read_bytes()
    scene = (package / CAPE).read_bytes()
    digest = hashlib.sha256(artwork).hexdigest()
    original = create_instance(package, "artwork", tmp_path / "runtime")
    snapshot = tmp_path / "portable.sqlite3"
    with closing(ControlService(original)) as control:
        ui = control.world_info()["ui"]
        assert ui["cover_art"] == ui["scene_art"]["windworn_woods"] == f"/world-art/{digest}.webp"
        assert ui["scene_art"]["lantern_cape"] != ui["cover_art"]
        with control.platform.store.read_transaction() as conn:
            assert conn.execute("SELECT COUNT(*) FROM world_artwork").fetchone()[0] == 2
        control.snapshot(snapshot)

    (package / ART).unlink()
    (package / CAPE).unlink()
    server = ControlHTTPServer(("127.0.0.1", 0), snapshot, runtime_root=tmp_path / "restored")
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/api/v1/world") as response:
            view = json.loads(response.read())
        assert view["ui"]["cover_art"] == ui["cover_art"]
        with urlopen(base + ui["cover_art"]) as response:
            assert response.read() == artwork
            assert response.headers.get_content_type() == "image/webp"
            assert "immutable" in response.headers["cache-control"]
        with urlopen(base + ui["scene_art"]["lantern_cape"]) as response:
            assert response.read() == scene
        with pytest.raises(HTTPError) as error:
            urlopen(base + f"/world-art/{'0' * 64}.webp")
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_upgrade_replaces_artwork_and_preserves_world_state(tmp_path: Path) -> None:
    package = tmp_path / "package"
    copytree(WORLD, package)
    db = create_instance(package, "artwork-upgrade", tmp_path / "runtime")
    with closing(ControlService(db)) as control:
        prior_url = control.world_info()["ui"]["cover_art"]
        store = control.platform.store
        with store.transaction():
            store.repositories.metadata.set("world_minute", 1234)
        prior_hash = store.repositories.metadata.get("config_hash")
    path = package / ART
    original = path.read_bytes()
    # A WebP container may include additional chunks. Change its bytes without
    # replacing the image fixture or changing its authored filename.
    replacement = original[:4] + (len(original) + 2).to_bytes(4, "little") + original[8:] + b"JUNK\x02\x00\x00\x00\x00\x00"
    path.write_bytes(replacement)
    result = upgrade_instance(package, db)
    assert result["config_hash"] != prior_hash

    with closing(ControlService(db)) as control:
        info = control.world_info()
        assert info["world_minute"] == 1234
        assert info["ui"]["cover_art"] != prior_url
        digest = hashlib.sha256(replacement).hexdigest()
        assert info["ui"]["cover_art"] == f"/world-art/{digest}.webp"
        assert control.world_artwork(digest) == replacement


def test_unsafe_or_invalid_artwork_fails_world_validation(tmp_path: Path) -> None:
    package = tmp_path / "package"
    copytree(WORLD, package)
    setting_path = package / "setting.yaml"
    setting = yaml.safe_load(setting_path.read_text())
    setting["ui"]["cover_art"] = "../outside.webp"
    setting_path.write_text(yaml.safe_dump(setting, allow_unicode=True))
    assert any("package-relative" in error for error in validate_config(package)["errors"])
    setting["ui"]["cover_art"] = ART
    setting_path.write_text(yaml.safe_dump(setting, allow_unicode=True))
    (package / ART).write_bytes(b"not a WebP")
    assert any("must be a WebP" in error for error in validate_config(package)["errors"])
    opened = AuthoringService(tmp_path).open_package(package)
    assert opened["status"] == "rejected"
    assert opened["template"]["world"]["ui"]["cover_art"] == ART
