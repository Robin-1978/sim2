from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..agents.engine import AgentEngine
from ..bootstrap.service import create_instance
from ..llm.gateway import LLMRouter
from ..platform.runtime import Platform
from .text import TextWorldSession
from .http import ControlHTTPServer


def main() -> None:
    parser = argparse.ArgumentParser(prog="sim2")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create a new SQLite world instance")
    init.add_argument("--config", type=Path, required=True)
    init.add_argument("--instance-id", required=True)
    init.add_argument("--runtime-root", type=Path, default=Path.home() / ".sim2")

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

    for name, help_text in [("status", "show instance status"), ("health", "check database and runtime health"), ("pause", "pause world advancement"), ("resume", "resume world advancement"), ("step", "advance one world minute"), ("metrics", "show runtime metrics"), ("audit", "export event and Agent audit")]:
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--db", type=Path, required=True)
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
    if args.command in {"status", "health", "pause", "resume", "step", "metrics", "audit", "snapshot", "restore", "enter-player", "inject"}:
        platform = Platform(args.db)
        try:
            if args.command == "status":
                result = platform.status()
            elif args.command == "health":
                result = platform.health()
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
