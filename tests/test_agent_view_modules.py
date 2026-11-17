"""Guards for the AgentViewCompiler mixin modules.

``AgentViewCompiler`` is composed from mixins in ``agent_view/`` that share the
attributes its constructor creates.  A method that mentions an attribute nothing
defines only fails when that branch runs, and a method defined by two mixins is
silently resolved by the class order, so both are checked statically.
"""

from __future__ import annotations

import ast
from pathlib import Path

from sim2.agent_view.compiler import AgentViewCompiler

PACKAGE = Path(__file__).parents[1] / "src" / "sim2" / "agent_view"


def _view_modules() -> list[Path]:
    return [
        path for path in sorted(PACKAGE.glob("*.py"))
        if path.name == "compiler.py" or "Mixin" in path.read_text(encoding="utf-8")
    ]


def _self_attributes(path: Path) -> tuple[set[str], set[str]]:
    loads: set[str] = set()
    stores: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            (stores if isinstance(node.ctx, ast.Store) else loads).add(node.attr)
    return loads, stores


def test_every_self_attribute_a_view_module_reads_is_defined_by_the_composed_class() -> None:
    loads: set[str] = set()
    stores: set[str] = set()
    for path in _view_modules():
        module_loads, module_stores = _self_attributes(path)
        loads |= module_loads
        stores |= module_stores
    undefined = sorted(loads - stores - set(dir(AgentViewCompiler)))
    assert not undefined, f"view modules read attributes nothing defines: {undefined}"


def test_no_method_is_defined_by_two_view_classes() -> None:
    owners: dict[str, list[str]] = {}
    for cls in AgentViewCompiler.__mro__:
        if cls is object:
            continue
        for name, value in vars(cls).items():
            if callable(value) or isinstance(value, (staticmethod, classmethod)):
                owners.setdefault(name, []).append(cls.__name__)
    duplicated = {name: classes for name, classes in owners.items() if len(classes) > 1 and not name.startswith("__")}
    assert not duplicated, f"methods defined by more than one view class: {duplicated}"


def test_view_mixins_do_not_import_the_composed_class() -> None:
    for path in _view_modules():
        if path.name == "compiler.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert "AgentViewCompiler" not in imported, f"{path.name} would create an import cycle with compiler.py"
