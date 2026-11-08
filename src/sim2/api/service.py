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

    def health(self) -> dict[str, Any]:
        return self.platform.health()

    def pause(self, reason: str = "operator") -> dict[str, Any]:
        return self.platform.pause(reason)

    def resume(self, reason: str = "operator") -> dict[str, Any]:
        return self.platform.resume(reason)

    def step(self) -> dict[str, Any]:
        return self.platform.step()

    def inject(self, event_type: str, payload: dict[str, Any], actor_id: str | None = None) -> dict[str, Any]:
        return self.platform.inject_event(event_type, payload, actor_id)

    def enter_player(self, player_id: str, origin_id: str, name: str | None = None) -> dict[str, Any]:
        return self.platform.enter_player(player_id, origin_id, name=name)

    def metrics(self) -> dict[str, Any]:
        return self.platform.metrics()

    def observer(self, event_limit: int = 20) -> dict[str, Any]:
        return self.platform.observer_view(event_limit=event_limit)

    def timeline(self, event_limit: int = 100, after_event_id: int = 0, include_ticks: bool = False) -> dict[str, Any]:
        return self.platform.timeline(limit=event_limit, after_event_id=after_event_id, include_ticks=include_ticks)

    def dialogue(self, speaker_id: str, listener_id: str, topic: str, message: str) -> dict[str, Any]:
        return self.platform.submit_dialogue(speaker_id, listener_id, topic, message)

    def trade_offer(self, offerer_id: str, recipient_id: str, give: dict[str, Any], want: dict[str, Any]) -> dict[str, Any]:
        return self.platform.create_trade_offer(offerer_id, recipient_id, give, want)

    def trade_accept(self, recipient_id: str, offer_id: str) -> dict[str, Any]:
        return self.platform.accept_trade_offer(recipient_id, offer_id)

    def audit(self) -> dict[str, Any]:
        return self.platform.export_audit()

    def snapshot(self, destination: Path) -> str:
        return str(self.platform.snapshot(destination))

    def restore(self, source: Path) -> dict[str, Any]:
        return self.platform.restore_snapshot(source)
