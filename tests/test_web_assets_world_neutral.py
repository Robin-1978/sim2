"""The web client's static assets name no shipped world; the world supplies its own title."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
WEB = ROOT / "src" / "sim2" / "web"
# Wording that once leaked from one world into the client: the placeholder genre and the empty-scene line.
LEAKED_WORDS = (
    "海岸生存",
    "潮声",
    "公社",
    "潮门",
    "集雨器",
    "信号透镜",
    "公共工具架",
    "盐沼",
    "灯笼巷",
    "观测台",
    "玛拉",
    "伊沃",
    "塞琳",
    "塔罗",
    "妮娅",
    "海藻口粮",
    "芦苇绳",
    "修理工具包",
    "marsh_rest_decided",
    "rationing_decided",
    "repair_priority_decided",
    "tide_gate",
    "rain_catcher",
    "signal_lens",
    "commons_tool_rack",
    "tidebound",
)


def _read_assets() -> dict[str, str]:
    """Read every shipped browser asset, including native ES modules."""

    result: dict[str, str] = {}
    for path in sorted(WEB.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".html", ".css", ".js"}:
            continue
        result[str(path.relative_to(WEB))] = path.read_text(encoding="utf-8")
    return result


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
    assert "index.html" in assets and "js/app.js" in assets and "js/admin_app.js" in assets
    worlds = _world_names()
    assert worlds  # the check must actually look at the shipped worlds
    for world, values in worlds.items():
        for value in values:
            for asset, text in assets.items():
                assert value not in text, f"{asset} carries {value!r} from the world {world}"
    for word in LEAKED_WORDS:
        for asset, text in assets.items():
            assert word not in text, f"{asset} carries the world wording {word!r}"


def test_world_vocabulary_moves_into_setting_ui():
    setting = yaml.safe_load((ROOT / "worlds" / "tidebound_commons" / "setting.yaml").read_text(encoding="utf-8"))
    ui = setting["ui"]
    assert ui["event_kinds"]["repair"]
    assert ui["event_titles"]["weather"] == "风暴"
    assert ui["result_labels"]["marsh_rest_decided"] == "休养已安排"
    assert ui["reason_labels"]["station.available"] == "工坊当前不可用"
    panels = (WEB / "js" / "render_panels.js").read_text(encoding="utf-8")
    assert "uiConfig().event_kinds" in panels
    assert "uiConfig().event_titles" in panels


def test_map_dialog_heading_is_filled_from_the_world_title():
    assets = _read_assets()
    assert 'id="map-world-title"' in assets["index.html"]
    assert '$("map-world-title").textContent = state.world?.title' in assets["js/render_panels.js"]
