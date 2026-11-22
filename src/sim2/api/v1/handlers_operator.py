"""Operator and authoring v1 handlers."""

from __future__ import annotations

from typing import Any

from .context import ApiError, RawResponse, RequestContext


def _authoring(ctx: RequestContext, method: str) -> dict[str, Any]:
    return ctx.server.service_call(method, dict(ctx.body))


def audit(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.service_call("audit")


def evolution_tasks(ctx: RequestContext) -> dict[str, Any]:
    return {"tasks": ctx.server.service_call("evolution_tasks")}


def snapshots(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.snapshot_list()


def snapshot_create(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.snapshot_create(dict(ctx.body))


def snapshot_download(ctx: RequestContext) -> RawResponse:
    result = ctx.server.snapshot_download(ctx.param("snapshot_id"))
    if isinstance(result, dict):
        status = 404 if result.get("failed_check") == "snapshot" else 400
        raise ApiError(status, "snapshot_not_found", str(result.get("message") or "snapshot not found"), failed_check="snapshot")
    filename, payload = result
    return RawResponse(200, payload, "application/octet-stream", filename=filename)


def snapshot_restore(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.snapshot_restore(dict(ctx.body))


def decide(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.decide(dict(ctx.body))


def simulate(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.simulate(dict(ctx.body))


def inject(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.service_call(
        "inject",
        str(ctx.body.get("event_type") or ""),
        dict(ctx.body.get("payload") or {}),
        ctx.body.get("actor_id"),
    )


def instance_create(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.create_instance(dict(ctx.body))


def instance_init(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.init_instance(dict(ctx.body))


def instance_activate(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.activate_instance(dict(ctx.body))


def instance_delete(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.delete_instance(dict(ctx.body))


def runtime_start(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.start_runtime(dict(ctx.body))


def runtime_pause(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.pause_runtime(str(ctx.body.get("reason") or "web"))


def runtime_resume(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.resume_runtime(str(ctx.body.get("reason") or "web"))


def runtime_stop(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.stop_runtime(str(ctx.body.get("reason") or "web-stop"))


def runtime_step(ctx: RequestContext) -> dict[str, Any]:
    return ctx.server.step_runtime()


def operator_dialogue(ctx: RequestContext) -> dict[str, Any]:
    speaker_raw = str(ctx.body.get("speaker_id") or "")
    speaker = ctx.server.resolve_agent_id(speaker_raw) or speaker_raw
    listener = ctx.server.resolve_agent_id(str(ctx.body.get("listener_id") or "")) or str(ctx.body.get("listener_id") or "")
    result = ctx.server.service_call(
        "dialogue",
        speaker,
        listener,
        str(ctx.body.get("topic") or "general"),
        str(ctx.body.get("message") or ""),
    )
    if result.get("status") == "accepted" and speaker_raw.strip().casefold() == "god":
        result = {
            **result,
            "reply": ctx.server.resolve_dialogue_reply(
                listener,
                provider_name=ctx.body.get("provider"),
                token=ctx.token,
            ),
        }
    return result
