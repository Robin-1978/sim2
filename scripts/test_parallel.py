"""Run the test files in parallel processes with isolated pytest temp roots.

The sequential suite is dominated by many small tests plus a few HTTP and
long-liveness cases.  This runner keeps each file's tests in one process so
module-level fixtures and the SQLite temp directories stay isolated, while
using several processes to shorten the wall clock on a developer machine.

Examples:

    python scripts/test_parallel.py --workers 4
    python scripts/test_parallel.py --workers 2 tests/test_social_authority_loop.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).parents[1]
TESTS = ROOT / "tests"


def _git_changed(since: str) -> list[str]:
    """Changed and untracked paths relative to ``since``."""

    diff = subprocess.run(
        ["git", "diff", "--name-only", since],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return sorted({line.strip() for line in (diff.stdout + "\n" + untracked.stdout).splitlines() if line.strip()})


def _select_changed(changed: list[str]) -> list[Path]:
    """Select test files affected by the changed source, world or test paths.

    Platform-wide changes deliberately fall back to the full suite.  A world
    change selects tests that mention that world; a test change selects that
    test; a docs/script-only change selects the architecture smoke tests.
    """

    all_tests = sorted(TESTS.glob("test_*.py"))
    selected: set[Path] = set()
    tokens: set[str] = set()
    force_all = False
    for raw in changed:
        raw_path = Path(raw)
        parts = raw_path.parts
        if raw in {"pyproject.toml", "setup.cfg", "tox.ini"}:
            force_all = True
        elif len(parts) >= 3 and parts[0] == "src" and parts[1] == "sim2":
            area = parts[2]
            tokens.update({f"sim2.{area}", area})
        elif len(parts) >= 2 and parts[0] == "worlds":
            tokens.add(parts[1])
        elif raw.startswith("tests/") and raw_path.suffix == ".py" and (ROOT / raw_path).exists():
            selected.add(ROOT / raw_path)
    if force_all:
        return all_tests
    for test in all_tests:
        text = test.read_text(encoding="utf-8")
        if any(token in text for token in tokens) or any(token in test.stem for token in tokens):
            selected.add(test)
    if any(raw.startswith("src/") for raw in changed):
        for name in ("test_architecture_layers.py", "test_architecture_content_neutral.py"):
            candidate = TESTS / name
            if candidate.exists():
                selected.add(candidate)
    if not selected:
        for name in ("test_architecture_layers.py", "test_definition_schema.py"):
            candidate = TESTS / name
            if candidate.exists():
                selected.add(candidate)
    # A platform-wide change is better served by the complete suite.
    if len(selected) > max(8, int(len(all_tests) * 0.6)):
        return all_tests
    return sorted(selected)


def _discover(patterns: list[str]) -> list[Path]:
    if patterns:
        files: list[Path] = []
        for pattern in patterns:
            path = ROOT / pattern
            if path.is_dir():
                files.extend(sorted(path.glob("test_*.py")))
            elif path.is_file():
                files.append(path)
            else:
                files.extend(sorted(TESTS.glob(pattern)))
        return list(dict.fromkeys(files))
    return sorted(TESTS.glob("test_*.py"))


def _groups(files: list[Path], workers: int) -> list[list[Path]]:
    """Greedy balance by file size; large integration files do not share a worker."""

    groups: list[list[Path]] = [[] for _ in range(workers)]
    weights = [0] * workers
    for path in sorted(files, key=lambda item: item.stat().st_size, reverse=True):
        index = min(range(workers), key=lambda item: weights[item])
        groups[index].append(path)
        weights[index] += path.stat().st_size
    return [sorted(group) for group in groups if group]


def _run_group(index: int, files: list[Path], basetemp: Path) -> tuple[int, str]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--basetemp",
        str(basetemp / f"worker-{index}"),
        *[str(path.relative_to(ROOT)) for path in files],
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT)))},
    )
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=min(4, max(2, (os.cpu_count() or 2) // 2)))
    parser.add_argument("--changed", action="store_true", help="Run only tests affected by git changes since --since.")
    parser.add_argument("--since", default="HEAD", help="Git revision for --changed (default: HEAD).")
    parser.add_argument("--dry-run", action="store_true", help="Print the selected files without running them.")
    parser.add_argument("patterns", nargs="*", help="Optional test files, directories or globs.")
    args = parser.parse_args()

    files = _select_changed(_git_changed(args.since)) if args.changed else _discover(args.patterns)
    if args.dry_run:
        print("\n".join(str(path.relative_to(ROOT)) for path in files))
        return 0
    if not files:
        print("no test files found", file=sys.stderr)
        return 2
    workers = max(1, min(int(args.workers), len(files)))
    groups = _groups(files, workers)
    with tempfile.TemporaryDirectory(prefix="sim2-parallel-tests-") as temp:
        basetemp = Path(temp)
        with ThreadPoolExecutor(max_workers=len(groups)) as pool:
            futures = [
                pool.submit(_run_group, index, group, basetemp)
                for index, group in enumerate(groups, start=1)
            ]
            results = [future.result() for future in futures]

    failed = False
    for index, (returncode, output) in enumerate(results, start=1):
        print(f"===== worker {index} =====")
        if returncode == 0:
            print(output.splitlines()[-1] if output else "passed")
        else:
            failed = True
            print(output)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
