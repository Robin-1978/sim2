"""Request context and response envelopes shared by v1 handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class RequestContext:
    server: Any
    method: str
    path: str
    params: dict[str, str]
    query: dict[str, list[str]]
    body: dict[str, Any]
    token: str
    session: dict[str, str]
    request_id: str
    last_event_id: str = ""

    def param(self, name: str, default: str = "") -> str:
        return str(self.params.get(name, default) or default)

    def query_value(self, name: str, default: str = "") -> str:
        values = self.query.get(name)
        return str(values[0]) if values else default

    def query_bool(self, name: str, default: bool = False) -> bool:
        raw = self.query_value(name, "1" if default else "0").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def query_int(self, name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
        raw = self.query_value(name)
        if not str(raw).strip():
            return default
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be an integer") from exc
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value


@dataclass(frozen=True)
class JsonResponse:
    status: int
    payload: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RawResponse:
    status: int
    body: bytes
    content_type: str
    filename: str | None = None


@dataclass(frozen=True)
class StreamResponse:
    after: int
    ticks: bool
    role: str = "observer"
    player_id: str = ""
    compact: bool = False


@dataclass(frozen=True)
class Route:
    method: str
    template: str
    role: str
    handler: Callable[[RequestContext], Any]
    request_model: str | None = None
    response_model: str | None = None
    idempotent: bool = False
    status: int = 200
    name: str = ""


class ApiError(Exception):
    """A transport error with a stable machine-readable code."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        failed_check: str | None = None,
    ):
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)
        self.message = str(message)
        self.failed_check = failed_check or code

    def payload(self, request_id: str) -> dict[str, Any]:
        return {
            "status": "error",
            "code": self.code,
            "message": self.message,
            "failed_check": self.failed_check,
            "request_id": request_id,
            "schema_version": "v1",
        }
