"""Session exchange handler."""

from __future__ import annotations

import secrets
from typing import Any

from .context import ApiError, RequestContext


def session(ctx: RequestContext) -> dict[str, Any]:
    role = str(ctx.body.get("role") or "observer").strip() or "observer"
    if role == "operator":
        supplied = str(ctx.body.get("operator_token") or "")
        if not secrets.compare_digest(supplied, ctx.server.operator_token):
            raise ApiError(403, "forbidden", "operator token is invalid", failed_check="session")
        token = ctx.server.issue_session("operator")
        return {"status": "ok", "role": "operator", "session_token": token}
    if role != "observer":
        raise ApiError(403, "forbidden", "only observer sessions may be created without a player", failed_check="session")
    token = ctx.server.issue_session("observer")
    return {"status": "ok", "role": "observer", "session_token": token}
