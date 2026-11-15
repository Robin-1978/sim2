from __future__ import annotations

from pathlib import Path
from typing import Any

from ..platform.runtime import Platform
from .authoring import AuthoringService


class ControlService:
    """Small transport-neutral control API for CLI, HTTP or a future UI."""

    def __init__(self, db_path: Path, *, authoring_root: Path | None = None):
        self.platform = Platform(db_path)
        runtime_root = Path(db_path).resolve().parent.parent
        if authoring_root is None:
            report = self.platform.store.repositories.metadata.get("bootstrap_report", {}) or {}
            source = report.get("config_dir") if isinstance(report, dict) else None
            if source:
                candidate = Path(str(source)).resolve().parent
                if candidate.is_dir():
                    authoring_root = candidate
        self.authoring = AuthoringService(authoring_root, runtime_root=runtime_root)

    def close(self) -> None:
        self.platform.close()

    def status(self) -> dict[str, Any]:
        with self.platform.store.read_transaction():
            return self.platform.status()

    def health(self) -> dict[str, Any]:
        with self.platform.store.read_transaction():
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
        with self.platform.store.read_transaction():
            return self.platform.metrics()

    def observer(self, event_limit: int = 20) -> dict[str, Any]:
        with self.platform.store.read_transaction():
            return self.platform.observer_view(event_limit=event_limit)

    def map(self, *, viewer_id: str | None = None, observer: bool = False) -> dict[str, Any]:
        with self.platform.store.read_transaction():
            return self.platform.map_view(viewer_id=viewer_id, observer=observer)

    def timeline(self, event_limit: int = 100, after_event_id: int = 0, include_ticks: bool = False) -> dict[str, Any]:
        with self.platform.store.read_transaction():
            return self.platform.timeline(limit=event_limit, after_event_id=after_event_id, include_ticks=include_ticks)

    def dialogue(self, speaker_id: str, listener_id: str, topic: str, message: str) -> dict[str, Any]:
        return self.platform.submit_dialogue(speaker_id, listener_id, topic, message)

    def trade_offer(self, offerer_id: str, recipient_id: str, give: dict[str, Any], want: dict[str, Any]) -> dict[str, Any]:
        return self.platform.create_trade_offer(offerer_id, recipient_id, give, want)

    def trade_accept(self, recipient_id: str, offer_id: str) -> dict[str, Any]:
        return self.platform.accept_trade_offer(recipient_id, offer_id)

    def audit(self) -> dict[str, Any]:
        with self.platform.store.read_transaction():
            return self.platform.export_audit()

    def evolution_tasks(self) -> list[dict[str, Any]]:
        with self.platform.store.read_transaction():
            return self.platform.ready_evolution_tasks()

    def snapshot(self, destination: Path) -> str:
        return str(self.platform.snapshot(destination))

    def restore(self, source: Path) -> dict[str, Any]:
        return self.platform.restore_snapshot(source)

    def authoring_schema(self) -> dict[str, Any]:
        return self.authoring.schema()

    def authoring_catalog(self) -> dict[str, Any]:
        return self.authoring.list_packages()

    def authoring_create(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.authoring.create_instance(
            str(body.get("package_id") or ""),
            str(body.get("instance_id") or ""),
        )

    def authoring_compile(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.authoring.compile(body.get("template"), source=body.get("source"), source_path=body.get("source_path"), draft_revision=body.get("draft_revision", 0))

    def authoring_smoke(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.authoring.smoke(
            body.get("template"),
            source=body.get("source"),
            source_path=body.get("source_path"),
            draft_revision=body.get("draft_revision", 0),
            minutes=int(body.get("minutes", 1)),
        )

    def authoring_export(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.authoring.export(body.get("template"), source=body.get("source"), source_path=body.get("source_path"), draft_revision=body.get("draft_revision", 0))

    def authoring_open(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = body or {}
        requested = body.get("path")
        if requested:
            try:
                return self.authoring.open_package(Path(str(requested)))
            except (TypeError, ValueError, OSError) as exc:
                return {"status": "rejected", "errors": [str(exc)], "diagnostics": self.authoring.diagnostics([str(exc)])}
        report = self.platform.store.repositories.metadata.get("bootstrap_report", {}) or {}
        source = report.get("config_dir") if isinstance(report, dict) else None
        if not source:
            return {"status": "rejected", "errors": ["this instance has no readable source package"]}
        try:
            return self.authoring.open_package(Path(str(source)), trusted=True)
        except (TypeError, ValueError, OSError) as exc:
            return {"status": "rejected", "errors": [str(exc)], "diagnostics": self.authoring.diagnostics([str(exc)])}

    def authoring_build(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.authoring.build_from_brief(str(body.get("brief") or ""), provider_name=body.get("provider"))
