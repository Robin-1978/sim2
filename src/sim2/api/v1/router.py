"""Versioned request router: matching, auth, validation, idempotency, errors."""

from __future__ import annotations

import json
import logging
import re
import secrets
from typing import Any
from urllib.parse import parse_qs

from pydantic import ValidationError

from ..errors import NoActiveWorldError
from .context import ApiError, JsonResponse, RequestContext, RawResponse, Route, StreamResponse
from .contracts import REQUEST_MODELS, RESPONSE_MODELS
from .routes import ROUTES


_LOGGER = logging.getLogger("sim2.http")
_PARAM = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _compile(template: str) -> re.Pattern[str]:
    pieces = []
    cursor = 0
    for match in _PARAM.finditer(template):
        pieces.append(re.escape(template[cursor : match.start()]))
        pieces.append(f"(?P<{match.group(1)}>[^/]+)")
        cursor = match.end()
    pieces.append(re.escape(template[cursor:]))
    return re.compile(f"^{''.join(pieces)}$")


_COMPILED = tuple((route, _compile(route.template)) for route in ROUTES)


def new_request_id() -> str:
    return f"req_{secrets.token_hex(8)}"


class V1Router:
    """Dispatch one HTTP request against the declarative v1 table."""

    def __init__(self, server: Any):
        self.server = server

    def match(self, method: str, path: str) -> tuple[Route, dict[str, str]] | None:
        for route, pattern in _COMPILED:
            if route.method != method:
                continue
            found = pattern.match(path)
            if found:
                return route, {key: value for key, value in found.groupdict().items()}
        return None

    def dispatch(
        self,
        method: str,
        path: str,
        *,
        query_string: str,
        body: dict[str, Any],
        token: str,
        last_event_id: str = "",
        idempotency_key: str = "",
        request_id: str | None = None,
    ) -> JsonResponse | RawResponse | StreamResponse:
        request_id = request_id or new_request_id()
        matched = self.match(method, path)
        if matched is None:
            raise ApiError(404, "not_found", f"no v1 route for {method} {path}", failed_check="route")
        route, params = matched
        ctx = RequestContext(
            server=self.server,
            method=method,
            path=path,
            params=params,
            query=parse_qs(query_string, keep_blank_values=True),
            body=dict(body),
            token=str(token or ""),
            session=self._session(token),
            request_id=request_id,
            last_event_id=str(last_event_id or ""),
        )
        if route.role == "player":
            # Resolve an agent id or exact name before binding the session, so
            # a hand-written request cannot bypass ownership by using a name.
            resolved = self.server.resolve_agent_id(ctx.param("player_id"))
            ctx.params["player_id"] = resolved
        self._authorize(route, ctx)
        ctx.body = self._validate_request(route, ctx)
        idem_key = self._idempotency_key(route, ctx, idempotency_key)
        if idem_key:
            stored = self.server.idem_lookup(ctx.token, idem_key)
            if stored is not None:
                return JsonResponse(stored["status"], dict(stored["value"]))
        try:
            result = route.handler(ctx)
        except ApiError:
            raise
        except NoActiveWorldError as exc:
            raise ApiError(409, "no_world", str(exc), failed_check="no_world") from exc
        except PermissionError as exc:
            raise ApiError(403, "forbidden", str(exc), failed_check="session") from exc
        except KeyError as exc:
            raise ApiError(404, "not_found", str(exc), failed_check="resource") from exc
        except ValidationError as exc:
            raise ApiError(422, "invalid_request", _validation_message(exc), failed_check="body") from exc
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ApiError(400, "invalid_request", str(exc), failed_check="request") from exc
        except Exception as exc:  # pragma: no cover - exercised by failure-injection tests
            _LOGGER.exception("unhandled v1 route error for %s: %s", route.name, exc)
            raise ApiError(500, "internal_error", "internal server error") from exc
        response = self._response(route, result)
        if idem_key and response.status < 500:
            self.server.idem_store(ctx.token, idem_key, response.status, response.payload)
        return response

    # ---- middleware ---------------------------------------------------------

    def _session(self, token: str) -> dict[str, str]:
        raw = str(token or "")
        if not raw:
            return {}
        if secrets.compare_digest(raw, self.server.operator_token):
            return {"role": "operator", "instance_id": str(self.server.instance_id)}
        return dict(self.server.session_for(raw) or {})

    def _authorize(self, route: Route, ctx: RequestContext) -> None:
        if route.role == "public":
            return
        if route.role == "launcher":
            if not self.server.has_world:
                return
            route_role = "operator"
        else:
            route_role = route.role
        player_id = ctx.param("player_id") if route.role == "player" else None
        allowed, message = self.server.authorize_session(ctx.token, route_role, player_id=player_id)
        if not allowed:
            raise ApiError(403, "forbidden", message, failed_check="session")

    def _validate_request(self, route: Route, ctx: RequestContext) -> dict[str, Any]:
        if route.method != "POST":
            return ctx.body
        model = REQUEST_MODELS.get(route.request_model or "")
        if model is None:
            return ctx.body
        try:
            validated = model.model_validate(ctx.body)
        except ValidationError as exc:
            raise ApiError(422, "invalid_request", _validation_message(exc), failed_check="body") from exc
        return validated.model_dump(mode="json")

    def _idempotency_key(self, route: Route, ctx: RequestContext, header_value: str = "") -> str:
        if not route.idempotent:
            return ""
        raw = str(header_value or "").strip() or str(ctx.body.get("idempotency_key") or "").strip()
        if not raw or len(raw) > 128:
            return ""
        return f"{route.name}:{raw}"

    def _response(self, route: Route, result: Any) -> JsonResponse | RawResponse | StreamResponse:
        if isinstance(result, (JsonResponse, RawResponse, StreamResponse)):
            return result
        if not isinstance(result, dict):
            raise ApiError(500, "contract_violation", f"route {route.name} returned {type(result).__name__}")
        model = RESPONSE_MODELS.get(route.response_model or "")
        if model is not None:
            try:
                result = model.model_validate(result).model_dump(mode="json")
            except ValidationError as exc:
                _LOGGER.error("response contract violation on %s: %s", route.name, exc)
                raise ApiError(500, "contract_violation", f"response for {route.name} does not match its contract") from exc
        return JsonResponse(route.status, result)


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(item) for item in first.get("loc", ()) if item != "body")
    message = str(first.get("msg") or "invalid request")
    return f"{location}: {message}" if location else message
