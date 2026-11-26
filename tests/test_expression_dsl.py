from __future__ import annotations

import pytest

from sim2.actions.conditions import ConditionEngine
from sim2.actions.effects import EffectPipeline
from sim2.dsl.expressions import BinaryExpression, ExpressionSyntaxError, LiteralExpression, ReferenceExpression, parse_expression


def test_condition_parser_preserves_precedence_and_parentheses() -> None:
    tree = parse_expression("actor.state.energy < 10 or actor.alive and not actor.stunned")
    assert isinstance(tree, BinaryExpression)
    assert tree.operator == "or"

    engine = ConditionEngine()
    agent = {"initial_state": {"energy": 20, "conditions": [], "status": "alive"}, "needs": {}, "inventory": {}}
    assert engine.evaluate("actor.state.energy < 10 or actor.alive and not actor.stunned", agent=agent)
    assert not engine.evaluate("(actor.state.energy < 10 or actor.alive) and actor.stunned", agent=agent)


def test_condition_parser_handles_configured_paths_literals_and_tri_state() -> None:
    engine = ConditionEngine()
    agent = {
        "role": "pathfinder",
        "faction_ids": ["watch"],
        "initial_state": {"location_id": "old_well", "status": "alive", "conditions": []},
        "needs": {"hydration": 2},
        "inventory": {},
    }
    context = {"destination_id": "old_well", "hydration_alert_threshold": 5}
    assert engine.evaluate("actor.role == pathfinder", agent=agent, context=context)
    assert engine.evaluate("actor.faction_id not_in [root_keepers]", agent=agent, context=context)
    assert engine.evaluate("actor.state.location_id == context.destination_id", agent=agent, context=context)
    assert engine.evaluate("actor.need.hydration <= context.hydration_alert_threshold", agent=agent, context=context)
    assert engine.evaluate_known("actor.unknown_field", agent=agent) is None


def test_dotted_operands_are_references_whatever_their_root() -> None:
    """The parser keeps no vocabulary of roots: dotted words are values, bare words are labels."""

    comparison = parse_expression("actor.state.fuel < supply.level")
    assert comparison.right == ReferenceExpression("supply.level")
    arithmetic = parse_expression("actor.state.fuel - burn.rate > 0")
    assert arithmetic.left.right == ReferenceExpression("burn.rate")
    membership = parse_expression("actor.role in [pilot, navigator]")
    assert membership.right.value == ("pilot", "navigator")
    assert parse_expression("actor.role == pathfinder").right == LiteralExpression("pathfinder")
    assert parse_expression('actor.role == "relay.keeper"').right == LiteralExpression("relay.keeper")
    assert parse_expression("actor.state.depth > 2.5").right == LiteralExpression(2.5)


def test_a_world_supplied_root_resolves_and_an_undeclared_one_fails_validation() -> None:
    engine = ConditionEngine()
    agent = {"initial_state": {"fuel": 20, "conditions": [], "status": "alive"}, "needs": {}, "inventory": {}}
    assert engine.evaluate("actor.state.fuel < supply.level", agent=agent, context={"supply": {"level": 50}})
    assert not engine.evaluate("actor.state.fuel < supply.level", agent=agent, context={"supply": {"level": 5}})
    # Without the value the comparison is false, never a string compare against the path text.
    assert not engine.evaluate("actor.state.fuel < supply.level", agent=agent)
    # Authoring-time validation reports the unknown root instead of treating it as a string.
    assert engine.evaluate_known("actor.state.fuel < supply.level", agent=agent) is None
    assert engine.evaluate_known("actor.state.fuel < context.limit", agent=agent, context={"limit": 50}) is True


def test_effect_formula_rejects_a_reference_root_it_cannot_resolve() -> None:
    effects = EffectPipeline()
    agent = {"combat_stats": {"attack_power": 9}, "initial_state": {}, "inventory": {}}
    assert effects._formula("actor.attack_power + weapon.bonus", agent, {"weapon": {"bonus": 3}}) == 12
    assert effects._formula("actor.attack_power + tool.bonus", agent, {"tool": {"bonus": 3}}) == 0


def test_expression_parser_rejects_code_and_unsupported_syntax() -> None:
    for expression in ("__import__('os').system('id')", "actor.state.energy; SQL", "actor.state.energy ** 2"):
        with pytest.raises(ExpressionSyntaxError):
            parse_expression(expression)


def test_cached_expression_reuses_one_tree_for_independent_bindings() -> None:
    expression = "world[context.slot] == null"
    assert parse_expression(expression) is parse_expression(f" {expression} ")
    engine = ConditionEngine()
    world = {"open": None, "closed": 1}
    assert engine.evaluate(expression, agent={}, world=world, context={"slot": "open"})
    assert not engine.evaluate(expression, agent={}, world=world, context={"slot": "closed"})
    assert engine.evaluate("world.placeholder != 'ready'", agent={}, world={"placeholder": 0})
    assert engine.evaluate_known("actor.alive and (", agent={}) is False


def test_effect_formula_uses_the_same_restricted_expression_ir() -> None:
    effects = EffectPipeline()
    agent = {"combat_stats": {"attack_power": 9}, "initial_state": {}, "inventory": {}}
    assert effects._formula("(actor.attack_power + 3) * target.defense / 2", agent, {"defense": 2}) == 12
    assert effects._formula("target.defense / 0", agent, {"defense": 2}) == 0
    assert effects._formula("actor.attack_power.__class__", agent, {}) == 0
