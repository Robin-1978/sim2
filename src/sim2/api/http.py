from __future__ import annotations

import json
import os
import secrets
from threading import RLock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .instances import InstanceRegistry
from .runtime_supervisor import RuntimeSupervisor
from .service import ControlService
from .web import web_asset


class _Handler(BaseHTTPRequestHandler):
    server: "ControlHTTPServer"

    def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover - avoid noisy library output
        return

    def _write(self, status: int, value: dict[str, Any]) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-cache")
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

    def _forbidden(self, message: str = "a local session with the required role is required") -> None:
        self._write(403, {"status": "forbidden", "message": message, "failed_check": "session"})

    def _require(self, role: str, *, player_id: str | None = None) -> bool:
        allowed, message = self.server.authorize_session(self._token(), role, player_id=player_id)
        if not allowed:
            self._forbidden(message)
        return allowed

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        try:
            asset = web_asset(path)
            if asset is not None:
                return self._write_bytes(200, *asset)
            if path == "/session":
                token = self.server.issue_session("observer")
                return self._write(200, {"status": "ok", "role": "observer", "session_token": token})
            if path == "/status":
                return self._write(200, self.server.status_payload())
            if path == "/instances":
                return self._write(200, self.server.instance_list())
            if path == "/runtime":
                return self._write(200, self.server.runtime_status())
            if path == "/health":
                health = self.server.service_call("health")
                return self._write(200 if health.get("status") == "ok" else 503, health)
            if path == "/metrics":
                return self._write(200, self.server.service_call("metrics"))
            if path == "/observer":
                query = parse_qs(urlparse(self.path).query)
                limit = int(query.get("limit", [20])[0])
                return self._write(200, self.server.service_call("observer", limit))
            if path == "/timeline":
                query = parse_qs(urlparse(self.path).query)
                limit = int(query.get("limit", [100])[0])
                after = int(query.get("after", [0])[0])
                ticks = query.get("ticks", ["0"])[0].lower() in {"1", "true", "yes"}
                return self._write(200, self.server.service_call("timeline", limit, after, ticks))
            if path == "/map":
                query = parse_qs(urlparse(self.path).query)
                viewer_id = query.get("viewer_id", [None])[0]
                observer = query.get("observer", ["0"])[0].lower() in {"1", "true", "yes"}
                return self._write(200, self.server.service_call("map", viewer_id=viewer_id, observer=observer))
            if path == "/player/entry":
                return self._write(200, self.server.service_call("player_entry"))
            if path == "/player/view":
                query = parse_qs(urlparse(self.path).query)
                player_id = str(query.get("player_id", [""])[0])
                return self._write(200, self.server.service_call(
                    "player_view",
                    player_id,
                    authorize_role="player",
                    authorize_token=self._token(),
                    authorize_player_id=player_id,
                ))
            if path == "/audit":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.service_call("audit"))
            if path == "/evolution/tasks":
                if not self._require("operator"):
                    return None
                return self._write(200, {"tasks": self.server.service_call("evolution_tasks")})
            if path == "/authoring/schema":
                return self._write(200, self.server.service_call("authoring_schema"))
            if path == "/authoring/catalog":
                return self._write(200, self.server.service_call("authoring_catalog"))
            return self._write(404, {"status": "not_found"})
        except PermissionError as exc:
            return self._forbidden(str(exc))
        except Exception as exc:
            return self._write(500, {"status": "error", "message": str(exc)})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        try:
            body = self._read_json()
            if path == "/session":
                role = str(body.get("role") or "observer")
                if role == "operator":
                    supplied = str(body.get("operator_token") or "")
                    if not secrets.compare_digest(supplied, self.server.operator_token):
                        return self._forbidden("operator token is invalid")
                    token = self.server.issue_session("operator")
                    return self._write(200, {"status": "ok", "role": "operator", "session_token": token})
                if role != "observer":
                    return self._forbidden("only observer sessions may be created without a player")
                token = self.server.issue_session("observer")
                return self._write(200, {"status": "ok", "role": "observer", "session_token": token})
            if path.startswith("/authoring/"):
                if not self._require("operator"):
                    return None
            if path == "/authoring/open":
                return self._write(200, self.server.service_call("authoring_open", body))
            if path == "/authoring/create":
                return self._write(200, self.server.create_instance(body))
            if path == "/authoring/build":
                return self._write(200, self.server.service_call("authoring_build", body))
            if path == "/authoring/compile":
                return self._write(200, self.server.service_call("authoring_compile", body))
            if path == "/authoring/smoke":
                return self._write(200, self.server.service_call("authoring_smoke", body))
            if path == "/authoring/export":
                return self._write(200, self.server.service_call("authoring_export", body))
            if path == "/instances":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.create_instance(body))
            if path == "/instances/activate":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.activate_instance(body))
            if path == "/runtime/start":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.start_runtime(body))
            if path == "/runtime/pause":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.pause_runtime(str(body.get("reason", "web"))))
            if path == "/runtime/resume":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.resume_runtime(str(body.get("reason", "web"))))
            if path == "/runtime/stop":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.stop_runtime(str(body.get("reason", "web-stop"))))
            if path == "/runtime/step":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.step_runtime())
            if path == "/pause":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.pause_runtime(str(body.get("reason", "http"))))
            if path == "/resume":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.resume_runtime(str(body.get("reason", "http"))))
            if path == "/step":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.step_runtime())
            if path == "/events":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.service_call("inject", str(body["event_type"]), dict(body.get("payload", {})), body.get("actor_id")))
            if path == "/players":
                return self._write(200, self.server.enter_player(body))
            if path == "/player/act":
                player_id = str(body.get("player_id") or "")
                return self._write(200, self.server.service_call(
                    "player_act",
                    body,
                    authorize_role="player",
                    authorize_token=self._token(),
                    authorize_player_id=player_id,
                ))
            if path == "/player/wait":
                return self._write(200, self.server.player_wait(
                    str(body.get("player_id") or ""),
                    authorize_token=self._token(),
                ))
            if path == "/dialogue":
                speaker_id = str(body.get("speaker_id") or "")
                role = "operator" if speaker_id == "god" else "player"
                return self._write(200, self.server.service_call(
                    "dialogue",
                    speaker_id,
                    str(body["listener_id"]),
                    str(body.get("topic", "general")),
                    str(body["message"]),
                    authorize_role=role,
                    authorize_token=self._token(),
                    authorize_player_id=speaker_id,
                ))
            if path == "/trade/offers":
                offerer_id = str(body.get("offerer_id") or "")
                return self._write(200, self.server.service_call(
                    "trade_offer",
                    offerer_id,
                    str(body["recipient_id"]),
                    dict(body.get("give", {})),
                    dict(body.get("want", {})),
                    authorize_role="player",
                    authorize_token=self._token(),
                    authorize_player_id=offerer_id,
                ))
            if path == "/trade/accept":
                recipient_id = str(body.get("recipient_id") or "")
                return self._write(200, self.server.service_call(
                    "trade_accept",
                    recipient_id,
                    str(body["offer_id"]),
                    authorize_role="player",
                    authorize_token=self._token(),
                    authorize_player_id=recipient_id,
                ))
            return self._write(404, {"status": "not_found"})
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._write(400, {"status": "bad_request", "message": str(exc)})
        except PermissionError as exc:
            return self._forbidden(str(exc))
        except Exception as exc:
            return self._write(500, {"status": "error", "message": str(exc)})


class ControlHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        db_path: Path,
        *,
        authoring_root: Path | None = None,
        operator_token: str | None = None,
        runtime_provider_factory: Any | None = None,
    ):
        resolved_db = Path(db_path).expanduser().resolve()
        self._control_lock = RLock()
        self._sessions_lock = RLock()
        self.control = ControlService(resolved_db, authoring_root=authoring_root)
        self.authoring_root = (
            Path(authoring_root).resolve()
            if authoring_root is not None
            else self.control.authoring.authoring_root
        )
        self.database_path = resolved_db
        container = resolved_db.parent.parent
        self.runtime_root = container.parent if container.name == "instances" else container
        self.runtime_provider_factory = runtime_provider_factory
        self.registry = InstanceRegistry(self.runtime_root, active_db=resolved_db)
        self.registry.mark_active(resolved_db)
        self.instance_id = str(self.registry.describe(resolved_db)["instance_id"])
        self.runtime = RuntimeSupervisor(self.control, provider_factory=self.runtime_provider_factory)
        self.operator_token = str(operator_token or os.environ.get("SIM2_OPERATOR_TOKEN") or secrets.token_urlsafe(32))
        self._sessions: dict[str, dict[str, str]] = {}
        self._closed = False
        super().__init__(address, _Handler)

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
            payload = self.control.status()
            payload["runtime"] = self.runtime.status()
            payload["instance"] = self.registry.describe(self.database_path)
            return payload

    def runtime_status(self) -> dict[str, Any]:
        with self._control_lock:
            payload = self.runtime.status()
            payload["instance"] = self.registry.describe(self.database_path)
            return payload

    def instance_list(self) -> dict[str, Any]:
        with self._control_lock:
            return {
                "status": "ok",
                "active_db": str(self.database_path),
                "instances": self.registry.list(active_db=self.database_path),
            }

    def create_instance(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._control_lock:
            result = self.control.authoring_create(body)
            if result.get("status") == "passed":
                result["instances"] = self.registry.list(active_db=self.database_path)
            return result

    def enter_player(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._control_lock:
            player_id = str(body["player_id"])
            result = self.control.enter_player(
                player_id,
                str(body["origin_id"]),
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
            self.runtime.close()
            previous = self.control
            try:
                replacement = ControlService(target, authoring_root=self.authoring_root)
            except Exception as exc:
                self.runtime = RuntimeSupervisor(previous, provider_factory=self.runtime_provider_factory)
                return {"status": "rejected", "message": f"cannot open instance: {exc}", "failed_check": "instance"}
            self.control = replacement
            self.database_path = target
            self.registry.active_db = target
            self.registry.mark_active(target)
            self.instance_id = str(self.registry.describe(target)["instance_id"])
            self.runtime = RuntimeSupervisor(replacement, provider_factory=self.runtime_provider_factory)
            previous.close()
            return {
                "status": "accepted",
                "instance": self.registry.describe(target),
                "runtime": self.runtime.status(),
            }

    def start_runtime(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._control_lock:
            return self.runtime.start(
                provider=body.get("provider") or None,
                max_world_minutes=body.get("max_world_minutes", 1440),
                max_llm_calls=body.get("max_llm_calls", 120),
            )

    def pause_runtime(self, reason: str = "web") -> dict[str, Any]:
        with self._control_lock:
            return self.runtime.pause(reason)

    def resume_runtime(self, reason: str = "web") -> dict[str, Any]:
        with self._control_lock:
            return self.runtime.resume(reason)

    def stop_runtime(self, reason: str = "web-stop") -> dict[str, Any]:
        with self._control_lock:
            return self.runtime.stop(reason)

    def step_runtime(self) -> dict[str, Any]:
        with self._control_lock:
            result = self.runtime.step()
            return {**result, "runtime": self.runtime.status()}

    def player_wait(self, player_id: str, *, authorize_token: str | None = None) -> dict[str, Any]:
        with self._control_lock:
            allowed, message = self.authorize_session(
                str(authorize_token or ""),
                "player",
                player_id=player_id,
            )
            if not allowed:
                raise PermissionError(message)
            if self.runtime.running:
                return {
                    "status": "rejected",
                    "failed_check": "runtime_running",
                    "message": "The autonomous world is already advancing; pause or stop it before using manual wait.",
                }
            return self.control.player_wait(player_id)

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

    def close(self) -> None:
        self._close_control()
        self.shutdown()
        self.server_close()

    def close_after_serve_forever(self) -> None:
        """Close resources after Ctrl-C has already exited serve_forever()."""

        self._close_control()
        self.server_close()

    def _close_control(self) -> None:
        with self._control_lock:
            if self._closed:
                return
            self._closed = True
            try:
                self.runtime.close()
            finally:
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
    resolved = db_path or registry.resolve(instance_id=instance_id)
    server = ControlHTTPServer(
        (host, port),
        Path(resolved),
        authoring_root=authoring_root,
    )
    try:
        server.serve_forever()
    finally:
        server.close_after_serve_forever()
    return server
