from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from tempfile import NamedTemporaryFile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class InstanceRegistry:
    """Discover independent SQLite instances under one runtime root."""

    def __init__(self, runtime_root: Path, active_db: Path | None = None):
        self.runtime_root = Path(runtime_root).resolve()
        self.instances_root = self.runtime_root / "instances"
        self.active_db = Path(active_db).resolve() if active_db is not None else None

    def db_path(self, instance_id: str) -> Path:
        normalized = str(instance_id or "").strip()
        if not normalized or any(part in normalized for part in ("/", "\\", "..")):
            raise ValueError("invalid instance_id")
        return self.instances_root / normalized / "world.sqlite3"

    def latest(self) -> Path:
        marker = self.runtime_root / "current_instance.json"
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
            selected = Path(str(value.get("db_path") or "")).expanduser().resolve()
            if selected.is_relative_to(self.instances_root) and selected.is_file():
                return selected
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
        candidates = sorted(
            self.instances_root.glob("*/world.sqlite3"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ) if self.instances_root.is_dir() else []
        if not candidates:
            raise FileNotFoundError(f"no world instances found under {self.instances_root}")
        return candidates[0].resolve()

    def mark_active(self, path: Path) -> None:
        selected = Path(path).expanduser().resolve()
        if not selected.is_relative_to(self.instances_root):
            return
        marker = self.runtime_root / "current_instance.json"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        temporary = marker.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"db_path": str(selected)}, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(marker)

    def _metadata(self, path: Path) -> dict[str, Any]:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute("SELECT key, value FROM instance_meta").fetchall()
        finally:
            connection.close()
        result: dict[str, Any] = {}
        for row in rows:
            try:
                result[str(row["key"])] = json.loads(row["value"])
            except (TypeError, ValueError, json.JSONDecodeError):
                result[str(row["key"])] = row["value"]
        return result

    def upgrade_bundled_for_play(self, path: Path) -> dict[str, Any] | None:
        """Upgrade a recognized bundled save before opening it for play."""

        from worlds import world_path
        from ..bootstrap.service import load_yaml, upgrade_instance

        path = Path(path).expanduser().resolve()
        saved = self._metadata(path)
        source = (saved.get("bootstrap_report") or {}).get("config_dir")
        if not source:
            return None
        try:
            package = world_path(str(saved.get("world_id") or "")).resolve()
        except (FileNotFoundError, ModuleNotFoundError, ValueError):
            return None
        if Path(source).resolve() != package:
            return None
        manifest = load_yaml(package, "manifest.yaml", {})
        previous = saved.get("config_version")
        if previous == manifest.get("config_version") or previous not in (manifest.get("upgrade_from") or []):
            return None
        directory = path.parent / "snapshots"
        directory.mkdir(exist_ok=True)
        with NamedTemporaryFile(prefix="before-package-upgrade-", suffix=".sqlite3", dir=directory, delete=False) as handle:
            snapshot = Path(handle.name)
        try:
            with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as old:
                with closing(sqlite3.connect(snapshot)) as backup:
                    old.backup(backup)
        except Exception:
            snapshot.unlink(missing_ok=True)
            raise
        outcome = upgrade_instance(package, path, preserve_controllers=True)
        return {**outcome, "snapshot": str(snapshot), "from": previous, "to": manifest["config_version"]}

    def describe(self, path: Path) -> dict[str, Any]:
        path = Path(path).resolve()
        stat = path.stat()
        metadata = self._metadata(path)
        setting = metadata.get("setting") if isinstance(metadata.get("setting"), dict) else {}
        return {
            "instance_id": str(metadata.get("instance_id") or path.parent.name),
            "world_id": str(metadata.get("world_id") or ""),
            "world_title": str(setting.get("name") or metadata.get("world_id") or ""),
            "world_minute": int(metadata.get("world_minute", 0) or 0),
            "runtime_status": str(metadata.get("runtime_status", "running") or "running"),
            "config_version": str(metadata.get("config_version") or ""),
            "db_path": str(path),
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "active": self.active_db == path,
        }

    def list(self, *, active_db: Path | None = None) -> list[dict[str, Any]]:
        active = Path(active_db).resolve() if active_db is not None else self.active_db
        if not self.instances_root.is_dir():
            return []
        items: list[dict[str, Any]] = []
        for path in sorted(self.instances_root.glob("*/world.sqlite3"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                item = self.describe(path)
            except (OSError, sqlite3.DatabaseError, ValueError) as exc:
                item = {
                    "instance_id": path.parent.name,
                    "world_id": "",
                    "world_minute": 0,
                    "runtime_status": "error",
                    "db_path": str(path.resolve()),
                    "modified_at": "",
                    "active": active == path.resolve(),
                    "error": str(exc),
                }
            item["active"] = bool(active == path.resolve())
            items.append(item)
        return items

    def resolve(self, *, instance_id: str | None = None, db_path: Path | str | None = None) -> Path:
        if db_path:
            path = Path(db_path).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(f"world database does not exist: {path}")
            return path
        if instance_id:
            path = self.db_path(instance_id)
            if not path.is_file():
                raise FileNotFoundError(f"world instance does not exist: {instance_id}")
            return path.resolve()
        return self.latest()

