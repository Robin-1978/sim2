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
