from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..agents.engine import AgentEngine
from ..bootstrap.service import create_instance
from ..llm.gateway import LLMRouter
from ..platform.runtime import Platform


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

    for name, help_text in [("status", "show instance status"), ("pause", "pause world advancement"), ("resume", "resume world advancement"), ("step", "advance one world minute"), ("metrics", "show runtime metrics"), ("audit", "export event and Agent audit")]:
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--db", type=Path, required=True)
    advance = sub.add_parser("advance", help="advance world minutes")
    advance.add_argument("--db", type=Path, required=True)
    advance.add_argument("--minutes", type=int, required=True)
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
    if args.command in {"status", "pause", "resume", "step", "advance", "metrics", "audit", "snapshot", "restore", "enter-player", "inject"}:
        platform = Platform(args.db)
        try:
            if args.command == "status":
                result = platform.status()
            elif args.command == "pause":
                result = platform.pause()
            elif args.command == "resume":
                result = platform.resume()
            elif args.command == "step":
                result = platform.step()
            elif args.command == "advance":
                result = platform.advance(args.minutes)
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
