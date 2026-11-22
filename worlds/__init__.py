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
    if not resource.joinpath("manifest.yaml").is_file():
        raise FileNotFoundError(f"bundled world package not found: {name}")
    # Namespace packages may be represented by importlib's MultiplexedPath,
    # which is not a pathlib.Path.  Prefer its single concrete root so CLI
    # callers still receive a normal filesystem directory.
    roots = getattr(resource, "_paths", None)
    if roots:
        return Path(roots[0])
    return Path(resource)


__all__ = ["world_path"]
