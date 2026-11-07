from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .service import ControlService


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

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        try:
            if path == "/status":
                return self._write(200, self.server.control.status())
            if path == "/metrics":
                return self._write(200, self.server.control.metrics())
            if path == "/audit":
                return self._write(200, self.server.control.audit())
            if path == "/evolution/tasks":
                return self._write(200, {"tasks": self.server.control.platform.ready_evolution_tasks()})
            return self._write(404, {"status": "not_found"})
        except Exception as exc:
            return self._write(500, {"status": "error", "message": str(exc)})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        try:
            body = self._read_json()
            if path == "/pause":
                return self._write(200, self.server.control.pause(str(body.get("reason", "http"))))
            if path == "/resume":
                return self._write(200, self.server.control.resume(str(body.get("reason", "http"))))
            if path == "/step":
                return self._write(200, self.server.control.step())
            if path == "/advance":
                return self._write(200, self.server.control.advance(int(body.get("minutes", 1))))
            if path == "/events":
                return self._write(200, self.server.control.inject(str(body["event_type"]), dict(body.get("payload", {})), body.get("actor_id")))
            if path == "/players":
                return self._write(200, self.server.control.enter_player(str(body["player_id"]), str(body["origin_id"]), body.get("name")))
            return self._write(404, {"status": "not_found"})
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._write(400, {"status": "bad_request", "message": str(exc)})
        except Exception as exc:
            return self._write(500, {"status": "error", "message": str(exc)})


class ControlHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], db_path: Path):
        self.control = ControlService(db_path)
        super().__init__(address, _Handler)

    def close(self) -> None:
        self.shutdown()
        self.server_close()
        self.control.close()


def serve(db_path: Path, host: str = "127.0.0.1", port: int = 8080) -> ControlHTTPServer:
    server = ControlHTTPServer((host, port), db_path)
    server.serve_forever()
    return server
