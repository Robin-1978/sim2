from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from ..agents.engine import AgentEngine
from ..bootstrap.service import create_instance, describe_config, validate_config, world_template_schema, write_world_package_from_template
from ..llm.gateway import LLMRouter
from ..platform.runtime import Platform
from .text import TextWorldSession
from .http import ControlHTTPServer
from .map_view import render_topology_map


def main() -> None:
    parser = argparse.ArgumentParser(prog="sim2")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create a new SQLite world instance")
    init.add_argument("--config", type=Path, required=True)
    init.add_argument("--instance-id", required=True)
    init.add_argument("--runtime-root", type=Path, default=Path.home() / ".sim2")

    validate_world = sub.add_parser("validate-world", help="validate a YAML world package and all cross references")
    validate_world.add_argument("--config", type=Path, required=True)

    describe_world = sub.add_parser("describe-world", help="show a world package inventory and validation result")
    describe_world.add_argument("--config", type=Path, required=True)

    sub.add_parser("world-schema", help="print the machine-readable WorldBuilder template schema")

    build_world = sub.add_parser("build-world", help="compile a WorldBuilder YAML template into a reviewable world package")
    build_source = build_world.add_mutually_exclusive_group(required=True)
    build_source.add_argument("--template", type=Path, help="existing unified YAML template")
    build_source.add_argument("--brief", help="world description for the WorldBuilder Agent")
    build_source.add_argument("--brief-file", type=Path, help="UTF-8 world description for the WorldBuilder Agent")
    build_world.add_argument("--output", type=Path, required=True)
    build_world.add_argument("--overwrite", action="store_true")
    build_world.add_argument("--instance-id")
    build_world.add_argument("--runtime-root", type=Path, default=Path.home() / ".sim2")
    build_world.add_argument("--provider", default="remote_beam", help="configured provider used for --brief")
    build_world.add_argument("--max-completion-tokens", type=int, default=8192, help="creation-time output budget; ordinary runtime roles keep their configured smaller budget")
    build_world.add_argument("--audit-output", type=Path, help="optional JSON transcript path without credentials")

    decide = sub.add_parser("decide", help="run one Decision Agent turn")
    decide.add_argument("--db", type=Path, required=True)
    decide.add_argument("--agent-id", required=True)
    decide.add_argument("--role", default="agent_decision")

    mud = sub.add_parser("mud", help="start a text player or observer session")
    mud.add_argument("--db", type=Path, required=True)
    mud.add_argument("--player-id")
    mud.add_argument("--origin")
    mud.add_argument("--name")
    mud.add_argument("--observer", action="store_true")

    tui = sub.add_parser("tui", help="open the live world map, timeline and character interface")
    tui.add_argument("--db", type=Path, required=True)
    tui.add_argument("--observer", action="store_true")
    tui.add_argument("--player-id")
    tui.add_argument("--origin")
    tui.add_argument("--name")
    tui.add_argument("--run", action="store_true", help="run the autonomous world in a background thread")
    tui.add_argument("--provider", help="provider name used by --run")

    simulate = sub.add_parser("simulate", help="run the automatic Agent/Platform loop")
    simulate.add_argument("--db", type=Path, required=True)
    simulate.add_argument("--minutes", type=int, required=True)
    simulate.add_argument("--wait-cooldown", type=int, default=30)
    simulate.add_argument("--llm-error-cooldown", type=int, default=60)
    simulate.add_argument("--provider", help="override all Agent roles with one configured provider (for example: remote)")

    serve = sub.add_parser("serve", help="start the local HTTP control API")
    serve.add_argument("--db", type=Path, required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)

    for name, help_text in [("status", "show instance status"), ("health", "check database and runtime health"), ("timeline", "show the public third-person timeline"), ("map", "show the location topology and character positions"), ("pause", "pause world advancement"), ("resume", "resume world advancement"), ("step", "advance one world minute"), ("metrics", "show runtime metrics"), ("audit", "export event and Agent audit")]:
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--db", type=Path, required=True)
        if name == "timeline":
            command.add_argument("--limit", type=int, default=100)
            command.add_argument("--after", type=int, default=0)
            command.add_argument("--ticks", action="store_true")
    snapshot = sub.add_parser("snapshot", help="copy a consistent SQLite snapshot")
    snapshot.add_argument("--db", type=Path, required=True)
    snapshot.add_argument("--destination", type=Path, required=True)
    restore = sub.add_parser("restore", help="restore a SQLite snapshot")
    restore.add_argument("--db", type=Path, required=True)
    restore.add_argument("--source", type=Path, required=True)
    enter = sub.add_parser("enter-player", help="enter a player from the configured template")
    enter.add_argument("--db", type=Path, required=True)
    enter.add_argument("--player-id", required=True)
    enter.add_argument("--origin", required=True)
    enter.add_argument("--name")
    inject = sub.add_parser("inject", help="append an external world event")
    inject.add_argument("--db", type=Path, required=True)
    inject.add_argument("--event-type", required=True)
    inject.add_argument("--payload", default="{}", help="JSON object")
    inject.add_argument("--actor-id")

    args = parser.parse_args()
    if args.command == "init":
        db = create_instance(args.config, args.instance_id, args.runtime_root)
        print(json.dumps({"db_path": str(db)}, ensure_ascii=False))
        return
    if args.command == "validate-world":
        report = validate_config(args.config)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["valid"]:
            raise SystemExit(1)
        return
    if args.command == "describe-world":
        report = describe_config(args.config)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["valid"]:
            raise SystemExit(1)
        return
    if args.command == "world-schema":
        print(json.dumps(world_template_schema(), ensure_ascii=False, indent=2))
        return
    if args.command == "build-world":
        build_audit = None
        if args.template:
            raw = yaml.safe_load(args.template.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("--template must contain a YAML object")
        else:
            brief = args.brief if args.brief is not None else args.brief_file.read_text(encoding="utf-8")
            from ..world_builder import WorldBuilderError, WorldBuilderService

            provider = LLMRouter.from_default_config().new_provider_for_name(
                args.provider,
                max_completion_tokens=args.max_completion_tokens,
                role="world_builder",
            )
            try:
                draft = WorldBuilderService(provider).build(brief)
            except WorldBuilderError as exc:
                failed_audit = {"status": "rejected", "message": str(exc), "usage": exc.usage, "transcript": exc.transcript}
                if args.audit_output:
                    args.audit_output.parent.mkdir(parents=True, exist_ok=True)
                    args.audit_output.write_text(json.dumps(failed_audit, ensure_ascii=False, indent=2), encoding="utf-8")
                raise
            raw = draft.template
            build_audit = {"iterations": draft.iterations, "usage": draft.usage, "transcript": draft.transcript}
        package = write_world_package_from_template(raw, args.output, overwrite=args.overwrite)
        result: dict[str, object] = {"status": "accepted", "package": str(package), "validation": describe_config(package)}
        if build_audit is not None:
            result["world_builder"] = {"iterations": build_audit["iterations"], "usage": build_audit["usage"]}
            if args.audit_output:
                args.audit_output.parent.mkdir(parents=True, exist_ok=True)
                args.audit_output.write_text(json.dumps(build_audit, ensure_ascii=False, indent=2), encoding="utf-8")
                result["audit"] = str(args.audit_output)
        if args.instance_id:
            result["db_path"] = str(create_instance(package, args.instance_id, args.runtime_root))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "tui":
        platform = Platform(args.db)
        try:
            observer = bool(args.observer or not args.player_id)
            if not observer:
                try:
                    platform.store.get_agent(args.player_id)
                except KeyError:
                    if not args.origin:
                        raise ValueError("--origin is required when creating a new player")
                    entered = platform.enter_player(args.player_id, args.origin, name=args.name)
                    if entered.get("status") != "accepted":
                        raise ValueError(str(entered))
        finally:
            platform.close()
        from .tui import run_tui
        run_tui(args.db, observer=observer, player_id=args.player_id, run_world=args.run, provider_name=args.provider)
        return
    if args.command == "decide":
        platform = Platform(args.db)
        try:
            router = LLMRouter.from_default_config()
            provider = router.provider_for_role(args.role)
            engine = AgentEngine(platform, provider)
            turn = engine.run(role=args.role, actor_id=args.agent_id)
            print(json.dumps({"response": turn.response, "result": turn.result, "iterations": turn.iterations}, ensure_ascii=False))
        finally:
            platform.close()
    if args.command == "mud":
        platform = Platform(args.db)
        try:
            if not args.observer:
                if not args.player_id:
                    raise ValueError("--player-id is required in player mode")
                try:
                    platform.store.get_agent(args.player_id)
                except KeyError:
                    if not args.origin:
                        raise ValueError("--origin is required when creating a new player")
                    entered = platform.enter_player(args.player_id, args.origin, name=args.name)
                    if entered.get("status") != "accepted":
                        raise ValueError(str(entered))
            TextWorldSession(platform, player_id=args.player_id, observer=args.observer).run()
        finally:
            platform.close()


        return
    if args.command == "simulate":
        platform = Platform(args.db)
        try:
            report = platform.run(
                args.minutes,
                provider_name=args.provider,
                wait_cooldown_minutes=args.wait_cooldown,
                llm_error_cooldown_minutes=args.llm_error_cooldown,
            )
            print(json.dumps({"report": report.as_dict(), "metrics": platform.metrics()}, ensure_ascii=False))
        finally:
            platform.close()
        return
    if args.command == "serve":
        server = ControlHTTPServer((args.host, args.port), args.db)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return
        finally:
            server.close()
        return
    if args.command in {"status", "health", "timeline", "map", "pause", "resume", "step", "metrics", "audit", "snapshot", "restore", "enter-player", "inject"}:
        platform = Platform(args.db)
        try:
            if args.command == "status":
                result = platform.status()
            elif args.command == "health":
                result = platform.health()
            elif args.command == "timeline":
                result = platform.timeline(limit=args.limit, after_event_id=args.after, include_ticks=args.ticks)
            elif args.command == "map":
                print(render_topology_map(platform.map_view(observer=True)))
                return
            elif args.command == "pause":
                result = platform.pause()
            elif args.command == "resume":
                result = platform.resume()
            elif args.command == "step":
                result = platform.step()
            elif args.command == "metrics":
                result = platform.metrics()
            elif args.command == "audit":
                result = platform.export_audit()
            elif args.command == "snapshot":
                result = {"snapshot": str(platform.snapshot(args.destination))}
            elif args.command == "restore":
                result = platform.restore_snapshot(args.source)
            elif args.command == "enter-player":
                result = platform.enter_player(args.player_id, args.origin, name=args.name)
            else:
                payload = json.loads(args.payload)
                if not isinstance(payload, dict):
                    raise ValueError("--payload must be a JSON object")
                result = platform.inject_event(args.event_type, payload, args.actor_id)
            print(json.dumps(result, ensure_ascii=False))
        finally:
            platform.close()


if __name__ == "__main__":
    main()
