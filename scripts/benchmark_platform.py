"""Measure one non-LLM Platform minute with a configurable agent count.

Example:
    python scripts/benchmark_platform.py --agents 1000
"""

from __future__ import annotations

import argparse
import copy
import json
import statistics
import tempfile
import time
from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents", type=int, default=1000)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=3, help="unmeasured startup ticks before steady-state samples")
    args = parser.parse_args()
    if args.agents < 1:
        raise SystemExit("--agents must be positive")
    if args.runs < 1:
        raise SystemExit("--runs must be positive")
    if args.warmup < 0:
        raise SystemExit("--warmup must not be negative")
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
            warmup_durations = []
            for _ in range(args.warmup):
                started = time.perf_counter()
                platform.step()
                warmup_durations.append((time.perf_counter() - started) * 1000)
            durations = []
            result = None
            for _ in range(args.runs):
                started = time.perf_counter()
                result = platform.step()
                durations.append((time.perf_counter() - started) * 1000)
            p95 = statistics.quantiles(durations, n=20, method="inclusive")[18] if len(durations) >= 2 else durations[0]
            print(json.dumps({
                "agents": args.agents,
                "warmup_runs": args.warmup,
                "warmup_elapsed_ms": [round(value, 3) for value in warmup_durations],
                "runs": args.runs,
                "elapsed_ms": [round(value, 3) for value in durations],
                "p95_ms": round(p95, 3),
                "result": result,
            }, ensure_ascii=False))
        finally:
            platform.close()


if __name__ == "__main__":
    main()
