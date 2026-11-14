from __future__ import annotations

from pathlib import Path


_WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
_ASSET_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
}


def web_asset(path: str) -> tuple[bytes, str] | None:
    """Return one bundled browser asset, rejecting path traversal."""

    name = "index.html" if path in {"", "/", "/index.html"} else path.removeprefix("/")
    if name not in _ASSET_TYPES:
        return None
    file_path = (_WEB_ROOT / name).resolve()
    if file_path.parent != _WEB_ROOT or not file_path.is_file():
        return None
    return file_path.read_bytes(), _ASSET_TYPES[name]

