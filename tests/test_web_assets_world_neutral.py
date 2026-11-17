"""The web client's static assets name no shipped world; the world supplies its own title."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
WEB = ROOT / "src" / "sim2" / "web"
ASSETS = ("index.html", "app.js", "admin.html", "admin.js")
# Wording that once leaked from one world into the client: the placeholder genre and the empty-scene line.
LEAKED_WORDS = ("海岸生存", "潮声")


def _read_assets() -> dict[str, str]:
    return {name: (WEB / name).read_text(encoding="utf-8") for name in ASSETS}


def _world_names() -> dict[str, list[str]]:
    """The display name, genre and season label every shipped world declares in setting.yaml."""

    names: dict[str, list[str]] = {}
    for setting_path in sorted((ROOT / "worlds").glob("*/setting.yaml")):
        setting = yaml.safe_load(setting_path.read_text(encoding="utf-8")) or {}
        values = [setting.get("name"), setting.get("genre"), (setting.get("world_clock") or {}).get("season")]
        names[setting_path.parent.name] = [str(value) for value in values if value and len(str(value)) >= 3]
    return names


def test_static_client_assets_name_no_shipped_world():
    assets = _read_assets()
    worlds = _world_names()
    assert worlds  # the check must actually look at the shipped worlds
    for world, values in worlds.items():
        for value in values:
            for asset, text in assets.items():
                assert value not in text, f"{asset} carries {value!r} from the world {world}"
    for word in LEAKED_WORDS:
        for asset, text in assets.items():
            assert word not in text, f"{asset} carries the world wording {word!r}"


def test_map_dialog_heading_is_filled_from_the_world_title():
    assets = _read_assets()
    assert 'id="map-world-title"' in assets["index.html"]
    assert '$("map-world-title").textContent = state.world?.title' in assets["app.js"]
