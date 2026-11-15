from __future__ import annotations

import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

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
        token = self._token()
        if secrets.compare_digest(token, self.server.operator_token):
            return True
        session = self.server.session_for(token)
        if session is None:
            self._forbidden()
            return False
        if session.get("role") == "operator":
            return True
        if role == "observer":
            return True
        if role == "player" and session.get("role") == "player" and (player_id is None or str(session.get("player_id")) == str(player_id)):
            return True
        self._forbidden(f"the session does not permit {role} access")
        return False

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
                return self._write(200, self.server.control.status())
            if path == "/health":
                health = self.server.control.health()
                return self._write(200 if health.get("status") == "ok" else 503, health)
            if path == "/metrics":
                return self._write(200, self.server.control.metrics())
            if path == "/observer":
                query = parse_qs(urlparse(self.path).query)
                limit = int(query.get("limit", [20])[0])
                return self._write(200, self.server.control.observer(limit))
            if path == "/timeline":
                query = parse_qs(urlparse(self.path).query)
                limit = int(query.get("limit", [100])[0])
                after = int(query.get("after", [0])[0])
                ticks = query.get("ticks", ["0"])[0].lower() in {"1", "true", "yes"}
                return self._write(200, self.server.control.timeline(limit, after, ticks))
            if path == "/map":
                query = parse_qs(urlparse(self.path).query)
                viewer_id = query.get("viewer_id", [None])[0]
                observer = query.get("observer", ["0"])[0].lower() in {"1", "true", "yes"}
                return self._write(200, self.server.control.map(viewer_id=viewer_id, observer=observer))
            if path == "/player/entry":
                return self._write(200, self.server.control.player_entry())
            if path == "/player/view":
                query = parse_qs(urlparse(self.path).query)
                player_id = str(query.get("player_id", [""])[0])
                if not self._require("player", player_id=player_id):
                    return None
                return self._write(200, self.server.control.player_view(player_id))
            if path == "/audit":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.control.audit())
            if path == "/evolution/tasks":
                if not self._require("operator"):
                    return None
                return self._write(200, {"tasks": self.server.control.evolution_tasks()})
            if path == "/authoring/schema":
                return self._write(200, self.server.control.authoring_schema())
            if path == "/authoring/catalog":
                return self._write(200, self.server.control.authoring_catalog())
            return self._write(404, {"status": "not_found"})
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
            if path == "/authoring/open":
                return self._write(200, self.server.control.authoring_open(body))
            if path == "/authoring/create":
                return self._write(200, self.server.control.authoring_create(body))
            if path == "/authoring/build":
                return self._write(200, self.server.control.authoring_build(body))
            if path == "/authoring/compile":
                return self._write(200, self.server.control.authoring_compile(body))
            if path == "/authoring/smoke":
                return self._write(200, self.server.control.authoring_smoke(body))
            if path == "/authoring/export":
                return self._write(200, self.server.control.authoring_export(body))
            if path == "/pause":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.control.pause(str(body.get("reason", "http"))))
            if path == "/resume":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.control.resume(str(body.get("reason", "http"))))
            if path == "/step":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.control.step())
            if path == "/events":
                if not self._require("operator"):
                    return None
                return self._write(200, self.server.control.inject(str(body["event_type"]), dict(body.get("payload", {})), body.get("actor_id")))
            if path == "/players":
                result = self.server.control.enter_player(str(body["player_id"]), str(body["origin_id"]), body.get("name"))
                if result.get("status") == "accepted":
                    result = {**result, "session_token": self.server.issue_session("player", player_id=str(body["player_id"]))}
                return self._write(200, result)
            if path == "/player/act":
                if not self._require("player", player_id=str(body.get("player_id") or "")):
                    return None
                return self._write(200, self.server.control.player_act(body))
            if path == "/player/wait":
                if not self._require("player", player_id=str(body.get("player_id") or "")):
                    return None
                return self._write(200, self.server.control.player_wait(str(body.get("player_id") or "")))
            if path == "/dialogue":
                speaker_id = str(body.get("speaker_id") or "")
                if speaker_id != "god" and not self._require("player", player_id=speaker_id):
                    return None
                return self._write(200, self.server.control.dialogue(str(body["speaker_id"]), str(body["listener_id"]), str(body.get("topic", "general")), str(body["message"])))
            if path == "/trade/offers":
                if not self._require("player", player_id=str(body.get("offerer_id") or "")):
                    return None
                return self._write(200, self.server.control.trade_offer(str(body["offerer_id"]), str(body["recipient_id"]), dict(body.get("give", {})), dict(body.get("want", {}))))
            if path == "/trade/accept":
                if not self._require("player", player_id=str(body.get("recipient_id") or "")):
                    return None
                return self._write(200, self.server.control.trade_accept(str(body["recipient_id"]), str(body["offer_id"])))
            return self._write(404, {"status": "not_found"})
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._write(400, {"status": "bad_request", "message": str(exc)})
        except Exception as exc:
            return self._write(500, {"status": "error", "message": str(exc)})


class ControlHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], db_path: Path, *, authoring_root: Path | None = None, operator_token: str | None = None):
        self.control = ControlService(db_path, authoring_root=authoring_root)
        self.operator_token = str(operator_token or os.environ.get("SIM2_OPERATOR_TOKEN") or secrets.token_urlsafe(32))
        self._sessions: dict[str, dict[str, str]] = {}
        super().__init__(address, _Handler)

    def issue_session(self, role: str, *, player_id: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        self._sessions[token] = {"role": str(role), **({"player_id": str(player_id)} if player_id else {})}
        return token

    def session_for(self, token: str) -> dict[str, str] | None:
        return self._sessions.get(str(token or ""))

    def close(self) -> None:
        self.shutdown()
        self.server_close()
        self.control.close()


def serve(db_path: Path, host: str = "127.0.0.1", port: int = 8080) -> ControlHTTPServer:
    server = ControlHTTPServer((host, port), db_path)
    server.serve_forever()
    return server
