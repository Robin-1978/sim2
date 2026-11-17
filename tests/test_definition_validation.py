from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sim2.bootstrap.service import create_instance
from sim2.platform.definition_validation import (
    BehaviorNodeNormalizer,
    _check_dynamic_inventory_parameters,
    _check_resource_transfers,
)
from sim2.platform.runtime import Platform

WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
REGISTRY_TABLES = ("action_def", "bt_def", "recipe_def", "intent", "event_log", "agent_state", "evolution_task", "instance_meta")


def transfer_action(**overrides: Any) -> dict[str, Any]:
    action: dict[str, Any] = {
        "parameters": {"item_id": "string", "quantity": "number"},
        "costs": {"reserve": {"item_parameter": "item_id", "item_quantity_parameter": "quantity"}},
        "effects": [
            {
                "type": "resource_transfer",
                "resource_id": "water",
                "source_item_parameter": "item_id",
                "quantity_parameter": "quantity",
            }
        ],
        "success": {"result": "moved"},
    }
    action.update(overrides)
    return action


def test_outcome_condition_cannot_read_a_dynamic_inventory_parameter() -> None:
    errors: list[str] = []
    _check_dynamic_inventory_parameters(
        {
            "parameters": {"item_id": "string"},
            "success": {"condition": "actor.inventory.item_id > 0"},
            "failure": {"condition": "actor.inventory.rations > 0"},
        },
        errors,
    )

    assert errors == [
        "action.success.condition treats dynamic parameter(s) item_id as inventory keys; "
        "use a literal visible item or omit the condition"
    ]


def test_resource_transfer_that_matches_its_reservation_is_valid() -> None:
    errors: list[str] = []
    _check_resource_transfers(transfer_action(), errors)
    assert errors == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"costs": {"reserve": {}}}, "resource_transfer requires costs.reserve.item_parameter so the transfer is conserved"),
        (
            {"costs": {"reserve": {"item_parameter": "missing"}}},
            "resource_transfer item_parameter 'missing' must be declared in action.parameters",
        ),
        (
            {"success": {"condition": "actor.inventory.water > 0"}},
            "resource_transfer success condition cannot inspect actor.inventory after reservation; "
            "use actor.alive or an observable world/target result",
        ),
        (
            {
                "effects": [
                    {
                        "type": "resource_transfer",
                        "resource_id": "water",
                        "source_item_parameter": "item_id",
                        "quantity_parameter": "other",
                    }
                ]
            },
            "resource_transfer.quantity_parameter must match costs.reserve.item_quantity_parameter",
        ),
        (
            {
                "costs": {"reserve": {"item_parameter": "item_id"}},
                "effects": [
                    {
                        "type": "resource_transfer",
                        "resource_id": "water",
                        "source_item_parameter": "item_id",
                        "quantity": 2,
                    }
                ],
            },
            "resource_transfer needs quantity 1 by default, or declare item_quantity_parameter for a variable amount",
        ),
    ],
)
def test_resource_transfer_must_move_what_the_action_reserved(overrides: dict[str, Any], expected: str) -> None:
    errors: list[str] = []
    _check_resource_transfers(transfer_action(**overrides), errors)
    assert expected in errors


def test_behavior_node_budget_is_shared_by_the_whole_walk() -> None:
    errors: list[str] = []
    tree = [{"type": "sequence", "children": [{"type": "wait"}] * 40}]

    normalized = BehaviorNodeNormalizer(None, errors, None).normalize_tree(tree)

    assert errors == ["a behavior may contain at most 32 nodes"]
    assert len(normalized[0]["children"]) == 31


def test_behavior_nesting_is_limited_to_eight_levels() -> None:
    node: dict[str, Any] = {"type": "wait"}
    for _ in range(10):
        node = {"type": "sequence", "children": [node]}
    errors: list[str] = []

    BehaviorNodeNormalizer(None, errors, None).normalize_tree([node])

    assert errors == ["behavior node 0.0.0.0.0.0.0.0.0 exceeds maximum nesting depth"]


def test_unsupported_node_types_and_shapes_are_reported_by_path() -> None:
    errors: list[str] = []

    normalized = BehaviorNodeNormalizer(None, errors, None).normalize_tree([{"type": "teleport"}, 5])

    assert errors == ["unsupported behavior node type: teleport", "behavior node 1 must be an object"]
    assert [node["type"] for node in normalized] == ["teleport"]


def registry_snapshot(platform: Platform) -> dict[str, list[tuple[Any, ...]]]:
    connection = platform.store.connection
    return {
        table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()]
        for table in REGISTRY_TABLES
    }


def test_unexecutable_followup_rolls_back_the_whole_registration(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "definition-rollback", tmp_path / "runtime"))
    try:
        before = registry_snapshot(platform)
        proposal = {
            "definition_type": "action",
            "name": "Craft Token",
            "definition": {
                "parameters": {"recipe_id": "string"},
                "duration": {"mode": "recipe_duration"},
                "costs": {"reserve": {"recipe_inputs": 1}},
                "success": {"condition": "recipe.crafted", "result": "token_crafted"},
                "failure": {"condition": "recipe.failed", "result": "craft_failed"},
            },
            "followup_recipe": {
                "name": "Token Recipe",
                "definition": {
                    "duration_minutes": 1,
                    "inputs": [{"item": "灯芯芦苇", "quantity": 1}],
                    "outputs": [{"item": "test_token", "quantity": 1}],
                },
            },
            "followup_behavior": {
                "name": "Craft Token Behavior",
                "assign_to": ["梅岚"],
                "definition": {
                    "summary": "Craft one token.",
                    "entry_conditions": ["actor.alive"],
                    "nodes": [{"type": "action", "action_id": "Craft Token"}],
                    "success_results": ["token_crafted"],
                    "failure_results": ["craft_failed"],
                },
            },
        }

        with pytest.raises(ValueError, match="Action followup behavior is not executable"):
            platform.submit_definition_proposal("god", proposal)

        assert registry_snapshot(platform) == before
    finally:
        platform.close()


def test_registered_behavior_is_unlocked_and_assigned_to_each_named_character(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "definition-behavior-assignment", tmp_path / "runtime"))
    try:
        result = platform.submit_definition_proposal(
            "god",
            {
                "definition_type": "behavior",
                "name": "Walk The Old Road",
                "assign_to": ["梅岚"],
                "definition": {
                    "summary": "Walk to the old road.",
                    "entry_conditions": ["actor.alive"],
                    "nodes": [{"type": "action", "action_id": "Travel To", "parameters": {"location_id": "旧商路"}}],
                    "success_results": ["arrived"],
                    "failure_results": ["blocked"],
                },
            },
        )

        assert result["status"] == "accepted", result
        assert result["assigned_to"] == ["梅岚"]
        repositories = platform.store.repositories
        behavior = next(item for item in repositories.entities.get_definitions("bt_def") if item.get("agent_label") == "Walk The Old Road")
        agent = repositories.entities.get_agent("mei_lan")
        assert behavior["id"] in agent["unlocked_bt_ids"]
        assignment = agent["behavior_assignments"][-1]
        assert assignment["behavior_id"] == behavior["id"]
        assert assignment["status"] == "assigned"
        assert assignment["source_event_id"] == result["event_id"]
        assert any(
            item.get("actor_id") == "mei_lan" and item.get("task_type") == f"behavior_assigned:{behavior['id']}"
            for item in platform.ready_evolution_tasks()
        )
    finally:
        platform.close()
