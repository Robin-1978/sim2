"""Observer-facing v1 handlers: public projections and authoring reads."""

from __future__ import annotations

from typing import Any

from ...bootstrap.service import world_template_schema
from .context import RequestContext, StreamResponse
from .contracts import json_schema_map


def status(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.status_payload()


def world(ctx: RequestContext) -> dict[str, Any]:
    if not ctx.server.has_world:
        return {
            "status": "no_world",
            "world_id": "",
            "instance_id": "",
            "instances": ctx.server.registry.list(active_db=None),
        }
    return ctx.server.service_call("world_info")


def worlds(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.world_catalog()


def instances(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.instance_list()


def runtime(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.runtime_status()


def health(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.service_call("health")


def metrics(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.service_call("metrics")


def observer(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.service_call("observer", ctx.query_int("limit", 20, minimum=1, maximum=200))


def timeline(ctx: RequestContext) -> dict[str, Any]:
    if ctx.query_value("view") == "chronicle":
        return ctx.server.service_call(
            "player_chronicle",
            ctx.query_int("limit", 100, minimum=1, maximum=500),
            ctx.query_int("after", 0, minimum=0),
            ctx.query_bool("latest"),
        )
    return ctx.server.service_call(
        "timeline",
        ctx.query_int("limit", 100, minimum=1, maximum=500),
        ctx.query_int("after", 0, minimum=0),
        ctx.query_bool("ticks"),
        ctx.query_bool("latest"),
    )


def map_view(ctx: RequestContext) -> dict[str, Any]:
    viewer_id = ctx.query_value("viewer_id") or None
    observer_mode = ctx.query_bool("observer")
    if ctx.session.get("role") == "player" and not observer_mode:
        viewer_id = str(ctx.session.get("player_id") or "") or viewer_id
    return ctx.server.service_call("map", viewer_id=viewer_id, observer=observer_mode)


def dialogue(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.service_call("observer_dialogue", ctx.query_int("limit", 20, minimum=1, maximum=100))


def decisions(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.service_call("observer_decisions", ctx.query_int("limit", 20, minimum=1, maximum=100))


def event_related(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.service_call("event_related", int(ctx.param("event_id")))


def stream(ctx: RequestContext) -> StreamResponse:
    if not ctx.session:
        ctx.session = ctx.server.session_context(ctx.query_value("session_token"))
    after = 0
    header = str(getattr(ctx, "last_event_id", "") or "")
    if header:
        try:
            after = int(header)
        except (TypeError, ValueError):
            after = 0
    after = ctx.query_int("after", after, minimum=0)
    return StreamResponse(
        after=after,
        ticks=ctx.query_bool("ticks"),
        role=str(ctx.session.get("role") or "observer"),
        player_id=str(ctx.session.get("player_id") or ""),
        compact=ctx.query_bool("compact"),
    )


def authoring_schema(ctx: RequestContext) -> dict[str, Any]:
    if not ctx.server.has_world:
        return {"status": "ok", "schema": world_template_schema()}
    return ctx.server.service_call("authoring_schema")


def authoring_catalog(ctx: RequestContext) -> dict[str, Any]:
    if not ctx.server.has_world:
        catalog = ctx.server.world_catalog()
        return {"status": "ok", "packages": catalog["worlds"]}
    return ctx.server.service_call("authoring_catalog")


def contracts(ctx: RequestContext) -> dict[str, Any]:
    return {"status": "ok", **json_schema_map()}
