"""The sim2 command line: each command, what it passes on, prints and refuses.

``main`` is the console entry point (``sim2 = "sim2.api.cli:main"``).  These
tests run it through ``sys.argv`` with fake collaborators that record what a
command asks of them, so they pin the arguments, the output text and the
failures without a database, a model or a server.
"""

from __future__ import annotations

import json
import logging
import sys
import types
from pathlib import Path

import pytest

import sim2.world_builder as world_builder
from sim2.api import cli

COMMANDS = (
    "init", "validate-world", "describe-world", "world-schema", "build-world", "decide", "mud", "tui",
    "simulate", "serve", "play", "status", "health", "timeline", "map", "pause", "resume", "step", "metrics",
    "audit", "snapshot", "restore", "enter-player", "sync-player-template", "inject",
)
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class Env:
    """What the fakes answer with; every call they get is appended to ``log`` in order."""

    def __init__(self) -> None:
        self.log: list[tuple] = []
        self.platform_results: dict[str, object] = {"enter_player": {"status": "accepted"}}
        self.known_agents: set[str] = set()
        self.metadata: dict[str, object] = {}
        self.validation: dict[str, object] = {"valid": True}
        self.description: dict[str, object] = {"valid": True, "name": "世界"}
        self.schema: dict[str, object] = {"title": "Schema é"}
        self.build_error: Exception | None = None
        self.engine_error: Exception | None = None
        self.serve_error: BaseException | None = None


@pytest.fixture
def env(monkeypatch):
    env = Env()
    log = env.log

    class FakePlatform:
        def __init__(self, db):
            self.db = db
            log.append(("Platform", db))
            self.store = types.SimpleNamespace(
                repositories=types.SimpleNamespace(
                    entities=types.SimpleNamespace(get_agent=self._get_agent),
                    metadata=types.SimpleNamespace(get=lambda key, default=None: env.metadata.get(key, default)),
                )
            )

        def _get_agent(self, agent_id):
            if agent_id not in env.known_agents:
                raise KeyError(agent_id)
            return {"id": agent_id}

        def close(self):
            log.append(("platform.close",))

        def __getattr__(self, name):
            def call(*args, **kwargs):
                log.append((f"platform.{name}", args, kwargs))
                result = env.platform_results.get(name)
                return result(*args, **kwargs) if callable(result) else result

            return call

    class FakeRouter:
        @classmethod
        def from_default_config(cls):
            log.append(("from_default_config",))
            return cls()

        def provider_for_role(self, role):
            log.append(("provider_for_role", role))
            return f"provider:{role}"

        def new_provider_for_name(self, name, **kwargs):
            log.append(("new_provider_for_name", name, kwargs))
            return f"provider:{name}"

    class FakeEngine:
        def __init__(self, platform, provider):
            log.append(("AgentEngine", type(platform).__name__, provider))

        def run(self, *, role, actor_id):
            log.append(("engine.run", role, actor_id))
            if env.engine_error is not None:
                raise env.engine_error
            return types.SimpleNamespace(response={"say": "é"}, result={"status": "accepted"}, iterations=3)

    class FakeSession:
        def __init__(self, platform, **kwargs):
            log.append(("TextWorldSession", type(platform).__name__, kwargs))

        def run(self):
            log.append(("session.run",))

    class FakeServer:
        def __init__(self, address, db_path, **kwargs):
            log.append(("ControlHTTPServer", address, db_path, kwargs))
            self.database_path = Path(db_path) if db_path is not None else None
            self.has_world = db_path is not None
            self.operator_token = kwargs.get("operator_token") or "generated-token"

        def serve_forever(self):
            log.append(("serve_forever",))
            if env.serve_error is not None:
                raise env.serve_error

        def close_after_serve_forever(self):
            log.append(("close_after_serve_forever",))

    class FakeRegistry:
        def __init__(self, runtime_root):
            log.append(("InstanceRegistry", runtime_root))

        def resolve(self, *, instance_id=None, db_path=None):
            log.append(("registry.resolve", instance_id))
            if instance_id == "missing":
                raise FileNotFoundError("no world instances found")
            return Path("/runtime/resolved.sqlite")

    class FakeBuilder:
        def __init__(self, provider):
            log.append(("WorldBuilderService", provider))

        def build(self, brief):
            log.append(("builder.build", brief))
            if env.build_error is not None:
                raise env.build_error
            return types.SimpleNamespace(
                template={"name": "drafted"}, iterations=4, usage={"prompt_tokens": 10}, transcript=[{"step": "é"}]
            )

    def create_instance(config, instance_id, runtime_root=None):
        log.append(("create_instance", config, instance_id, runtime_root))
        return Path("/runtime/db.sqlite")

    def validate_config(config):
        log.append(("validate_config", config))
        return env.validation

    def describe_config(config):
        log.append(("describe_config", config))
        return env.description

    def write_package(raw, output, *, overwrite=False):
        log.append(("write_world_package", raw, output, overwrite))
        return Path(output)

    def run_tui(db, **kwargs):
        log.append(("run_tui", db, kwargs))

    def reply_handler(platform, player_id, provider_name=None):
        log.append(("reply_handler", type(platform).__name__, player_id, provider_name))
        return "handler"

    monkeypatch.setattr(cli, "Platform", FakePlatform)
    monkeypatch.setattr(cli, "LLMRouter", FakeRouter)
    monkeypatch.setattr(cli, "AgentEngine", FakeEngine)
    monkeypatch.setattr(cli, "TextWorldSession", FakeSession)
    monkeypatch.setattr(cli, "ControlHTTPServer", FakeServer)
    monkeypatch.setattr(cli, "InstanceRegistry", FakeRegistry)
    monkeypatch.setattr(cli, "create_instance", create_instance)
    monkeypatch.setattr(cli, "validate_config", validate_config)
    monkeypatch.setattr(cli, "describe_config", describe_config)
    monkeypatch.setattr(cli, "world_template_schema", lambda: env.schema)
    monkeypatch.setattr(cli, "write_world_package_from_template", write_package)
    monkeypatch.setattr(cli, "render_topology_map", lambda view: f"MAP:{view}")
    monkeypatch.setattr(cli, "world_path", lambda name: Path("/worlds") / name)
    monkeypatch.setattr(cli, "_dialogue_reply_handler", reply_handler)
    monkeypatch.setattr(world_builder, "WorldBuilderService", FakeBuilder)
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: log.append(("basicConfig", kwargs)))
    monkeypatch.setitem(sys.modules, "sim2.api.tui", _module("sim2.api.tui", run_tui=run_tui))
    monkeypatch.setattr(Path, "home", lambda: Path("/home/someone"))
    return env


def _module(name: str, **attributes):
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


class Outcome:
    """How one ``main()`` call ended.

    Reading ``exit`` or ``error`` counts as checking the ending. ``_every_ending_is_checked`` fails a test whose
    command ended in an exit or an exception that the test never looked at, so a command that fails after
    printing the right text cannot pass by accident.
    """

    def __init__(self, out: str, err: str, exit_value: object, error: Exception | None) -> None:
        self.out = out
        self.err = err
        self._exit_value = exit_value
        self._error = error
        self.checked = False

    @property
    def exit(self) -> object:
        self.checked = True
        return self._exit_value

    @property
    def error(self) -> Exception | None:
        self.checked = True
        return self._error

    @property
    def unchecked_ending(self) -> object:
        """The exception or exit value nobody looked at; ``None`` when the command simply returned."""

        if self.checked:
            return None
        return self._error if self._error is not None else self._exit_value


_OUTCOMES: list[Outcome] = []


@pytest.fixture(autouse=True)
def _every_ending_is_checked():
    _OUTCOMES.clear()
    yield
    unchecked = [repr(outcome.unchecked_ending) for outcome in _OUTCOMES if outcome.unchecked_ending is not None]
    _OUTCOMES.clear()
    assert not unchecked, f"commands ended in an exit or an exception the test never looked at: {unchecked}"


def run_cli(monkeypatch, capsys, *argv: str) -> Outcome:
    """Run ``main`` as the console script would; report output, exit and error."""

    monkeypatch.setattr(sys, "argv", ["sim2", *argv])
    exit_value = error = None
    try:
        cli.main()
    except SystemExit as stop:
        exit_value = stop.code
    except Exception as exc:  # the command's own failure is part of what is pinned
        error = exc
    captured = capsys.readouterr()
    outcome = Outcome(captured.out, captured.err, exit_value, error)
    _OUTCOMES.append(outcome)
    return outcome


def compact(value) -> str:
    return json.dumps(value, ensure_ascii=False) + "\n"


def indented(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


# ---------------------------------------------------------------- the parser


def test_the_parser_offers_exactly_these_commands_in_this_order(env, monkeypatch, capsys):
    result = run_cli(monkeypatch, capsys, "--help")
    assert result.exit == 0
    assert result.out.startswith("usage: sim2 [-h]")
    assert "{" + ",".join(COMMANDS) + "}" in result.out
    assert env.log == []


@pytest.mark.parametrize(
    "argv, fragment",
    [
        ((), "the following arguments are required: command"),
        (("nonsense",), "invalid choice"),
        (("init",), "--instance-id"),
        (("init", "--instance-id", "x", "--config", "/a", "--world", "b"), "not allowed with argument"),
        (("validate-world",), "--config"),
        (("describe-world",), "--config"),
        (("build-world", "--output", "/o"), "one of the arguments --template --brief --brief-file is required"),
        (("build-world", "--template", "/t", "--brief", "b", "--output", "/o"), "not allowed with argument"),
        (("build-world", "--brief", "b"), "--output"),
        (("decide", "--db", "/d"), "--agent-id"),
        (("decide", "--agent-id", "a"), "--db"),
        (("mud",), "--db"),
        (("tui",), "--db"),
        (("simulate", "--db", "/d"), "--minutes"),
        (("simulate", "--minutes", "5"), "--db"),
        (("serve", "--db", "/d", "--instance-id", "i"), "not allowed with argument"),
        (("serve", "--log-level", "loud"), "invalid choice"),
        (("snapshot", "--db", "/d"), "--destination"),
        (("restore", "--db", "/d"), "--source"),
        (("enter-player", "--db", "/d", "--player-id", "p"), "--origin"),
        (("enter-player", "--db", "/d", "--origin", "o"), "--player-id"),
        (("sync-player-template",), "--db"),
        (("inject", "--db", "/d"), "--event-type"),
        (("status",), "--db"),
        (("health",), "--db"),
        (("timeline",), "--db"),
        (("map",), "--db"),
        (("pause",), "--db"),
        (("resume",), "--db"),
        (("step",), "--db"),
        (("metrics",), "--db"),
        (("audit",), "--db"),
        (("simulate", "--db", "/d", "--minutes", "soon"), "invalid int value"),
        (("simulate", "--db", "/d", "--minutes", "5", "--wait-cooldown", "x"), "invalid int value"),
        (("simulate", "--db", "/d", "--minutes", "5", "--llm-error-cooldown", "x"), "invalid int value"),
        (("serve", "--port", "http"), "invalid int value"),
        (("timeline", "--db", "/d", "--limit", "x"), "invalid int value"),
        (("timeline", "--db", "/d", "--after", "x"), "invalid int value"),
        (("build-world", "--brief", "b", "--output", "/o", "--max-completion-tokens", "x"), "invalid int value"),
    ],
)
def test_a_malformed_command_line_is_refused_before_anything_runs(env, monkeypatch, capsys, argv, fragment):
    result = run_cli(monkeypatch, capsys, *argv)
    assert result.exit == 2
    assert fragment in result.err
    assert env.log == []


# ---------------------------------------------------------- world packages


def test_init_creates_an_instance_of_the_default_world(env, monkeypatch, capsys):
    result = run_cli(monkeypatch, capsys, "init", "--instance-id", "demo", "--runtime-root", "/runtime")
    assert env.log == [("create_instance", Path("/worlds/starter_village"), "demo", Path("/runtime"))]
    assert result.out == compact({"db_path": "/runtime/db.sqlite"})


def test_init_takes_a_bundled_world_id_or_a_package_directory(env, monkeypatch, capsys):
    run_cli(monkeypatch, capsys, "init", "--instance-id", "a", "--world", "aster_relay", "--runtime-root", "/r")
    run_cli(monkeypatch, capsys, "init", "--instance-id", "b", "--config", "/my/package", "--runtime-root", "/r")
    assert env.log == [
        ("create_instance", Path("/worlds/aster_relay"), "a", Path("/r")),
        ("create_instance", Path("/my/package"), "b", Path("/r")),
    ]


def test_init_keeps_instances_under_the_home_directory_by_default(env, monkeypatch, capsys):
    run_cli(monkeypatch, capsys, "init", "--instance-id", "demo")
    assert env.log == [("create_instance", Path("/worlds/starter_village"), "demo", Path("/home/someone/.sim2"))]


@pytest.mark.parametrize(
    "command, call, report_key",
    [("validate-world", "validate_config", "validation"), ("describe-world", "describe_config", "description")],
)
def test_validation_commands_print_the_report_and_exit_one_when_invalid(env, monkeypatch, capsys, command, call, report_key):
    report = {"valid": True, "names": ["世界"]}
    setattr(env, report_key, report)
    ok = run_cli(monkeypatch, capsys, command, "--config", "/pkg")
    assert ok.exit is None and ok.out == indented(report)

    report = {"valid": False, "errors": ["missing manifest"]}
    setattr(env, report_key, report)
    bad = run_cli(monkeypatch, capsys, command, "--config", "/pkg")
    assert bad.exit == 1 and bad.out == indented(report)
    assert env.log == [(call, Path("/pkg")), (call, Path("/pkg"))]


def test_world_schema_prints_the_template_schema_without_escaping_text(env, monkeypatch, capsys):
    result = run_cli(monkeypatch, capsys, "world-schema")
    assert result.out == indented(env.schema)
    assert "é" in result.out


def _template_file(tmp_path: Path, text: str = "name: Tiny\n") -> Path:
    path = tmp_path / "template.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_build_world_writes_a_package_from_a_template(env, monkeypatch, capsys, tmp_path):
    result = run_cli(monkeypatch, capsys, "build-world", "--template", str(_template_file(tmp_path)), "--output", "/out/pkg")
    assert env.log == [
        ("write_world_package", {"name": "Tiny"}, Path("/out/pkg"), False),
        ("describe_config", Path("/out/pkg")),
    ]
    assert result.out == indented({"status": "accepted", "package": "/out/pkg", "validation": env.description})


def test_build_world_can_overwrite_and_create_an_instance(env, monkeypatch, capsys, tmp_path):
    result = run_cli(
        monkeypatch, capsys, "build-world", "--template", str(_template_file(tmp_path)), "--output", "/out/pkg",
        "--overwrite", "--instance-id", "demo", "--runtime-root", "/rt",
    )
    assert env.log == [
        ("write_world_package", {"name": "Tiny"}, Path("/out/pkg"), True),
        ("describe_config", Path("/out/pkg")),
        ("create_instance", Path("/out/pkg"), "demo", Path("/rt")),
    ]
    assert result.out == indented(
        {"status": "accepted", "package": "/out/pkg", "validation": env.description, "db_path": "/runtime/db.sqlite"}
    )


def test_build_world_refuses_a_template_that_is_not_an_object(env, monkeypatch, capsys, tmp_path):
    result = run_cli(
        monkeypatch, capsys, "build-world", "--template", str(_template_file(tmp_path, "- a\n- b\n")), "--output", "/o"
    )
    assert isinstance(result.error, ValueError) and str(result.error) == "--template must contain a YAML object"
    assert env.log == []


def test_build_world_asks_the_builder_agent_for_a_brief(env, monkeypatch, capsys):
    result = run_cli(monkeypatch, capsys, "build-world", "--brief", "A moon base", "--output", "/out/pkg")
    assert env.log == [
        ("from_default_config",),
        ("new_provider_for_name", "remote_beam", {"max_completion_tokens": None, "role": "world_builder"}),
        ("WorldBuilderService", "provider:remote_beam"),
        ("builder.build", "A moon base"),
        ("write_world_package", {"name": "drafted"}, Path("/out/pkg"), False),
        ("describe_config", Path("/out/pkg")),
    ]
    assert result.out == indented({
        "status": "accepted",
        "package": "/out/pkg",
        "validation": env.description,
        "world_builder": {"iterations": 4, "usage": {"prompt_tokens": 10}},
    })


def test_build_world_passes_the_provider_and_token_budget_on(env, monkeypatch, capsys):
    run_cli(
        monkeypatch, capsys, "build-world", "--brief", "b", "--output", "/o", "--provider", "other", "--max-completion-tokens", "99"
    )
    assert ("new_provider_for_name", "other", {"max_completion_tokens": 99, "role": "world_builder"}) in env.log


def test_build_world_reads_the_brief_from_a_file(env, monkeypatch, capsys, tmp_path):
    brief = tmp_path / "brief.txt"
    brief.write_text("月球基地\n", encoding="utf-8")
    run_cli(monkeypatch, capsys, "build-world", "--brief-file", str(brief), "--output", "/o")
    assert ("builder.build", "月球基地\n") in env.log


def test_build_world_sends_an_empty_brief_on_instead_of_reading_a_file(env, monkeypatch, capsys):
    result = run_cli(monkeypatch, capsys, "build-world", "--brief", "", "--output", "/o")
    assert result.error is None
    assert ("builder.build", "") in env.log


def test_build_world_saves_the_builder_transcript_when_asked(env, monkeypatch, capsys, tmp_path):
    audit = tmp_path / "logs" / "nested" / "audit.json"
    result = run_cli(
        monkeypatch, capsys, "build-world", "--brief", "b", "--output", "/o", "--audit-output", str(audit)
    )
    expected = {"iterations": 4, "usage": {"prompt_tokens": 10}, "transcript": [{"step": "é"}]}
    assert audit.read_text(encoding="utf-8") == json.dumps(expected, ensure_ascii=False, indent=2)
    assert json.loads(result.out)["audit"] == str(audit)
    assert json.loads(result.out)["world_builder"] == {"iterations": 4, "usage": {"prompt_tokens": 10}}


def test_a_template_build_writes_no_transcript_even_when_asked(env, monkeypatch, capsys, tmp_path):
    audit = tmp_path / "audit.json"
    result = run_cli(
        monkeypatch, capsys, "build-world", "--template", str(_template_file(tmp_path)), "--output", "/o",
        "--audit-output", str(audit),
    )
    assert not audit.exists()
    assert "audit" not in json.loads(result.out) and "world_builder" not in json.loads(result.out)


def test_a_rejected_draft_is_saved_and_raised_again(env, monkeypatch, capsys, tmp_path):
    audit = tmp_path / "deep" / "er" / "audit.json"
    env.build_error = world_builder.WorldBuilderError("rejected draft", transcript=[{"step": "x"}], usage={"prompt_tokens": 7})
    result = run_cli(monkeypatch, capsys, "build-world", "--brief", "b", "--output", "/o", "--audit-output", str(audit))
    assert result.error is env.build_error
    expected = {"status": "rejected", "message": "rejected draft", "usage": {"prompt_tokens": 7}, "transcript": [{"step": "x"}]}
    assert audit.read_text(encoding="utf-8") == json.dumps(expected, ensure_ascii=False, indent=2)
    assert result.out == ""
    assert not any(entry[0] == "write_world_package" for entry in env.log)


def test_a_rejected_draft_without_an_audit_path_only_raises(env, monkeypatch, capsys, tmp_path):
    env.build_error = world_builder.WorldBuilderError("rejected draft", transcript=[], usage={})
    result = run_cli(monkeypatch, capsys, "build-world", "--brief", "b", "--output", str(tmp_path / "pkg"))
    assert result.error is env.build_error
    assert list(tmp_path.iterdir()) == []


def test_other_builder_failures_are_not_saved_as_rejections(env, monkeypatch, capsys, tmp_path):
    audit = tmp_path / "audit.json"
    env.build_error = RuntimeError("provider down")
    result = run_cli(monkeypatch, capsys, "build-world", "--brief", "b", "--output", "/o", "--audit-output", str(audit))
    assert isinstance(result.error, RuntimeError) and not audit.exists()


# --------------------------------------------------- agents and players


def test_decide_runs_one_turn_of_the_default_role(env, monkeypatch, capsys):
    result = run_cli(monkeypatch, capsys, "decide", "--db", "/db", "--agent-id", "a1")
    assert env.log == [
        ("Platform", Path("/db")),
        ("from_default_config",),
        ("provider_for_role", "agent_decision"),
        ("AgentEngine", "FakePlatform", "provider:agent_decision"),
        ("engine.run", "agent_decision", "a1"),
        ("platform.close",),
    ]
    assert result.out == compact({"response": {"say": "é"}, "result": {"status": "accepted"}, "iterations": 3})


def test_decide_takes_another_role(env, monkeypatch, capsys):
    run_cli(monkeypatch, capsys, "decide", "--db", "/db", "--agent-id", "a1", "--role", "god")
    assert ("provider_for_role", "god") in env.log and ("engine.run", "god", "a1") in env.log


def test_decide_closes_the_platform_when_the_turn_fails(env, monkeypatch, capsys):
    env.engine_error = RuntimeError("boom")
    result = run_cli(monkeypatch, capsys, "decide", "--db", "/db", "--agent-id", "a1")
    assert result.error is env.engine_error
    assert env.log[-1] == ("platform.close",)


def test_mud_observer_runs_a_session_without_a_player(env, monkeypatch, capsys):
    run_cli(monkeypatch, capsys, "mud", "--db", "/db", "--observer")
    assert env.log == [
        ("Platform", Path("/db")),
        ("TextWorldSession", "FakePlatform", {"player_id": None, "observer": True, "reply_handler": None}),
        ("session.run",),
        ("platform.close",),
    ]


def test_mud_player_session_replies_through_the_chosen_provider(env, monkeypatch, capsys):
    env.known_agents = {"p1"}
    run_cli(monkeypatch, capsys, "mud", "--db", "/db", "--player-id", "p1", "--provider", "remote")
    assert env.log == [
        ("Platform", Path("/db")),
        ("reply_handler", "FakePlatform", "p1", "remote"),
        ("TextWorldSession", "FakePlatform", {"player_id": "p1", "observer": False, "reply_handler": "handler"}),
        ("session.run",),
        ("platform.close",),
    ]


def test_mud_enters_a_new_player_first(env, monkeypatch, capsys):
    run_cli(monkeypatch, capsys, "mud", "--db", "/db", "--player-id", "p2", "--origin", "forager", "--name", "Pat")
    assert env.log[:2] == [("Platform", Path("/db")), ("platform.enter_player", ("p2", "forager"), {"name": "Pat"})]
    assert ("session.run",) in env.log


def test_mud_observer_ignores_the_player_arguments(env, monkeypatch, capsys):
    run_cli(monkeypatch, capsys, "mud", "--db", "/db", "--observer", "--player-id", "p9", "--origin", "o")
    assert env.log == [
        ("Platform", Path("/db")),
        ("TextWorldSession", "FakePlatform", {"player_id": "p9", "observer": True, "reply_handler": None}),
        ("session.run",),
        ("platform.close",),
    ]


def test_mud_player_mode_needs_a_player_id(env, monkeypatch, capsys):
    result = run_cli(monkeypatch, capsys, "mud", "--db", "/db")
    assert result.exit == "--player-id is required in player mode"
    assert env.log == [("Platform", Path("/db")), ("platform.close",)]


def test_mud_new_player_needs_an_origin(env, monkeypatch, capsys):
    result = run_cli(monkeypatch, capsys, "mud", "--db", "/db", "--player-id", "p2")
    assert result.exit == "--origin is required when creating a new player"
    assert env.log == [("Platform", Path("/db")), ("platform.close",)]


def test_mud_reports_a_refused_entry_with_the_allowed_origins(env, monkeypatch, capsys):
    env.platform_results["enter_player"] = {"status": "rejected", "message": "Unknown origin.", "allowed_values": ["a", "b"]}
    result = run_cli(monkeypatch, capsys, "mud", "--db", "/db", "--player-id", "p2", "--origin", "zzz")
    assert result.exit == "Cannot enter player: Unknown origin. Available origins: a, b."
    assert env.log[-1] == ("platform.close",)


def test_tui_opens_as_an_observer_by_default(env, monkeypatch, capsys):
    run_cli(monkeypatch, capsys, "tui", "--db", "/db")
    assert env.log == [
        ("Platform", Path("/db")),
        ("platform.close",),
        ("run_tui", Path("/db"), {"observer": True, "player_id": None, "run_world": False, "provider_name": None}),
    ]


def test_tui_enters_the_player_and_closes_before_opening(env, monkeypatch, capsys):
    run_cli(
        monkeypatch, capsys, "tui", "--db", "/db", "--player-id", "p1", "--origin", "forager", "--name", "Pat",
        "--run", "--provider", "remote",
    )
    assert env.log == [
        ("Platform", Path("/db")),
        ("platform.enter_player", ("p1", "forager"), {"name": "Pat"}),
        ("platform.close",),
        ("run_tui", Path("/db"), {"observer": False, "player_id": "p1", "run_world": True, "provider_name": "remote"}),
    ]


def test_tui_observer_flag_wins_over_a_player_id(env, monkeypatch, capsys):
    run_cli(monkeypatch, capsys, "tui", "--db", "/db", "--observer", "--player-id", "p1")
    assert env.log[-1] == (
        "run_tui", Path("/db"), {"observer": True, "player_id": "p1", "run_world": False, "provider_name": None}
    )
    assert not any(entry[0] == "platform.enter_player" for entry in env.log)


def test_tui_does_not_open_when_the_player_cannot_enter(env, monkeypatch, capsys):
    result = run_cli(monkeypatch, capsys, "tui", "--db", "/db", "--player-id", "p1")
    assert result.exit == "--origin is required when creating a new player"
    assert env.log == [("Platform", Path("/db")), ("platform.close",)]


def test_simulate_runs_the_loop_and_prints_report_and_metrics(env, monkeypatch, capsys):
    env.platform_results["run"] = types.SimpleNamespace(as_dict=lambda: {"decisions": 2, "note": "é"})
    env.platform_results["metrics"] = {"llm_calls": 5}
    result = run_cli(monkeypatch, capsys, "simulate", "--db", "/db", "--minutes", "5")
    assert env.log == [
        ("Platform", Path("/db")),
        ("platform.run", (5,), {"provider_name": None, "wait_cooldown_minutes": 30, "llm_error_cooldown_minutes": 60}),
        ("platform.metrics", (), {}),
        ("platform.close",),
    ]
    assert result.out == compact({"report": {"decisions": 2, "note": "é"}, "metrics": {"llm_calls": 5}})


def test_simulate_passes_the_cooldowns_and_provider_on(env, monkeypatch, capsys):
    env.platform_results["run"] = types.SimpleNamespace(as_dict=lambda: {})
    run_cli(
        monkeypatch, capsys, "simulate", "--db", "/db", "--minutes", "9", "--wait-cooldown", "7",
        "--llm-error-cooldown", "11", "--provider", "remote",
    )
    assert ("platform.run", (9,), {"provider_name": "remote", "wait_cooldown_minutes": 7, "llm_error_cooldown_minutes": 11}) in env.log


def test_simulate_closes_the_platform_when_the_run_fails(env, monkeypatch, capsys):
    def failing_run(*args, **kwargs):
        raise RuntimeError("provider down")

    env.platform_results["run"] = failing_run
    result = run_cli(monkeypatch, capsys, "simulate", "--db", "/db", "--minutes", "5")
    assert isinstance(result.error, RuntimeError)
    assert env.log[-1] == ("platform.close",)


# ---------------------------------------------------------------- serving


def test_serve_listens_on_the_defaults_and_closes_afterwards(env, monkeypatch, capsys):
    result = run_cli(monkeypatch, capsys, "serve", "--db", "/db")
    assert env.log == [
        ("basicConfig", {"level": logging.WARNING, "format": LOG_FORMAT, "datefmt": LOG_DATE_FORMAT}),
        ("ControlHTTPServer", ("127.0.0.1", 8080), Path("/db"), {"authoring_root": None, "operator_token": None}),
        ("serve_forever",),
        ("close_after_serve_forever",),
    ]
    assert result.out == compact({
        "status": "listening", "url": "http://127.0.0.1:8080", "db": "/db", "launcher": False, "operator_token": "generated-token",
    })


def test_serve_passes_every_option_on(env, monkeypatch, capsys):
    result = run_cli(
        monkeypatch, capsys, "serve", "--db", "/db", "--host", "0.0.0.0", "--port", "9000", "--authoring-root", "/authoring",
        "--operator-token", "tok", "--log-level", "debug",
    )
    assert env.log[0] == ("basicConfig", {"level": logging.DEBUG, "format": LOG_FORMAT, "datefmt": LOG_DATE_FORMAT})
    assert env.log[1] == (
        "ControlHTTPServer", ("0.0.0.0", 9000), Path("/db"), {"authoring_root": Path("/authoring"), "operator_token": "tok"}
    )
    assert json.loads(result.out) == {"status": "listening", "url": "http://0.0.0.0:9000", "db": "/db", "launcher": False, "operator_token": "tok"}


@pytest.mark.parametrize("name, level", [("info", logging.INFO), ("warning", logging.WARNING), ("error", logging.ERROR)])
def test_serve_maps_the_log_level_name(env, monkeypatch, capsys, name, level):
    run_cli(monkeypatch, capsys, "serve", "--db", "/db", "--log-level", name)
    assert env.log[0] == ("basicConfig", {"level": level, "format": LOG_FORMAT, "datefmt": LOG_DATE_FORMAT})


def test_serve_resolves_the_database_from_the_instance_registry(env, monkeypatch, capsys):
    result = run_cli(monkeypatch, capsys, "serve", "--instance-id", "inst", "--runtime-root", "/rt")
    assert env.log[:2] == [("InstanceRegistry", Path("/rt")), ("registry.resolve", "inst")]
    assert env.log[3][2] == Path("/runtime/resolved.sqlite")
    assert json.loads(result.out)["db"] == "/runtime/resolved.sqlite"


def test_serve_without_any_instances_starts_the_launcher(env, monkeypatch, capsys):
    result = run_cli(monkeypatch, capsys, "serve", "--instance-id", "missing")
    assert env.log[1] == ("registry.resolve", "missing")
    server = next(entry for entry in env.log if entry[0] == "ControlHTTPServer")
    assert server[2] is None
    assert json.loads(result.out)["launcher"] is True
    assert json.loads(result.out)["db"] is None


def test_serve_without_a_database_uses_the_default_runtime_root(env, monkeypatch, capsys):
    run_cli(monkeypatch, capsys, "serve")
    assert env.log[:2] == [("InstanceRegistry", Path("/home/someone/.sim2")), ("registry.resolve", None)]


def test_serve_does_not_ask_the_registry_when_given_a_database(env, monkeypatch, capsys):
    run_cli(monkeypatch, capsys, "serve", "--db", "/db")
    assert not any(entry[0] == "InstanceRegistry" for entry in env.log)


def test_serve_ends_quietly_on_ctrl_c(env, monkeypatch, capsys):
    env.serve_error = KeyboardInterrupt()
    result = run_cli(monkeypatch, capsys, "serve", "--db", "/db")
    assert result.error is None and result.exit is None
    assert env.log[-2:] == [("serve_forever",), ("close_after_serve_forever",)]


def test_serve_closes_the_server_when_it_fails(env, monkeypatch, capsys):
    env.serve_error = RuntimeError("port busy")
    result = run_cli(monkeypatch, capsys, "serve", "--db", "/db")
    assert isinstance(result.error, RuntimeError)
    assert env.log[-1] == ("close_after_serve_forever",)


# --------------------------------------------------- instance operations


@pytest.mark.parametrize(
    "command, method",
    [
        ("status", "status"), ("health", "health"), ("pause", "pause"), ("resume", "resume"),
        ("step", "step"), ("metrics", "metrics"), ("audit", "export_audit"),
    ],
)
def test_instance_commands_print_what_the_platform_answers(env, monkeypatch, capsys, command, method):
    answer = {"status": "ok", "世界": "é"}
    env.platform_results[method] = answer
    result = run_cli(monkeypatch, capsys, command, "--db", "/db")
    assert env.log == [("Platform", Path("/db")), (f"platform.{method}", (), {}), ("platform.close",)]
    assert result.out == compact(answer)


def test_timeline_defaults(env, monkeypatch, capsys):
    env.platform_results["timeline"] = {"events": []}
    result = run_cli(monkeypatch, capsys, "timeline", "--db", "/db")
    assert env.log[1] == ("platform.timeline", (), {"limit": 100, "after_event_id": 0, "include_ticks": False})
    assert result.out == compact({"events": []})


def test_timeline_options(env, monkeypatch, capsys):
    run_cli(monkeypatch, capsys, "timeline", "--db", "/db", "--limit", "5", "--after", "7", "--ticks")
    assert env.log[1] == ("platform.timeline", (), {"limit": 5, "after_event_id": 7, "include_ticks": True})


def test_map_prints_the_rendered_map_not_json(env, monkeypatch, capsys):
    env.platform_results["map_view"] = "the-view"
    result = run_cli(monkeypatch, capsys, "map", "--db", "/db")
    assert env.log == [("Platform", Path("/db")), ("platform.map_view", (), {"observer": True}), ("platform.close",)]
    assert result.out == "MAP:the-view\n"


def test_snapshot_prints_the_file_that_was_written(env, monkeypatch, capsys):
    env.platform_results["snapshot"] = Path("/snaps/world.sqlite")
    result = run_cli(monkeypatch, capsys, "snapshot", "--db", "/db", "--destination", "/snaps")
    assert env.log[1] == ("platform.snapshot", (Path("/snaps"),), {})
    assert result.out == compact({"snapshot": "/snaps/world.sqlite"})


def test_restore_prints_the_platform_answer(env, monkeypatch, capsys):
    env.platform_results["restore_snapshot"] = {"status": "restored"}
    result = run_cli(monkeypatch, capsys, "restore", "--db", "/db", "--source", "/snaps/world.sqlite")
    assert env.log[1] == ("platform.restore_snapshot", (Path("/snaps/world.sqlite"),), {})
    assert result.out == compact({"status": "restored"})


def test_enter_player_prints_the_platform_answer_even_when_it_refuses(env, monkeypatch, capsys):
    env.platform_results["enter_player"] = {"status": "rejected", "message": "no"}
    result = run_cli(monkeypatch, capsys, "enter-player", "--db", "/db", "--player-id", "p", "--origin", "o", "--name", "Pat")
    assert env.log[1] == ("platform.enter_player", ("p", "o"), {"name": "Pat"})
    assert result.out == compact({"status": "rejected", "message": "no"}) and result.exit is None


def test_enter_player_name_is_optional(env, monkeypatch, capsys):
    run_cli(monkeypatch, capsys, "enter-player", "--db", "/db", "--player-id", "p", "--origin", "o")
    assert env.log[1] == ("platform.enter_player", ("p", "o"), {"name": None})


def test_inject_appends_an_event(env, monkeypatch, capsys):
    env.platform_results["inject_event"] = {"status": "accepted", "event_id": 4}
    result = run_cli(
        monkeypatch, capsys, "inject", "--db", "/db", "--event-type", "storm", "--payload", '{"force": 3}', "--actor-id", "god"
    )
    assert env.log[1] == ("platform.inject_event", ("storm", {"force": 3}, "god"), {})
    assert result.out == compact({"status": "accepted", "event_id": 4})


def test_inject_defaults_to_an_empty_payload_and_no_actor(env, monkeypatch, capsys):
    run_cli(monkeypatch, capsys, "inject", "--db", "/db", "--event-type", "storm")
    assert env.log[1] == ("platform.inject_event", ("storm", {}, None), {})


@pytest.mark.parametrize("payload, error", [("[1]", ValueError), ('"x"', ValueError), ("{oops", json.JSONDecodeError)])
def test_inject_refuses_a_payload_that_is_not_a_json_object(env, monkeypatch, capsys, payload, error):
    result = run_cli(monkeypatch, capsys, "inject", "--db", "/db", "--event-type", "storm", "--payload", payload)
    assert isinstance(result.error, error)
    if error is ValueError:
        assert str(result.error) == "--payload must be a JSON object"
    assert env.log == [("Platform", Path("/db")), ("platform.close",)]


def _package_with_template(tmp_path: Path, text: str | None = "name: Tiny\nentry: x\n") -> Path:
    package = tmp_path / "package"
    package.mkdir()
    if text is not None:
        (package / "player_template.yaml").write_text(text, encoding="utf-8")
    return package


def test_sync_player_template_loads_the_template_of_the_given_package(env, monkeypatch, capsys, tmp_path):
    env.platform_results["sync_player_template"] = {"status": "synced"}
    package = _package_with_template(tmp_path)
    result = run_cli(monkeypatch, capsys, "sync-player-template", "--db", "/db", "--config", str(package))
    assert env.log[1] == ("platform.sync_player_template", ({"name": "Tiny", "entry": "x"},), {})
    assert result.out == compact({"status": "synced"})


def test_sync_player_template_treats_an_empty_template_file_as_empty(env, monkeypatch, capsys, tmp_path):
    package = _package_with_template(tmp_path, "")
    run_cli(monkeypatch, capsys, "sync-player-template", "--db", "/db", "--config", str(package))
    assert env.log[1] == ("platform.sync_player_template", ({},), {})


def test_sync_player_template_finds_the_package_the_instance_was_made_from(env, monkeypatch, capsys, tmp_path):
    package = _package_with_template(tmp_path)
    env.metadata["bootstrap_report"] = {"config_dir": str(package)}
    run_cli(monkeypatch, capsys, "sync-player-template", "--db", "/db")
    assert env.log[1] == ("platform.sync_player_template", ({"name": "Tiny", "entry": "x"},), {})


@pytest.mark.parametrize("report", [None, "text", {}, {"config_dir": ""}])
def test_sync_player_template_needs_a_package_when_the_instance_names_none(env, monkeypatch, capsys, report):
    if report is not None:
        env.metadata["bootstrap_report"] = report
    result = run_cli(monkeypatch, capsys, "sync-player-template", "--db", "/db")
    assert result.exit == "sync-player-template needs --config because the instance has no bootstrap_report config_dir"
    assert env.log == [("Platform", Path("/db")), ("platform.close",)]


def test_sync_player_template_reports_a_missing_template_file(env, monkeypatch, capsys, tmp_path):
    package = _package_with_template(tmp_path, None)
    result = run_cli(monkeypatch, capsys, "sync-player-template", "--db", "/db", "--config", str(package))
    assert result.exit == f"player template not found: {package / 'player_template.yaml'}"
    assert env.log == [("Platform", Path("/db")), ("platform.close",)]
