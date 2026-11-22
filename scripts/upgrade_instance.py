"""Apply a world package's definitions and UI/economy config to an existing save.

Run with the game server stopped:

    python scripts/upgrade_instance.py --world worlds/riverbend_market --instance-id riverbend_market_p6wh

The script preserves entities, agents, inventories, world properties, memories,
relationships, story state and runtime-authored world rules. It only upserts
config metadata and the action/behavior/recipe definition tables.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sim2.bootstrap.service import upgrade_instance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", required=True, type=Path, help="world package directory")
    parser.add_argument("--instance-id", required=True, help="instance id under the runtime root")
    parser.add_argument("--runtime-root", type=Path, default=Path.home() / ".sim2")
    args = parser.parse_args()
    db_path = args.runtime_root / "instances" / args.instance_id / "world.sqlite3"
    result = upgrade_instance(args.world, db_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
