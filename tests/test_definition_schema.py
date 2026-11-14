from __future__ import annotations

from sim2.agents.tool_registry import tool_schemas_for_role
from sim2.world.definition_compiler import DEFINITION_TYPES, compile_definition_shape
from sim2.world.definition_schema import propose_definition_schema


def test_god_tool_uses_the_canonical_definition_schema() -> None:
    canonical = propose_definition_schema()
    tool = next(item for item in tool_schemas_for_role("god") if item["function"]["name"] == "propose_definition")
    assert tool["function"]["parameters"] == canonical


def test_definition_schema_factory_returns_independent_documents() -> None:
    first = propose_definition_schema()
    second = propose_definition_schema()
    first["properties"]["name"]["description"] = "changed"
    assert second["properties"]["name"]["description"] != "changed"


def test_structural_compiler_normalizes_model_shorthand_without_mutating_input() -> None:
    raw = {
        "reserve": {"recipe_inputs": True},
        "duration": {"mode": "recipe_duration"},
        "success": [{"result": "crafted"}],
        "failure": [{"result": "failed"}],
    }
    compiled, errors = compile_definition_shape("action", "Craft Token", raw)
    assert errors == []
    assert compiled["costs"]["reserve"]["recipe_inputs"] == 1
    assert compiled["success"] == {"result": "crafted"}
    assert compiled["failure"] == {"result": "failed"}
    assert "costs" not in raw
    assert isinstance(raw["success"], list)


def test_structural_compiler_is_the_schema_definition_type_source() -> None:
    schema_types = tuple(propose_definition_schema()["properties"]["definition_type"]["enum"])
    assert schema_types == DEFINITION_TYPES


def test_structural_compiler_rejects_unsupported_effect_and_missing_outcome_summary() -> None:
    action, action_errors = compile_definition_shape(
        "action",
        "Bad Effect",
        {
            "duration": {"minutes": 1},
            "effects": [{"type": "invented_mutation"}],
            "success": {"result": "done"},
            "failure": {"result": "failed"},
        },
    )
    assert action["duration"]["minutes"] == 1
    assert any("unsupported effect type" in error for error in action_errors)

    _, behavior_errors = compile_definition_shape(
        "behavior",
        "Incomplete Outcome",
        {"nodes": [{"type": "wait"}], "outcome_events": [{"event_type": "probe.completed"}]},
    )
    assert any("outcome_events[0]" in error for error in behavior_errors)

