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

    def _write_bytes(self, status: int, body: bytes, content_type: str, *, filename: str | None = None) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-cache")
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

    def _forbidden(self, message: str = "a local session with the required role is required") -> None:
        self._write(403, {"status": "forbidden", "message": message, "failed_check": "session"})

    def _require(self, role: str, *, player_id: str | None = None) -> bool:
        allowed, message = self.server.authorize_session(self._token(), role, player_id=player_id)
        if not allowed:
            self._forbidden(message)
        return allowed

    def _serve_stream(self, after: int, ticks: bool) -> None:
        """Push public timeline deltas and runtime/dialogue/decision notices.

        The stream is read-only: it reuses the same public projections as the
        polling endpoints and never triggers an Agent turn. Clients resume
        with `?after=<event_id>` or the `Last-Event-ID` header after a
        disconnect; per-feed payloads carry their own ids for de-duplication.
        """

        last_id_header = self.headers.get("Last-Event-ID")
        if last_id_header:
            try:
                after = int(str(last_id_header).strip())
            except (TypeError, ValueError):
                pass
        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "keep-alive")
        self.send_header("x-accel-buffering", "no")
        self.end_headers()

        def emit(name: str, payload: dict[str, Any]) -> bool:
            try:
                text = json.dumps(payload, ensure_ascii=False)
                self.wfile.write(f"event: {name}\ndata: {text}\n\n".encode("utf-8"))
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, ValueError):
                return False

        import time as _time

        cursor = max(0, int(after))
        primed = False
        last_dialogue = -1
        last_decision = -1
        last_runtime = ""
        if not emit("ready", {"after": cursor}):
            return
        beat = _time.monotonic()
        while True:
            try:
                timeline = self.server.service_call("timeline", 100, cursor, ticks)
            except Exception:
                return
            events = timeline.get("events", []) if isinstance(timeline, dict) else []
            if events:
                cursor = max(cursor, int(timeline.get("next_after_event_id", cursor) or cursor))
                if not emit("timeline", {"events": events, "next_after_event_id": cursor}):
                    return
            try:
                runtime = self.server.runtime_status()
            except Exception:
                return
            signature = json.dumps(runtime, ensure_ascii=False, sort_keys=True)
            if signature != last_runtime:
                last_runtime = signature
                if not emit("runtime", runtime):
                    return
            try:
                dialogue = self.server.service_call("observer_dialogue", 5)
            except Exception:
                dialogue = None
            if isinstance(dialogue, dict):
                fresh = [item for item in (dialogue.get("exchanges") or []) if int(item.get("dialogue_id") or -1) > last_dialogue]
                last_dialogue = max([last_dialogue] + [int(item.get("dialogue_id") or -1) for item in (dialogue.get("exchanges") or [])])
                if primed and fresh and not emit("dialogue", {"exchanges": fresh}):
                    return
            try:
                decisions = self.server.service_call("observer_decisions", 10)
            except Exception:
                decisions = None
            if isinstance(decisions, dict):
                fresh_decisions = [item for item in (decisions.get("decisions") or []) if int(item.get("event_id") or -1) > last_decision]
                last_decision = max([last_decision] + [int(item.get("event_id") or -1) for item in (decisions.get("decisions") or [])])
                if primed and fresh_decisions and not emit("decisions", {"decisions": fresh_decisions}):
                    return
            primed = True
            if _time.monotonic() - beat >= 15:
                beat = _time.monotonic()
                try:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ValueError):
                    return
            _time.sleep(1.0)

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
            if path == "/observer/dialogue":
                query = parse_qs(urlparse(self.path).query)
                limit = int(query.get("limit", [20])[0])
                return self._write(200, self.server.service_call("observer_dialogue", limit))
            if path == "/observer/decisions":
                query = parse_qs(urlparse(self.path).query)
                limit = int(query.get("limit", [20])[0])
                return self._write(200, self.server.service_call("observer_decisions", limit))
            if path == "/events/related":
                query = parse_qs(urlparse(self.path).query)
                return self._write(200, self.server.service_call("event_related", int(query.get("event_id", [0])[0])))
            if path == "/events/stream":
                query = parse_qs(urlparse(self.path).query)
                try:
                    after = int(query.get("after", [0])[0])
                except (TypeError, ValueError):
                    after = 0
                ticks = str(query.get("ticks", ["0"])[0]).lower() in {"1", "true", "yes"}
                return self._serve_stream(after, ticks)
            if path == "/player/entry":
                return self._write(200, self.server.service_call("player_entry"))
            if path == "/player/view":
                query = parse_qs(urlparse(self.path).query)
                raw_player_id = str(query.get("player_id", [""])[0])
                player_id = self.server.resolve_agent_id(raw_player_id)
                return self._write(200, self.server.service_call(
                    "player_view",
                    player_id,
                    authorize_role="player",
                    authorize_token=self._token(),
                    authorize_player_id=player_id,
                ))
            if path == "/trade/offers":
                query = parse_qs(urlparse(self.path).query)
                agent_id = str(query.get("agent_id", [""])[0])
                include_closed = str(query.get("include_closed", ["0"])[0]).lower() in {"1", "true", "yes"}
                return self._write(200, self.server.trade_offers(agent_id, include_closed=include_closed, token=self._token()))
            if path == "/snapshots":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.snapshot_list())
            if path == "/snapshots/download":
                if not self._require("operator"):
                    return None
                query = parse_qs(urlparse(self.path).query)
                result = self.server.snapshot_download(str(query.get("snapshot_id", [""])[0]))
                if isinstance(result, dict):
                    return self._write(404 if result.get("failed_check") == "snapshot" else 400, result)
                filename, payload = result
                return self._write_bytes(200, payload, "application/octet-stream", filename=filename)
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
            idem_token = self._token()
            idem_key = "" if path == "/session" else self.server._idem_key_value(body, self.headers)
            if idem_key:
                stored = self.server.idem_lookup(idem_token, idem_key)
                if stored is not None:
                    return self._write(stored["status"], stored["value"])

                captured: dict[str, Any] = {}
                _original_write = self._write

                def _capturing_write(status: int, value: dict[str, Any]):  # type: ignore[no-redef]
                    if "done" not in captured:
                        captured["done"] = True
                        self.server.idem_store(idem_token, idem_key, status, value)
                    return _original_write(status, value)

                self._write = _capturing_write  # type: ignore[method-assign]
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
            if path == "/authoring/validate":
                return self._write(200, self.server.service_call("authoring_validate", body))
            if path == "/authoring/describe":
                return self._write(200, self.server.service_call("authoring_describe", body))
            if path == "/authoring/build-world":
                return self._write(200, self.server.service_call("authoring_build_world", body))
            if path == "/instances":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.create_instance(body))
            if path == "/instances/init":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.init_instance(body))
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
            if path == "/decide":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.decide(body))
            if path == "/simulate":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.simulate(body))
            if path == "/snapshots":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.snapshot_create(body))
            if path == "/restore":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.snapshot_restore(body))
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
                player_id = self.server.resolve_agent_id(str(body.get("player_id") or ""))
                body = {**body, "player_id": player_id}
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
                raw_speaker_id = str(body.get("speaker_id") or "")
                speaker_id = self.server.resolve_agent_id(raw_speaker_id) or raw_speaker_id
                listener_id = self.server.resolve_agent_id(str(body.get("listener_id") or "")) or str(body.get("listener_id") or "")
                role = "operator" if raw_speaker_id.strip().casefold() == "god" else "player"
                return self._write(200, self.server.service_call(
                    "dialogue",
                    speaker_id,
                    listener_id,
                    str(body.get("topic", "general")),
                    str(body["message"]),
                    authorize_role=role,
                    authorize_token=self._token(),
                    authorize_player_id=speaker_id,
                ))
            if path == "/trade/offers":
                offerer_id = self.server.resolve_agent_id(str(body.get("offerer_id") or "")) or str(body.get("offerer_id") or "")
                recipient_raw = str(body.get("recipient_id") or "")
                recipient_id = self.server.resolve_agent_id(recipient_raw) or recipient_raw
                return self._write(200, self.server.service_call(
                    "trade_offer",
                    offerer_id,
                    recipient_id,
                    dict(body.get("give", {})),
                    dict(body.get("want", {})),
                    authorize_role="player",
                    authorize_token=self._token(),
                    authorize_player_id=offerer_id,
                ))
            if path == "/trade/accept":
                recipient_id = self.server.resolve_agent_id(str(body.get("recipient_id") or "")) or str(body.get("recipient_id") or "")
                return self._write(200, self.server.service_call(
                    "trade_accept",
                    recipient_id,
                    str(body["offer_id"]),
                    authorize_role="player",
                    authorize_token=self._token(),
                    authorize_player_id=recipient_id,
                ))
            if path == "/trade/counter":
                counterparty_raw = str(body.get("counterparty_id") or body.get("recipient_id") or "")
                counterparty_id = self.server.resolve_agent_id(counterparty_raw) or counterparty_raw
                return self._write(200, self.server.service_call(
                    "trade_counter",
                    counterparty_id,
                    str(body["offer_id"]),
                    dict(body.get("give", {})),
                    dict(body.get("want", {})),
                    authorize_role="player",
                    authorize_token=self._token(),
                    authorize_player_id=counterparty_id,
                ))
            if path == "/trade/reject":
                recipient_id = self.server.resolve_agent_id(str(body.get("recipient_id") or "")) or str(body.get("recipient_id") or "")
                return self._write(200, self.server.service_call(
                    "trade_reject",
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
        self._idem_lock = RLock()
        self._idem_store: dict[tuple[str, str], dict[str, Any]] = {}
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

    def _resolve_agent_id_locked(self, value: str | None) -> str:
        raw = str(value or "").strip()
        if not raw:
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

    def player_wait(self, player_id: str, *, authorize_token: str | None = None) -> dict[str, Any]:
        with self._control_lock:
            resolved = self._resolve_agent_id_locked(player_id)
            allowed, message = self.authorize_session(
                str(authorize_token or ""),
                "player",
                player_id=resolved,
            )
            if not allowed:
                raise PermissionError(message)
            if self.runtime.running:
                return {
                    "status": "rejected",
                    "failed_check": "runtime_running",
                    "message": "The autonomous world is already advancing; pause or stop it before using manual wait.",
                }
            return self.control.player_wait(resolved)

    def trade_offers(self, agent_id: str, *, include_closed: bool = False, token: str | None = None) -> dict[str, Any]:
        with self._control_lock:
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
            if self.runtime.running:
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

    def init_instance(self, body: dict[str, Any]) -> dict[str, Any]:
        with self._control_lock:
            package_id = str(body.get("package_id") or body.get("world") or "").strip()
            instance_id = str(body.get("instance_id") or "").strip()
            if not package_id or not instance_id:
                return {"status": "rejected", "message": "package_id and instance_id are required", "failed_check": "instance"}
            result = self.control.authoring_create({"package_id": package_id, "instance_id": instance_id})
            if result.get("status") == "passed":
                result["instances"] = self.registry.list(active_db=self.database_path)
                if body.get("activate"):
                    activation = self.activate_instance({"instance_id": instance_id})
                    result["activation"] = activation
            elif result.get("status") == "rejected" and "unknown world package" in " ".join(result.get("errors") or []):
                # CLI `init --world` fallback for bundled worlds not under the authoring root.
                try:
                    from worlds import world_path

                    config_dir = world_path(package_id)
                except Exception as exc:
                    return {"status": "rejected", "message": f"unknown world package: {package_id}: {exc}", "failed_check": "package_id"}
                try:
                    from ..bootstrap.service import create_instance as bootstrap_create_instance

                    db_path = bootstrap_create_instance(config_dir, instance_id, self.runtime_root)
                except (FileExistsError, ValueError, OSError) as exc:
                    return {"status": "rejected", "message": str(exc), "failed_check": "instance"}
                result = {"status": "passed", "package_id": package_id, "instance_id": instance_id, "db_path": str(db_path)}
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
