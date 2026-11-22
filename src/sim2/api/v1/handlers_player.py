"""Player-facing v1 handlers: read model, commands, dialogue and trades."""

from __future__ import annotations

from typing import Any

from .context import ApiError, JsonResponse, RequestContext


def _player(ctx: RequestContext) -> str:
    return ctx.server.resolve_agent_id(ctx.param("player_id"))


def player_entry(ctx: RequestContext) -> dict[str, Any]:
    if not ctx.server.has_world:
        return {"status": "no_world", "origins": [], "players": [], "can_create_player": False}
    return ctx.server.service_call("player_entry")


def enter_player(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.enter_player(dict(ctx.body))


def player_view(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.service_call("player_view", _player(ctx))


def player_actions(ctx: RequestContext) -> dict[str, Any]:
    player_id = _player(ctx)
    body = {**ctx.body, "player_id": player_id}
    return ctx.server.service_call("player_act", body)


def player_wait(ctx: RequestContext) -> JsonResponse:
    player_id = _player(ctx)
    if ctx.server.runtime_status().get("running"):
        raise ApiError(
            409,
            "runtime_running",
            "The autonomous world is already advancing; pause or stop it before using manual wait.",
            failed_check="runtime_running",
        )
    state = ctx.server.player_state(player_id)
    if not state.get("exists"):
        raise ApiError(404, "player_not_found", f"player does not exist: {player_id}", failed_check="player_id")
    body = ctx.body
    task = ctx.server.tasks.submit(
        player_id,
        minutes=int(body.get("minutes", 1) or 1),
        provider=body.get("provider"),
        instance_id=ctx.server.instance_id,
    )
    return JsonResponse(202, {"status": "accepted", "task": task})


def player_task(ctx: RequestContext) -> dict[str, Any]:
    task = ctx.server.tasks.get(_player(ctx), ctx.param("task_id"))
    if task is None:
        raise ApiError(404, "task_not_found", "manual task does not exist", failed_check="task_id")
    return {"status": "ok", "task": task}


def player_task_cancel(ctx: RequestContext) -> dict[str, Any]:
    task = ctx.server.tasks.cancel(_player(ctx), ctx.param("task_id"))
    if task is None:
        raise ApiError(404, "task_not_found", "manual task does not exist", failed_check="task_id")
    return {"status": "ok", "task": task}


def player_dialogue(ctx: RequestContext) -> dict[str, Any]:
    player_id = _player(ctx)
    listener = ctx.server.resolve_agent_id(str(ctx.body.get("listener_id") or "")) or str(ctx.body.get("listener_id") or "")
    result = ctx.server.service_call(
        "dialogue",
        player_id,
        listener,
        str(ctx.body.get("topic") or "general"),
        str(ctx.body.get("message") or ""),
    )
    if result.get("status") == "accepted":
        ctx.server.queue_dialogue_reply(
            player_id,
            provider_name=ctx.body.get("provider"),
            token=ctx.token,
        )
        result = {**result, "reply": {"status": "pending", "message": "NPC 正在回复。"}}
    return result


def player_trades(ctx: RequestContext) -> dict[str, Any]:
    return {
        "status": "ok",
        "offers": ctx.server.trade_offers(
            _player(ctx),
            include_closed=ctx.query_bool("include_closed"),
            token=ctx.token,
        )["offers"],
    }


def player_trade_offer(ctx: RequestContext) -> dict[str, Any]:
    player_id = _player(ctx)
    recipient = ctx.server.resolve_agent_id(str(ctx.body.get("recipient_id") or "")) or str(ctx.body.get("recipient_id") or "")
    return ctx.server.service_call(
        "trade_offer",
        player_id,
        recipient,
        dict(ctx.body.get("give") or {}),
        dict(ctx.body.get("want") or {}),
    )


def player_trade_accept(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.service_call("trade_accept", _player(ctx), ctx.param("offer_id"))


def player_trade_counter(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.service_call(
        "trade_counter",
        _player(ctx),
        ctx.param("offer_id"),
        dict(ctx.body.get("give") or {}),
        dict(ctx.body.get("want") or {}),
    )


def player_trade_reject(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.service_call("trade_reject", _player(ctx), ctx.param("offer_id"))
