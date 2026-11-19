"""Capability bindings: literals in a Behavior, parameters from an Agent."""

from __future__ import annotations

from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform
from sim2.world.bindings import resolve_behavior_effect
from sim2.world.verbs import specific_capability_target

WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"


ACTION_DEFS = {
    "withdraw_stock": {
        "id": "withdraw_stock",
        "parameters": {"resource_id": "string", "scope": "string"},
        "effects": [
            {
                "type": "resource_withdrawal",
                "resource_id": "context.resource_id",
                "amount": 1,
                "scope": "context.scope",
            }
        ],
    }
}


def test_literal_behavior_binding_resolves_without_agent_parameters() -> None:
    behavior = {
        "id": "withdraw_resource",
        "nodes": [
            {
                "type": "action",
                "action_id": "withdraw_stock",
                "parameters": {"resource_id": "energy_reserve", "scope": "energy_withdrawal"},
            }
        ],
    }

    effect = resolve_behavior_effect(behavior, "resource_withdrawal", ACTION_DEFS)

    assert effect == {
        "type": "resource_withdrawal",
        "resource_id": "energy_reserve",
        "amount": 1,
        "scope": "energy_withdrawal",
    }


def test_literal_binding_cannot_be_overridden_by_an_agent() -> None:
    behavior = {
        "id": "withdraw_resource",
        "nodes": [
            {
                "type": "action",
                "action_id": "withdraw_stock",
                "parameters": {"resource_id": "energy_reserve", "scope": "energy_withdrawal"},
            }
        ],
    }

    effect = resolve_behavior_effect(
        behavior,
        "resource_withdrawal",
        ACTION_DEFS,
        parameters={"resource_id": "water_reserve", "scope": "water_withdrawal"},
    )

    assert effect["resource_id"] == "energy_reserve"
    assert effect["scope"] == "energy_withdrawal"


def test_context_binding_resolves_from_agent_parameters() -> None:
    behavior = {
        "id": "withdraw_resource",
        "agent_parameters": {"resource_id": {"type": "string", "required": True}},
        "nodes": [
            {
                "type": "action",
                "action_id": "withdraw_stock",
                "parameters": {"resource_id": "context.resource_id", "scope": "resource_withdrawal"},
            }
        ],
    }

    effect = resolve_behavior_effect(
        behavior,
        "resource_withdrawal",
        ACTION_DEFS,
        parameters={"resource_id": "coolant_reserve"},
    )

    assert effect["resource_id"] == "coolant_reserve"
    assert effect["scope"] == "resource_withdrawal"


def test_nested_behavior_binding_reaches_the_action() -> None:
    behavior_defs = {
        "withdraw_resource": {
            "id": "withdraw_resource",
            "agent_parameters": {"resource_id": {"type": "string", "required": True}},
            "nodes": [
                {
                    "type": "action",
                    "action_id": "withdraw_stock",
                    "parameters": {"resource_id": "context.resource_id", "scope": "resource_withdrawal"},
                }
            ],
        }
    }
    parent = {
        "id": "get_fuel",
        "nodes": [
            {
                "type": "behavior",
                "behavior_id": "withdraw_resource",
                "parameters": {"resource_id": "fuel_reserve"},
            }
        ],
    }

    effect = resolve_behavior_effect(
        parent,
        "resource_withdrawal",
        ACTION_DEFS,
        behavior_defs,
        parameters={"resource_id": "water_reserve"},
    )

    assert effect["resource_id"] == "fuel_reserve"


def test_literal_stock_reserve_is_a_target_specific_transfer() -> None:
    definition = {
        "verb": "transfer_resource",
        "parameters": {},
        "costs": {"reserve": {"village_food": 1}},
        "effects": [{"type": "inventory_add", "item": "rations", "quantity": 1}],
    }
    assert specific_capability_target(definition) == ("transfer_resource", "village_food")

    generic = {
        "verb": "transfer_resource",
        "parameters": {"resource_id": "string"},
        "costs": {"reserve": {"resource_parameter": "resource_id", "resource_amount": 1}},
        "effects": [{"type": "inventory_add", "item": "rations", "quantity": 1}],
    }
    assert specific_capability_target(generic) is None


def _specific_withdrawal(target: str, *, name: str = "Withdraw Cistern Water") -> dict:
    return {
        "definition_type": "action",
        "name": name,
        "definition": {
            "summary": "Withdraw one unit from a named stock.",
            "parameters": {},
            "duration": {"minutes": 1},
            "effects": [{
                "type": "resource_withdrawal",
                "resource_id": target,
                "amount": 1,
                "scope": "resource_withdrawal",
            }],
            "success": {"condition": "actor.alive", "result": "resource_withdrawn"},
            "failure": {"condition": "actor.incapacitated", "result": "withdrawal_failed"},
        },
    }


def test_a_specific_transfer_is_rejected_even_without_a_generic(tmp_path: Path) -> None:
    """The first target-specific Action is not a bootstrap exception."""

    platform = Platform(create_instance(WORLD, "specific-transfer", tmp_path / "runtime"))
    try:
        rejected = platform.submit_definition_proposal("god", _specific_withdrawal("cistern_water"))
        assert rejected["status"] == "rejected", rejected
        assert any("must bind its target" in str(item) for item in rejected.get("errors") or []), rejected
    finally:
        platform.close()


def test_a_generic_transfer_is_accepted_and_binds_at_runtime(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "generic-transfer", tmp_path / "runtime"))
    try:
        generic = {
            "definition_type": "action",
            "name": "Withdraw Stored Resource",
            "definition": {
                "summary": "Withdraw one unit of a visible shared resource.",
                "parameters": {"resource_id": "string"},
                "duration": {"minutes": 1},
                "effects": [{
                    "type": "resource_withdrawal",
                    "resource_id": "context.resource_id",
                    "amount": 1,
                    "scope": "resource_withdrawal",
                }],
                "success": {"condition": "actor.alive", "result": "resource_withdrawn"},
                "failure": {"condition": "actor.incapacitated", "result": "withdrawal_failed"},
            },
            "followup_behavior": {
                "name": "Withdraw Stored Resource Behavior",
                "assign_to": ["mei_lan"],
                "definition": {
                    "agent_label": "Withdraw Stored Resource",
                    "entry_conditions": ["actor.alive"],
                    "estimated_minutes": 1,
                    "agent_parameters": {
                        "resource_id": {"type": "string", "required": True, "max_length": 120},
                    },
                    "parameter_bindings": {"resource_location_id": "resource_location"},
                    "nodes": [
                        {
                            "type": "action",
                            "action_id": "travel_to",
                            "parameters": {"location_id": "context.resource_location_id"},
                        },
                        {
                            "type": "action",
                            "action_id": "withdraw_stored_resource",
                            "parameters": {"resource_id": "context.resource_id"},
                        },
                    ],
                    "success_results": ["resource_withdrawn"],
                    "failure_results": ["withdrawal_failed"],
                },
            },
        }
        accepted = platform.submit_definition_proposal("god", generic)
        assert accepted["status"] == "accepted", accepted
        action = next(
            item for item in platform.store.repositories.entities.get_definitions("action_def")
            if item.get("id") == "withdraw_stored_resource"
        )
        assert action["verb"] == "transfer_resource"
    finally:
        platform.close()


def test_travel_node_derives_a_stock_location_from_the_agent_resource_binding() -> None:
    from sim2.world.bindings import iter_behavior_effects

    behavior = {
        "id": "withdraw_resource",
        "agent_parameters": {"resource_id": {"type": "string", "required": True}},
        "parameter_bindings": {"resource_location_id": "resource_location"},
        "nodes": [
            {
                "type": "action",
                "action_id": "travel_to",
                "parameters": {"location_id": "context.resource_location_id"},
            },
            {
                "type": "action",
                "action_id": "withdraw_stock",
                "parameters": {"resource_id": "context.resource_id", "scope": "resource_withdrawal"},
            },
        ],
    }
    action_defs = {
        **ACTION_DEFS,
        "travel_to": {
            "id": "travel_to",
            "parameters": {"location_id": "string"},
            "effects": [{"type": "location_set", "location_id": "context.location_id"}],
        },
    }

    effects = list(
        iter_behavior_effects(
            behavior,
            action_defs,
            parameters={"resource_id": "water_reserve", "resource_location_id": "water_plant"},
        )
    )

    assert effects[0][1]["location_id"] == "water_plant"
    assert effects[1][1]["resource_id"] == "water_reserve"
