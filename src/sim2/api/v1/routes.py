"""Declarative route table for the versioned web API.

Every route declares its method, path template, required role, request model
and (where stable) response model.  Adding a capability means adding one row
here plus one handler; authentication, validation, idempotency and error
mapping live in ``router.py`` and apply to every row automatically.
"""

from __future__ import annotations

from . import handlers_auth as auth
from . import handlers_observer as observer
from . import handlers_operator as operator
from . import handlers_player as player
from .context import Route


ROUTES: tuple[Route, ...] = (
    # ---- bootstrap / status -------------------------------------------------
    Route("POST", "/api/v1/session", "public", auth.session, request_model="session", response_model="session", name="session.create"),
    Route("GET", "/api/v1/status", "public", observer.status, name="status.get"),
    Route("GET", "/api/v1/world", "public", observer.world, response_model="world", name="world.get"),
    Route("GET", "/api/v1/worlds", "public", observer.worlds, name="worlds.list"),
    Route("GET", "/api/v1/instances", "public", observer.instances, response_model="instances", name="instances.list"),
    Route("GET", "/api/v1/runtime", "public", observer.runtime, response_model="runtime", name="runtime.get"),
    Route("GET", "/api/v1/health", "public", observer.health, name="health.get"),
    Route("GET", "/api/v1/metrics", "public", observer.metrics, name="metrics.get"),
    Route("GET", "/api/v1/meta/contracts", "public", observer.contracts, name="contracts.get"),
    # ---- observer -----------------------------------------------------------
    Route("GET", "/api/v1/observer/summary", "observer", observer.observer, name="observer.summary"),
    Route("GET", "/api/v1/observer/timeline", "observer", observer.timeline, name="observer.timeline"),
    Route("GET", "/api/v1/observer/dialogue", "observer", observer.dialogue, name="observer.dialogue"),
    Route("GET", "/api/v1/observer/decisions", "observer", observer.decisions, name="observer.decisions"),
    Route("GET", "/api/v1/map", "observer", observer.map_view, name="map.get"),
    Route("GET", "/api/v1/events/{event_id}/related", "observer", observer.event_related, name="events.related"),
    Route("GET", "/api/v1/stream", "public", observer.stream, name="stream.open"),
    # ---- player -------------------------------------------------------------
    Route("GET", "/api/v1/player/entry", "public", player.player_entry, response_model="player_entry", name="player.entry"),
    Route("POST", "/api/v1/player/enter", "public", player.enter_player, request_model="enter_player", name="player.enter"),
    Route("GET", "/api/v1/player/{player_id}/view", "player", player.player_view, response_model="player_view", name="player.view"),
    Route("POST", "/api/v1/player/{player_id}/actions", "player", player.player_actions, request_model="action", idempotent=True, name="player.actions"),
    Route("POST", "/api/v1/player/{player_id}/wait", "player", player.player_wait, request_model="wait", idempotent=True, status=202, name="player.wait"),
    Route("GET", "/api/v1/player/{player_id}/tasks/{task_id}", "player", player.player_task, response_model="task", name="player.task"),
    Route("POST", "/api/v1/player/{player_id}/tasks/{task_id}/cancel", "player", player.player_task_cancel, request_model="reason", idempotent=True, name="player.task.cancel"),
    Route("POST", "/api/v1/player/{player_id}/dialogue", "player", player.player_dialogue, request_model="dialogue", idempotent=True, name="player.dialogue"),
    Route("GET", "/api/v1/player/{player_id}/trades", "player", player.player_trades, name="player.trades"),
    Route("POST", "/api/v1/player/{player_id}/trades", "player", player.player_trade_offer, request_model="trade_offer", idempotent=True, name="player.trade.offer"),
    Route("POST", "/api/v1/player/{player_id}/trades/{offer_id}/accept", "player", player.player_trade_accept, request_model="reason", idempotent=True, name="player.trade.accept"),
    Route("POST", "/api/v1/player/{player_id}/trades/{offer_id}/counter", "player", player.player_trade_counter, request_model="trade_counter", idempotent=True, name="player.trade.counter"),
    Route("POST", "/api/v1/player/{player_id}/trades/{offer_id}/reject", "player", player.player_trade_reject, request_model="reason", idempotent=True, name="player.trade.reject"),
    # ---- operator -----------------------------------------------------------
    Route("POST", "/api/v1/operator/dialogue", "operator", operator.operator_dialogue, request_model="operator_dialogue", idempotent=True, name="operator.dialogue"),
    Route("GET", "/api/v1/operator/health", "operator", observer.health, name="operator.health"),
    Route("GET", "/api/v1/operator/metrics", "operator", observer.metrics, name="operator.metrics"),
    Route("GET", "/api/v1/operator/audit", "operator", operator.audit, name="operator.audit"),
    Route("GET", "/api/v1/operator/evolution/tasks", "operator", operator.evolution_tasks, name="operator.evolution.tasks"),
    Route("GET", "/api/v1/operator/snapshots", "operator", operator.snapshots, name="operator.snapshots"),
    Route("GET", "/api/v1/operator/snapshots/{snapshot_id}/download", "operator", operator.snapshot_download, name="operator.snapshots.download"),
    Route("POST", "/api/v1/operator/snapshots", "operator", operator.snapshot_create, request_model="snapshot", idempotent=True, name="operator.snapshots.create"),
    Route("POST", "/api/v1/operator/snapshots/{snapshot_id}/restore", "operator", operator.snapshot_restore, request_model="snapshot_restore", idempotent=True, name="operator.snapshots.restore"),
    Route("POST", "/api/v1/operator/decide", "operator", operator.decide, request_model="decide", idempotent=True, name="operator.decide"),
    Route("POST", "/api/v1/operator/simulate", "operator", operator.simulate, request_model="simulate", idempotent=True, name="operator.simulate"),
    Route("POST", "/api/v1/operator/events", "operator", operator.inject, request_model="inject", idempotent=True, name="operator.events"),
    Route("POST", "/api/v1/instances", "operator", operator.instance_create, request_model="instance", idempotent=True, name="instances.create"),
    Route("POST", "/api/v1/instances/init", "launcher", operator.instance_init, request_model="instance", idempotent=True, name="instances.init"),
    Route("POST", "/api/v1/instances/activate", "operator", operator.instance_activate, request_model="instance", idempotent=True, name="instances.activate"),
    Route("POST", "/api/v1/runtime/start", "operator", operator.runtime_start, request_model="runtime_start", idempotent=True, name="runtime.start"),
    Route("POST", "/api/v1/runtime/pause", "operator", operator.runtime_pause, request_model="reason", idempotent=True, name="runtime.pause"),
    Route("POST", "/api/v1/runtime/resume", "operator", operator.runtime_resume, request_model="reason", idempotent=True, name="runtime.resume"),
    Route("POST", "/api/v1/runtime/stop", "operator", operator.runtime_stop, request_model="reason", idempotent=True, name="runtime.stop"),
    Route("POST", "/api/v1/runtime/step", "operator", operator.runtime_step, request_model="reason", idempotent=True, name="runtime.step"),
    # ---- authoring ----------------------------------------------------------
    Route("GET", "/api/v1/authoring/schema", "public", observer.authoring_schema, name="authoring.schema"),
    Route("GET", "/api/v1/authoring/catalog", "public", observer.authoring_catalog, name="authoring.catalog"),
    Route("POST", "/api/v1/authoring/open", "operator", lambda ctx: operator._authoring(ctx, "authoring_open"), request_model="command", idempotent=True, name="authoring.open"),
    Route("POST", "/api/v1/authoring/create", "operator", operator.instance_create, request_model="instance", idempotent=True, name="authoring.create"),
    Route("POST", "/api/v1/authoring/build", "operator", lambda ctx: operator._authoring(ctx, "authoring_build"), request_model="command", idempotent=True, name="authoring.build"),
    Route("POST", "/api/v1/authoring/compile", "operator", lambda ctx: operator._authoring(ctx, "authoring_compile"), request_model="command", idempotent=True, name="authoring.compile"),
    Route("POST", "/api/v1/authoring/smoke", "operator", lambda ctx: operator._authoring(ctx, "authoring_smoke"), request_model="command", idempotent=True, name="authoring.smoke"),
    Route("POST", "/api/v1/authoring/export", "operator", lambda ctx: operator._authoring(ctx, "authoring_export"), request_model="command", idempotent=True, name="authoring.export"),
    Route("POST", "/api/v1/authoring/validate", "operator", lambda ctx: operator._authoring(ctx, "authoring_validate"), request_model="command", idempotent=True, name="authoring.validate"),
    Route("POST", "/api/v1/authoring/describe", "operator", lambda ctx: operator._authoring(ctx, "authoring_describe"), request_model="command", idempotent=True, name="authoring.describe"),
    Route("POST", "/api/v1/authoring/build-world", "operator", lambda ctx: operator._authoring(ctx, "authoring_build_world"), request_model="command", idempotent=True, name="authoring.build_world"),
)


def route_table() -> list[dict[str, str]]:
    """Return the route table for documentation and coverage tests."""

    return [
        {
            "method": route.method,
            "path": route.template,
            "role": route.role,
            "request_model": route.request_model or "",
            "response_model": route.response_model or "",
            "idempotent": "yes" if route.idempotent else "no",
            "name": route.name,
        }
        for route in ROUTES
    ]
