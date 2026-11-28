"""Artwork authored with a world package and resolved when it becomes a save."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from pathlib import Path
from typing import Any


_PATH = re.compile(r"assets/[a-zA-Z0-9_-]+\.webp")
MAX_ARTWORK_BYTES = 8 * 1024 * 1024


def artwork_paths(ui: Any) -> list[str]:
    if not isinstance(ui, dict):
        return []
    scenes = ui.get("scene_art", {})
    if not isinstance(scenes, dict):
        raise ValueError("ui.scene_art must map location IDs to artwork paths")
    paths = ([ui["cover_art"]] if "cover_art" in ui else []) + list(scenes.values())
    for name in paths:
        if not isinstance(name, str) or not _PATH.fullmatch(name):
            raise ValueError(f"artwork path must be a package-relative assets/*.webp file: {name!r}")
    return list(dict.fromkeys(paths))


def validate_webp(name: str, data: bytes) -> bytes:
    if (
        len(data) < 20 or len(data) > MAX_ARTWORK_BYTES
        or data[:4] != b"RIFF" or data[8:12] != b"WEBP"
        or data[12:16] not in {b"VP8 ", b"VP8L", b"VP8X"}
        or int.from_bytes(data[4:8], "little") != len(data) - 8
    ):
        raise ValueError(f"artwork {name} must be a WebP image of at most 8 MiB")
    return data


def read_artwork_file(root: Path, name: str) -> bytes:
    if not isinstance(name, str) or not _PATH.fullmatch(name):
        raise ValueError("artwork path must be a package-relative assets/*.webp file")
    try:
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError(f"artwork {name} must be inside its package")
        if not path.is_file():
            raise ValueError(f"could not read package artwork {name}")
        with path.open("rb") as source:
            return validate_webp(name, source.read(MAX_ARTWORK_BYTES + 1))
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"could not read package artwork {name}") from exc


def read_package_artwork(root: Path, ui: Any) -> dict[str, bytes]:
    return {name: read_artwork_file(root, name) for name in artwork_paths(ui)}


def decode_template_artwork(world: dict[str, Any]) -> dict[str, bytes]:
    names = artwork_paths(world.get("ui"))
    encoded = world.get("artwork", {})
    if not isinstance(encoded, dict) or set(encoded) != set(names):
        raise ValueError("world.artwork must contain exactly the images referenced by world.ui")
    result = {}
    for name in names:
        value = encoded[name]
        if not isinstance(value, str) or len(value) > 4 * ((MAX_ARTWORK_BYTES + 2) // 3):
            raise ValueError(f"world.artwork[{name}] must be base64-encoded WebP of at most 8 MiB")
        try:
            result[name] = validate_webp(name, base64.b64decode(value, validate=True))
        except (binascii.Error, UnicodeEncodeError) as exc:
            raise ValueError(f"world.artwork[{name}] must be valid base64") from exc
    return result


def artwork_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolved_setting_artwork(
    setting: dict[str, Any], artwork: dict[str, bytes],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    if not artwork:
        return setting, {}
    digests = {name: artwork_digest(data) for name, data in artwork.items()}
    urls = {name: f"/world-art/{digest}.webp" for name, digest in digests.items()}
    ui = dict(setting["ui"])
    if "cover_art" in ui:
        ui["cover_art"] = urls[ui["cover_art"]]
    if "scene_art" in ui:
        ui["scene_art"] = {location_id: urls[name] for location_id, name in ui["scene_art"].items()}
    return {**setting, "ui": ui}, {digest: artwork[name] for name, digest in digests.items()}
