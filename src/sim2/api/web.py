from __future__ import annotations

from pathlib import Path


_WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
_ASSET_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


def web_asset(path: str) -> tuple[bytes, str] | None:
    """Return one bundled browser asset, rejecting path traversal.

    The game and admin clients load native ES modules from ``web/js``; the
    resolver therefore accepts nested files under the web root while still
    refusing absolute paths, parent traversal and unknown extensions.
    """

    name = "index.html" if path in {"", "/", "/index.html"} else path.removeprefix("/")
    if not name or name.startswith("/"):
        return None
    candidate = (_WEB_ROOT / name).resolve()
    try:
        candidate.relative_to(_WEB_ROOT)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    content_type = _ASSET_TYPES.get(candidate.suffix.lower())
    if content_type is None:
        return None
    return candidate.read_bytes(), content_type
