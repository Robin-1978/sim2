from __future__ import annotations

import hashlib
import json
from contextlib import closing
from pathlib import Path
from shutil import copytree
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
import yaml

from sim2.api.authoring import AuthoringService
from sim2.api.http import ControlHTTPServer
from sim2.api.service import ControlService
from sim2.bootstrap.service import create_instance, upgrade_instance, validate_config


WORLD = Path(__file__).parents[1] / "worlds" / "windward_skyfall"
ART = "assets/dawn.webp"
CAPE = "assets/lantern-cape.webp"


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
