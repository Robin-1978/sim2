"""Bundled declarative world packages."""

from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path


_WORLD_ID = re.compile(r"^[a-z][a-z0-9_]*$")


def world_path(world_id: str) -> Path:
    """Return one installed world package as a filesystem path."""

    name = str(world_id or "").strip()
    if not _WORLD_ID.fullmatch(name):
        raise ValueError(f"invalid bundled world id: {world_id!r}")
    resource = files(f"{__name__}.{name}")
    path = Path(str(resource))
    if not path.is_dir() or not (path / "manifest.yaml").is_file():
        raise FileNotFoundError(f"bundled world package not found: {name}")
    return path


__all__ = ["world_path"]
