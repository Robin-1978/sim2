"""A tiny, world-neutral expression parser for declarative predicates.

The world package owns the names that may appear in a path.  This module only
owns syntax and produces a safe expression tree; it never resolves or executes
a path.  Runtime evaluators supply the path resolver and therefore keep world
state access behind their existing permission and projection boundaries.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterator


class ExpressionSyntaxError(ValueError):
    """Raised when a configured expression is outside the supported grammar."""


class Expression:
    """Marker base class for the expression IR."""


@dataclass(frozen=True)
class LiteralExpression(Expression):
    value: Any


@dataclass(frozen=True)
class ReferenceExpression(Expression):
    path: str


@dataclass(frozen=True)
class UnaryExpression(Expression):
    operator: str
    operand: Expression


@dataclass(frozen=True)
class BinaryExpression(Expression):
    operator: str
    left: Expression
    right: Expression


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str
    position: int


# Bare words intentionally allow Unicode, colon and hyphen.  They are data
# keys/labels from a world package, not Python identifiers.
_TOKEN_RE = re.compile(
    r"""
    (?P<space>\s+)
    |(?P<string>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')
    |(?P<number>(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)
    |(?P<operator>==|!=|>=|<=|>|<|\+|-|\*|/|\(|\)|\[|\]|,)
    |(?P<word>[^\s()\[\],=!<>]+)
    """,
    re.VERBOSE | re.UNICODE,
)


def _tokens(source: str) -> Iterator[_Token]:
    position = 0
    while position < len(source):
        match = _TOKEN_RE.match(source, position)
        if match is None:
            raise ExpressionSyntaxError(f"unexpected character at position {position}")
        position = match.end()
        kind = match.lastgroup
        if kind == "space":
            continue
        if kind == "operator":
            yield _Token("operator", match.group(kind), match.start())
        else:
            yield _Token(kind or "", match.group(kind), match.start())
    yield _Token("eof", "", len(source))


_REFERENCE_PREFIXES = (
    "actor.",
    "target.",
    "world.",
    "context.",
    "event.",
    "location.",
    "destination.",
    "area.",
    "object.",
    "resource.",
    "route.",
    "research.",
    "secret.",
    "inventory.",
    "recipe.",
    "inputs.",
    "medicine.",
    "patient.",
    "counterparty.",
    "communication.",
    "institution.",
    "alarm.",
    "attack.",
    "combat.",
    "conflict.",
    "defense.",
    "dialogue.",
    "food.",
    "water.",
    "ration.",
    "threat.",
    "treatment.",
    "outputs.",
    "station.",
    "weapon.",
)


def _looks_like_reference(value: str) -> bool:
    return value.startswith(_REFERENCE_PREFIXES) or value in {
        "true", "false", "action.none", "alarm.broadcast", "attack.resolved",
        "communication.channel_open", "conflict.available", "danger.detected",
        "defense.window_complete", "dialogue.completed", "entity.installed",
        "food.consumed", "food.unavailable", "hostile_contact", "inputs.lost",
        "location.locked", "market.empty", "medicine.failed", "negotiation.recorded",
        "object.recovered", "patient.fever_reduced", "ration.purchased",
        "ration.withdrawn", "resource.collected", "resource.empty", "route.completed",
        "source.accessible", "source.destroyed", "target.out_of_range",
        "target.refused", "threat.overwhelming", "treatment.completed",
        "trust.collapsed", "village_food.empty", "water.consumed", "water.unavailable",
    }


class _Parser:
    def __init__(self, source: str):
        self.source = source
        self.items = list(_tokens(source))
        self.index = 0

    def current(self) -> _Token:
        return self.items[self.index]

    def advance(self) -> _Token:
        token = self.current()
        self.index += 1
        return token

    def accept(self, *, kind: str | None = None, text: str | None = None) -> _Token | None:
        token = self.current()
        if kind is not None and token.kind != kind:
            return None
        if text is not None and token.text != text:
            return None
        self.index += 1
        return token

    def expect(self, *, kind: str | None = None, text: str | None = None) -> _Token:
        token = self.accept(kind=kind, text=text)
        if token is None:
            expected = text or kind or "token"
            actual = self.current().text or "end of expression"
            raise ExpressionSyntaxError(f"expected {expected} at position {self.current().position}, got {actual!r}")
        return token

    def parse(self) -> Expression:
        if self.current().kind == "eof":
            raise ExpressionSyntaxError("expression is empty")
        result = self.parse_or()
        if self.current().kind != "eof":
            raise ExpressionSyntaxError(f"unexpected token {self.current().text!r} at position {self.current().position}")
        return result

    def parse_or(self) -> Expression:
        result = self.parse_and()
        while self.accept(kind="word", text="or"):
            result = BinaryExpression("or", result, self.parse_and())
        return result

    def parse_and(self) -> Expression:
        result = self.parse_not()
        while self.accept(kind="word", text="and"):
            result = BinaryExpression("and", result, self.parse_not())
        return result

    def parse_not(self) -> Expression:
        if self.accept(kind="word", text="not"):
            return UnaryExpression("not", self.parse_not())
        return self.parse_comparison()

    def parse_comparison(self) -> Expression:
        left = self.parse_additive(reference=True)
        token = self.current()
        if token.kind == "operator" and token.text in {"==", "!=", ">=", "<=", ">", "<"}:
            operator = self.advance().text
            return BinaryExpression(operator, left, self.parse_additive(reference=False))
        if token.kind == "word" and token.text in {"in", "not_in"}:
            operator = self.advance().text
            return BinaryExpression(operator, left, self.parse_primary(reference=False, collection=True))
        return left

    def parse_additive(self, *, reference: bool) -> Expression:
        result = self.parse_multiplicative(reference=reference)
        while self.current().kind == "operator" and self.current().text in {"+", "-"}:
            operator = self.advance().text
            result = BinaryExpression(operator, result, self.parse_multiplicative(reference=False))
        return result

    def parse_multiplicative(self, *, reference: bool) -> Expression:
        result = self.parse_unary(reference=reference)
        while self.current().kind == "operator" and self.current().text in {"*", "/"}:
            operator = self.advance().text
            result = BinaryExpression(operator, result, self.parse_unary(reference=False))
        return result

    def parse_unary(self, *, reference: bool) -> Expression:
        if self.current().kind == "operator" and self.current().text in {"+", "-"}:
            operator = self.advance().text
            return UnaryExpression(operator, self.parse_unary(reference=reference))
        return self.parse_primary(reference=reference)

    def parse_primary(self, *, reference: bool, collection: bool = False) -> Expression:
        if self.accept(kind="operator", text="("):
            result = self.parse_or()
            self.expect(kind="operator", text=")")
            return result
        if collection and self.accept(kind="operator", text="["):
            values: list[Expression] = []
            if not self.accept(kind="operator", text="]"):
                while True:
                    values.append(self.parse_primary(reference=False))
                    if self.accept(kind="operator", text="]"):
                        break
                    self.expect(kind="operator", text=",")
            return LiteralExpression([item.value if isinstance(item, LiteralExpression) else item for item in values])
        token = self.advance()
        if token.kind == "string":
            if token.text.startswith('"'):
                try:
                    return LiteralExpression(json.loads(token.text))
                except json.JSONDecodeError as exc:
                    raise ExpressionSyntaxError(f"invalid string at position {token.position}") from exc
            inner = token.text[1:-1]
            return LiteralExpression(inner.replace("\\'", "'").replace("\\\\", "\\"))
        if token.kind == "number":
            try:
                number: int | float = float(token.text) if any(char in token.text for char in ".eE") else int(token.text)
            except ValueError as exc:
                raise ExpressionSyntaxError(f"invalid number at position {token.position}") from exc
            return LiteralExpression(number)
        if token.kind != "word":
            raise ExpressionSyntaxError(f"expected value at position {token.position}")
        if token.text == "true":
            return LiteralExpression(True)
        if token.text == "false":
            return LiteralExpression(False)
        if token.text in {"null", "none"}:
            return LiteralExpression(None)
        if reference or _looks_like_reference(token.text):
            return ReferenceExpression(token.text)
        return LiteralExpression(token.text)


def parse_expression(source: str) -> Expression:
    """Parse one supported condition/formula expression into a safe IR tree."""

    return _Parser(str(source or "").strip()).parse()
