"""The Story role's view: its source event, the validated source, the offered beats, known facts and progress tracks."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform

WORLD = Path(__file__).parents[1] / "worlds" / "starter_village"
INFORMATION_TYPES = [
    "exploration.completed", "research.completed", "object.recovered",
    "relationship.changed", "negotiation.result_recorded",
]


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One freshly created world; every test works on its own copy of the database."""

    path = create_instance(WORLD, "story-view", tmp_path_factory.mktemp("story-view-template"))
    Platform(path).close()
    return path


@pytest.fixture()
def platform(template: Path, tmp_path: Path):
    database = tmp_path / "world.sqlite3"
    shutil.copyfile(template, database)
    instance = Platform(database)
    try:
        yield instance
    finally:
        instance.close()


def _event(platform: Platform, event_type: str, actor: str | None, minute: int, **payload: Any) -> int:
    with platform.store.transaction():
        return platform.store.repositories.events.append_event(event_type, actor, minute, payload)


def _fact(platform: Platform, agent_id: str, key: str, raw: str | None = None, **data: Any) -> None:
    with platform.store.transaction():
        platform.store.connection.execute(
            "INSERT INTO knowledge_fact(agent_id,fact_key,data_json) VALUES (?,?,?)",
            (agent_id, key, json.dumps(data) if raw is None else raw),
        )


def _clear_facts(platform: Platform) -> None:
    with platform.store.transaction():
        platform.store.connection.execute("DELETE FROM knowledge_fact")


def _story_state(platform: Platform, state: dict[str, Any]) -> None:
    with platform.store.transaction():
        platform.store.repositories.metadata.set("story_state", state)


def _view(platform: Platform, source_event_id: int | None = None) -> dict[str, Any]:
    return platform.view.build_decision_view("story", role="story", source_event_id=source_event_id)


def _source(platform: Platform, source_event_id: int | None = None) -> dict[str, Any]:
    return _view(platform, source_event_id)["validated_source"]


def _beats(count: int, **extra: Any) -> list[dict[str, Any]]:
    return [{"id": f"beat_{index:02d}", "summary": f"Summary {index:02d}", **extra} for index in range(count)]


# ---- which event the Story session is about --------------------------------------------------------------------
def test_without_events_the_view_has_no_source(platform: Platform) -> None:
    assert _source(platform) == {}


def test_the_latest_information_event_is_the_default_source(platform: Platform) -> None:
    _event(platform, "exploration.completed", "fei_lan", 3)
    _event(platform, "research.completed", "he_yan", 4)
    assert _source(platform)["event_type"] == "research.completed"


@pytest.mark.parametrize("event_type", INFORMATION_TYPES)
def test_each_information_event_type_can_be_the_default_source(platform: Platform, event_type: str) -> None:
    _event(platform, event_type, "fei_lan", 5)
    assert _source(platform)["event_type"] == event_type


def test_other_event_types_are_not_a_default_source_but_can_be_named(platform: Platform) -> None:
    event_id = _event(platform, "entity.installed", "fei_lan", 5)
    assert _source(platform) == {}
    assert _source(platform, event_id)["event_type"] == "entity.installed"


def test_the_default_source_skips_events_made_by_story_and_god(platform: Platform) -> None:
    _event(platform, "research.completed", "fei_lan", 3)
    _event(platform, "research.completed", "story", 4)
    _event(platform, "exploration.completed", "god", 5)
    assert _source(platform)["world_minute"] == 3


def test_events_without_an_actor_can_be_a_default_source(platform: Platform) -> None:
    _event(platform, "research.completed", None, 3)
    assert _source(platform)["actor"] == "The world"


def test_a_behavior_wrapper_is_only_the_fallback_source(platform: Platform) -> None:
    _event(platform, "research.completed", "fei_lan", 3)
    _event(platform, "behavior.completed", "fei_lan", 4)
    assert _source(platform)["event_type"] == "research.completed"


def test_the_behavior_wrapper_is_used_when_no_information_event_exists(platform: Platform) -> None:
    _event(platform, "behavior.completed", "fei_lan", 4)
    assert _source(platform)["event_type"] == "behavior.completed"


@pytest.mark.parametrize("actor", ["story", "god"])
def test_the_behavior_fallback_skips_story_and_god(platform: Platform, actor: str) -> None:
    _event(platform, "behavior.completed", actor, 4)
    assert _source(platform) == {}


def test_an_explicit_source_wins_over_newer_events(platform: Platform) -> None:
    first = _event(platform, "research.completed", "fei_lan", 5)
    _event(platform, "relationship.changed", "he_yan", 6)
    source = _source(platform, first)
    assert (source["event_type"], source["world_minute"]) == ("research.completed", 5)


def test_an_unknown_source_id_has_no_source_and_no_fallback(platform: Platform) -> None:
    _event(platform, "research.completed", "fei_lan", 5)
    _event(platform, "behavior.completed", "fei_lan", 6)
    assert _source(platform, 999_999) == {}


@pytest.mark.parametrize(("fillers", "found"), [(79, True), (80, False)])
def test_the_view_only_sees_the_eighty_newest_events(platform: Platform, fillers: int, found: bool) -> None:
    target = _event(platform, "research.completed", "fei_lan", 5)
    for index in range(fillers):
        _event(platform, "misc.note", None, 6 + index)
    assert bool(_source(platform, target)) is found


# ---- the memory query ---------------------------------------------------------------------------------------
def test_the_memory_query_is_the_thread_keywords_when_nothing_happened(platform: Platform) -> None:
    assert _view(platform)["memory_query"] == "story narrative thread"


def test_the_memory_query_adds_the_source_event_and_the_latest_three_beats(platform: Platform) -> None:
    _event(platform, "research.completed", "fei_lan", 5, result="Water confirmed")
    _story_state(platform, {"emergent_beats": [{"summary": text} for text in ("one", "two", "three", "four")]})
    assert _view(platform)["memory_query"] == "story narrative thread research.completed Water confirmed two three four"


def test_story_memories_come_from_the_story_role_with_the_view_query(platform: Platform, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, int]] = []

    def fake_memory_query(store: Any, role: str, query: str, *, limit: int = 6) -> list[dict[str, Any]]:
        calls.append((role, query, limit))
        return [{"summary": "Remember the channel"}]

    monkeypatch.setattr("sim2.agent_view.story_view.role_memory_query", fake_memory_query)
    _event(platform, "research.completed", "fei_lan", 5)
    view = _view(platform)
    assert calls == [("story", view["memory_query"], 6)]
    assert view["memories"] == view["long_term_memories"] == [{"summary": "Remember the channel"}]
    assert view["short_term_memories"] == []


# ---- the validated source ---------------------------------------------------------------------------------------
def test_the_source_names_its_actor_and_location(platform: Platform) -> None:
    _event(platform, "research.completed", "fei_lan", 40, location_id="granary")
    source = _source(platform)
    assert source["event_type"] == "research.completed"
    assert source["actor"] == "费兰"
    assert source["location"] == "粮仓"
    assert source["world_minute"] == 40


def test_unknown_actor_and_location_ids_are_reported_as_they_are(platform: Platform) -> None:
    _event(platform, "research.completed", "stranger", 40, location_id="nowhere")
    source = _source(platform)
    assert (source["actor"], source["location"]) == ("stranger", "nowhere")


def test_a_source_without_a_location_is_unknown(platform: Platform) -> None:
    _event(platform, "research.completed", "fei_lan", 40)
    assert _source(platform)["location"] == "unknown"


def test_the_outcome_location_wins_over_the_event_location(platform: Platform) -> None:
    _event(platform, "research.completed", "fei_lan", 40, location_id="clinic", outcome={"actual": {"location": "granary"}})
    assert _source(platform)["location"] == "粮仓"


def test_an_outcome_without_a_location_falls_back_to_the_event_location(platform: Platform) -> None:
    _event(platform, "research.completed", "fei_lan", 40, location_id="clinic", outcome={"actual": {}})
    assert _source(platform)["location"] == "草药诊所"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"outcome": {"result": "from outcome"}, "result": "from event", "fact": "from fact"}, "from outcome"),
        ({"result": "from event", "fact": "from fact"}, "from event"),
        ({"fact": "from fact"}, "from fact"),
        ({}, "recorded"),
    ],
)
def test_the_source_result_prefers_the_outcome_then_the_event_then_its_fact(platform: Platform, payload: dict[str, Any], expected: str) -> None:
    _event(platform, "research.completed", "fei_lan", 40, **payload)
    assert _source(platform)["result"] == expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"fact": "event fact", "outcome": {"expected": {"summary": "expected summary"}}}, "event fact"),
        ({"outcome": {"expected": {"summary": "expected summary"}}}, "expected summary"),
        ({}, ""),
    ],
)
def test_the_source_fact_prefers_the_event_then_the_expected_outcome(platform: Platform, payload: dict[str, Any], expected: str) -> None:
    _event(platform, "research.completed", "fei_lan", 40, **payload)
    assert _source(platform)["fact"] == expected


def test_a_known_research_source_is_named_and_an_unknown_one_is_kept(platform: Platform) -> None:
    _event(platform, "research.completed", "fei_lan", 40, source_id="old_pact_tablet")
    assert _source(platform)["research_source"] == "旧契约石板"
    _event(platform, "research.completed", "fei_lan", 41, source_id="mystery")
    assert _source(platform)["research_source"] == "mystery"


def test_a_source_without_a_research_source_does_not_name_one(platform: Platform) -> None:
    _event(platform, "research.completed", "fei_lan", 40)
    assert "research_source" not in _source(platform)


def test_an_allocation_source_reports_what_was_handed_out(platform: Platform) -> None:
    rows: list[Any] = [{"subject": f"Resident {index}", "total": index} for index in range(14)]
    rows[3] = "not a row"
    rows[5] = {"total": 9}
    event_id = _event(
        platform, "institution.allocation_completed", "fei_lan", 40,
        consumed=6, remaining=14, item="grain", allocations=rows, result="ignored",
    )
    source = _source(platform, event_id)
    assert source["result"] == "Allocated 6 grain; 14 remains in the shared pool."
    summary = source["allocation_summary"]
    assert len(summary) == 11  # twelve rows are read, one of them is not a row
    assert summary[0] == {"subject": "Resident 0", "amount": 0}
    assert summary[3] == {"subject": "Resident 4", "amount": 4}
    assert summary[4] == {"subject": "a character", "amount": 9}
    assert summary[-1] == {"subject": "Resident 11", "amount": 11}


def test_an_allocation_without_an_item_names_a_resource(platform: Platform) -> None:
    event_id = _event(platform, "institution.allocation_completed", "fei_lan", 40, consumed=2, remaining=0)
    source = _source(platform, event_id)
    assert source["result"] == "Allocated 2 resource; 0 remains in the shared pool."
    assert source["allocation_summary"] == []


def test_other_sources_have_no_allocation_summary(platform: Platform) -> None:
    _event(platform, "research.completed", "fei_lan", 40, consumed=2)
    assert "allocation_summary" not in _source(platform)


# ---- the public fact behind the source -----------------------------------------------------------------------
def _fact_source(platform: Platform, event_type: str = "research.completed", **payload: Any) -> dict[str, Any]:
    event_id = _event(platform, event_type, "fei_lan", 40, fact="Event wording", **payload)
    return _source(platform, event_id)


def test_the_public_fact_of_the_actor_replaces_the_event_wording(platform: Platform) -> None:
    _fact(platform, "fei_lan", "researched:well:origin", source_type="research", observed_at=40, fact="Public wording")
    source = _fact_source(platform)
    assert source["fact"] == "Public wording"
    assert source["fact_key"] == "researched:well:origin"


def test_the_observation_minute_may_be_text(platform: Platform) -> None:
    _fact(platform, "fei_lan", "k", source_type="research", observed_at="40", fact="Public wording")
    assert _fact_source(platform)["fact"] == "Public wording"


@pytest.mark.parametrize(
    "row",
    [
        {"agent_id": "he_yan", "source_type": "research", "observed_at": 40},
        {"agent_id": "fei_lan", "source_type": "research", "observed_at": 41},
        {"agent_id": "fei_lan", "source_type": "exploration", "observed_at": 40},
        {"agent_id": "fei_lan", "source_type": "", "observed_at": 40},
    ],
)
def test_facts_of_another_actor_minute_or_source_type_are_ignored(platform: Platform, row: dict[str, Any]) -> None:
    _fact(platform, row["agent_id"], "k", source_type=row["source_type"], observed_at=row["observed_at"], fact="Other wording")
    source = _fact_source(platform)
    assert source["fact"] == "Event wording"
    assert "fact_key" not in source


@pytest.mark.parametrize(
    ("event_type", "source_type"),
    [
        ("research.completed", "research"),
        ("exploration.completed", "exploration"),
        ("object.recovered", "recovery"),
        ("entity.installed", "deployment"),
    ],
)
def test_each_event_type_matches_its_configured_fact_source_type(platform: Platform, event_type: str, source_type: str) -> None:
    other = "deployment" if source_type != "deployment" else "research"
    _fact(platform, "fei_lan", "other", source_type=other, observed_at=40, fact="Wrong wording")
    _fact(platform, "fei_lan", "right", source_type=source_type, observed_at=40, fact="Right wording")
    source = _fact_source(platform, event_type)
    assert (source["fact"], source["fact_key"]) == ("Right wording", "right")


@pytest.mark.parametrize("source_type", ["research", "exploration", "recovery", "deployment"])
def test_other_event_types_accept_any_information_source_type(platform: Platform, source_type: str) -> None:
    _fact(platform, "fei_lan", "k", source_type=source_type, observed_at=40, fact="Public wording")
    assert _fact_source(platform, "relationship.changed")["fact"] == "Public wording"


def test_other_event_types_reject_unrelated_source_types(platform: Platform) -> None:
    _fact(platform, "fei_lan", "k", source_type="rumour", observed_at=40, fact="Public wording")
    assert _fact_source(platform, "relationship.changed")["fact"] == "Event wording"


def test_the_fact_key_falls_back_to_the_fact_source(platform: Platform) -> None:
    _fact(platform, "fei_lan", "", source_type="research", observed_at=40, fact="Public wording", source_id="old_pact_tablet")
    assert _fact_source(platform)["fact_key"] == "old_pact_tablet"


def test_the_newest_matching_fact_wins(platform: Platform) -> None:
    _fact(platform, "fei_lan", "older", source_type="research", observed_at=40, fact="Older wording")
    _fact(platform, "fei_lan", "newer", source_type="research", observed_at=40, fact="Newer wording")
    source = _fact_source(platform)
    assert (source["fact"], source["fact_key"]) == ("Newer wording", "newer")


def test_unreadable_rows_and_rows_without_a_fact_are_skipped(platform: Platform) -> None:
    _fact(platform, "fei_lan", "good", source_type="research", observed_at=40, fact="Good wording")
    _fact(platform, "fei_lan", "empty", source_type="research", observed_at=40, fact="")
    _fact(platform, "tian_yan", "garbage", raw="not json")  # of an agent whose own view is never built here
    source = _fact_source(platform)
    assert (source["fact"], source["fact_key"]) == ("Good wording", "good")


@pytest.mark.parametrize(("fillers", "found"), [(63, True), (64, False)])
def test_only_the_newest_sixty_four_facts_are_searched(platform: Platform, fillers: int, found: bool) -> None:
    _fact(platform, "fei_lan", "target", source_type="research", observed_at=40, fact="Public wording")
    with platform.store.transaction():
        platform.store.connection.executemany(
            "INSERT INTO knowledge_fact(agent_id,fact_key,data_json) VALUES (?,?,?)",
            [("he_yan", f"filler:{index}", json.dumps({"source_type": "other", "fact": "Filler"})) for index in range(fillers)],
        )
    assert (_fact_source(platform)["fact"] == "Public wording") is found


# ---- the beats on offer -------------------------------------------------------------------------------------
def test_a_configured_beat_is_offered_once_its_evidence_exists(platform: Platform) -> None:
    _event(platform, "exploration.completed", "fei_lan", 10)
    _fact(platform, "fei_lan", "explored:greywood_edge:route", source_type="exploration", observed_at=10, fact="A route exists")
    refs = [item["ref"] for item in _view(platform)["story_beats"]]
    assert "story:greywood_route_found" in refs


def test_a_configured_beat_waits_for_its_fact_and_its_source_type(platform: Platform) -> None:
    _event(platform, "exploration.completed", "fei_lan", 10)
    assert "story:greywood_route_found" not in [item["ref"] for item in _view(platform)["story_beats"]]
    _fact(platform, "fei_lan", "explored:greywood_edge:route", source_type="exploration", observed_at=10, fact="A route exists")
    event_id = _event(platform, "research.completed", "fei_lan", 11)
    assert "story:greywood_route_found" not in [item["ref"] for item in _view(platform, event_id)["story_beats"]]


@pytest.mark.parametrize("status", ["active", "completed"])
def test_a_beat_that_already_started_is_not_offered_again(platform: Platform, status: str) -> None:
    _story_state(platform, {"emergent_beats": _beats(2), "beat_00": {"status": status}})
    assert [item["ref"] for item in _view(platform)["story_beats"]] == ["story:beat_01"]


def test_a_pending_beat_is_still_offered(platform: Platform) -> None:
    _story_state(platform, {"emergent_beats": _beats(1), "beat_00": {"status": "pending"}})
    assert [item["ref"] for item in _view(platform)["story_beats"]] == ["story:beat_00"]


def test_emergent_beats_are_listed_in_order_and_capped_at_twelve(platform: Platform) -> None:
    _story_state(platform, {"emergent_beats": _beats(14)})
    refs = [item["ref"] for item in _view(platform)["story_beats"]]
    assert refs == [f"story:beat_{index:02d}" for index in range(12)]


def test_a_choice_projects_the_beat_for_the_model(platform: Platform) -> None:
    beat = {
        "id": "gate_watch", "agent_label": "Watch the gate", "summary": "The gate needs watching.",
        "trigger_events": ["research.completed", "exploration.completed"], "audience": ["fei_lan"],
        "followup_behaviors": ["granary"],
    }
    _event(platform, "research.completed", "fei_lan", 10)
    _story_state(platform, {"emergent_beats": [beat]})
    choice = _view(platform)["story_beats"][0]
    assert choice["ref"] == "story:gate_watch"
    assert choice["choice"] == "Watch the gate"
    assert choice["summary"] == "The gate needs watching."
    assert choice["when"] == "exploration.completed; research.completed"
    assert choice["requires"] == "A recent validated discovery is required."
    assert choice["audience"] == ["费兰"]
    assert choice["followup_behaviors"] == ["粮仓"]
    assert choice["impact"] == ["Platform records the beat, informs its configured audience, and unlocks only configured behaviors."]


def test_a_choice_without_triggers_uses_the_default_wording(platform: Platform) -> None:
    _story_state(platform, {"emergent_beats": [{"id": "plain", "summary": "A plain beat."}]})
    choice = _view(platform)["story_beats"][0]
    assert choice["when"] == "The latest evidence supports a new story direction."
    assert choice["requires"] == "A recent validated discovery is required."
    assert (choice["audience"], choice["followup_behaviors"]) == ([], [])


def test_a_required_fact_is_listed_by_its_visible_name(platform: Platform) -> None:
    _fact(platform, "fei_lan", "explored:granary:route", source_type="exploration", observed_at=1, fact="A route")
    _story_state(platform, {"emergent_beats": [{"id": "route", "summary": "A route.", "trigger_facts": ["explored:granary:route"]}]})
    assert _view(platform)["story_beats"][0]["requires"] == "Explored: 粮仓 — route"


@pytest.mark.parametrize(
    ("beat", "label"),
    [
        ({"id": "b", "agent_label": "Label", "summary": "Summary text."}, "Label"),
        ({"id": "b", "summary": "Summary text."}, "Summary text."),
        ({"id": "b", "summary": ""}, "b"),
    ],
)
def test_a_choice_is_labelled_by_label_then_summary_then_id(platform: Platform, beat: dict[str, Any], label: str) -> None:
    _story_state(platform, {"emergent_beats": [beat]})
    assert _view(platform)["story_beats"][0]["choice"] == label


def test_a_source_of_another_type_hides_a_beat_that_names_other_evidence(platform: Platform) -> None:
    beat = {"id": "needs_trade", "summary": "Needs trade.", "trigger_events": ["trade.completed"]}
    _story_state(platform, {"emergent_beats": [beat]})
    research = _event(platform, "research.completed", "fei_lan", 10)
    trade = _event(platform, "trade.completed", "fei_lan", 11)
    assert "story:needs_trade" not in [item["ref"] for item in _view(platform, research)["story_beats"]]
    assert "story:needs_trade" in [item["ref"] for item in _view(platform, trade)["story_beats"]]


def test_a_beat_that_names_evidence_is_offered_when_its_event_was_observed(platform: Platform) -> None:
    beat = {"id": "needs_trade", "summary": "Needs trade.", "trigger_events": ["trade.completed"]}
    _story_state(platform, {"emergent_beats": [beat]})
    assert "story:needs_trade" not in [item["ref"] for item in _view(platform)["story_beats"]]
    _event(platform, "trade.completed", "fei_lan", 11)
    assert "story:needs_trade" in [item["ref"] for item in _view(platform)["story_beats"]]


# ---- known facts -----------------------------------------------------------------------------------------------
def test_known_facts_are_newest_first_and_never_repeat_a_key_or_a_text(platform: Platform) -> None:
    _clear_facts(platform)
    # inserted oldest first; the view reads the newest first
    _fact(platform, "he_yan", "bad:key", raw="not json")
    _fact(platform, "he_yan", "k_with_underscores:part")
    _fact(platform, "he_yan", "k3", summary="Four")
    _fact(platform, "he_yan", "", fact="three")
    _fact(platform, "he_yan", "", fact="Three")
    _fact(platform, "he_yan", "k2", fact="one")
    _fact(platform, "he_yan", "k1", fact="Two")
    _fact(platform, "he_yan", "k1", fact="One")
    facts = [item["fact"] for item in _view(platform)["known_facts"]]
    assert facts == ["One", "Three", "Four", "k with underscores — part", "bad — key"]


def test_known_facts_are_world_knowledge_with_full_confidence(platform: Platform) -> None:
    _clear_facts(platform)
    _fact(platform, "he_yan", "k1", fact="One")
    assert _view(platform)["known_facts"] == [{"fact": "One", "source_type": "world_knowledge", "confidence": 1.0}]


def test_known_facts_stop_at_twelve(platform: Platform) -> None:
    _clear_facts(platform)
    for index in range(15):
        _fact(platform, "he_yan", f"k{index:02d}", fact=f"Fact {index:02d}")
    facts = [item["fact"] for item in _view(platform)["known_facts"]]
    assert facts == [f"Fact {index:02d}" for index in range(14, 2, -1)]


def test_known_facts_use_visible_names(platform: Platform) -> None:
    _clear_facts(platform)
    _fact(platform, "he_yan", "k1", fact="granary is full")
    assert _view(platform)["known_facts"][0]["fact"] == "粮仓 is full"


# ---- progress tracks ---------------------------------------------------------------------------------------------
def _progress(platform: Platform, tracks: Any) -> list[dict[str, Any]]:
    with platform.store.transaction():
        rules = dict(platform.store.repositories.metadata.get("rules", {}) or {})
        rules["civilization_progress"] = tracks
        platform.store.repositories.metadata.set("rules", rules)
    return _view(platform)["world_progress_candidates"]


def test_progress_tracks_are_listed_with_their_evidence(platform: Platform) -> None:
    tracks = {
        "harvest": {
            "required": "3", "description": "Bring in the harvest.", "evidence_event_types": ["research.completed"],
            "evidence_behaviors": ["bring_grain"], "evidence_sources": [{"id": "granary"}],
        },
    }
    assert _progress(platform, tracks) == [{
        "progress_key": "harvest", "required": 3.0, "description": "Bring in the harvest.",
        "evidence_event_types": ["research.completed"], "evidence_behaviors": ["bring_grain"],
        "evidence_sources": [{"id": "granary"}],
    }]


def test_progress_tracks_default_their_missing_fields(platform: Platform) -> None:
    assert _progress(platform, {"bare": {}, "zero": {"required": 0}}) == [
        {"progress_key": "bare", "required": 1.0, "description": "", "evidence_event_types": [], "evidence_behaviors": [], "evidence_sources": []},
        {"progress_key": "zero", "required": 1.0, "description": "", "evidence_event_types": [], "evidence_behaviors": [], "evidence_sources": []},
    ]


def test_progress_entries_that_are_not_tracks_are_skipped(platform: Platform) -> None:
    candidates = _progress(platform, {"track": {"required": 2}, "empty": None, "text": "not a track"})
    assert [item["progress_key"] for item in candidates] == ["track"]


@pytest.mark.parametrize("tracks", [{}, None])
def test_a_world_without_progress_tracks_lists_none(platform: Platform, tracks: Any) -> None:
    assert _progress(platform, tracks) == []


# ---- the assembled view ---------------------------------------------------------------------------------------------
def test_the_view_keeps_its_sections_in_order(platform: Platform) -> None:
    assert list(_view(platform)) == [
        "context_version", "role", "prompt_protocol", "platform_rules", "world_time", "self", "identity", "needs",
        "inventory", "injuries", "drives", "observation", "validated_source", "nearby_entities", "known_facts",
        "audience_candidates", "recent_actions", "recent_story_events", "recent_history", "knowledge_gaps",
        "research_sources", "memories", "long_term_memories", "short_term_memories", "memory_query",
        "recent_world_changes", "recent_agent_outcomes", "direct_relationships", "active_task",
        "executable_behaviors", "story_beats", "world_progress_candidates", "active_world_rules",
        "last_platform_result", "tools",
    ]


def test_the_validated_source_keeps_its_fields_in_order(platform: Platform) -> None:
    _event(platform, "research.completed", "fei_lan", 40, source_id="old_pact_tablet")
    assert list(_source(platform)) == ["event_type", "actor", "location", "result", "fact", "world_minute", "research_source"]


def test_the_view_reports_recent_events_and_the_latest_platform_result(platform: Platform) -> None:
    first = _event(platform, "research.completed", "fei_lan", 40, summary="The seal was read.")
    newest = _event(platform, "story.beat.activated", "story", 41, summary="A thread began.")
    view = _view(platform, first)
    assert view["observation"]["source"] == "validated_world_events"
    assert view["observation"]["facts"][:2] == ["story.beat.activated: A thread began.", "research.completed: The seal was read."]
    assert view["last_platform_result"]["event_id"] == newest
    assert [item["event_id"] for item in view["recent_story_events"]] == [newest]
    assert [item["event_id"] for item in view["recent_actions"]][:2] == [newest, first]


def test_the_view_has_no_latest_result_in_a_world_without_events(platform: Platform) -> None:
    with platform.store.transaction():
        platform.store.connection.execute("DELETE FROM event_log")
    view = _view(platform)
    assert view["last_platform_result"] is None
    assert view["recent_actions"] == []
    assert view["observation"]["facts"] == []


def test_the_story_role_is_described_as_the_story_agent(platform: Platform) -> None:
    view = _view(platform)
    assert view["role"] == "story"
    assert view["self"]["name"] == "Story Agent"
    assert view["executable_behaviors"] == []
    assert view["identity"] == {"traits": {}, "beliefs": [], "capabilities": {"story_scope": True}}
