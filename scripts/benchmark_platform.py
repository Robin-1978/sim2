"""Measure one non-LLM Platform minute with a configurable agent count.

Example:
    PYTHONPATH=src python scripts/benchmark_platform.py --agents 1000
"""

from __future__ import annotations

import argparse
import copy
import json
import tempfile
import time
from pathlib import Path

from sandbox.bootstrap.service import create_instance
from sandbox.platform.runtime import Platform


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents", type=int, default=1000)
    args = parser.parse_args()
    if args.agents < 1:
        raise SystemExit("--agents must be positive")
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    with tempfile.TemporaryDirectory(prefix="sim2-benchmark-") as directory:
        db_path = create_instance(config, "benchmark", Path(directory))
        platform = Platform(db_path)
        try:
            template = platform.store.get_agent("lu_xingzhou")
            with platform.store.transaction():
                for index in range(max(0, args.agents - 9)):
                    agent_id = f"benchmark_agent_{index:04d}"
                    value = copy.deepcopy(template)
                    value["id"] = agent_id
                    value["name"] = agent_id
                    platform.store.put_agent(agent_id, value, agent_id)
            started = time.perf_counter()
            result = platform.advance(1)
            elapsed_ms = (time.perf_counter() - started) * 1000
            print(json.dumps({"agents": args.agents, "elapsed_ms": round(elapsed_ms, 3), "result": result}, ensure_ascii=False))
        finally:
            platform.close()


if __name__ == "__main__":
    main()
