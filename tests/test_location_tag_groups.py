"""Location tag groups come from the world's rules; the engine assumes no tag name."""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform
from sim2.world.navigation import (
    configured_location_tags,
    nearest_location_with_tag,
    nearest_safe_location,
)

STARTER_VILLAGE = Path(__file__).parents[1] / "worlds" / "starter_village"


class _Metadata:
    def __init__(self, rules: dict[str, Any]) -> None:
        self._values = {"rules": rules}

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)


class _Entities:
    def __init__(self, entities: dict[str, dict[str, Any]]) -> None:
        self._entities = entities

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        return self._entities[entity_id]


def _corridor(rules: dict[str, Any], *, b: dict[str, Any] | None = None, c: dict[str, Any] | None = None) -> Any:
    """Three locations in a row (a - b - c) behind the store interface the navigation helpers read."""

    places = {
        "a": {"id": "a", "entity_type": "location", "tags": [], "routes": ["b"]},
        "b": {"id": "b", "entity_type": "location", "tags": [], "routes": ["a", "c"], **(b or {})},
        "c": {"id": "c", "entity_type": "location", "tags": [], "routes": ["b"], **(c or {})},
    }
    return SimpleNamespace(repositories=SimpleNamespace(metadata=_Metadata(rules), entities=_Entities(places)))


def test_a_tag_group_the_world_does_not_declare_is_empty():
    for rules in ({}, {"location_tag_groups": None}, {"location_tag_groups": {}}, {"location_tag_groups": {"safe": ["haven"]}}):
        assert configured_location_tags(_corridor(rules), "health_service") == set()


def test_a_declared_tag_group_is_read_as_declared():
    store = _corridor({"location_tag_groups": {"safe": ["haven", 7], "health_service": "infirmary"}})
    assert configured_location_tags(store, "safe") == {"haven", "7"}
    assert configured_location_tags(store, "health_service") == {"infirmary"}


def test_a_health_service_is_found_only_through_the_declared_group():
    clinic = {"tags": ["health", "clinic"]}
    undeclared = _corridor({}, c=clinic)
    # A location tagged like a clinic is not a health service until the world says so.
    assert nearest_location_with_tag(undeclared, "a", configured_location_tags(undeclared, "health_service")) is None

    declared = _corridor({"location_tag_groups": {"health_service": ["clinic"]}}, c=clinic)
    assert nearest_location_with_tag(declared, "a", configured_location_tags(declared, "health_service")) == "c"
    assert nearest_location_with_tag(declared, "a", set()) is None

    renamed = _corridor({"location_tag_groups": {"health_service": ["infirmary"]}}, b={"tags": ["clinic"]}, c={"tags": ["infirmary"]})
    # The nearer location carries a tag the world did not declare, so it does not count.
    assert nearest_location_with_tag(renamed, "a", configured_location_tags(renamed, "health_service")) == "c"


def test_a_safe_location_by_tag_needs_the_declared_group_but_the_safe_flag_does_not():
    tagged = {"tags": ["safe"]}
    assert nearest_safe_location(_corridor({}, b=tagged), "a") is None
    assert nearest_safe_location(_corridor({"location_tag_groups": {"safe": ["safe"]}}, b=tagged), "a") == "b"

    renamed = _corridor({"location_tag_groups": {"safe": ["haven"]}}, b=tagged, c={"tags": ["haven"]})
    assert nearest_safe_location(renamed, "a") == "c"
    # The location's own ``safe`` flag is data on the location and needs no group.
    assert nearest_safe_location(_corridor({}, c={"safe": True}), "a") == "c"


def _seek_treatment(
    tmp_path: Path,
    name: str,
    edit_world: Callable[[Platform, dict[str, Any]], None],
    *,
    travel_only: bool = False,
) -> tuple[str, float, str | None]:
    """Edit the starter village and let one injured character seek treatment.

    Returns where she ends up, her health, and the target line her decision
    view showed for the behavior.  With ``travel_only`` the behavior keeps just
    its travel step, so the final location shows where the health service
    resolver sent her.
    """

    path = create_instance(STARTER_VILLAGE, name, tmp_path / "runtime")
    setup = Platform(path)
    rules = copy.deepcopy(setup.store.repositories.metadata.get("rules", {}) or {})
    edit_world(setup, rules)
    setup.store.repositories.metadata.set("rules", rules)
    if travel_only:
        behavior = copy.deepcopy(next(item for item in setup.store.repositories.entities.get_definitions("bt_def") if item["id"] == "seek_treatment"))
        behavior["nodes"] = [node for node in behavior["nodes"] if node.get("action_id") == "travel_to"]
        setup.store.repositories.entities.put_definition("bt_def", "seek_treatment", behavior)
    setup.store.connection.commit()
    setup.close()
    # The condition engines read the rules when the Platform opens (platform/runtime.py:39),
    # so the Platform that acts is opened after the edit.
    platform = Platform(path)
    try:
        qiao = platform.store.repositories.entities.get_agent("qiao_ning")
        qiao["initial_state"]["location_id"] = "village_square"
        qiao["initial_state"]["health"] = 70
        qiao["inventory"]["medicine"] = 1
        platform.store.repositories.entities.update_agent("qiao_ning", qiao)
        platform.store.connection.commit()
        offered = next((item for item in platform.decision_view("qiao_ning")["executable_behaviors"] if item["ref"] == "behavior:seek_treatment"), None)
        result = platform.submit_behavior(BehaviorIntent("qiao_ning", "behavior:seek_treatment", {}, {}, "Recover from an injury.", 0.9, f"{name}-intent"))
        if result["status"] == "accepted":
            for _ in range(int(result["remaining_minutes"])):
                platform.step()
        after = platform.store.repositories.entities.get_agent("qiao_ning")["initial_state"]
        return str(after["location_id"]), float(after["health"]), (offered or {}).get("target")
    finally:
        platform.close()


def _retag_clinic(platform: Platform, rules: dict[str, Any]) -> None:
    """The world calls its health service an infirmary: declare the tag and put it on the clinic."""

    rules["location_tag_groups"]["health_service"] = ["infirmary"]
    clinic = platform.store.repositories.entities.get_entity("clinic")
    clinic["tags"] = ["infirmary", "research"]
    platform.store.repositories.entities.put_entity("clinic", "location", str(clinic["name"]), clinic)


def test_treatment_follows_the_health_service_tag_the_world_declares(tmp_path: Path):
    location, health, target = _seek_treatment(tmp_path, "tag-group-renamed", _retag_clinic)
    assert (location, health) == ("clinic", 100)
    assert (target or "").startswith("Health service:")


def test_a_world_that_declares_no_health_service_sends_nobody_to_a_clinic_tagged_place(tmp_path: Path):
    def drop(platform: Platform, rules: dict[str, Any]) -> None:
        rules["location_tag_groups"].pop("health_service")

    # Control: with the group declared the view names the service and the travel step reaches it.
    location, _, target = _seek_treatment(tmp_path, "tag-group-declared", lambda platform, rules: None, travel_only=True)
    assert location == "clinic" and (target or "").startswith("Health service:")
    # The clinic still carries its ``health`` tag, but nothing declares it a health service:
    # the view names none and the character stays where she was.
    location, _, target = _seek_treatment(tmp_path, "tag-group-dropped", drop, travel_only=True)
    assert location == "village_square" and not (target or "").startswith("Health service:")
