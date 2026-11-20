from __future__ import annotations

from sim2.actions.conditions import ConditionEngine


def test_world_bracket_notation_is_the_same_flat_projection() -> None:
    """God-authored rules may use either world.key or world["key"]."""

    engine = ConditionEngine()
    world = {"tide_gate_integrity": 0.5, "public confidence": 0.4}

    assert engine.evaluate('world["tide_gate_integrity"] < 0.8', agent={}, world=world)
    assert engine.evaluate("world['tide_gate_integrity'] >= 0.5", agent={}, world=world)
    assert engine.evaluate_known(
        'world["tide_gate_integrity"] >= 0.8', agent={}, world=world
    ) is False
    assert engine.evaluate('world["public_confidence"] >= 0.4', agent={}, world={"public_confidence": 0.4})


def test_unknown_bracket_property_stays_false() -> None:
    engine = ConditionEngine()
    assert not engine.evaluate('world["missing.property"] > 0', agent={}, world={})


def test_rule_compiler_accepts_a_bracketed_world_condition() -> None:
    from sim2.world.rules import RuleCompiler

    engine = ConditionEngine()
    compiler = RuleCompiler(
        {
            "world_rules": [
                {
                    "id": "maintenance_watch",
                    "condition": 'world["tide_gate_integrity"] < 0.8',
                    "effect": "tide_gate.maintenance_due",
                }
            ]
        },
        engine,
        world_keys={"tide_gate_integrity"},
    )
    rule = compiler.all_rules()[0]
    assert compiler.matches(rule, agent={}, target={}, context={}, world={"tide_gate_integrity": 0.5})
    assert not compiler.matches(rule, agent={}, target={}, context={}, world={"tide_gate_integrity": 0.9})
