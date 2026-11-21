"""Pin v1 routing: auth matrix, validation, idempotency and error mapping."""

from __future__ import annotations

import pytest

from sim2.api.errors import NoActiveWorldError
from sim2.api.v1.context import ApiError, JsonResponse, RawResponse, StreamResponse
from sim2.api.v1.routes import ROUTES, route_table
from sim2.api.v1.router import V1Router


OPERATOR_SESSION = "op"
OBSERVER_SESSION = "obs"
PLAYER_SESSION = "mira-session"


class FakeTasks:
    def __init__(self):
        self.submitted = []
        self.cancelled = []
        self.task = {
            "task_id": "task_1",
            "player_id": "mira_id",
            "state": "queued",
            "requested_minutes": 2,
            "completed_minutes": 0,
            "world_minute": 7,
            "decisions": 0,
            "llm_errors": 0,
            "stop_reason": "",
            "result": {},
            "error": "",
        }

    def submit(self, player_id, *, minutes, provider, instance_id):
        self.submitted.append((player_id, minutes, provider, instance_id))
        return {**self.task, "player_id": player_id, "requested_minutes": minutes}

    def get(self, player_id, task_id):
        return {**self.task, "player_id": player_id} if task_id == "task_1" else None

    def cancel(self, player_id, task_id):
        return {**self.task, "player_id": player_id, "state": "cancelling"} if task_id == "task_1" else None


class FakeRegistry:
    def list(self, active_db=None):
        return [{"instance_id": "inst-1", "active": True}]


class FakeServer:
    operator_token = "operator-secret"
    instance_id = "inst-1"
    has_world = True

    def __init__(self):
        self.calls = []
        self.sessions = {
            OPERATOR_SESSION: {"role": "operator"},
            OBSERVER_SESSION: {"role": "observer"},
            PLAYER_SESSION: {"role": "player", "player_id": "mira_id", "instance_id": "inst-1"},
        }
        self.issued = 0
        self.idem = {}
        self.tasks = FakeTasks()
        self.registry = FakeRegistry()
        self.fail_with = None
        self.runtime = {"state": "paused", "running": False, "world_minute": 7, "world_revision": 3}
        self.player = {
            "status": "ok",
            "revision": 3,
            "last_event_id": 9,
            "server_minute": 7,
            "world_id": "world",
            "world_time": {},
            "player": {"id": "mira_id", "name": "Mira"},
            "inventory": {},
            "item_labels": {},
            "needs": {},
            "capabilities": [],
            "goals": [],
            "chapter": None,
            "onboarding": None,
            "active_task": {},
            "actions": [],
            "unavailable_actions": [],
            "location": {},
            "world_properties": {},
            "world_property_labels": {},
            "facilities": [],
            "ui": {},
            "alert_thresholds": {},
            "routes": [],
            "map": {},
            "recent_outcomes": [],
            "nearby_entities": [],
            "nearby_participants": [],
            "recent_dialogue": [],
        }

    # ---- session surface ----------------------------------------------------
    def issue_session(self, role, *, player_id=None):
        self.issued += 1
        return f"issued-{self.issued}"

    def session_for(self, token):
        return self.sessions.get(str(token or ""))

    def session_context(self, token):
        if str(token or "") == self.operator_token:
            return {"role": "operator"}
        return dict(self.sessions.get(str(token or "")) or {})

    def authorize_session(self, token, role, *, player_id=None):
        self.calls.append(("authorize", token, role, player_id))
        if token == self.operator_token:
            return True, ""
        session = self.sessions.get(str(token or ""))
        if session is None:
            return False, "a local session with the required role is required"
        if session.get("role") == "operator":
            return True, ""
        if role == "observer":
            return True, ""
        if role == "player" and session.get("role") == "player" and player_id in (None, session.get("player_id")):
            if session.get("instance_id") == self.instance_id:
                return True, ""
            return False, "the player session belongs to a different world instance"
        return False, f"the session does not permit {role} access"

    def resolve_agent_id(self, value):
        return {"Mira": "mira_id", "Luo Wen": "luo_id"}.get(str(value or "").strip(), str(value or "").strip())

    # ---- canned application surface ----------------------------------------
    def status_payload(self):
        return {"status": "paused", "world_minute": 7}

    def world_catalog(self):
        return {"status": "ok", "worlds": [], "bundled": [], "authored": []}

    def instance_list(self):
        return {"status": "ok", "active_db": "/db.sqlite3", "instance_id": "inst-1", "instances": [{"instance_id": "inst-1", "active": True}]}

    def runtime_status(self):
        return dict(self.runtime)

    def player_state(self, player_id):
        return {"exists": player_id == "mira_id", "active": True, "world_minute": 7, "world_revision": 3}

    def enter_player(self, body):
        return {"status": "accepted", "player_id": body["player_id"], "session_token": "player-token", "resumed": False}

    def trade_offers(self, agent_id, *, include_closed=False, token=None):
        return {"status": "ok", "offers": [{"offer_id": "o1", "include_closed": include_closed}]}

    def resolve_dialogue_reply(self, player_id, *, provider_name=None, token=None):
        return {"status": "accepted"}

    def snapshot_list(self):
        return {"status": "ok", "snapshots": [], "instance_id": "inst-1"}

    def snapshot_create(self, body):
        return {"status": "ok", "snapshot_id": "snap1", "snapshots": []}

    def snapshot_restore(self, body):
        return {"status": "ok", "restored": body.get("snapshot_id")}

    def snapshot_download(self, snapshot_id):
        return ("snap.sqlite3", b"SQLite format 3\x00")

    def decide(self, body):
        return {"response": "", "result": {"status": "accepted"}}

    def simulate(self, body):
        return {"status": "ok", "report": {}, "metrics": {}}

    def create_instance(self, body):
        return {"status": "passed", "instance_id": body.get("instance_id")}

    def init_instance(self, body):
        return {"status": "passed", "instance_id": body.get("instance_id")}

    def activate_instance(self, body):
        return {"status": "accepted", "instance": {"instance_id": body.get("instance_id")}}

    def start_runtime(self, body):
        return {"status": "accepted", "state": "running", "running": True, "run_id": "r1"}

    def pause_runtime(self, reason="web"):
        return {"status": "accepted", "state": "pausing", "running": True}

    def resume_runtime(self, reason="web"):
        return {"status": "accepted", "state": "running", "running": True}

    def stop_runtime(self, reason="web-stop"):
        return {"status": "accepted", "state": "stopped", "running": False}

    def step_runtime(self):
        return {"status": "accepted", "to_minute": 8, "runtime": {"state": "paused", "running": False}}

    def service_call(self, method, *args, **kwargs):
        self.calls.append(("service", method, args, kwargs))
        if self.fail_with is not None:
            error, self.fail_with = self.fail_with, None
            raise error
        if method == "player_view":
            return dict(self.player)
        if method == "player_act":
            return {"status": "accepted", "action": args[0].get("behavior_ref")}
        if method in {"world_info"}:
            return {
                "status": "ok",
                "world_id": "world",
                "instance_id": "inst-1",
                "title": "World",
                "premise": "",
                "genre": "",
                "locale": "zh-CN",
                "season": "",
                "calendar": {},
                "world_clock": {},
                "world_minute": 7,
                "ui": {},
            }
        if method == "player_entry":
            return {
                "status": "ok",
                "world_id": "world",
                "entry_location": {"id": "quay", "name": "Quay"},
                "origins": [{"id": "steward"}],
                "players": [],
                "can_create_player": True,
            }
        if method in {"observer", "timeline", "observer_dialogue", "observer_decisions", "event_related"}:
            return {"status": "ok", "events": [], "exchanges": [], "decisions": [], "event": {}, "related": []}
        if method == "map":
            return {"status": "ok", "nodes": []}
        if method == "health":
            return {"status": "ok"}
        if method == "metrics":
            return {"status": "ok", "metrics": {}}
        if method == "audit":
            return {"status": "ok", "audit": {}}
        if method == "evolution_tasks":
            return []
        if method == "dialogue":
            return {"status": "accepted", "dialogue_id": 1}
        if method == "trade_offer":
            return {"status": "accepted", "offer_id": "o2"}
        if method in {"trade_accept", "trade_counter", "trade_reject"}:
            return {"status": "accepted"}
        if method.startswith("authoring_"):
            return {"status": "passed", "method": method}
        return {"status": "ok", "method": method}

    def idem_lookup(self, token, key):
        found = self.idem.get((token, key))
        return {"status": found[0], "value": dict(found[1])} if found else None

    def idem_store(self, token, key, status, value):
        self.idem[(token, key)] = (status, dict(value))


def _router(server=None):
    return V1Router(server or FakeServer())


def _call(router, method, path, *, token="", body=None, query="", headers_idem=""):
    return router.dispatch(
        method,
        path,
        query_string=query,
        body=dict(body or {}),
        token=token,
        idempotency_key=headers_idem,
        request_id="req_test",
    )


def test_route_table_is_unique_and_fully_declared():
    seen = set()
    for route in ROUTES:
        key = (route.method, route.template)
        assert key not in seen, key
        seen.add(key)
        assert route.role in {"public", "observer", "player", "operator", "launcher"}
        assert callable(route.handler)
    table = route_table()
    assert len(table) == len(ROUTES)
    assert any(item["path"] == "/api/v1/player/{player_id}/view" for item in table)


def test_session_exchange_and_operator_token():
    router = _router()
    response = _call(router, "POST", "/api/v1/session", body={"role": "observer"})
    assert isinstance(response, JsonResponse)
    assert response.status == 200
    assert response.payload["role"] == "observer"
    assert response.payload["session_token"].startswith("issued-")

    with pytest.raises(ApiError) as denied:
        _call(router, "POST", "/api/v1/session", body={"role": "operator", "operator_token": "wrong"})
    assert denied.value.status == 403

    accepted = _call(router, "POST", "/api/v1/session", body={"role": "operator", "operator_token": "operator-secret"})
    assert accepted.payload["role"] == "operator"


def test_legacy_paths_are_gone():
    with pytest.raises(ApiError) as missing:
        _call(_router(), "GET", "/player/view", query="player_id=mira_id")
    assert missing.value.status == 404
    assert missing.value.code == "not_found"


def test_player_routes_require_matching_player_session():
    router = _router()
    with pytest.raises(ApiError) as anonymous:
        _call(router, "GET", "/api/v1/player/mira_id/view")
    assert anonymous.value.status == 403

    with pytest.raises(ApiError) as wrong:
        _call(router, "GET", "/api/v1/player/luo_id/view")
    assert wrong.value.status == 403

    response = _call(router, "GET", "/api/v1/player/mira_id/view", token=PLAYER_SESSION)
    assert response.status == 200
    assert response.payload["revision"] == 3


def test_player_name_resolves_to_canonical_id_before_auth():
    response = _call(_router(), "GET", "/api/v1/player/Mira/view", token=PLAYER_SESSION)
    assert response.status == 200
    assert response.payload["player"]["id"] == "mira_id"


def test_player_action_uses_path_identity_and_validation():
    server = FakeServer()
    router = V1Router(server)
    response = _call(
        router,
        "POST",
        "/api/v1/player/mira_id/actions",
        token=PLAYER_SESSION,
        body={"behavior_ref": "behavior:travel", "parameters": {"target": "quay"}, "confidence": 1},
    )
    assert response.payload["status"] == "accepted"
    called = next(item for item in server.calls if item[0] == "service" and item[1] == "player_act")
    assert called[2][0]["player_id"] == "mira_id"

    with pytest.raises(ApiError) as invalid:
        _call(
            router,
            "POST",
            "/api/v1/player/mira_id/actions",
            token=PLAYER_SESSION,
            body={"confidence": "not-a-number"},
        )
    assert invalid.value.status == 422


def test_wait_returns_202_and_cancel_is_player_bound():
    server = FakeServer()
    router = V1Router(server)
    response = _call(router, "POST", "/api/v1/player/mira_id/wait", token=PLAYER_SESSION, body={"minutes": 2})
    assert response.status == 202
    assert response.payload["task"]["task_id"] == "task_1"
    assert server.tasks.submitted == [("mira_id", 2, None, "inst-1")]

    with pytest.raises(ApiError) as missing_player:
        _call(router, "POST", "/api/v1/player/luo_id/wait", token=PLAYER_SESSION, body={"minutes": 1})
    assert missing_player.value.status == 403

    with pytest.raises(ApiError) as invalid:
        _call(router, "POST", "/api/v1/player/mira_id/wait", token=PLAYER_SESSION, body={"minutes": "soon"})
    assert invalid.value.status == 422

    cancelled = _call(router, "POST", "/api/v1/player/mira_id/tasks/task_1/cancel", token=PLAYER_SESSION, body={})
    assert cancelled.payload["task"]["state"] == "cancelling"


def test_wait_reports_runtime_conflict_before_player_lookup():
    server = FakeServer()
    server.runtime = {"state": "running", "running": True, "world_minute": 9, "world_revision": 4}
    router = V1Router(server)
    with pytest.raises(ApiError) as conflict:
        _call(router, "POST", "/api/v1/player/mira_id/wait", token=PLAYER_SESSION, body={"minutes": 1})
    assert conflict.value.status == 409
    assert conflict.value.failed_check == "runtime_running"


def test_observer_routes_require_a_session_and_validate_query():
    router = _router()
    with pytest.raises(ApiError) as anonymous:
        _call(router, "GET", "/api/v1/observer/timeline")
    assert anonymous.value.status == 403

    with pytest.raises(ApiError) as bad:
        _call(router, "GET", "/api/v1/observer/timeline", token=OBSERVER_SESSION, query="limit=abc")
    assert bad.value.status == 400

    response = _call(router, "GET", "/api/v1/observer/timeline", token=OBSERVER_SESSION, query="limit=5")
    assert response.payload["status"] == "ok"


def test_operator_routes_reject_observer_and_allow_operator():
    router = _router()
    with pytest.raises(ApiError) as denied:
        _call(router, "POST", "/api/v1/runtime/start", token=OBSERVER_SESSION, body={"max_world_minutes": 1})
    assert denied.value.status == 403

    response = _call(router, "POST", "/api/v1/runtime/start", token=OPERATOR_SESSION, body={"max_world_minutes": 1})
    assert response.payload["run_id"] == "r1"


def test_launcher_init_is_public_only_before_a_world_exists():
    server = FakeServer()
    server.has_world = False
    router = V1Router(server)
    response = _call(router, "POST", "/api/v1/instances/init", body={"package_id": "starter", "instance_id": "one"})
    assert response.payload["status"] == "passed"

    server.has_world = True
    with pytest.raises(ApiError) as denied:
        _call(router, "POST", "/api/v1/instances/init", body={"package_id": "starter", "instance_id": "two"})
    assert denied.value.status == 403

    accepted = _call(
        router,
        "POST",
        "/api/v1/instances/init",
        token=OPERATOR_SESSION,
        body={"package_id": "starter", "instance_id": "two"},
    )
    assert accepted.payload["status"] == "passed"


def test_idempotency_replays_the_first_response():
    server = FakeServer()
    router = V1Router(server)
    body = {"event_type": "story.note", "payload": {"n": 1}}
    first = _call(router, "POST", "/api/v1/operator/events", token=OPERATOR_SESSION, body=body, headers_idem="k1")
    calls_after_first = len([item for item in server.calls if item[1] == "inject"])
    second = _call(router, "POST", "/api/v1/operator/events", token=OPERATOR_SESSION, body={**body, "payload": {"n": 2}}, headers_idem="k1")
    assert first.payload == second.payload
    assert len([item for item in server.calls if item[1] == "inject"]) == calls_after_first


def test_error_mapping_covers_permission_world_and_input_errors():
    server = FakeServer()
    router = V1Router(server)
    server.fail_with = NoActiveWorldError("no world")
    with pytest.raises(ApiError) as no_world:
        _call(router, "GET", "/api/v1/health")
    assert no_world.value.status == 409

    server.fail_with = ValueError("bad value")
    with pytest.raises(ApiError) as bad:
        _call(router, "GET", "/api/v1/health")
    assert bad.value.status == 400

    server.fail_with = PermissionError("not yours")
    with pytest.raises(ApiError) as forbidden:
        _call(router, "GET", "/api/v1/health")
    assert forbidden.value.status == 403


def test_response_contract_violation_is_reported_without_leaking_detail():
    server = FakeServer()
    server.runtime = {"running": False}
    router = V1Router(server)
    with pytest.raises(ApiError) as violation:
        _call(router, "GET", "/api/v1/runtime")
    assert violation.value.status == 500
    assert violation.value.code == "contract_violation"
    assert "state" not in violation.value.message


def test_stream_and_snapshot_download_use_raw_response_types():
    server = FakeServer()
    router = V1Router(server)
    stream = _call(router, "GET", "/api/v1/stream", token=PLAYER_SESSION, query="after=0")
    assert isinstance(stream, StreamResponse)
    assert stream.role == "player" and stream.player_id == "mira_id"

    download = _call(router, "GET", "/api/v1/operator/snapshots/snap1/download", token=OPERATOR_SESSION)
    assert isinstance(download, RawResponse)
    assert download.content_type == "application/octet-stream"
    assert download.filename == "snap.sqlite3"


def test_contract_schema_endpoint_lists_requests_and_responses():
    response = _call(_router(), "GET", "/api/v1/meta/contracts")
    assert response.payload["version"] == "v1"
    assert "player_view" in response.payload["responses"]
    assert "wait" in response.payload["requests"]


@pytest.mark.parametrize("route", ROUTES, ids=lambda item: item.name)
def test_every_declared_route_dispatches_through_the_middleware(route):
    """Every row in the v1 table must reach its handler with its declared role.

    The generic fake answers the application methods with contract-shaped
    payloads, so a missing import, a wrong response type or a forgotten auth
    branch fails on the exact route name.
    """

    server = FakeServer()
    router = V1Router(server)
    params = {
        "{player_id}": "mira_id",
        "{event_id}": "9",
        "{offer_id}": "o1",
        "{task_id}": "task_1",
        "{snapshot_id}": "snap1",
        "{instance_id}": "inst-1",
    }
    path = route.template
    for placeholder, value in params.items():
        path = path.replace(placeholder, value)
    token = {
        "public": "",
        "observer": OBSERVER_SESSION,
        "player": PLAYER_SESSION,
        "operator": OPERATOR_SESSION,
        "launcher": "",
    }[route.role]
    if route.role == "launcher":
        server.has_world = False
    bodies = {
        "session": {"role": "observer"},
        "enter_player": {"player_id": "hero"},
        "trade_offer": {"recipient_id": "luo_id"},
        "dialogue": {"listener_id": "luo_id", "message": "hello"},
        "operator_dialogue": {"speaker_id": "god", "listener_id": "mira_id", "message": "hello"},
        "inject": {"event_type": "story.note"},
        "decide": {"agent_id": "mira_id"},
        "snapshot_restore": {"snapshot_id": "snap1"},
    }
    body = bodies.get(route.request_model or "", {})
    result = _call(router, route.method, path, token=token, body=body)
    assert isinstance(result, (JsonResponse, RawResponse, StreamResponse)), route.name
