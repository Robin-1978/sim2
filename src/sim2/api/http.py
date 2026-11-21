from __future__ import annotations

import json
import logging
import os
import secrets
import time
from collections.abc import Callable
from threading import RLock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .authoring import AuthoringService
from .catalog import bundled_world_catalog, merge_catalog
from .errors import NoActiveWorldError
from .instances import InstanceRegistry
from .runtime_supervisor import RuntimeSupervisor
from .service import ControlService
from .v1.context import ApiError, JsonResponse, RawResponse, StreamResponse
from .v1.router import V1Router, new_request_id
from .v1.stream import StreamHub
from .v1.tasks import ManualTaskRunner
from .web import web_asset


_LOGGER = logging.getLogger("sim2.http")



class _Handler(BaseHTTPRequestHandler):
    server: "ControlHTTPServer"

    def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover - structured logging below
        return

    def _session_label(self) -> str:
        token = self._token()
        if not token:
            return "session=none"
        session = self.server.session_context(token)
        role = str(session.get("role") or "")
        return f"session={role}" if role else "session=invalid"

    def _log_request(self, status: int, value: dict[str, Any] | None = None, request_id: str = "") -> None:
        started = getattr(self, "_request_started", None)
        elapsed = (time.monotonic() - started) * 1000 if started is not None else 0.0
        prefix = f"[{request_id}] " if request_id else ""
        safe_path = str(getattr(self, "path", "?"))
        if "session_token=" in safe_path:
            safe_path = safe_path.split("?", 1)[0] + "?<redacted>"
        message = f"{prefix}{getattr(self, 'command', '?')} {safe_path} -> {status} {elapsed:.1f}ms {self._session_label()}"
        if status >= 400 and isinstance(value, dict):
            detail = value.get("message") or value.get("failed_check")
            if detail:
                message += f" error={str(detail)[:160]!r}"
        _LOGGER.log(logging.WARNING if status >= 400 else logging.INFO, message)

    def _write_json(
        self,
        status: int,
        value: dict[str, Any],
        request_id: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        payload = dict(value)
        payload.setdefault("schema_version", "v1")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._log_request(status, payload, request_id)
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        if request_id:
            self.send_header("x-request-id", request_id)
        for key, value in (headers or {}).items():
            self.send_header(str(key), str(value))
        self.end_headers()
        self.wfile.write(body)

    def _write_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        filename: str | None = None,
        request_id: str = "",
    ) -> None:
        self._log_request(status, request_id=request_id)
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-cache")
        if request_id:
            self.send_header("x-request-id", request_id)
        if filename:
            safe = "".join(item if item.isalnum() or item in {"-", "_", "."} else "_" for item in filename)
            self.send_header("content-disposition", f'attachment; filename="{safe}"')
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _token(self) -> str:
        header = str(self.headers.get("x-sim2-session") or "").strip()
        if header:
            return header
        authorization = str(self.headers.get("authorization") or "")
        return authorization.removeprefix("Bearer ").strip()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        self._request_started = time.monotonic()
        parsed = urlparse(self.path)
        asset = web_asset(parsed.path)
        if asset is not None:
            return self._write_bytes(200, *asset)
        request_id = new_request_id()
        try:
            result = self.server.router.dispatch(
                "GET",
                parsed.path,
                query_string=parsed.query,
                body={},
                token=self._token(),
                last_event_id=str(self.headers.get("Last-Event-ID") or ""),
                request_id=request_id,
            )
            return self._write_result(result, request_id)
        except ApiError as exc:
            return self._write_json(exc.status, exc.payload(request_id), request_id)
        except Exception as exc:  # pragma: no cover - defensive transport boundary
            _LOGGER.exception("unhandled GET error for %s: %s", parsed.path, exc)
            return self._write_json(
                500,
                {"status": "error", "code": "internal_error", "message": "internal server error", "request_id": request_id},
                request_id,
            )

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        self._request_started = time.monotonic()
        parsed = urlparse(self.path)
        request_id = new_request_id()
        try:
            body = self._read_json()
        except (json.JSONDecodeError, ValueError) as exc:
            return self._write_json(
                400,
                {"status": "error", "code": "invalid_json", "message": str(exc), "failed_check": "body", "request_id": request_id},
                request_id,
            )
        try:
            result = self.server.router.dispatch(
                "POST",
                parsed.path,
                query_string=parsed.query,
                body=body,
                token=self._token(),
                idempotency_key=str(self.headers.get("Idempotency-Key") or ""),
                request_id=request_id,
            )
            return self._write_result(result, request_id)
        except ApiError as exc:
            return self._write_json(exc.status, exc.payload(request_id), request_id)
        except Exception as exc:  # pragma: no cover - defensive transport boundary
            _LOGGER.exception("unhandled POST error for %s: %s", parsed.path, exc)
            return self._write_json(
                500,
                {"status": "error", "code": "internal_error", "message": "internal server error", "request_id": request_id},
                request_id,
            )

    def _write_result(self, result: Any, request_id: str) -> None:
        if isinstance(result, StreamResponse):
            subscription = self.server.stream.subscribe(result.role, result.player_id, result.after, result.ticks)
            return self.server.stream.serve(self, subscription)
        if isinstance(result, RawResponse):
            return self._write_bytes(
                result.status,
                result.body,
                result.content_type,
                filename=result.filename,
                request_id=request_id,
            )
        if isinstance(result, JsonResponse):
            return self._write_json(result.status, result.payload, request_id, result.headers)
        return self._write_json(
            500,
            {"status": "error", "code": "internal_error", "message": "invalid route result", "request_id": request_id},
            request_id,
        )


class ControlHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        db_path: Path | None,
        *,
        authoring_root: Path | None = None,
        operator_token: str | None = None,
        runtime_provider_factory: Any | None = None,
        runtime_root: Path | None = None,
    ):
        resolved_db = Path(db_path).expanduser().resolve() if db_path is not None else None
        self._control_lock = RLock()
        self._sessions_lock = RLock()
        self.control = (
            ControlService(resolved_db, authoring_root=authoring_root)
            if resolved_db is not None
            else None
        )
        if resolved_db is not None:
            container = resolved_db.parent.parent
            inferred_root = container.parent if container.name == "instances" else container
        else:
            inferred_root = Path(runtime_root or (Path.home() / ".sim2")).expanduser().resolve()
        self.runtime_root = Path(runtime_root).expanduser().resolve() if runtime_root is not None else inferred_root
        self.authoring_root = (
            Path(authoring_root).resolve()
            if authoring_root is not None
            else (self.control.authoring.authoring_root if self.control is not None else None)
        )
        self.database_path = resolved_db
        self.runtime_provider_factory = runtime_provider_factory
        self.registry = InstanceRegistry(self.runtime_root, active_db=resolved_db)
        if resolved_db is not None:
            self.registry.mark_active(resolved_db)
            self.instance_id = str(self.registry.describe(resolved_db)["instance_id"])
        else:
            self.instance_id = ""
        self.runtime = (
            RuntimeSupervisor(self.control, provider_factory=self.runtime_provider_factory)
            if self.control is not None
            else None
        )
        self.operator_token = str(operator_token or os.environ.get("SIM2_OPERATOR_TOKEN") or secrets.token_urlsafe(32))
        self._sessions: dict[str, dict[str, str]] = {}
        self._idem_lock = RLock()
        self._idem_store: dict[tuple[str, str], dict[str, Any]] = {}
        self._closed = False
        self.router = V1Router(self)
        self.stream = StreamHub(self)
        self.tasks = ManualTaskRunner(self)
        super().__init__(address, _Handler)

    def session_context(self, token: str) -> dict[str, str]:
        """Describe a session without ever exposing the token."""

        raw = str(token or "")
        if not raw:
            return {}
        if secrets.compare_digest(raw, self.operator_token):
            return {"role": "operator", "instance_id": self.instance_id}
        return dict(self.session_for(raw) or {})

    @property
    def has_world(self) -> bool:
        return self.control is not None and self.database_path is not None

    def authorize_session(
        self,
        token: str,
        role: str,
        *,
        player_id: str | None = None,
    ) -> tuple[bool, str]:
        if secrets.compare_digest(str(token or ""), self.operator_token):
            return True, ""
        session = self.session_for(str(token or ""))
        if session is None:
            return False, "a local session with the required role is required"
        if session.get("role") == "operator":
            return True, ""
        if role == "observer":
            return True, ""
        if role == "player" and session.get("role") == "player" and (
            player_id is None or str(session.get("player_id")) == str(player_id)
        ):
            if str(session.get("instance_id")) == self.instance_id:
                return True, ""
            return False, "the player session belongs to a different world instance"
        return False, f"the session does not permit {role} access"

    def service_call(
        self,
        method: str,
        *args: Any,
        authorize_role: str | None = None,
        authorize_token: str | None = None,
        authorize_player_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Keep one live ControlService for the duration of a request.

        Instance activation swaps and closes service objects. Serializing the
        call here prevents an otherwise valid HTTP request from observing a
        half-switched server or using a service after it has been closed.
        """

        with self._control_lock:
            if self.control is None:
                raise NoActiveWorldError("no world instance is open; create or activate one first")
            if authorize_role is not None:
                allowed, message = self.authorize_session(
                    str(authorize_token or ""),
                    authorize_role,
                    player_id=authorize_player_id,
                )
                if not allowed:
                    raise PermissionError(message)
            service = self.control
            operation = getattr(service, method)
            return operation(*args, **kwargs)

    def status_payload(self) -> dict[str, Any]:
        with self._control_lock:
            if self.control is None or self.database_path is None:
                return {
                    "status": "no_world",
                    "runtime": self.runtime_status(),
                    "instance": None,
                    "instances": self.registry.list(active_db=None),
                }
            payload = self.control.status()
            payload["runtime"] = self.runtime.status()
            payload["instance"] = self.registry.describe(self.database_path)
            return payload

    def runtime_status(self) -> dict[str, Any]:
        with self._control_lock:
            if self.runtime is None or self.database_path is None:
                return {
                    "status": "no_world",
                    "state": "no_world",
                    "running": False,
                    "thread_alive": False,
                    "run_id": None,
                    "provider": None,
                    "world_minute": 0,
                    "world_runtime_status": "no_world",
                    "llm_calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "last_error": "",
                    "stop_reason": "",
                    "available_providers": RuntimeSupervisor.available_providers_for_config(),
                    "llm_configured": RuntimeSupervisor.llm_configured(),
                    "history": [],
                    "instance": None,
                }
            payload = self.runtime.status()
            payload["instance"] = self.registry.describe(self.database_path)
            return payload

    def world_catalog(self) -> dict[str, Any]:
        bundled = bundled_world_catalog()
        authored: list[dict[str, Any]] = []
        with self._control_lock:
            if self.control is not None:
                try:
                    authored = list(self.control.authoring.list_packages().get("packages") or [])
                except (OSError, ValueError, TypeError):
                    authored = []
            elif self.authoring_root is not None:
                try:
                    authored = list(
                        AuthoringService(self.authoring_root, runtime_root=self.runtime_root)
                        .list_packages()
                        .get("packages")
                        or []
                    )
                except (OSError, ValueError, TypeError):
                    authored = []
        for item in authored:
            item.setdefault("source", "authored")
        return {
            "status": "ok",
            "worlds": merge_catalog(bundled, authored),
            "bundled": bundled,
            "authored": authored,
        }

    def instance_list(self) -> dict[str, Any]:
        with self._control_lock:
            return {
                "status": "ok",
                "active_db": str(self.database_path) if self.database_path is not None else "",
                "instance_id": self.instance_id,
                "instances": self.registry.list(active_db=self.database_path),
            }

    def create_instance(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._control_lock:
            if self.control is None:
                raise NoActiveWorldError("no world instance is open; create one from the launcher")
            result = self.control.authoring_create(body)
            if result.get("status") == "passed":
                result["instances"] = self.registry.list(active_db=self.database_path)
            return result

    def enter_player(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._control_lock:
            if self.control is None:
                raise NoActiveWorldError("no world instance is open")
            player_id = str(body["player_id"])
            result = self.control.enter_player(
                player_id,
                str(body.get("origin_id") or ""),
                body.get("name"),
            )
            if result.get("status") == "accepted":
                result = {
                    **result,
                    "session_token": self.issue_session("player", player_id=player_id),
                }
            return result

    def activate_instance(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._control_lock:
            try:
                target = self.registry.resolve(
                    instance_id=body.get("instance_id"),
                    db_path=body.get("db_path"),
                )
            except (OSError, ValueError) as exc:
                return {"status": "rejected", "message": str(exc), "failed_check": "instance"}
            if target == self.database_path:
                return {"status": "accepted", "already_active": True, "runtime": self.runtime_status()}
            self.tasks.cancel_all()
            previous = self.control
            if self.runtime is not None:
                self.runtime.close()
            try:
                replacement = ControlService(target, authoring_root=self.authoring_root)
            except Exception as exc:
                if previous is not None:
                    self.runtime = RuntimeSupervisor(previous, provider_factory=self.runtime_provider_factory)
                else:
                    self.runtime = None
                return {"status": "rejected", "message": f"cannot open instance: {exc}", "failed_check": "instance"}
            self.control = replacement
            self.database_path = target
            self.registry.active_db = target
            self.registry.mark_active(target)
            self.instance_id = str(self.registry.describe(target)["instance_id"])
            self.runtime = RuntimeSupervisor(replacement, provider_factory=self.runtime_provider_factory)
            if previous is not None:
                previous.close()
            return {
                "status": "accepted",
                "instance": self.registry.describe(target),
                "runtime": self.runtime.status(),
            }

    def _runtime_or_error(self) -> RuntimeSupervisor:
        if self.runtime is None:
            raise NoActiveWorldError("no world instance is open")
        return self.runtime

    def start_runtime(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._control_lock:
            return self._runtime_or_error().start(
                provider=body.get("provider") or None,
                max_world_minutes=body.get("max_world_minutes", 1440),
                max_llm_calls=body.get("max_llm_calls", 120),
            )

    def pause_runtime(self, reason: str = "web") -> dict[str, Any]:
        with self._control_lock:
            return self._runtime_or_error().pause(reason)

    def resume_runtime(self, reason: str = "web") -> dict[str, Any]:
        with self._control_lock:
            return self._runtime_or_error().resume(reason)

    def stop_runtime(self, reason: str = "web-stop") -> dict[str, Any]:
        with self._control_lock:
            return self._runtime_or_error().stop(reason)

    def step_runtime(self) -> dict[str, Any]:
        with self._control_lock:
            runtime = self._runtime_or_error()
            result = runtime.step()
            return {**result, "runtime": runtime.status()}

    def _resolve_agent_id_locked(self, value: str | None) -> str:
        raw = str(value or "").strip()
        if not raw or self.control is None:
            return ""
        try:
            canonical = self.control.platform.targets.canonical_id(raw)
        except Exception:
            canonical = None
        candidate = str(canonical or raw)
        try:
            agent = self.control.platform.store.repositories.entities.get_agent(candidate)
            return str(agent.get("id") or candidate)
        except KeyError:
            return raw

    def resolve_agent_id(self, value: str | None) -> str:
        """Resolve an agent id or exact name to its canonical id for auth.

        Name resolution is public projection data (observer sees names), so it
        is safe before authorization. Unknown values pass through unchanged so
        Platform can return its precise participant error with hints.
        """

        with self._control_lock:
            return self._resolve_agent_id_locked(value)

    def world_revision(self) -> int:
        with self._control_lock:
            if self.control is None:
                return 0
            return int(self.control.platform.store.repositories.metadata.get("world_revision", 0) or 0)

    def player_state(self, player_id: str) -> dict[str, Any]:
        """Return whether a player exists and is currently executing a task."""

        with self._control_lock:
            if self.control is None:
                return {"exists": False, "active": False, "world_minute": 0, "world_revision": 0}
            resolved = self._resolve_agent_id_locked(player_id)
            try:
                agent = self.control.platform.store.repositories.entities.get_agent(resolved)
            except KeyError:
                return {"exists": False, "active": False, "world_minute": 0, "world_revision": 0}
            state = agent.get("initial_state", {}) or {}
            active = bool(
                state.get("status") in {"executing", "paused"}
                or state.get("active_action")
                or state.get("active_bt_instance")
            )
            return {
                "exists": True,
                "active": active,
                "world_minute": int(self.control.platform.store.repositories.metadata.get("world_minute", 0) or 0),
                "world_revision": int(self.control.platform.store.repositories.metadata.get("world_revision", 0) or 0),
            }

    def advance_player_chunk(
        self,
        player_id: str,
        minutes: int = 1,
        *,
        provider_name: str | None = None,
        instance_id: str = "",
    ) -> dict[str, Any]:
        """Advance one bounded chunk without holding the server control lock.

        The runtime object is validated under the lock and used outside it, so
        read requests can interleave between world minutes instead of waiting
        for a whole 480-step manual wait.
        """

        with self._control_lock:
            runtime = self._runtime_or_error()
            if instance_id and str(instance_id) != str(self.instance_id):
                return {
                    "status": "rejected",
                    "failed_check": "instance_changed",
                    "message": "the world instance changed before the manual task finished",
                }
            resolved = self._resolve_agent_id_locked(player_id)
            if not resolved:
                return {"status": "rejected", "failed_check": "player_id", "message": "player_id is required"}
            if runtime.running:
                return {
                    "status": "rejected",
                    "failed_check": "runtime_running",
                    "message": "The autonomous world is already advancing; pause or stop it before using manual wait.",
                }
        return runtime.advance_manual(minutes, provider_name=provider_name, reason="web-player-wait")

    def player_wait(self, player_id: str, *, minutes: int = 1, provider_name: str | None = None, authorize_token: str | None = None) -> dict[str, Any]:
        """Synchronous wait kept for internal callers; HTTP uses tasks."""

        return self.advance_player_chunk(
            player_id,
            minutes,
            provider_name=provider_name,
            instance_id=self.instance_id,
        )

    def resolve_dialogue_reply(self, player_id: str, *, provider_name: str | None = None, token: str | None = None) -> dict[str, Any]:
        with self._control_lock:
            runtime = self._runtime_or_error()
            resolved = self._resolve_agent_id_locked(player_id)
            allowed, message = self.authorize_session(str(token or ""), "player", player_id=resolved)
            if not allowed:
                raise PermissionError(message)
        # Provider calls must not hold the server control lock.
        return runtime.resolve_pending_interactions(provider_name)

    def trade_offers(self, agent_id: str, *, include_closed: bool = False, token: str | None = None) -> dict[str, Any]:
        with self._control_lock:
            if self.control is None:
                raise NoActiveWorldError("no world instance is open")
            viewer = self._resolve_agent_id_locked(agent_id)
            if not viewer:
                return {"status": "rejected", "message": "agent_id is required", "failed_check": "agent_id"}
            allowed, message = self.authorize_session(str(token or ""), "player", player_id=viewer)
            if not allowed:
                # Operators may inspect any agent; players only their own.
                allowed_operator, _ = self.authorize_session(str(token or ""), "operator")
                if not allowed_operator:
                    raise PermissionError(message)
            return {"status": "ok", "offers": self.control.trade_offers(viewer, include_closed=bool(include_closed))}

    def decide(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._control_lock:
            if self.control is None:
                raise NoActiveWorldError("no world instance is open")
            agent_id = self._resolve_agent_id_locked(str(body.get("agent_id") or "").strip()) or str(body.get("agent_id") or "").strip()
            role = str(body.get("role") or "agent_decision").strip() or "agent_decision"
            if not agent_id:
                return {"status": "rejected", "message": "agent_id is required", "failed_check": "agent_id"}
            provider = None
            if self.runtime_provider_factory is not None:
                try:
                    provider = self.runtime_provider_factory(None).get(role)
                except Exception:
                    provider = None
            return self.control.decide(agent_id, role, provider)

    def simulate(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._control_lock:
            runtime = self._runtime_or_error()
            if self.control is None:
                raise NoActiveWorldError("no world instance is open")
            if runtime.running:
                return {"status": "rejected", "message": "Pause or stop the autonomous runner before using simulate.", "failed_check": "runtime_running"}
            providers = None
            provider_name = body.get("provider") or None
            if self.runtime_provider_factory is not None:
                try:
                    providers = self.runtime_provider_factory(provider_name)
                except Exception as exc:
                    return {"status": "rejected", "message": str(exc), "failed_check": "provider"}
            return self.control.simulate(
                int(body.get("minutes", 1)),
                wait_cooldown_minutes=int(body.get("wait_cooldown_minutes", body.get("wait_cooldown", 30))),
                llm_error_cooldown_minutes=int(body.get("llm_error_cooldown_minutes", body.get("llm_cooldown", 60))),
                provider_name=None if providers is not None else provider_name,
                providers=providers,
            )

    def _create_package_instance(self, package_id: str, instance_id: str) -> dict[str, Any]:
        """Create one instance from a catalog package, bundled or authored."""

        if self.control is not None:
            result = self.control.authoring_create({"package_id": package_id, "instance_id": instance_id})
            unknown = result.get("status") == "rejected" and "unknown world package" in " ".join(result.get("errors") or [])
            if not unknown:
                return result
        elif self.authoring_root is not None:
            result = AuthoringService(self.authoring_root, runtime_root=self.runtime_root).create_instance(
                package_id,
                instance_id,
            )
            unknown = result.get("status") == "rejected" and "unknown world package" in " ".join(result.get("errors") or [])
            if not unknown:
                return result
        # Bundled packages are importable even when no authoring root is set.
        try:
            from worlds import world_path
            from ..bootstrap.service import create_instance as bootstrap_create_instance

            config_dir = world_path(package_id)
        except Exception as exc:
            return {
                "status": "rejected",
                "message": f"unknown world package: {package_id}: {exc}",
                "failed_check": "package_id",
            }
        try:
            db_path = bootstrap_create_instance(config_dir, instance_id, self.runtime_root)
        except (FileExistsError, ValueError, OSError) as exc:
            return {"status": "rejected", "message": str(exc), "failed_check": "instance"}
        return {
            "status": "passed",
            "package_id": package_id,
            "instance_id": instance_id,
            "runtime_root": str(self.runtime_root),
            "db_path": str(db_path),
        }

    def init_instance(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._control_lock:
            package_id = str(body.get("package_id") or body.get("world") or "").strip()
            instance_id = str(body.get("instance_id") or "").strip()
            if not package_id or not instance_id:
                return {"status": "rejected", "message": "package_id and instance_id are required", "failed_check": "instance"}
            result = self._create_package_instance(package_id, instance_id)
            if result.get("status") == "passed":
                result["instances"] = self.registry.list(active_db=self.database_path)
                if body.get("activate") or self.control is None:
                    activation = self.activate_instance({"instance_id": instance_id})
                    result["activation"] = activation
                    if activation.get("status") == "accepted":
                        result["active"] = True
                        result["instances"] = self.registry.list(active_db=self.database_path)
            return result

    def snapshots_root(self) -> Path:
        root = Path(self.runtime_root) / "snapshots"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def snapshot_list(self) -> dict[str, Any]:
        with self._control_lock:
            root = self.snapshots_root()
            items: list[dict[str, Any]] = []
            for path in sorted(root.glob("*.sqlite3"), key=lambda item: item.stat().st_mtime, reverse=True):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                items.append({
                    "snapshot_id": path.stem,
                    "path": str(path),
                    "size": int(stat.st_size),
                    "modified_at": stat.st_mtime,
                    "instance_id": str(self.instance_id),
                })
            return {"status": "ok", "snapshots": items, "instance_id": str(self.instance_id)}

    def snapshot_create(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._control_lock:
            import time

            if self.control is None:
                raise NoActiveWorldError("no world instance is open")
            label = str(body.get("label") or "manual").strip().replace("/", "-").replace("\\", "-") or "manual"
            minute = int(self.control.status().get("world_minute", 0))
            filename = f"{self.instance_id}_m{minute}_{label}_{int(time.time())}.sqlite3"
            destination = self.snapshots_root() / filename
            snapshot_path = self.control.snapshot(destination)
            return {"status": "ok", "snapshot_id": Path(snapshot_path).stem, "path": str(snapshot_path), **self.snapshot_list()}

    def snapshot_download(self, snapshot_id: str) -> tuple[str, bytes] | dict[str, Any]:
        """Read one server-side snapshot for download without touching the live DB."""

        with self._control_lock:
            name = str(snapshot_id or "").strip()
            if not name:
                return {"status": "rejected", "message": "snapshot_id is required", "failed_check": "snapshot"}
            candidate = Path(name)
            if not candidate.is_file():
                candidate = self.snapshots_root() / (name if name.endswith(".sqlite3") else name + ".sqlite3")
            try:
                resolved = candidate.resolve()
                resolved.relative_to(self.snapshots_root().resolve())
            except (OSError, ValueError):
                return {"status": "rejected", "message": f"snapshot does not exist: {name}", "failed_check": "snapshot"}
            if not resolved.is_file():
                return {"status": "rejected", "message": f"snapshot does not exist: {name}", "failed_check": "snapshot"}
            try:
                return (resolved.name, resolved.read_bytes())
            except OSError as exc:
                return {"status": "rejected", "message": str(exc), "failed_check": "snapshot"}

    def snapshot_restore(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._control_lock:
            if self.control is None or self.runtime is None:
                raise NoActiveWorldError("no world instance is open")
            snapshot_id = str(body.get("snapshot_id") or body.get("path") or "").strip()
            if not snapshot_id:
                return {"status": "rejected", "message": "snapshot_id is required", "failed_check": "snapshot"}
            candidate = Path(snapshot_id)
            if not candidate.is_file():
                candidate = self.snapshots_root() / (snapshot_id if snapshot_id.endswith(".sqlite3") else snapshot_id + ".sqlite3")
            if not candidate.is_file():
                return {"status": "rejected", "message": f"snapshot does not exist: {snapshot_id}", "failed_check": "snapshot"}
            self.runtime.close()
            try:
                result = self.control.restore(candidate)
            except (FileNotFoundError, ValueError, OSError) as exc:
                return {"status": "rejected", "message": str(exc), "failed_check": "snapshot"}
            return {"status": "ok", "restored": str(candidate), "world_status": result, "runtime": self.runtime.status()}

    def issue_session(self, role: str, *, player_id: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        with self._sessions_lock:
            self._sessions[token] = {
                "role": str(role),
                "instance_id": self.instance_id,
                **({"player_id": str(player_id)} if player_id else {}),
            }
        return token

    def session_for(self, token: str) -> dict[str, str] | None:
        with self._sessions_lock:
            return self._sessions.get(str(token or ""))

    @staticmethod
    def _idem_key_value(body: dict[str, Any], headers: Any) -> str:
        try:
            header_value = str(headers.get("Idempotency-Key") or "").strip()
        except Exception:
            header_value = ""
        key = header_value or str(body.get("idempotency_key") or "").strip()
        return key if len(key) <= 128 else ""

    def idem_lookup(self, token: str, key: str) -> dict[str, Any] | None:
        if not key:
            return None
        with self._idem_lock:
            stored = self._idem_store.get((str(token or ""), key))
            return {"status": stored["status"], "value": dict(stored["value"])} if stored else None

    def idem_store(self, token: str, key: str, status: int, value: dict[str, Any]) -> None:
        if not key or status >= 500 or not isinstance(value, dict):
            return
        with self._idem_lock:
            while len(self._idem_store) >= 500:
                self._idem_store.pop(next(iter(self._idem_store)))
            self._idem_store[(str(token or ""), key)] = {"status": int(status), "value": dict(value)}

    def close(self) -> None:
        self._close_background()
        self._close_control()
        self.shutdown()
        self.server_close()

    def close_after_serve_forever(self) -> None:
        """Close resources after Ctrl-C has already exited serve_forever()."""

        self._close_background()
        self._close_control()
        self.server_close()

    def _close_background(self) -> None:
        try:
            self.tasks.close()
        finally:
            self.stream.close()

    def _close_control(self) -> None:
        with self._control_lock:
            if self._closed:
                return
            self._closed = True
            try:
                if self.runtime is not None:
                    self.runtime.close()
            finally:
                if self.control is not None:
                    self.control.close()


def serve(
    db_path: Path | None,
    host: str = "127.0.0.1",
    port: int = 8080,
    *,
    runtime_root: Path | None = None,
    instance_id: str | None = None,
    authoring_root: Path | None = None,
) -> ControlHTTPServer:
    root = Path(runtime_root or (Path.home() / ".sim2")).expanduser().resolve()
    registry = InstanceRegistry(root)
    try:
        resolved: Path | None = db_path or registry.resolve(instance_id=instance_id)
    except FileNotFoundError:
        # Starting with no world is a supported launcher state; the browser
        # creates the first instance from a bundled template.
        resolved = None
    server = ControlHTTPServer(
        (host, port),
        resolved,
        authoring_root=authoring_root,
        runtime_root=root,
    )
    try:
        server.serve_forever()
    finally:
        server.close_after_serve_forever()
    return server
