from __future__ import annotations

from sim2.agents.tool_registry import tool_schemas_for_role
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

