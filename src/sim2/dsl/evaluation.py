"""Evaluate the shared expression AST with field bindings supplied by the caller."""

from __future__ import annotations

import operator
from typing import Any, Callable

from .expressions import BinaryExpression, Expression, IndexedReference, LiteralExpression, ReferenceExpression, UnaryExpression


UNKNOWN = object()
_COMPARISONS = {
    "==": operator.eq, "!=": operator.ne, ">=": operator.ge,
    "<=": operator.le, ">": operator.gt, "<": operator.lt,
}


def evaluate_tree(
    tree: Expression, resolve: Callable[[str], Any], resolve_index: Callable[[str, str], Any], *, missing: Any = UNKNOWN,
) -> Any:
    def visit(tree: Expression) -> Any:
        if isinstance(tree, LiteralExpression):
            if isinstance(tree.value, tuple):
                return [
                    visit(item)
                    if isinstance(item, Expression) else item
                    for item in tree.value
                ]
            return tree.value
        if isinstance(tree, ReferenceExpression):
            return resolve(tree.path)
        if isinstance(tree, IndexedReference):
            key = visit(tree.key)
            if not isinstance(key, (str, int, float)) or isinstance(key, bool):
                return missing
            return resolve_index(tree.path, str(key))
        if isinstance(tree, UnaryExpression):
            value = visit(tree.operand)
            if value is UNKNOWN:
                return UNKNOWN
            if tree.operator == "not":
                return not bool(value)
            if tree.operator == "+":
                return +value
            if tree.operator == "-":
                return -value
            return value
        if not isinstance(tree, BinaryExpression):
            return missing
        if tree.operator == "and":
            left = visit(tree.left)
            if left is not UNKNOWN and not bool(left):
                return False
            right = visit(tree.right)
            if right is not UNKNOWN and not bool(right):
                return False
            return UNKNOWN if (left is UNKNOWN or right is UNKNOWN) else True
        if tree.operator == "or":
            left = visit(tree.left)
            if left is not UNKNOWN and bool(left):
                return True
            right = visit(tree.right)
            if right is not UNKNOWN and bool(right):
                return True
            return UNKNOWN if (left is UNKNOWN or right is UNKNOWN) else False
        left = visit(tree.left)
        right = visit(tree.right)
        if left is UNKNOWN or right is UNKNOWN:
            return UNKNOWN
        if left is None and tree.operator in {"in", "not_in"}:
            return tree.operator == "not_in"
        try:
            if tree.operator == "in":
                return left in right
            if tree.operator == "not_in":
                return left not in right
            if tree.operator == "+":
                return left + right
            if tree.operator == "-":
                return left - right
            if tree.operator == "*":
                return left * right
            if tree.operator == "/":
                return left / right if right != 0 else 0
            return compare(left, tree.operator, right)
        except (TypeError, KeyError):
            return False

    return visit(tree)


def compare(left: Any, operator: str, right: Any) -> bool:
    """Compare numeric configuration values without binary-edge errors."""

    if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
        epsilon = 1e-9 * max(1.0, abs(float(left)), abs(float(right)))
        if operator == "==":
            return abs(float(left) - float(right)) <= epsilon
        if operator == "!=":
            return abs(float(left) - float(right)) > epsilon
        if operator == ">=":
            return float(left) + epsilon >= float(right)
        if operator == "<=":
            return float(left) - epsilon <= float(right)
        if operator == ">":
            return float(left) > float(right) + epsilon
        if operator == "<":
            return float(left) < float(right) - epsilon
    return _COMPARISONS[operator](left, right)
