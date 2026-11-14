from __future__ import annotations

import pytest

from sim2.actions.conditions import ConditionEngine
from sim2.actions.effects import EffectPipeline
from sim2.dsl.expressions import BinaryExpression, ExpressionSyntaxError, parse_expression


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


def test_expression_parser_rejects_code_and_unsupported_syntax() -> None:
    for expression in ("__import__('os').system('id')", "actor.state.energy; SQL", "actor.state.energy ** 2"):
        with pytest.raises(ExpressionSyntaxError):
            parse_expression(expression)


def test_effect_formula_uses_the_same_restricted_expression_ir() -> None:
    effects = EffectPipeline()
    agent = {"combat_stats": {"attack_power": 9}, "initial_state": {}, "inventory": {}}
    assert effects._formula("(actor.attack_power + 3) * target.defense / 2", agent, {"defense": 2}) == 12
    assert effects._formula("target.defense / 0", agent, {"defense": 2}) == 0
    assert effects._formula("actor.attack_power.__class__", agent, {}) == 0
