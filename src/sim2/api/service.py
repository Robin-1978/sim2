from __future__ import annotations

from pathlib import Path
from typing import Any

from ..intents.models import BehaviorIntent
from ..platform.runtime import Platform
from .authoring import AuthoringService


class ControlService:
    """Small transport-neutral control API for CLI, HTTP or a future UI."""

    def __init__(self, db_path: Path, *, authoring_root: Path | None = None):
        self.platform = Platform(db_path)
        resolved = Path(db_path).resolve()
        container = resolved.parent.parent
        runtime_root = container.parent if container.name == "instances" else container
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

    def player_entry(self) -> dict[str, Any]:
        """Return generic player creation choices from the loaded world pack."""

        template = self.platform.store.repositories.metadata.get("player_template", {}) or {}
        player = template.get("player", {}) if isinstance(template, dict) else {}
        entry_id = str(player.get("entry_location_id") or "")
        try:
            entry = self.platform.store.repositories.entities.get_entity(entry_id)
        except KeyError:
            entry = {}
        origins = []
        for item in player.get("origin_choices", []) or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            origins.append({"id": str(item["id"]), "goal": str(item.get("starting_goal") or ""), "traits": dict(item.get("traits") or {})})
        return {
            "status": "ok",
            "world_id": self.platform.store.repositories.metadata.get("world_id"),
            "entry_location": {"id": entry_id, "name": str(entry.get("name") or entry_id)},
            "origins": origins,
        }

    def player_view(self, player_id: str) -> dict[str, Any]:
        """Build a compact, playable projection for one player."""

        player_id = str(player_id or "").strip()
        if not player_id:
            return {"status": "rejected", "message": "player_id is required"}
        try:
            decision = self.platform.decision_view(player_id, role="agent_decision")
        except KeyError:
            return {"status": "rejected", "message": "player does not exist", "failed_check": "player_id"}
        map_view = self.platform.map_view(viewer_id=player_id, observer=False)
        current_id = str((self.platform.store.repositories.entities.get_agent(player_id).get("initial_state") or {}).get("location_id") or "")
        nodes = {str(item.get("id")): item for item in map_view.get("nodes", []) if isinstance(item, dict)}
        current = nodes.get(current_id, {})
        routes = []
        for destination in current.get("routes", []) or []:
            target = nodes.get(str(destination))
            if not target:
                continue
            edge = next((item for item in map_view.get("edges", []) if {str(item.get("from")), str(item.get("to"))} == {current_id, str(destination)}), {})
            routes.append({
                "id": str(destination),
                "name": str(target.get("name") or destination),
                "distance": edge.get("distance", 1),
                "minutes": edge.get("minutes", 1),
                "safe": bool(target.get("safe", False)),
            })
        active_task = decision.get("active_task", {}) or {}
        actions = list(decision.get("executable_behaviors", []) or [])
        # Travel is a generic player affordance. The decision projection hides
        # a travel BT when its destination is not selected yet; the web client
        # supplies each known route as an explicit, validated intent instead.
        for route in routes:
            if active_task.get("action") or active_task.get("bt"):
                break
            actions.insert(0, {
                "ref": "behavior:travel",
                "choice": f"前往 {route['name']}",
                "name": f"前往 {route['name']}",
                "summary": f"沿已知路线前往 {route['name']}。",
                "purpose": "移动到另一个已知地点",
                "parameters": {"location_id": route["id"]},
                "time": f"预计 {route['minutes']} 分钟",
                "cost": [],
                "impact": ["抵达目标地点"],
                "target_required": False,
                "kind": "travel",
            })
        player_projection = dict(decision.get("self", {}) or {})
        player_projection["location_id"] = current_id
        return {
            "status": "ok",
            "world_id": map_view.get("world_id"),
            "world_time": decision.get("world_time"),
            "player": player_projection,
            "inventory": decision.get("inventory", {}),
            "needs": decision.get("needs", {}),
            "active_task": active_task,
            "actions": actions[:24],
            "routes": routes,
            "map": map_view,
            "recent_outcomes": decision.get("recent_outcomes", [])[:6],
            "nearby_entities": decision.get("nearby_entities", [])[:12],
        }

    def player_act(self, body: dict[str, Any]) -> dict[str, Any]:
        player_id = str(body.get("player_id") or "").strip()
        if not player_id:
            return {"status": "rejected", "message": "player_id is required"}
        intent = BehaviorIntent.from_json(dict(body), actor_id=player_id)
        return self.platform.submit_behavior(intent)

    def player_wait(self, player_id: str) -> dict[str, Any]:
        """Advance one minute for a manual player session."""

        try:
            self.platform.store.repositories.entities.get_agent(str(player_id))
        except KeyError:
            return {"status": "rejected", "message": "player does not exist", "failed_check": "player_id"}
        return self.platform.step()

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
