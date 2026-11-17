from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform

ROOT = Path(__file__).parents[1]
PROPOSAL_TABLES = ("intent", "event_log", "entity", "agent_state", "knowledge_fact", "knowledge_gap", "memory_item", "instance_meta")
STOCKED_RESOURCE = "God cannot create a stocked resource directly; a behavior result must create or recover the supply"
NAME_LABEL = "name must be a short visible label without markup or embedded parameter text"
DUPLICATE_WATER = "an entity with visible name clean_water already exists (resource:clean_water); use it instead of creating a duplicate"


@pytest.fixture
def platform(tmp_path: Path):
    instance = Platform(create_instance(ROOT / "worlds" / "starter_village", "entity-proposal", tmp_path / "runtime"))
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def town(tmp_path: Path):
    instance = Platform(create_instance(ROOT / "worlds" / "three_lanterns_town", "entity-proposal-town", tmp_path / "runtime"))
    try:
        yield instance
    finally:
        instance.close()


def snapshot(platform: Platform) -> dict[str, list[tuple[Any, ...]]]:
    connection = platform.store.connection
    return {
        table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()]
        for table in PROPOSAL_TABLES
    }


def last_intent(platform: Platform) -> dict[str, Any]:
    row = platform.store.connection.execute("SELECT status,created_minute,data_json,result_json FROM intent ORDER BY rowid DESC LIMIT 1").fetchone()
    return {"status": row["status"], "created_minute": row["created_minute"], "data": json.loads(row["data_json"]), "result": json.loads(row["result_json"])}


def entity(platform: Platform, entity_id: str) -> dict[str, Any]:
    return platform.store.repositories.entities.get_entity(entity_id)


def set_rules(platform: Platform, rules: dict[str, Any]) -> None:
    with platform.store.transaction():
        platform.store.repositories.metadata.set("rules", rules)


def rules_of(platform: Platform) -> dict[str, Any]:
    return dict(platform.store.repositories.metadata.get("rules", {}) or {})


def location_proposal(name: str = "北渡口", **state: Any) -> dict[str, Any]:
    return {"entity_type": "location", "name": name, "description": "A ford north of the bridge.", "state": state}


def agent_proposal(**state: Any) -> dict[str, Any]:
    complete = {
        "role": "courier",
        "ultimate_goal": "Carry word between the gate and the square.",
        "initial_state": {"location_id": "village_square"},
        "initial_bt_ids": ["communicate"],
    }
    complete.update(state)
    return {"entity_type": "agent", "name": "苏河", "description": "A courier.", "state": complete}


def research_proposal(name: str, **state: Any) -> dict[str, Any]:
    return {
        "entity_type": "document",
        "entity_id": "proposed_marker",
        "name": name,
        "description": "A marker introduced after the opening story.",
        "location_id": "north_watch",
        "state": {
            "location_id": "north_watch",
            "research_assignees": ["罗杉"],
            "research_discoveries": [{"id": "fact:proposed_marker:one", "fact": "The marker is reliable.", "required": 1}],
            **state,
        },
    }


def test_every_failing_step_reports_its_error_in_the_order_the_steps_run(platform: Platform) -> None:
    proposal = {"name": "<b>Archive</b>", "entity_id": "river_bridge", "location_id": "no_such_place", "state": {"research_required": 0}}

    result = platform.submit_entity_proposal("operator", proposal)

    assert result == {
        "status": "rejected",
        "message": "Entity proposal failed validation.",
        "failed_check": "entity_schema",
        "errors": [
            "entity_type is required",
            NAME_LABEL,
            "location_id does not exist",
            "research_required must be a positive number",
            "entity_id already exists",
        ],
        "retryable": True,
    }
    stored = last_intent(platform)
    assert stored["status"] == "rejected" and stored["result"] == result
    assert stored["data"] == {**proposal, "entity_id": "river_bridge", "actor_id": "operator"}  # the proposal as received


@pytest.mark.parametrize("name", ["x" * 121, "two\nlines", "carriage\rreturn", "<b>bold</b>", "a > b"])
def test_visible_name_must_be_a_short_label_without_markup(platform: Platform, name: str) -> None:
    result = platform.submit_entity_proposal("operator", {"entity_type": "object", "name": name})

    assert result["errors"] == [NAME_LABEL]


def test_visible_name_may_use_all_120_characters(platform: Platform) -> None:
    assert platform.submit_entity_proposal("operator", {"entity_type": "object", "name": "y" * 120})["status"] == "accepted"
    assert platform.submit_entity_proposal("operator", {"entity_type": "object", "name": ""})["errors"] == ["name is required"]


@pytest.mark.parametrize("name", ["clean_water", "  CLEAN_Water ", "Clean _ Water", "clean\u00a0_water"])
def test_visible_names_are_unique_ignoring_case_and_spacing(platform: Platform, name: str) -> None:
    result = platform.submit_entity_proposal("operator", {"entity_type": "object", "name": name})

    assert result["failed_check"] == "entity_name_duplicate"
    assert result["errors"] == [DUPLICATE_WATER]


def test_the_most_specific_failed_check_names_the_rejection(platform: Platform) -> None:
    stocked = {"entity_type": "resource", "name": "银矿", "state": {"quantity": 5}}

    duplicate = platform.submit_entity_proposal("god", {**stocked, "name": "clean_water"})
    taken_id = platform.submit_entity_proposal("god", {**stocked, "entity_id": "river_bridge"})

    assert duplicate["failed_check"] == "entity_name_duplicate"
    assert duplicate["errors"] == [DUPLICATE_WATER, STOCKED_RESOURCE]
    assert taken_id["failed_check"] == "god_resource_creation"
    assert taken_id["errors"] == [STOCKED_RESOURCE, "entity_id already exists"]


def test_location_routes_are_stored_by_canonical_id_and_joined_to_the_neighbors(platform: Platform) -> None:
    result = platform.submit_entity_proposal("god", location_proposal(
        routes=["河桥", "river_bridge", "村广场", ""],
        route_distances={"河桥": 2, "村广场": 4.5},
        tags=["ford"],
        public=True,
        safe=False,
        discoveries=["a worn cairn"],
        map_position=[3, 4],
        region_id="north",
        condition="misty",
    ))

    assert result["status"] == "accepted", result
    ford = entity(platform, result["entity_id"])
    assert ford["routes"] == ["river_bridge", "village_square"]
    assert ford["route_distances"] == {"river_bridge": 2, "village_square": 4.5}
    assert (ford["tags"], ford["public"], ford["safe"], ford["discoveries"], ford["map_position"], ford["region_id"]) == (["ford"], True, False, ["a worn cairn"], [3, 4], "north")
    assert ford["initial_state"] == {"condition": "misty"}  # only mutable state stays in initial_state
    assert entity(platform, "river_bridge")["routes"][-1] == result["entity_id"]
    assert entity(platform, "river_bridge")["route_distances"][result["entity_id"]] == 2.0
    assert entity(platform, "village_square")["route_distances"][result["entity_id"]] == 4.5
    assert last_intent(platform)["data"]["state"]["routes"] == ["河桥", "river_bridge", "村广场", ""]  # the intent keeps the proposal as received


def test_a_neighbor_without_a_declared_distance_gets_one(platform: Platform) -> None:
    result = platform.submit_entity_proposal("god", location_proposal(routes=["river_bridge", "village_square"], route_distances={"river_bridge": 7}))

    assert result["status"] == "accepted", result
    assert entity(platform, "river_bridge")["route_distances"][result["entity_id"]] == 7.0
    assert entity(platform, "village_square")["route_distances"][result["entity_id"]] == 1.0


def test_neighbors_store_the_distance_back_as_a_float(platform: Platform) -> None:
    god = platform.submit_entity_proposal("god", location_proposal(routes=["river_bridge"], route_distances={"river_bridge": 7}))
    operator = platform.submit_entity_proposal("operator", location_proposal("西渡口", routes=["village_square"], route_distances={"village_square": "5"}))

    assert type(entity(platform, "river_bridge")["route_distances"][god["entity_id"]]) is float
    assert entity(platform, "village_square")["route_distances"][operator["entity_id"]] == 5.0
    assert type(entity(platform, "village_square")["route_distances"][operator["entity_id"]]) is float


def test_a_neighbor_that_already_routes_to_the_new_place_keeps_one_route(platform: Platform) -> None:
    bridge = entity(platform, "river_bridge")
    stored = {key: value for key, value in bridge.items() if key not in {"id", "entity_type", "name"}}
    with platform.store.transaction():
        platform.store.repositories.entities.put_entity("river_bridge", "location", bridge["name"], {**stored, "routes": [*bridge["routes"], "future_ford"]})

    result = platform.submit_entity_proposal("god", {**location_proposal("新渡口", routes=["river_bridge"]), "entity_id": "future_ford"})

    assert result["status"] == "accepted", result
    assert entity(platform, "river_bridge")["routes"].count("future_ford") == 1


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ({}, "a dynamic location requires at least one existing route neighbor"),
        ({"routes": []}, "a dynamic location requires at least one existing route neighbor"),
        ({"routes": "river_bridge"}, "a dynamic location requires at least one existing route neighbor"),
        ({"routes": ["no_such_place"]}, "location routes reference unknown location no_such_place"),
        ({"routes": ["lin_bo"]}, "location route lin_bo is not a location"),
        ({"routes": ["river_bridge"], "route_distances": [1]}, "location route_distances must be an object"),
        ({"routes": ["river_bridge"], "route_distances": {"village_square": 1}}, "location route distance references non-route village_square"),
        ({"routes": ["river_bridge"], "route_distances": {"river_bridge": 0}}, "location route distance for river_bridge must be positive"),
        ({"routes": ["river_bridge"], "route_distances": {"river_bridge": True}}, "location route distance for river_bridge must be positive"),
        ({"routes": ["river_bridge"], "route_distances": {"river_bridge": "far"}}, "location route distance for river_bridge must be positive"),
    ],
)
def test_god_location_needs_real_route_neighbors_and_positive_distances(platform: Platform, state: dict[str, Any], message: str) -> None:
    result = platform.submit_entity_proposal("god", location_proposal(**state))

    assert result["failed_check"] == "entity_schema"
    assert result["errors"] == [message]


def test_only_god_must_connect_a_new_location(platform: Platform) -> None:
    result = platform.submit_entity_proposal("operator", location_proposal("孤岛", tags=["public"]))

    assert result["status"] == "accepted", result


@pytest.mark.parametrize("field", ["quantity", "amount"])
def test_god_cannot_stock_a_resource_but_may_create_an_empty_node(platform: Platform, field: str) -> None:
    stocked = platform.submit_entity_proposal("god", {"entity_type": "resource", "name": "银矿", "state": {field: 5}})
    empty = platform.submit_entity_proposal("god", {"entity_type": "resource", "name": "空银矿", "state": {field: 0}})
    delivered = platform.submit_entity_proposal("operator", {"entity_type": "resource", "name": "运来的银矿", "state": {field: 5}})

    assert stocked["failed_check"] == "god_resource_creation"
    assert stocked["errors"] == [STOCKED_RESOURCE]
    assert empty["status"] == "accepted" and delivered["status"] == "accepted"


def test_quantity_takes_precedence_over_amount(platform: Platform) -> None:
    result = platform.submit_entity_proposal("god", {"entity_type": "material", "name": "空银矿", "state": {"quantity": 0, "amount": 5}})

    assert result["status"] == "accepted", result


@pytest.mark.parametrize(
    ("configured", "limited", "free"),
    [
        (["document"], "document", "resource"),
        ("missing", "material", "document"),
        (None, None, "resource"),
        ([], None, "material"),
    ],
)
def test_the_stocked_resource_limit_follows_the_configured_entity_types(platform: Platform, configured: Any, limited: str | None, free: str) -> None:
    rules = rules_of(platform)
    if configured == "missing":
        rules.pop("resource_entity_types")
    else:
        rules["resource_entity_types"] = configured
    set_rules(platform, rules)

    if limited is not None:
        rejected = platform.submit_entity_proposal("god", {"entity_type": limited, "name": "受限品", "state": {"quantity": 5}})
        assert rejected["failed_check"] == "god_resource_creation"
    accepted = platform.submit_entity_proposal("god", {"entity_type": free, "name": "自由品", "state": {"quantity": 5}})
    assert accepted["status"] == "accepted", accepted


def test_location_id_must_exist_and_wins_over_the_state_location(platform: Platform) -> None:
    missing = platform.submit_entity_proposal("operator", {"entity_type": "object", "name": "界石", "location_id": "no_such_place"})
    placed = platform.submit_entity_proposal("operator", {"entity_type": "object", "name": "界石", "location_id": "village_square", "state": {"condition": "worn", "location_id": "inn"}})

    assert missing["errors"] == ["location_id does not exist"]
    assert entity(platform, placed["entity_id"])["initial_state"] == {"condition": "worn", "location_id": "village_square"}


@pytest.mark.parametrize(
    ("entity_type", "state", "errors"),
    [
        ("research_source", {}, ["a research_source must declare at least one research_discoveries entry"]),
        (
            "research_source",
            {"research_discoveries": {}},
            [
                "a research_source must declare at least one research_discoveries entry",
                "research_discoveries[0] must declare an id or fact",
                "a dynamic research source must declare at least one research_assignee",
            ],
        ),
        (
            "document",
            {"research_discoveries": []},
            ["research_discoveries must be a non-empty list or object", "a dynamic research source must declare at least one research_assignee"],
        ),
        (
            "document",
            {"research_discoveries": "text"},
            ["research_discoveries must be a non-empty list or object", "a dynamic research source must declare at least one research_assignee"],
        ),
        (
            "document",
            {"research_discoveries": [{"fact": "  "}, {}], "research_assignees": ["林泊"]},
            ["research_discoveries[0] must declare an id or fact", "research_discoveries[1] must declare an id or fact"],
        ),
    ],
)
def test_research_discoveries_and_assignees_are_required_before_anything_is_created(platform: Platform, entity_type: str, state: dict[str, Any], errors: list[str]) -> None:
    before = snapshot(platform)

    result = platform.submit_entity_proposal("operator", {"entity_type": entity_type, "name": "研究页", "state": state})

    assert result["failed_check"] == "entity_schema"
    assert result["errors"] == errors
    assert {table: rows for table, rows in snapshot(platform).items() if table in {"entity", "knowledge_gap"}} == {table: rows for table, rows in before.items() if table in {"entity", "knowledge_gap"}}


@pytest.mark.parametrize("value", [0, 0.5, -1, True, "2", [1]])
def test_research_required_must_be_a_positive_number(platform: Platform, value: Any) -> None:
    result = platform.submit_entity_proposal("operator", {"entity_type": "object", "name": "研究页", "state": {"research_required": value}})

    assert result["errors"] == ["research_required must be a positive number"]


@pytest.mark.parametrize("value", [1, 2.5])
def test_research_required_of_one_or_more_is_kept(platform: Platform, value: Any) -> None:
    result = platform.submit_entity_proposal("operator", {"entity_type": "object", "name": "研究页", "state": {"research_required": value}})

    assert entity(platform, result["entity_id"])["initial_state"]["research_required"] == value


@pytest.mark.parametrize(
    ("assignees", "errors"),
    [
        ("不存在的人", ["unknown character in research_assignees: 不存在的人"]),
        (["林泊", "不存在的人"], ["unknown character in research_assignees: 不存在的人"]),
        ({"name": "林泊"}, ["research_assignees must be a character name or a list of character names"]),
        ([None], ["unknown character in research_assignees: None"]),
    ],
)
def test_research_assignees_must_name_known_characters(platform: Platform, assignees: Any, errors: list[str]) -> None:
    state = {"research_discoveries": [{"id": "fact:zzqx", "fact": "zzqx unique finding"}], "research_assignees": assignees}

    result = platform.submit_entity_proposal("operator", {"entity_type": "document", "name": "研究页", "state": state})

    assert result["errors"] == errors


def test_every_failing_agent_check_is_reported_in_the_order_it_runs(platform: Platform) -> None:
    result = platform.submit_entity_proposal("god", agent_proposal(
        role="",
        ultimate_goal="",
        initial_state={},
        capabilities={"telepathy": 0.5, "research": 2},
        initial_bt_ids=[],
        faction_ids=["ghost faction", "river_bridge"],
    ))

    assert result["failed_check"] == "agent_schema"
    assert result["message"] == "Agent proposal failed validation."
    assert result["errors"] == [
        "agent.state.role is required",
        "agent.state.ultimate_goal is required",
        "agent.state.initial_state.location_id must name an existing location",
        "agent.state.capabilities references unknown capabilities: telepathy",
        "agent capability research must be between 0 and 1",
        "agent.state.initial_bt_ids must include at least one existing behavior",
        "unknown faction: ghost faction",
        "river_bridge is not a faction",
    ]
    assert last_intent(platform)["status"] == "rejected"


@pytest.mark.parametrize("value", [1.5, -0.1, True, "0.5", None])
def test_agent_capabilities_must_be_numbers_between_zero_and_one(platform: Platform, value: Any) -> None:
    result = platform.submit_entity_proposal("god", agent_proposal(capabilities={"research": value}))

    assert result["errors"] == ["agent capability research must be between 0 and 1"]


def test_agent_location_must_be_a_location(platform: Platform) -> None:
    result = platform.submit_entity_proposal("god", agent_proposal(initial_state={"location_id": "lin_bo"}))

    assert result["errors"] == ["lin_bo is not a location"]


def test_the_proposal_location_names_the_agent_location(platform: Platform) -> None:
    proposal = agent_proposal(initial_state={})
    proposal["location_id"] = "village_square"

    result = platform.submit_entity_proposal("god", proposal)

    assert result["status"] == "accepted", result
    assert platform.store.repositories.entities.get_agent(result["entity_id"])["initial_state"]["location_id"] == "village_square"


def test_agent_state_is_completed_from_the_configured_defaults(platform: Platform) -> None:
    result = platform.submit_entity_proposal("god", agent_proposal(
        initial_state={"location_id": "村广场", "health": 150},
        needs={"satiety": 30},
        capabilities={"research": 0.7},
        initial_bt_ids=["communicate", "Discuss concrete terms with another character.", "Negotiate", "negotiate", "communicate"],
        faction_ids=["村务会"],
        inventory={"rations": 2},
    ))

    assert result["status"] == "accepted", result
    agent = platform.store.repositories.entities.get_agent(result["entity_id"])
    assert (agent["name"], agent["role"]) == ("苏河", "courier")
    assert agent["initial_state"]["location_id"] == "village_square"
    assert agent["initial_state"]["health"] == 100  # clamped to the configured attribute range
    assert (agent["initial_state"]["status"], agent["initial_state"]["injuries"], agent["initial_state"]["action_progress"]) == ("idle", [], 0.0)
    assert agent["needs"] == {"belonging": 60.0, "esteem": 60.0, "safety": 70.0, "satiety": 30.0, "self_actualization": 40.0}
    assert agent["capabilities"]["research"] == 0.7 and agent["capabilities"]["combat"] == 0.0
    assert agent["initial_bt_ids"] == ["communicate", "negotiate"]  # labels resolve to ids, repeats are dropped
    assert agent["unlocked_bt_ids"] == [] and agent["faction_ids"] == ["village_council"]
    assert agent["inventory"] == {"rations": 2} and agent["traits"] == {} and agent["story_goals"] == []


def test_agent_knowledge_seed_becomes_knowledge_facts(platform: Platform) -> None:
    result = platform.submit_entity_proposal("god", agent_proposal(knowledge_seed=[
        {"id": "k_a", "fact": "The bridge is old.", "source_type": "rumor", "confidence": 0.4, "claim_status": "hearsay"},
        {"fact_key": "k_b", "summary": "Summary becomes the fact."},
        {"id": "k_c"},
        {"fact": "an entry without an id is skipped"},
        "not an object",
    ]))

    assert result["status"] == "accepted", result
    rows = platform.store.connection.execute("SELECT fact_key,data_json FROM knowledge_fact WHERE agent_id=? ORDER BY fact_key", (result["entity_id"],)).fetchall()
    assert [(row["fact_key"], json.loads(row["data_json"])) for row in rows] == [
        ("k_a", {"fact": "The bridge is old.", "source_type": "rumor", "confidence": 0.4, "claim_status": "hearsay"}),
        ("k_b", {"fact": "Summary becomes the fact.", "source_type": "god_created", "confidence": 1.0, "claim_status": "observed"}),
        ("k_c", {"fact": "k_c", "source_type": "god_created", "confidence": 1.0, "claim_status": "observed"}),
    ]


def test_a_knowledge_seed_that_fails_midway_rolls_the_whole_creation_back(platform: Platform) -> None:
    before = snapshot(platform)

    with pytest.raises(ValueError):
        platform.submit_entity_proposal("god", agent_proposal(knowledge_seed=[
            {"id": "k_ok", "fact": "This fact was written first."},
            {"id": "k_bad", "fact": "This one has no usable confidence.", "confidence": "abc"},
        ]))

    assert snapshot(platform) == before


def test_accepted_proposal_is_recorded_as_intent_event_and_god_memory(platform: Platform) -> None:
    for _ in range(3):
        platform.step()

    god = platform.submit_entity_proposal("god", {"entity_type": "object", "name": "界石", "description": "A marker stone."})
    operator = platform.submit_entity_proposal("operator", {"entity_type": "object", "name": "界石二号", "description": "Another marker."})

    assert set(god) == {"status", "intent_id", "entity_id", "event_id", "applied"}
    assert god["status"] == "accepted" and god["applied"] is True
    connection = platform.store.connection
    intent = connection.execute("SELECT status,created_minute,data_json,result_json FROM intent WHERE intent_id=?", (god["intent_id"],)).fetchone()
    assert (intent["status"], intent["created_minute"], json.loads(intent["result_json"])) == ("accepted", 3, god)
    assert json.loads(intent["data_json"]) == {"entity_type": "object", "name": "界石", "description": "A marker stone.", "entity_id": god["entity_id"], "actor_id": "god"}
    event = connection.execute("SELECT event_type,actor_id,world_minute,data_json FROM event_log WHERE event_id=?", (god["event_id"],)).fetchone()
    payload = json.loads(event["data_json"])
    assert (event["event_type"], event["actor_id"], event["world_minute"]) == ("entity.created", "god", 3)
    assert {key: payload[key] for key in ("intent_id", "entity_id", "entity_type", "summary")} == {
        "intent_id": god["intent_id"], "entity_id": god["entity_id"], "entity_type": "object", "summary": "A marker stone.",
    }
    memories = [json.loads(row["data_json"]) for row in connection.execute("SELECT data_json FROM memory_item WHERE agent_id='god' AND memory_type='world_entity'").fetchall()]
    assert [(item["entity_id"], item["summary"], item["status"]) for item in memories] == [(god["entity_id"], "Created object: 界石.", "accepted")]  # an operator proposal leaves no God memory
    assert operator["status"] == "accepted"


def test_research_novelty_compares_sources_at_the_same_place_only(town: Platform) -> None:
    assert town.submit_entity_proposal("god", research_proposal("第一份记录"))["status"] == "accepted"
    elsewhere = research_proposal("异地记录")
    elsewhere.update(entity_id="elsewhere_record")
    elsewhere.pop("location_id")
    elsewhere["state"]["location_id"] = "old_road"  # only the state names the place
    same_place = {**research_proposal("同地记录"), "entity_id": "same_place_record"}

    moved = town.submit_entity_proposal("god", elsewhere)
    repeated = town.submit_entity_proposal("god", same_place)

    assert moved["status"] == "accepted", moved
    assert repeated["failed_check"] == "research_novelty_duplicate"
    assert repeated["errors"][0].startswith("research source repeats existing evidence (第一份记录): research focus overlaps 100% with existing source 第一份记录")


def test_research_source_without_a_question_records_its_focus_and_gap(town: Platform) -> None:
    result = town.submit_entity_proposal("god", research_proposal("可读的残页"))

    assert result["status"] == "accepted", result
    focus = "The marker is reliable. A marker introduced after the opening story."
    assert entity(town, "proposed_marker")["initial_state"]["research_focus"] == focus
    gap = next(item for item in town.store.repositories.memory.list_knowledge_gaps() if item["gap_id"] == "source:proposed_marker")
    assert (gap["status"], gap["topic_key"], gap["question"], gap["current_source_id"]) == ("active", "source:proposed_marker", focus, "proposed_marker")
    assert (gap["source_name"], gap["actor_id"]) == ("可读的残页", "god")


def test_research_source_with_a_novelty_key_and_question_keys_its_gap_by_them(town: Platform) -> None:
    result = town.submit_entity_proposal("god", research_proposal("可读的残页", research_novelty_key="marker_origin", research_question="Who raised the marker?"))

    assert result["status"] == "accepted", result
    gap = next(item for item in town.store.repositories.memory.list_knowledge_gaps() if item["gap_id"] == "marker_origin")
    assert (gap["status"], gap["topic_key"], gap["question"], gap["current_source_id"]) == ("active", "marker_origin", "Who raised the marker?", "proposed_marker")
    assert entity(town, "proposed_marker")["initial_state"]["research_focus"] == "marker_origin Who raised the marker? The marker is reliable. A marker introduced after the opening story."
