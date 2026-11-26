from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from worlds import world_path

from ..bootstrap.artwork import artwork_digest, read_artwork_file


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return value if isinstance(value, dict) else {}


def _cover_art(root: Path, setting: dict[str, Any]) -> bytes | None:
    ui = setting.get("ui")
    if not isinstance(ui, dict) or not ui.get("cover_art"):
        return None
    try:
        return read_artwork_file(root, ui["cover_art"])
    except ValueError:
        return None


def catalog_artwork(world_id: str, digest: str) -> bytes | None:
    try:
        root = world_path(world_id)
    except (ImportError, OSError, ValueError):
        return None
    image = _cover_art(root, _load_yaml(root / "setting.yaml"))
    return image if image and artwork_digest(image) == digest else None


def bundled_world_catalog() -> list[dict[str, Any]]:
    """Summarize the importable world packages shipped with sim2.

    This only reads presentation-facing manifest and setting fields.  It does
    not validate or open a world, so the launcher can render even when a
    package is broken; creation still goes through the normal validator.
    """

    try:
        root = Path(str(files("worlds")))
    except (ImportError, ModuleNotFoundError, TypeError):
        return []
    if not root.is_dir():
        return []
    worlds: list[dict[str, Any]] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        manifest_path = path / "manifest.yaml"
        if not path.is_dir() or not manifest_path.is_file():
            continue
        manifest = _load_yaml(manifest_path)
        setting = _load_yaml(path / "setting.yaml")
        world_id = str(manifest.get("world_id") or path.name)
        artwork = _cover_art(path, setting)
        try:
            order = int(manifest.get("order") or 100)
        except (TypeError, ValueError):
            order = 100
        worlds.append(
            {
                "id": world_id,
                "name": str(setting.get("name") or manifest.get("name") or world_id),
                "premise": str(setting.get("premise") or ""),
                "genre": str(setting.get("genre") or ""),
                "cover_art": f"/catalog-art/{path.name}/{artwork_digest(artwork)}.webp" if artwork else "",
                "locale": str(setting.get("locale") or "en"),
                "kind": str(manifest.get("kind") or "world"),
                "order": order,
                "config_version": str(manifest.get("config_version") or ""),
                "source": "bundled",
                "status": "passed",
                "errors": [],
            }
        )
    worlds.sort(key=lambda item: (item["kind"] in {"probe", "test"}, item["order"], item["name"]))
    return worlds


def merge_catalog(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge catalogs by world id, keeping the first (most specific) entry."""

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            world_id = str(item.get("id") or "").strip()
            if not world_id or world_id in seen:
                continue
            seen.add(world_id)
            merged.append(dict(item))
    return merged
