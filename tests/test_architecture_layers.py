"""Package layering guard.

Module-level imports between ``sim2`` packages must stay acyclic.  Imports
inside functions and ``TYPE_CHECKING`` blocks are lazy by design and are not
counted here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "sim2"
# ``services["intent"]._reject_intent(...)``: a private method of another service.
CROSS_SERVICE_PRIVATE_CALL = re.compile(r"""\[\s*["'][a-z_]+["']\s*\]\._[a-z]""")


def _target_package(path: Path, node: ast.ImportFrom) -> str | None:
    """The top-level ``sim2`` package an import statement reaches, if any."""

    if node.level == 0:
        parts = (node.module or "").split(".")
        return parts[1] if parts[0] == "sim2" and len(parts) > 1 else None
    here = path.relative_to(SRC).parent.parts
    base = here[: len(here) - (node.level - 1)]
    parts = [*base, *((node.module or "").split(".") if node.module else [])]
    return parts[0] if parts else None


def _package_edges() -> dict[str, set[str]]:
    edges: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        parts = path.relative_to(SRC).parts
        if len(parts) < 2:
            continue
        source = parts[0]
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            nodes = [node]
            if isinstance(node, ast.If) and "TYPE_CHECKING" not in ast.dump(node.test):
                nodes = list(node.body) + list(node.orelse)
            for statement in nodes:
                if not isinstance(statement, ast.ImportFrom):
                    continue
                target = _target_package(path, statement)
                if target and target != source and (SRC / target).is_dir():
                    edges.setdefault(source, set()).add(target)
    return edges


def _cycles(edges: dict[str, set[str]]) -> list[list[str]]:
    """Strongly connected components with more than one package (Tarjan)."""

    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []

    def visit(node: str) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)
        for neighbour in sorted(edges.get(node, ())):
            if neighbour not in index:
                visit(neighbour)
                low[node] = min(low[node], low[neighbour])
            elif neighbour in on_stack:
                low[node] = min(low[node], index[neighbour])
        if low[node] == index[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                found.append(sorted(component))

    for node in sorted(edges):
        if node not in index:
            visit(node)
    return found


def test_module_level_imports_between_packages_are_acyclic():
    assert _cycles(_package_edges()) == []


def test_world_configuration_package_depends_on_nothing_above_the_dsl():
    assert _package_edges().get("world", set()) <= {"dsl"}


def test_agent_projection_does_not_import_the_agent_runtime():
    # agents -> agent_view -> agents closed a loop through platform.
    assert "agents" not in _package_edges().get("agent_view", set())


def test_platform_services_call_each_other_through_public_methods():
    offenders = [
        f"{path.relative_to(SRC)}:{number}"
        for path in sorted(SRC.rglob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if CROSS_SERVICE_PRIVATE_CALL.search(line)
    ]
    assert offenders == []
