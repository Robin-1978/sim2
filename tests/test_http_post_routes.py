"""Pin the POST routes of the control API: idempotent replay, operator gating, player actions and error mapping."""

from __future__ import annotations

import io
import json
from http.client import HTTPMessage

import pytest

from sim2.api.http import _Handler

OPERATOR_SESSION = "op"
OBSERVER_SESSION = "obs"
PLAYER_SESSION = "mira-session"
FORBIDDEN = "a local session with the required role is required"


class FakeServer:
    """Stands in for ControlHTTPServer: it records every call a handler makes and answers with the call itself."""

    operator_token = "operator-secret"

    def __init__(self):
        self.calls = []
        self.stored = {}
        self.sessions = {OPERATOR_SESSION: ("operator", None), OBSERVER_SESSION: ("observer", None), PLAYER_SESSION: ("player", "mira_id")}
        self.fail_with = None
        self.issued = 0

    def _answer(self, kind, name, args, kwargs):
        self.calls.append((kind, name, args, kwargs))
        if self.fail_with is not None:
            error, self.fail_with = self.fail_with, None
            raise error
        return {"status": "ok", "kind": kind, "name": name}

    def authorize_session(self, token, role, *, player_id=None):
        self.calls.append(("authorize", token, role, player_id))
        if token == self.operator_token:
            return True, ""
        session_role, owner = self.sessions.get(token, (None, None))
        if session_role == "operator" or (session_role == "observer" and role == "observer"):
            return True, ""
        if session_role == "player" and role == "player" and player_id in (None, owner):
            return True, ""
        return False, FORBIDDEN

    def service_call(self, method, *args, authorize_role=None, authorize_token=None, authorize_player_id=None, **kwargs):
        if authorize_role is not None:
            allowed, message = self.authorize_session(authorize_token, authorize_role, player_id=authorize_player_id)
            if not allowed:
                raise PermissionError(message)
        return self._answer("service", method, args, kwargs)

    def resolve_agent_id(self, value):
        raw = str(value or "").strip()
        return {"Mira": "mira_id", "Luo Wen": "luo_id"}.get(raw, raw)

    def issue_session(self, role, *, player_id=None):
        self.issued += 1
        self.calls.append(("issue_session", role, player_id))
        return f"issued-{self.issued}"

    def session_for(self, token):
        return None

    def idem_lookup(self, token, key):
        return self.stored.get((token, key))

    def idem_store(self, token, key, status, value):
        self.calls.append(("idem_store", token, key, status))
        self.stored[(token, key)] = {"status": status, "value": dict(value)}

    @staticmethod
    def _idem_key_value(body, headers):
        return str(headers.get("Idempotency-Key") or body.get("idempotency_key") or "").strip()

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args, **kwargs: self._answer("server", name, args, kwargs)


class RecordedRequest(_Handler):
    """A handler that reads from memory and records the response instead of writing to a socket."""

    def __init__(self, server, path, headers, payload):
        self.server = server
        self.command = "POST"
        self.path = path
        self.headers = headers
        self.rfile = io.BytesIO(payload)
        self.wfile = io.BytesIO()
        self.status = None

    def send_response(self, code, message=None):
        self.status = code

    def send_header(self, keyword, value):
        pass

    def end_headers(self):
        pass


def post(server, path, body=None, *, token="", headers=None, raw=None):
    payload = raw if raw is not None else json.dumps(body if body is not None else {}).encode("utf-8")
    message = HTTPMessage()
    message["content-length"] = str(len(payload))
    if token:
        message["x-sim2-session"] = token
    for key, value in (headers or {}).items():
        message[key] = value
    request = RecordedRequest(server, path, message, payload)
    request.do_POST()
    return request.status, json.loads(request.wfile.getvalue())


def made(server):
    """The calls a request made, without the authorization checks."""

    return [call for call in server.calls if call[0] != "authorize"]


@pytest.fixture
def server():
    return FakeServer()


OPERATOR_CALLS = [
    ("/authoring/open", {"a": 1}, ("service", "authoring_open", ({"a": 1},), {})),
    ("/authoring/create", {"a": 1}, ("server", "create_instance", ({"a": 1},), {})),
    ("/authoring/build", {"a": 1}, ("service", "authoring_build", ({"a": 1},), {})),
    ("/authoring/compile", {"a": 1}, ("service", "authoring_compile", ({"a": 1},), {})),
    ("/authoring/smoke", {"a": 1}, ("service", "authoring_smoke", ({"a": 1},), {})),
    ("/authoring/export", {"a": 1}, ("service", "authoring_export", ({"a": 1},), {})),
    ("/authoring/validate", {"a": 1}, ("service", "authoring_validate", ({"a": 1},), {})),
    ("/authoring/describe", {"a": 1}, ("service", "authoring_describe", ({"a": 1},), {})),
    ("/authoring/build-world", {"a": 1}, ("service", "authoring_build_world", ({"a": 1},), {})),
    ("/instances", {"a": 1}, ("server", "create_instance", ({"a": 1},), {})),
    ("/instances/init", {"a": 1}, ("server", "init_instance", ({"a": 1},), {})),
    ("/instances/activate", {"a": 1}, ("server", "activate_instance", ({"a": 1},), {})),
    ("/runtime/start", {"a": 1}, ("server", "start_runtime", ({"a": 1},), {})),
    ("/runtime/pause", {}, ("server", "pause_runtime", ("web",), {})),
    ("/runtime/resume", {}, ("server", "resume_runtime", ("web",), {})),
    ("/runtime/stop", {}, ("server", "stop_runtime", ("web-stop",), {})),
    ("/runtime/step", {"a": 1}, ("server", "step_runtime", (), {})),
    ("/decide", {"a": 1}, ("server", "decide", ({"a": 1},), {})),
    ("/simulate", {"a": 1}, ("server", "simulate", ({"a": 1},), {})),
    ("/snapshots", {"a": 1}, ("server", "snapshot_create", ({"a": 1},), {})),
    ("/restore", {"a": 1}, ("server", "snapshot_restore", ({"a": 1},), {})),
    ("/pause", {}, ("server", "pause_runtime", ("http",), {})),
    ("/resume", {}, ("server", "resume_runtime", ("http",), {})),
    ("/step", {"a": 1}, ("server", "step_runtime", (), {})),
    ("/events", {"event_type": "flood", "payload": {"depth": 3}, "actor_id": "mira_id"}, ("service", "inject", ("flood", {"depth": 3}, "mira_id"), {})),
]


@pytest.mark.parametrize(("path", "body", "call"), OPERATOR_CALLS, ids=[item[0] for item in OPERATOR_CALLS])
def test_an_operator_session_reaches_the_server_call_behind_each_route(server, path, body, call):
    status, result = post(server, path, body, token=OPERATOR_SESSION)
    assert status == 200
    assert result["status"] == "ok"
    assert server.calls == [("authorize", OPERATOR_SESSION, "operator", None), call]


@pytest.mark.parametrize("path", [item[0] for item in OPERATOR_CALLS] + ["/authoring/unknown", "/authoring/"])
@pytest.mark.parametrize("token", ["", OBSERVER_SESSION, PLAYER_SESSION, "stranger"])
def test_operator_routes_refuse_every_other_session_before_doing_anything(server, path, token):
    status, result = post(server, path, {"event_type": "flood"}, token=token)
    assert status == 403
    assert result == {"status": "forbidden", "message": FORBIDDEN, "failed_check": "session"}
    assert server.calls == [("authorize", token, "operator", None)]


def test_the_operator_token_is_accepted_directly_or_as_a_bearer_credential(server):
    assert post(server, "/step", token=server.operator_token)[0] == 200
    assert post(server, "/step", headers={"authorization": f"Bearer {server.operator_token}"})[0] == 200
    assert [call[1] for call in server.calls if call[0] == "authorize"] == [server.operator_token, server.operator_token]


def test_an_unknown_authoring_route_is_not_found_for_an_operator(server):
    assert post(server, "/authoring/unknown", token=OPERATOR_SESSION) == (404, {"status": "not_found"})
    assert made(server) == []


@pytest.mark.parametrize("path", ["/", "/unknown", "/trade", "/session/", "/authoring", "/instances/"])
def test_an_unknown_route_is_not_found_without_any_authorization(server, path):
    assert post(server, path, token=OPERATOR_SESSION) == (404, {"status": "not_found"})
    assert server.calls == []


def test_a_query_string_does_not_change_the_route(server):
    status, _ = post(server, "/step?after=3", token=OPERATOR_SESSION)
    assert status == 200
    assert made(server) == [("server", "step_runtime", (), {})]


def test_runtime_and_http_pause_use_their_own_default_reasons_and_accept_a_given_one(server):
    for path in ("/runtime/pause", "/pause"):
        post(server, path, {"reason": "maintenance"}, token=OPERATOR_SESSION)
    post(server, "/runtime/stop", {"reason": 5}, token=OPERATOR_SESSION)
    assert made(server) == [
        ("server", "pause_runtime", ("maintenance",), {}),
        ("server", "pause_runtime", ("maintenance",), {}),
        ("server", "stop_runtime", ("5",), {}),
    ]


def test_an_injected_event_needs_a_type_and_defaults_its_payload_and_actor(server):
    status, result = post(server, "/events", {}, token=OPERATOR_SESSION)
    assert (status, result["status"]) == (400, "bad_request")
    assert made(server) == []
    post(server, "/events", {"event_type": "flood"}, token=OPERATOR_SESSION)
    assert made(server) == [("service", "inject", ("flood", {}, None), {})]


# --- sessions ---


def test_a_session_defaults_to_an_observer_without_any_credential(server):
    assert post(server, "/session", {}) == (200, {"status": "ok", "role": "observer", "session_token": "issued-1"})
    assert post(server, "/session", {"role": "observer"})[0] == 200
    assert post(server, "/session", {"role": ""})[1]["role"] == "observer"
    assert server.calls == [("issue_session", "observer", None)] * 3


def test_an_operator_session_needs_the_operator_token(server):
    status, result = post(server, "/session", {"role": "operator", "operator_token": server.operator_token})
    assert (status, result["role"]) == (200, "operator")
    assert server.calls == [("issue_session", "operator", None)]
    server.calls.clear()
    status, result = post(server, "/session", {"role": "operator", "operator_token": "wrong"})
    assert (status, result["message"]) == (403, "operator token is invalid")
    assert post(server, "/session", {"role": "operator"})[0] == 403
    assert server.calls == []


@pytest.mark.parametrize("role", ["player", "god", "Observer"])
def test_only_observer_and_operator_sessions_are_created_without_a_player(server, role):
    status, result = post(server, "/session", {"role": role})
    assert (status, result["message"]) == (403, "only observer sessions may be created without a player")
    assert server.calls == []


# --- players ---


def test_entering_a_player_needs_no_session(server):
    status, result = post(server, "/players", {"player_id": "mira_id", "origin_id": "village"})
    assert (status, result["name"]) == (200, "enter_player")
    assert made(server) == [("server", "enter_player", ({"player_id": "mira_id", "origin_id": "village"},), {})]


def test_a_player_action_is_authorized_for_the_resolved_player(server):
    status, _ = post(server, "/player/act", {"player_id": "Mira", "message": "go"}, token=PLAYER_SESSION)
    assert status == 200
    assert server.calls == [
        ("authorize", PLAYER_SESSION, "player", "mira_id"),
        ("service", "player_act", ({"player_id": "mira_id", "message": "go"},), {}),
    ]
    server.calls.clear()
    status, result = post(server, "/player/act", {"player_id": "Luo Wen"}, token=PLAYER_SESSION)
    assert (status, result["message"]) == (403, FORBIDDEN)
    assert server.calls == [("authorize", PLAYER_SESSION, "player", "luo_id")]


def test_a_player_wait_forwards_its_minutes_provider_and_credential(server):
    post(server, "/player/wait", {"player_id": "Mira", "minutes": "5", "provider": "remote"}, token=PLAYER_SESSION)
    post(server, "/player/wait", {"player_id": "Mira", "minutes": 0})
    assert made(server) == [
        ("server", "player_wait", ("Mira",), {"minutes": 5, "provider_name": "remote", "authorize_token": PLAYER_SESSION}),
        ("server", "player_wait", ("Mira",), {"minutes": 1, "provider_name": None, "authorize_token": ""}),
    ]
    assert post(server, "/player/wait", {"player_id": "Mira", "minutes": "soon"})[0] == 400


@pytest.mark.parametrize(
    ("speaker", "role", "speaker_id"),
    [("Mira", "player", "mira_id"), ("mira_id", "player", "mira_id"), ("God", "operator", "God"), (" god ", "operator", "god"), ("", "player", "")],
)
def test_a_dialogue_is_authorized_as_the_speaker_or_as_god_for_an_operator(server, speaker, role, speaker_id):
    token = OPERATOR_SESSION if role == "operator" else PLAYER_SESSION
    post(server, "/dialogue", {"speaker_id": speaker, "listener_id": "Luo Wen", "message": "hello"}, token=token)
    assert server.calls[0] == ("authorize", token, role, speaker_id)
    if speaker_id:
        assert made(server) == [("service", "dialogue", (speaker_id, "luo_id", "general", "hello"), {})]


def test_a_dialogue_needs_a_message_and_a_matching_session(server):
    assert post(server, "/dialogue", {"speaker_id": "Mira", "listener_id": "Luo Wen"}, token=PLAYER_SESSION)[0] == 400
    status, result = post(server, "/dialogue", {"speaker_id": "God", "listener_id": "Mira", "message": "x"}, token=PLAYER_SESSION)
    assert (status, result["message"]) == (403, FORBIDDEN)
    assert made(server) == []


def test_a_dialogue_topic_defaults_to_general(server):
    post(server, "/dialogue", {"speaker_id": "Mira", "listener_id": "Luo Wen", "topic": "grain", "message": "x"}, token=PLAYER_SESSION)
    assert made(server)[0][2][2] == "grain"


def test_a_trade_offer_resolves_both_parties_and_defaults_what_is_given_and_wanted(server):
    status, _ = post(server, "/trade/offers", {"offerer_id": "Mira", "recipient_id": "Luo Wen", "give": {"grain": 1}}, token=PLAYER_SESSION)
    assert status == 200
    assert server.calls == [
        ("authorize", PLAYER_SESSION, "player", "mira_id"),
        ("service", "trade_offer", ("mira_id", "luo_id", {"grain": 1}, {}), {}),
    ]
    assert post(server, "/trade/offers", {"offerer_id": "Luo Wen", "recipient_id": "Mira"}, token=PLAYER_SESSION)[0] == 403
    assert post(server, "/trade/offers", {"offerer_id": "Mira", "give": "grain"}, token=PLAYER_SESSION)[0] == 400


@pytest.mark.parametrize(("path", "method"), [("/trade/accept", "trade_accept"), ("/trade/reject", "trade_reject")])
def test_accepting_or_rejecting_an_offer_is_authorized_for_the_recipient(server, path, method):
    status, _ = post(server, path, {"recipient_id": "Mira", "offer_id": 7}, token=PLAYER_SESSION)
    assert status == 200
    assert server.calls == [("authorize", PLAYER_SESSION, "player", "mira_id"), ("service", method, ("mira_id", "7"), {})]
    assert post(server, path, {"recipient_id": "Mira"}, token=PLAYER_SESSION)[0] == 400
    assert post(server, path, {"recipient_id": "Luo Wen", "offer_id": "1"}, token=PLAYER_SESSION)[0] == 403


def test_a_counter_offer_falls_back_to_the_recipient_when_there_is_no_counterparty(server):
    body = {"recipient_id": "Mira", "offer_id": "o1", "give": {"salt": 1}, "want": {"grain": 2}}
    assert post(server, "/trade/counter", body, token=PLAYER_SESSION)[0] == 200
    assert server.calls == [
        ("authorize", PLAYER_SESSION, "player", "mira_id"),
        ("service", "trade_counter", ("mira_id", "o1", {"salt": 1}, {"grain": 2}), {}),
    ]
    server.calls.clear()
    status, result = post(server, "/trade/counter", {**body, "counterparty_id": "Luo Wen"}, token=PLAYER_SESSION)
    assert (status, result["message"]) == (403, FORBIDDEN)
    assert server.calls == [("authorize", PLAYER_SESSION, "player", "luo_id")]
    assert post(server, "/trade/counter", {"recipient_id": "Mira"}, token=PLAYER_SESSION)[0] == 400


# --- idempotent replay ---


def test_a_repeated_request_gets_the_stored_response_without_reaching_the_server_again(server):
    first = post(server, "/step", {"idempotency_key": "k1"}, token=OPERATOR_SESSION)
    calls = list(server.calls)
    second = post(server, "/step", {"idempotency_key": "k1"}, token=OPERATOR_SESSION)
    assert second == first
    assert server.calls == calls
    assert calls[-1] == ("idem_store", OPERATOR_SESSION, "k1", 200)


def test_the_key_may_come_from_the_header_and_is_scoped_to_the_session(server):
    post(server, "/step", token=OPERATOR_SESSION, headers={"Idempotency-Key": "h1"})
    stored_calls = len(server.calls)
    post(server, "/step", token=server.operator_token, headers={"Idempotency-Key": "h1"})
    assert len(server.calls) > stored_calls
    assert set(server.stored) == {(OPERATOR_SESSION, "h1"), (server.operator_token, "h1")}


def test_a_rejected_request_is_stored_and_replayed_too(server):
    first = post(server, "/step", {"idempotency_key": "k1"}, token=OBSERVER_SESSION)
    assert first[0] == 403
    assert server.stored[(OBSERVER_SESSION, "k1")]["status"] == 403
    server.calls.clear()
    assert post(server, "/step", {"idempotency_key": "k1"}, token=OBSERVER_SESSION) == first
    assert server.calls == []


def test_only_the_first_response_of_a_request_is_remembered(server):
    server.fail_with = RuntimeError("boom")
    status, _ = post(server, "/step", {"idempotency_key": "k1"}, token=OPERATOR_SESSION)
    assert status == 500
    assert [call for call in server.calls if call[0] == "idem_store"] == [("idem_store", OPERATOR_SESSION, "k1", 500)]


def test_a_session_request_is_never_replayed(server):
    first = post(server, "/session", {"idempotency_key": "k1"})
    second = post(server, "/session", {"idempotency_key": "k1"})
    assert first[1]["session_token"] != second[1]["session_token"]
    assert server.stored == {}


def test_no_key_means_nothing_is_stored(server):
    post(server, "/step", token=OPERATOR_SESSION)
    assert server.stored == {}


# --- error mapping ---


@pytest.mark.parametrize(
    ("error", "status", "label"),
    [
        (KeyError("offer_id"), 400, "bad_request"),
        (TypeError("bad type"), 400, "bad_request"),
        (ValueError("bad value"), 400, "bad_request"),
        (json.JSONDecodeError("bad json", "{", 1), 400, "bad_request"),
        (RuntimeError("kaput"), 500, "error"),
    ],
)
def test_server_errors_become_bad_requests_or_internal_errors(server, error, status, label):
    server.fail_with = error
    result = post(server, "/step", token=OPERATOR_SESSION)
    assert result == (status, {"status": label, "message": str(error)})


def test_a_permission_error_from_the_server_is_forbidden(server):
    server.fail_with = PermissionError("not yours")
    assert post(server, "/step", token=OPERATOR_SESSION) == (403, {"status": "forbidden", "message": "not yours", "failed_check": "session"})


@pytest.mark.parametrize(
    ("raw", "message"),
    [(b"{not json", "Expecting property name enclosed in double quotes"), (b"[1, 2]", "request body must be a JSON object"), (b"7", "request body must be a JSON object")],
)
def test_a_body_that_is_not_a_json_object_is_a_bad_request(server, raw, message):
    status, result = post(server, "/step", token=OPERATOR_SESSION, raw=raw)
    assert status == 400
    assert result["status"] == "bad_request"
    assert result["message"].startswith(message)
    assert server.calls == []


def test_an_empty_body_is_an_empty_object(server):
    assert post(server, "/step", token=OPERATOR_SESSION, raw=b"")[0] == 200


def test_a_bad_content_length_is_a_bad_request(server):
    message = HTTPMessage()
    message["content-length"] = "many"
    request = RecordedRequest(server, "/step", message, b"{}")
    request.do_POST()
    assert request.status == 400
    assert json.loads(request.wfile.getvalue())["status"] == "bad_request"
