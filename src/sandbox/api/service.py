from __future__ import annotations

from pathlib import Path
from typing import Any

from ..platform.runtime import Platform


class ControlService:
    """Small transport-neutral control API for CLI, HTTP or a future UI."""

    def __init__(self, db_path: Path):
        self.platform = Platform(db_path)

    def close(self) -> None:
        self.platform.close()

    def status(self) -> dict[str, Any]:
        return self.platform.status()

    def pause(self, reason: str = "operator") -> dict[str, Any]:
        return self.platform.pause(reason)

    def resume(self, reason: str = "operator") -> dict[str, Any]:
        return self.platform.resume(reason)

    def step(self) -> dict[str, Any]:
        return self.platform.step()

    def advance(self, minutes: int) -> dict[str, Any]:
        return self.platform.advance(minutes)

    def inject(self, event_type: str, payload: dict[str, Any], actor_id: str | None = None) -> dict[str, Any]:
        return self.platform.inject_event(event_type, payload, actor_id)

    def metrics(self) -> dict[str, Any]:
        return self.platform.metrics()

    def audit(self) -> dict[str, Any]:
        return self.platform.export_audit()

    def snapshot(self, destination: Path) -> str:
        return str(self.platform.snapshot(destination))
