"""Regression tests for the absorbing-state audit of a real Three Lanterns run.

Each test pins one generic framework property that let the audited instance
stop evolving: wake semantics, Story beat visibility, stagnation detection,
reachability of proposed content, and revisable institutions.  Nothing here
depends on world-specific branches in Platform; the Three Lanterns package is
only the configuration fixture.
"""

from __future__ import annotations

from pathlib import Path
import json

import pytest
import yaml

from sim2.agents.engine import AgentEngine
from sim2.bootstrap.service import create_instance, validate_config
from sim2.evolution.config import normalize_evolution_config
from sim2.platform.runtime import Platform
from sim2.prompting.renderer import render_context

WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
BOUNDARY_BEAT = "记录界碑研究结果并决定下一步核验"


class WaitProvider:
    """Provider double that records the rendered prompt and waits."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.calls.append({"system": system, "context": context, "tools": tools, "messages": messages})
        return {"intent_type": "wait", "reason": "The evidence needs no new content."}


def _town(tmp_path: Path, name: str) -> Platform:
    return Platform(create_instance(WORLD, name, tmp_path / "runtime"))


def _step(platform: Platform, minutes: int) -> None:
    for _ in range(int(minutes)):
        platform.step()


def _story_tasks(platform: Platform) -> list[dict]:
    return [item for item in platform.ready_evolution_tasks() if item["role"] == "story"]


# --- manual_only wake semantics -------------------------------------------------


def test_manual_only_wake_is_not_compiled_as_a_direct_event() -> None:
    compiled = normalize_evolution_config({
        "wakes": [
            {"on": "x.done", "for": "story", "manual_only": True},
            {"on": "y.done", "for": "story"},
        ],
    })
    story = compiled["event_wake_policy"]["consumers"]["story"]
    assert story["direct_event_types"] == ["y.done"]
    assert story["manual_event_types"] == ["x.done"]


def test_manual_only_event_skips_automatic_fan_out_but_admits_explicit_producers(tmp_path: Path) -> None:
    platform = _town(tmp_path, "manual-only-wake")
    try:
        event_id = platform.store.append_event("institution.allocation_completed", "mei_lan", 2, {"amount": 1})
        # Platform's own publication of the event is the automatic path.
        platform._wake_configured_evolution_consumers(event_id, "institution.allocation_completed", "mei_lan", 2)
        assert _story_tasks(platform) == []
        # A behavior or Action that names Story in its own ``wake`` list is
        # an explicit producer declaration and still reaches Story.
        task_id = platform._queue_event_consumer_task(
            "story", event_id, "institution.allocation_completed", "mei_lan", 2, explicit=True,
        )
        assert task_id
        assert [item["task_id"] for item in _story_tasks(platform)] == [task_id]
    finally:
        platform.close()


# --- evidence-keyed wake groups ---------------------------------------------------


def test_distinct_by_compiles_into_an_evidence_group() -> None:
    compiled = normalize_evolution_config({
        "wakes": [{"on": "research.completed", "for": "god", "distinct_by": ["source_id", "stage_id"]}],
    })
    group = compiled["event_wake_policy"]["consumers"]["god"]["event_groups"][0]
    assert group["distinct_by"] == ["source_id", "stage_id"]
    assert group["minimum_events"] == 1 and group["cooldown_minutes"] == 0
    assert compiled["event_wake_policy"]["consumers"]["god"]["direct_event_types"] == []

    try:
        normalize_evolution_config({"wakes": [{"on": "x", "for": "god", "distinct_by": [3]}]})
    except ValueError as exc:
        assert "distinct_by" in str(exc)
    else:
        raise AssertionError("a non-string distinct_by key must be rejected")


def _distinct_god_policy(platform: Platform, **extra: object) -> None:
    platform.store.set_meta("evolution", normalize_evolution_config({
        "wakes": [{
            "id": "research_synthesis", "on": "research.completed", "for": "god",
            "distinct_by": ["source_id", "stage_id"], "tools": ["wait"], **extra,
        }],
    }))


def _research_event(platform: Platform, minute: int, source: str, stage: str) -> int:
    return platform.store.append_event(
        "research.completed", "luo_shan", minute, {"source_id": source, "stage_id": stage},
    )


def test_distinct_by_wakes_once_per_evidence_key_and_never_by_calendar(tmp_path: Path) -> None:
    platform = _town(tmp_path, "distinct-wake")
    try:
        _distinct_god_policy(platform)
        first = _research_event(platform, 1, "stone", "stage_1")
        first_task = platform._queue_event_consumer_task("god", first, "research.completed", "luo_shan", 1)
        assert first_task

        # The same source and stage is the same evidence, however often it is repeated.
        repeat = _research_event(platform, 2, "stone", "stage_1")
        assert platform._queue_event_consumer_task("god", repeat, "research.completed", "luo_shan", 2) is None

        # New evidence one minute later wakes again: there is no week-long lockout.
        second = _research_event(platform, 3, "stone", "stage_2")
        second_task = platform._queue_event_consumer_task("god", second, "research.completed", "luo_shan", 3)
        assert second_task and second_task != first_task
        task = next(item for item in platform.ready_evolution_tasks() if item["task_id"] == second_task)
        assert [item["event_id"] for item in task["source_events"]] == [second]
    finally:
        platform.close()


def test_distinct_by_counts_only_novel_keys_toward_the_minimum(tmp_path: Path) -> None:
    platform = _town(tmp_path, "distinct-minimum")
    try:
        _distinct_god_policy(platform, count=2)
        queue = lambda event_id, minute: platform._queue_event_consumer_task("god", event_id, "research.completed", "luo_shan", minute)
        assert queue(_research_event(platform, 1, "stone", "stage_1"), 1) is None
        assert queue(_research_event(platform, 2, "stone", "stage_1"), 2) is None  # repeat: still one key
        task_id = queue(_research_event(platform, 3, "road", "stage_1"), 3)  # second key reaches the minimum
        assert task_id
        task = next(item for item in platform.ready_evolution_tasks() if item["task_id"] == task_id)
        assert len(task["source_events"]) == 2
        # Spent keys do not count again.
        assert queue(_research_event(platform, 4, "stone", "stage_1"), 4) is None
    finally:
        platform.close()


# --- derived stagnation signal ----------------------------------------------------


def test_stagnation_config_compiles_and_rejects_bad_fields() -> None:
    compiled = normalize_evolution_config({"stagnation": {"window": "1d"}})
    assert compiled["stagnation"] == {"window_minutes": 1440, "max_interval_minutes": 10080}
    compiled = normalize_evolution_config({"stagnation": {"window": "6h", "max_interval": "2d"}})
    assert compiled["stagnation"] == {"window_minutes": 360, "max_interval_minutes": 2880}
    compiled = normalize_evolution_config({"stagnation": {"window": "1d", "max_characters": 8}})
    assert compiled["stagnation"] == {"window_minutes": 1440, "max_interval_minutes": 10080, "max_characters": 8}
    for bad, message in (
        ({"stagnation": {"max_interval": "1d"}}, "window is required"),
        ({"stagnation": {"window": "1d", "max_interval": "1h"}}, "must not be shorter"),
        ({"stagnation": {"window": "1d", "threshold": 3}}, "unknown stagnation field"),
        ({"stagnation": "1d"}, "must be an object"),
        ({"stagnation": {"window": "1d", "max_characters": 0}}, "max_characters must be an integer from 1 to 64"),
        ({"stagnation": {"window": "1d", "max_characters": 65}}, "max_characters must be an integer from 1 to 64"),
        ({"stagnation": {"window": "1d", "max_characters": "many"}}, "max_characters must be an integer from 1 to 64"),
        ({"stagnation": {"window": "1d", "max_characters": True}}, "max_characters must be an integer from 1 to 64"),
    ):
        with pytest.raises(ValueError, match=message):
            normalize_evolution_config(bad)


def test_stagnation_progress_event_types_are_configurable() -> None:
    compiled = normalize_evolution_config({"stagnation": {"window": "1d", "progress_event_types": ["finding.confirmed"]}})
    assert compiled["stagnation"]["progress_event_types"] == ["finding.confirmed"]


def _stagnation_world(platform: Platform, window: str = "1h", max_interval: str | None = None, max_characters: int | None = None) -> None:
    spec = {
        "window": window,
        **({"max_interval": max_interval} if max_interval else {}),
        **({"max_characters": max_characters} if max_characters else {}),
    }
    platform.store.set_meta("evolution", normalize_evolution_config({
        "stagnation": spec,
        "wakes": [{
            "id": "option_stagnation", "on": "world.stagnation", "for": "god",
            "tools": ["propose_entity", "wait"],
            "purpose": "No character gained a new executable option for a full window.",
            "result": "Create one reachable continuation, or wait only when the diagnostics show none is justified.",
        }],
    }))


def _stagnation_minutes(platform: Platform) -> list[int]:
    return [
        int(row["world_minute"])
        for row in platform.store.connection.execute(
            "SELECT world_minute FROM event_log WHERE event_type='world.stagnation' ORDER BY event_id"
        ).fetchall()
    ]


def test_stagnation_signal_has_a_baseline_then_repeats_at_doubling_spacing(tmp_path: Path) -> None:
    platform = _town(tmp_path, "stagnation-spacing")
    try:
        _stagnation_world(platform)
        _step(platform, 60)
        assert _stagnation_minutes(platform) == []  # the first window only records the baseline
        _step(platform, 240)
        # Windows of stagnation 1, 2 and 4 publish; windows 3 does not.
        assert _stagnation_minutes(platform) == [120, 180, 300]
        state = platform.store.get_meta("stagnation_state")
        assert state["stagnant_windows"] == 4 and state["last_novel_minute"] == 60
    finally:
        platform.close()


def test_meaningful_progress_resets_option_stagnation(tmp_path: Path) -> None:
    platform = _town(tmp_path, "stagnation-progress")
    try:
        _stagnation_world(platform)
        _step(platform, 60)
        with platform.store.transaction():
            platform.store.append_event("research.progressed", "luo_shan", 90, {"source_id": "boundary_stone", "stage_id": "stage_1"})
        _step(platform, 60)
        assert _stagnation_minutes(platform) == []
        assert platform.store.get_meta("stagnation_state")["progressed_last_window"] is True
    finally:
        platform.close()


def test_stagnation_spacing_is_capped_by_max_interval(tmp_path: Path) -> None:
    platform = _town(tmp_path, "stagnation-cap")
    try:
        _stagnation_world(platform, window="1h", max_interval="1h")
        _step(platform, 240)
        assert _stagnation_minutes(platform) == [120, 180, 240]
    finally:
        platform.close()


def test_new_parameter_option_counts_as_growth_and_restarts_the_spacing(tmp_path: Path) -> None:
    platform = _town(tmp_path, "stagnation-growth")
    try:
        _stagnation_world(platform)
        _step(platform, 120)
        assert _stagnation_minutes(platform) == [120]

        # A new research source adds only a parameter option to the existing
        # research behavior.  It still gives Luo Shan something new to do.
        created = platform.submit_entity_proposal("god", {
            "entity_type": "document",
            "entity_id": "later_marker",
            "name": "后续标记记录",
            "description": "A source introduced after the opening story.",
            "location_id": "north_watch",
            "state": {
                "location_id": "north_watch",
                "readable": True,
                "research_assignees": ["luo_shan"],
                "research_discoveries": [{"id": "fact:later_marker:one", "fact": "The marker is reliable.", "required": 1}],
            },
        })
        assert created["status"] == "accepted"
        _step(platform, 60)
        assert _stagnation_minutes(platform) == [120]  # growth at minute 180: no event
        state = platform.store.get_meta("stagnation_state")
        assert state["stagnant_windows"] == 0 and state["last_novel_minute"] == 180
        assert "behavior:research_boundary_stone|source_id=后续标记记录" in state["frontier"]["luo_shan"]

        _step(platform, 60)
        assert _stagnation_minutes(platform) == [120, 240]  # the spacing restarted at one window
    finally:
        platform.close()


def test_stagnation_event_names_what_each_character_can_and_cannot_do(tmp_path: Path) -> None:
    platform = _town(tmp_path, "stagnation-evidence")
    try:
        _stagnation_world(platform)
        _step(platform, 120)
        event = next(item for item in platform.store.recent_events(limit=50) if item["event_type"] == "world.stagnation")
        assert event["stagnant_windows"] == 1 and event["window_minutes"] == 60
        rows = {item["name"]: item for item in event["characters"]}
        assert {"罗杉", "韩秋", "梅岚"} <= set(rows)
        assert "Inspect Cistern" in rows["梅岚"]["options"]
        # The blocked entry carries the failing condition, not a bare refusal.
        blocked = {item["choice"]: item["needs"] for item in rows["韩秋"]["blocked"]}
        assert any("boundary_stone_research" in need for need in blocked["Secure North Watch"])
        assert "罗杉 can:" in event["summary"] and "Secure North Watch" in event["summary"]
    finally:
        platform.close()


def test_god_is_woken_with_the_stagnation_evidence_in_its_prompt(tmp_path: Path) -> None:
    platform = _town(tmp_path, "stagnation-god-wake")
    try:
        _stagnation_world(platform)
        _step(platform, 120)
        tasks = [item for item in platform.ready_evolution_tasks() if item["task_type"] == "event_group:option_stagnation"]
        assert len(tasks) == 1 and tasks[0]["role"] == "god"
        provider = WaitProvider()
        turn = AgentEngine(platform, provider).run(
            role="god", actor_id="god", task_type=tasks[0]["task_type"], source_events=tasks[0]["source_events"],
        )
        assert turn.result["status"] == "wait"
        prompt = provider.calls[0]["context"]
        assert "<triggering_evidence>" in prompt
        assert "No character gained a new executable option" in prompt
        assert "韩秋 can:" in prompt
    finally:
        platform.close()


def test_wake_on_stagnation_requires_a_configured_window(tmp_path: Path) -> None:
    package = tmp_path / "stagnation-package"
    package.mkdir()
    for path in WORLD.iterdir():
        if path.is_file():
            (package / path.name).write_bytes(path.read_bytes())
    evolution = yaml.safe_load((package / "evolution.yaml").read_text(encoding="utf-8"))
    assert validate_config(package)["valid"] is True
    evolution.pop("stagnation")
    (package / "evolution.yaml").write_text(yaml.safe_dump(evolution, allow_unicode=True, sort_keys=False), encoding="utf-8")
    report = validate_config(package)
    assert report["valid"] is False
    assert any("world.stagnation" in error and "window" in error for error in report["errors"])


def test_a_large_roster_is_evaluated_through_a_rotating_cohort(tmp_path: Path) -> None:
    """Three characters, two evaluated per window: ids sort han_qiu < luo_shan < mei_lan."""

    platform = _town(tmp_path, "stagnation-cohort")
    try:
        _stagnation_world(platform, max_characters=2)
        _step(platform, 60)  # window 1 starts at index 2 and wraps: mei_lan, han_qiu
        state = platform.store.get_meta("stagnation_state")
        assert sorted(state["frontier"]) == ["han_qiu", "mei_lan"]
        assert _stagnation_minutes(platform) == []

        _step(platform, 60)  # window 2: luo_shan, mei_lan; luo_shan's first sight is a baseline, not growth
        state = platform.store.get_meta("stagnation_state")
        assert sorted(state["frontier"]) == ["han_qiu", "luo_shan", "mei_lan"]
        assert state["stagnant_windows"] == 1 and state["last_novel_minute"] == 60
        assert _stagnation_minutes(platform) == [120]

        event = next(item for item in platform.store.recent_events(limit=50) if item["event_type"] == "world.stagnation")
        assert sorted(item["name"] for item in event["characters"]) == ["梅岚", "罗杉"]
        assert event["omitted_characters"] == 1
        assert "1 more character(s) omitted." in event["summary"]
    finally:
        platform.close()


def test_growth_in_a_sampled_character_resets_the_stagnation_counter(tmp_path: Path) -> None:
    platform = _town(tmp_path, "stagnation-cohort-growth")
    try:
        _stagnation_world(platform, max_characters=2)
        _step(platform, 120)  # the cohort has covered all three characters
        created = platform.submit_entity_proposal("god", {
            "entity_type": "document",
            "entity_id": "sampled_marker",
            "name": "抽样标记记录",
            "description": "A source introduced after the baseline.",
            "location_id": "north_watch",
            "state": {
                "location_id": "north_watch",
                "readable": True,
                "research_assignees": ["luo_shan"],
                "research_discoveries": [{"id": "fact:sampled_marker:one", "fact": "The marker is reliable.", "required": 1}],
            },
        })
        assert created["status"] == "accepted"
        _step(platform, 60)  # window 3: han_qiu, luo_shan; luo_shan gained an option
        state = platform.store.get_meta("stagnation_state")
        assert state["stagnant_windows"] == 0 and state["last_novel_minute"] == 180
        assert "behavior:research_boundary_stone|source_id=抽样标记记录" in state["frontier"]["luo_shan"]
    finally:
        platform.close()


def test_stagnation_is_silent_without_configuration(tmp_path: Path) -> None:
    platform = _town(tmp_path, "stagnation-off")
    try:
        platform.store.set_meta("evolution", normalize_evolution_config({"cadence": {"god": "1d"}}))
        _step(platform, 240)
        assert _stagnation_minutes(platform) == []
        assert platform.store.get_meta("stagnation_state") is None
    finally:
        platform.close()


def test_configured_resource_safety_publishes_one_crossing_event(tmp_path: Path) -> None:
    platform = _town(tmp_path, "resource-safety")
    try:
        rules = dict(platform.store.get_meta("rules", {}) or {})
        rules["resource_safety"] = [{"resource_id": "cistern_water", "minimum_reserve": 12, "event_type": "resource.safety_low"}]
        platform.store.set_meta("rules", rules)
        _set_quantity(platform, "cistern_water", 11)
        platform.step()
        platform.step()
        events = [item for item in platform.store.recent_events(limit=80) if item["event_type"] == "resource.safety_low"]
        assert len(events) == 1 and events[0]["resource_id"] == "cistern_water"
    finally:
        platform.close()


def test_resource_safety_repeat_signal_reopens_a_persistent_shortage(tmp_path: Path) -> None:
    platform = _town(tmp_path, "resource-safety-repeat")
    try:
        rules = dict(platform.store.get_meta("rules", {}) or {})
        rules["resource_safety"] = [{
            "resource_id": "cistern_water",
            "minimum_reserve": 12,
            "repeat_every_minutes": 2,
            "event_type": "resource.safety_low",
        }]
        platform.store.set_meta("rules", rules)
        _set_quantity(platform, "cistern_water", 11)
        platform.step()  # crossing at minute 1
        platform.step()  # repeat interval not reached
        platform.step()  # repeat at minute 3
        events = [item for item in platform.store.recent_events(limit=100) if item["event_type"] == "resource.safety_low"]
        events.sort(key=lambda item: item["world_minute"])
        assert [item["world_minute"] for item in events] == [1, 3]
        assert [item["signal_kind"] for item in events] == ["crossing", "repeat"]
    finally:
        platform.close()


# --- Story beat visibility in the milestone session ----------------------------


def _research_milestone(platform: Platform) -> tuple[int, int]:
    with platform.store.transaction():
        research_id = platform.store.append_event(
            "research.completed",
            "luo_shan",
            10,
            {"source_id": "boundary_stone", "stage_id": "researched:boundary_stone:markings", "fact": "The boundary marks overlap."},
        )
    progress = platform.submit_world_progress(
        "story", "boundary_knowledge", None, "Luo Shan completed the two-stage boundary stone research.",
        source_event_ids=[research_id],
    )
    assert progress["status"] == "accepted" and progress["milestone"] is True
    return research_id, int(progress["event_id"])


def test_configured_story_beat_is_offered_in_the_milestone_session(tmp_path: Path) -> None:
    platform = _town(tmp_path, "milestone-beat-visible")
    try:
        research_id, milestone_id = _research_milestone(platform)
        for event_id in (research_id, milestone_id):
            view = platform.view.build_decision_view("story", role="story", source_event_id=event_id)
            assert BOUNDARY_BEAT in [item["choice"] for item in view["story_beats"]]
    finally:
        platform.close()


def test_milestone_session_activates_configured_beat_and_unlocks_its_followup(tmp_path: Path) -> None:
    platform = _town(tmp_path, "milestone-beat-activates")
    try:
        _, milestone_id = _research_milestone(platform)
        before = platform.view.build_decision_view("han_qiu", role="agent_decision")
        assert "behavior:secure_north_watch" not in {item["ref"] for item in before["executable_behaviors"]}

        # The model names only the configured beat; it supplies no fact text.
        result = platform.submit_story_beat("story", {"beat": BOUNDARY_BEAT}, source_event_ids=[milestone_id])
        assert result["status"] == "accepted", result
        assert platform.store.get_meta("story_state")["boundary_stone_research"]["status"] == "active"

        keys = {item["fact_key"] for item in platform.store.list_knowledge_facts("han_qiu", limit=100)}
        assert "story:boundary_stone_research" in keys
        after = platform.view.build_decision_view("han_qiu", role="agent_decision")
        assert "behavior:secure_north_watch" in {item["ref"] for item in after["executable_behaviors"]}
    finally:
        platform.close()


def test_story_thread_memory_closes_with_the_story_beat(tmp_path: Path) -> None:
    platform = _town(tmp_path, "story-memory-lifecycle")
    try:
        _, milestone_id = _research_milestone(platform)
        result = platform.submit_story_beat("story", {"beat": BOUNDARY_BEAT}, source_event_ids=[milestone_id])
        assert result["status"] == "accepted", result
        before = platform.store.connection.execute(
            "SELECT data_json FROM memory_item WHERE agent_id='story' AND memory_type='story_thread'"
        ).fetchall()
        assert any(json.loads(row["data_json"])["status"] == "active" for row in before)

        platform._complete_story_beats_for_actor("han_qiu", "secure_north_watch", 20)

        after = platform.store.connection.execute(
            "SELECT data_json FROM memory_item WHERE agent_id='story' AND memory_type='story_thread'"
        ).fetchall()
        closed = [
            json.loads(row["data_json"])
            for row in after
            if json.loads(row["data_json"]).get("beat_id") == "boundary_stone_research"
        ]
        assert closed and all(item["status"] == "completed" for item in closed)
        assert all(item["closed_by_behavior"] == "secure_north_watch" for item in closed)
    finally:
        platform.close()


def test_emergent_beat_is_not_accepted_when_a_configured_beat_applies(tmp_path: Path) -> None:
    platform = _town(tmp_path, "milestone-beat-required")
    try:
        _, milestone_id = _research_milestone(platform)
        result = platform.submit_story_beat(
            "story",
            {
                "beat": "拓片与刻石互证",
                "summary": "罗杉把拓片和刻石互相印证，界线走向更清楚了。",
                "audience": ["罗杉"],
            },
            source_event_ids=[milestone_id],
        )
        assert result["status"] == "rejected"
        assert result["failed_check"] == "story_beat_choice"
        assert BOUNDARY_BEAT in result["available_choices"]
    finally:
        platform.close()


# --- Reachability of proposed content -----------------------------------------


def _source_proposal(name: str, assignees: list[str]) -> dict:
    return {
        "entity_type": "document",
        "entity_id": "proposed_marker",
        "name": name,
        "description": "A marker introduced after the opening story.",
        "location_id": "north_watch",
        "state": {
            "location_id": "north_watch",
            "readable": True,
            "research_assignees": assignees,
            "research_discoveries": [{"id": "fact:proposed_marker:one", "fact": "The marker is reliable.", "required": 1}],
        },
    }


def _entity_exists(platform: Platform, entity_id: str) -> bool:
    try:
        platform.store.get_entity(entity_id)
    except KeyError:
        return False
    return True


def _event_count(platform: Platform) -> int:
    return int(platform.store.connection.execute("SELECT COUNT(*) FROM event_log").fetchone()[0])


def test_research_source_nobody_assigned_can_reach_is_rejected_and_rolled_back(tmp_path: Path) -> None:
    platform = _town(tmp_path, "unreachable-source")
    try:
        events_before = _event_count(platform)
        catalog_before = [item["id"] for item in platform.store.research_source_catalog()]
        result = platform.submit_entity_proposal("god", _source_proposal("无人可读的残页", ["梅岚"]))

        assert result["status"] == "rejected" and result["failed_check"] == "entity_unreachable"
        assert result["retryable"] is True
        # The rejection names the assignee and who could do the work instead.
        assert "梅岚" in result["errors"][0]
        assert result["errors"][-1] == "characters who can research configured sources now: 罗杉"
        # The whole proposal rolled back: no entity, no event, no new catalog row.
        assert not _entity_exists(platform, "proposed_marker")
        assert _event_count(platform) == events_before
        assert [item["id"] for item in platform.store.research_source_catalog()] == catalog_before
        status = platform.store.connection.execute(
            "SELECT status FROM intent WHERE intent_type = 'propose_entity'"
        ).fetchall()
        assert [row["status"] for row in status] == ["rejected"]
    finally:
        platform.close()


def test_one_unreachable_assignee_rejects_the_whole_research_source(tmp_path: Path) -> None:
    platform = _town(tmp_path, "unreachable-mixed-assignees")
    try:
        result = platform.submit_entity_proposal("god", _source_proposal("半数可读的残页", ["罗杉", "梅岚"]))
        assert result["status"] == "rejected" and result["failed_check"] == "entity_unreachable"
        assert not _entity_exists(platform, "proposed_marker")
    finally:
        platform.close()


def test_unknown_research_assignee_is_a_schema_rejection(tmp_path: Path) -> None:
    platform = _town(tmp_path, "unknown-assignee")
    try:
        result = platform.submit_entity_proposal("god", _source_proposal("无主残页", ["不存在的人"]))
        assert result["status"] == "rejected" and result["failed_check"] == "entity_schema"
        assert "unknown character in research_assignees: 不存在的人" in result["errors"]
        assert not _entity_exists(platform, "proposed_marker")
    finally:
        platform.close()


def test_research_source_for_a_capable_researcher_is_accepted_by_visible_name(tmp_path: Path) -> None:
    platform = _town(tmp_path, "reachable-source")
    try:
        result = platform.submit_entity_proposal("god", _source_proposal("可读的残页", ["罗杉"]))
        assert result["status"] == "accepted", result
        snapshot = platform.view.option_snapshot("luo_shan")
        assert "behavior:research_boundary_stone|source_id=可读的残页" in snapshot["options"]
    finally:
        platform.close()


def test_near_duplicate_research_source_is_rejected_even_with_a_new_name(tmp_path: Path) -> None:
    platform = _town(tmp_path, "research-novelty-guard")
    try:
        first = _source_proposal("第一份界标记录", ["罗杉"])
        first["description"] = "The boundary marks and old road record still need comparison."
        first["state"]["research_discoveries"] = [{"fact": "界碑刻文与旧商路记录是否同源，尚待核对。", "required": 1}]
        assert platform.submit_entity_proposal("god", first)["status"] == "accepted"
        second = _source_proposal("第二份界标续页", ["罗杉"])
        second["description"] = "A continuation page comparing the boundary marks and old road record."
        second["state"]["research_discoveries"] = [{"fact": "界碑刻文与旧商路记录是否同源，尚待核对。", "required": 1}]
        result = platform.submit_entity_proposal("god", second)
        assert result["status"] == "rejected"
        assert result["failed_check"] == "research_novelty_duplicate"
    finally:
        platform.close()


def test_overlapping_research_novelty_keys_are_rejected(tmp_path: Path) -> None:
    platform = _town(tmp_path, "research-key-overlap")
    try:
        def proposal(name: str, key: str) -> dict:
            return {
                "entity_type": "document",
                "name": name,
                "description": "A trade clue from the old road.",
                "location_id": "old_road",
                "state": {
                    "location_id": "old_road",
                    "research_assignees": ["罗杉"],
                    "research_novelty_key": key,
                    "research_question": "trade confidence cause",
                    "research_discoveries": [{"fact": "trade confidence cause unknown"}],
                },
            }
        assert platform.submit_entity_proposal("god", proposal("贸易线索甲", "old_road_trade_confidence_cause"))["status"] == "accepted"
        result = platform.submit_entity_proposal("god", proposal("贸易线索乙", "trade_confidence_low_cause_unknown"))
        assert result["status"] == "rejected" and result["failed_check"] == "research_novelty_duplicate"
    finally:
        platform.close()


def test_research_source_in_an_unreachable_place_is_rejected(tmp_path: Path) -> None:
    platform = _town(tmp_path, "unreachable-place")
    try:
        with platform.store.transaction():
            platform.store.put_entity("island_nowhere", "location", "无路小岛", {
                "description": "A place no route leads to.", "routes": {}, "initial_state": {},
            })
        proposal = _source_proposal("孤岛残页", ["罗杉"])
        proposal["location_id"] = "island_nowhere"
        proposal["state"]["location_id"] = "island_nowhere"
        result = platform.submit_entity_proposal("god", proposal)
        assert result["status"] == "rejected" and result["failed_check"] == "entity_unreachable"
        assert "罗杉 can research other sources but cannot reach 孤岛残页" in result["errors"][0]
        assert not _entity_exists(platform, "proposed_marker")
    finally:
        platform.close()


def _recipe_proposal() -> dict:
    return {
        "definition_type": "recipe",
        "name": "灯芯芦苇束灯",
        "definition": {
            "duration_minutes": 45,
            "inputs": [{"item": "reed", "quantity": 2}],
            "outputs": [{"item": "lamp", "quantity": 1}],
        },
    }


def test_recipe_is_rejected_when_no_registered_action_can_craft(tmp_path: Path) -> None:
    platform = _town(tmp_path, "recipe-without-craft-action")
    try:
        assert not any(platform._is_recipe_action(item) for item in platform.store.get_definitions("action_def"))
        result = platform.submit_definition_proposal("god", _recipe_proposal())
        assert result["status"] == "rejected" and result["failed_check"] == "definition_schema"
        assert any("no registered Action can craft a recipe" in error for error in result["errors"])
        assert platform.store.get_definitions("recipe_def") == []
    finally:
        platform.close()


def test_recipe_is_accepted_when_a_registered_action_can_craft(tmp_path: Path) -> None:
    platform = Platform(create_instance(Path(__file__).parents[1] / "worlds" / "starter_village", "recipe-with-craft-action", tmp_path / "runtime"))
    try:
        assert any(platform._is_recipe_action(item) for item in platform.store.get_definitions("action_def"))
        result = platform.submit_definition_proposal("god", _recipe_proposal())
        assert result["status"] == "accepted", result
        assert "灯芯芦苇束灯" in {item.get("name") for item in platform.store.get_definitions("recipe_def")}
    finally:
        platform.close()


# --- Revisable institutions ---------------------------------------------------

INSTITUTION = "蓄水配给议事会"


def _record_operation(platform: Platform, minute: int) -> None:
    """Record one verified cistern review the way Platform does when a behavior completes."""

    with platform.store.transaction():
        event_id = platform.store.append_event("cistern.reviewed", "mei_lan", minute, {"behavior_id": "inspect_cistern"})
        platform._record_active_institution_entries(event_id, "cistern.reviewed", "mei_lan", minute, {"behavior_id": "inspect_cistern"})


def _active_institution(platform: Platform) -> dict:
    with platform.store.transaction():
        for minute in (1, 2, 3):
            platform.store.append_event("cistern.reviewed", "mei_lan", minute, {"behavior_id": "inspect_cistern"})
    proposed = platform.submit_institution_proposal("god", {
        "name": INSTITUTION,
        "purpose": "Share the cistern reserve according to verified public work.",
        "evidence": "Three verified cistern reviews show the reserve needs a rule.",
        "evidence_sources": [{"event_types": ["cistern.reviewed"], "behavior_ids": ["inspect_cistern"]}],
        "minimum_evidence": 3,
        "ledger": {"period": "week", "selectors": [{"event_types": ["cistern.reviewed"], "behavior_ids": ["inspect_cistern"], "category": "civic_work", "points": 1}]},
        "policy": {"allocation": {"minimum_per_subject": 1, "contribution_share": 0.5, "category": "civic_work"}},
    })
    assert proposed["status"] == "accepted", proposed
    activated = platform.activate_institution("god", INSTITUTION, "The evidence is sufficient.")
    assert activated["status"] == "accepted", activated
    return platform.store.list_institutions(status="active", limit=1)[0]


def _revise(platform: Platform, **terms) -> dict:
    return platform.revise_institution("god", INSTITUTION, reason="The recorded allocations show the minimum cannot be covered.", **terms)


def test_institution_cannot_be_revised_before_it_has_operated(tmp_path: Path) -> None:
    platform = _town(tmp_path, "revise-needs-evidence")
    try:
        _active_institution(platform)
        result = _revise(platform, policy={"allocation": {"minimum_per_subject": 0.25, "contribution_share": 0.5}})
        assert result["status"] == "rejected" and result["failed_check"] == "institution_evidence"
        assert result["observed_operations"] == 0
        assert platform.store.list_institutions(status="active", limit=1)[0]["policy"]["allocation"]["minimum_per_subject"] == 1
    finally:
        platform.close()


def test_revised_policy_governs_the_next_allocation_and_is_visible_to_god(tmp_path: Path) -> None:
    platform = _town(tmp_path, "revise-policy")
    try:
        _active_institution(platform)
        _record_operation(platform, 10)
        before = platform.calculate_institution_allocation(INSTITUTION, ["mei_lan", "han_qiu"], 1.0, "week:0")
        assert before["status"] == "rejected" and before["failed_check"] == "minimum_allocation"  # 2 subjects x 1 > pool of 1

        result = _revise(platform, policy={"allocation": {"minimum_per_subject": 0.25, "contribution_share": 0.8, "category": "civic_work"}})
        assert result["status"] == "accepted", result
        assert result["institution"]["revision"] == 1
        assert "at least 0.25 per subject" in result["terms"] and "80%" in result["terms"]

        after = platform.calculate_institution_allocation(INSTITUTION, ["mei_lan", "han_qiu"], 1.0, "week:0")
        assert after["status"] == "accepted"
        assert {row["subject"]: row["minimum"] for row in after["allocations"]} == {"mei_lan": 0.25, "han_qiu": 0.25}

        event = next(item for item in platform.store.recent_events(limit=50) if item["event_type"] == "institution.revised")
        assert event["before"]["policy"]["allocation"]["minimum_per_subject"] == 1
        assert event["after"]["policy"]["allocation"]["minimum_per_subject"] == 0.25
        assert event["revision"] == 1 and event["reason"].startswith("The recorded allocations")

        view = platform.view.build_decision_view("god", role="god")
        terms = next(item["terms"] for item in view["active_institutions"] if item["name"] == INSTITUTION)
        assert "at least 0.25 per subject" in terms and "revised 1 time(s)" in terms
    finally:
        platform.close()


def test_each_revision_needs_new_operating_evidence(tmp_path: Path) -> None:
    platform = _town(tmp_path, "revise-twice")
    try:
        _active_institution(platform)
        _record_operation(platform, 10)
        assert _revise(platform, policy={"allocation": {"minimum_per_subject": 0.5, "contribution_share": 0.5}})["status"] == "accepted"

        again = _revise(platform, policy={"allocation": {"minimum_per_subject": 0.4, "contribution_share": 0.5}})
        assert again["status"] == "rejected" and again["failed_check"] == "institution_evidence"

        _record_operation(platform, 20)
        second = _revise(platform, policy={"allocation": {"minimum_per_subject": 0.4, "contribution_share": 0.5}})
        assert second["status"] == "accepted" and second["institution"]["revision"] == 2
    finally:
        platform.close()


def test_revised_ledger_selectors_change_what_future_work_is_credited(tmp_path: Path) -> None:
    platform = _town(tmp_path, "revise-ledger")
    try:
        institution = _active_institution(platform)
        _record_operation(platform, 10)

        def credited(event_type: str, minute: int) -> float:
            with platform.store.transaction():
                event_id = platform.store.append_event(event_type, "luo_shan", minute, {})
                platform._record_active_institution_entries(event_id, event_type, "luo_shan", minute, {})
            totals = platform.store.institution_ledger_totals(institution["institution_id"])
            return sum(float(row["total"]) for row in totals if row["subject_id"] == "luo_shan")

        assert credited("resource.gathered", 11) == 0
        result = _revise(platform, ledger={"selectors": [
            {"event_types": ["cistern.reviewed"], "behavior_ids": ["inspect_cistern"], "category": "civic_work", "points": 1},
            {"event_types": ["resource.gathered"], "category": "civic_work", "points": 2},
        ]})
        assert result["status"] == "accepted", result
        assert credited("resource.gathered", 12) == 2
        assert platform.store.list_institutions(status="active", limit=1)[0]["ledger"]["period"] == "week"
    finally:
        platform.close()


@pytest.mark.parametrize(
    "terms, expected",
    [
        ({"policy": {"allocation": {"minimum_per_subject": 1, "contribution_share": 1.5}}}, "contribution_share must be a number from 0 to 1"),
        ({"policy": {"allocation": {"minimum_per_subject": -1, "contribution_share": 0.5}}}, "minimum_per_subject must be a number of at least 0"),
        ({"policy": {"allocation": {"minimum_per_subject": 1, "bonus": 3}}}, "policy.allocation has unknown field(s): bonus"),
        ({"policy": {"rationing": {}}}, "policy has unknown field(s): rationing"),
        ({"policy": {"note": "x"}}, "policy.allocation is required when replacing the policy"),
        ({"ledger": {"selectors": [{"category": "civic_work", "points": 1}]}}, "a selector without a source would credit every event"),
        ({"ledger": {"selectors": [{"event_types": ["x.done"], "points": 0}]}}, "points must be a positive number"),
        ({"ledger": {"period": "day", "selectors": [{"event_types": ["x.done"]}]}}, "the ledger period cannot change after activation"),
        ({"ledger": {"period": "week"}}, "ledger.selectors is required when replacing the ledger"),
    ],
)
def test_revision_that_cannot_be_settled_is_rejected_and_changes_nothing(tmp_path: Path, terms: dict, expected: str) -> None:
    platform = _town(tmp_path, "revise-invalid")
    try:
        before = _active_institution(platform)
        _record_operation(platform, 10)
        result = _revise(platform, **terms)
        assert result["status"] == "rejected" and result["failed_check"] == "institution_terms", result
        assert any(expected in error for error in result["errors"]), result["errors"]
        after = platform.store.list_institutions(status="active", limit=1)[0]
        assert after["policy"] == before["policy"] and after["ledger"] == before["ledger"]
        assert int(after.get("revision", 0) or 0) == 0
    finally:
        platform.close()


def test_revision_must_change_something_and_must_name_an_exact_active_institution(tmp_path: Path) -> None:
    platform = _town(tmp_path, "revise-guards")
    try:
        current = _active_institution(platform)
        _record_operation(platform, 10)
        unchanged = _revise(platform, policy=current["policy"])
        assert unchanged["status"] == "rejected" and unchanged["failed_check"] == "institution_unchanged"

        nothing = _revise(platform)
        assert nothing["status"] == "rejected" and nothing["failed_check"] == "institution_terms"

        unknown = platform.revise_institution("god", "不存在的议事会", policy=current["policy"], reason="No such institution exists here.")
        assert unknown["status"] == "rejected" and unknown["failed_check"] == "institution_choice"
        assert unknown["available_choices"] == [INSTITUTION]

        vague = platform.revise_institution("god", INSTITUTION, policy={"allocation": {"minimum_per_subject": 0.5}}, reason="short")
        assert vague["status"] == "rejected" and vague["failed_check"] == "reason"

        outsider = platform.revise_institution("mei_lan", INSTITUTION, policy={"allocation": {"minimum_per_subject": 0.5}}, reason="A character asks for more water.")
        assert outsider["status"] == "rejected" and outsider["failed_check"] == "institution_authority"
    finally:
        platform.close()


def test_proposal_with_a_ledger_selector_that_credits_every_event_is_rejected(tmp_path: Path) -> None:
    platform = _town(tmp_path, "proposal-selector-guard")
    try:
        result = platform.submit_institution_proposal("god", {
            "name": "万事记功会",
            "purpose": "Credit everything the town does, whatever it is.",
            "evidence_sources": [{"event_types": ["cistern.reviewed"]}],
            "minimum_evidence": 1,
            "ledger": {"period": "week", "selectors": [{"category": "civic_work", "points": 1}]},
            "policy": {"allocation": {"minimum_per_subject": 1, "contribution_share": 2}},
        })
        assert result["status"] == "rejected" and result["failed_check"] == "institution_terms"
        assert any("without a source" in error for error in result["errors"])
        assert any("contribution_share" in error for error in result["errors"])
        assert platform.store.list_institutions(limit=10) == []
    finally:
        platform.close()


def test_god_revises_through_the_tool_and_other_roles_cannot(tmp_path: Path) -> None:
    from sim2.agents.tools import AgentToolExecutor, ROLE_TOOLS, tool_schemas_for_role

    platform = _town(tmp_path, "revise-tool")
    try:
        _active_institution(platform)
        _record_operation(platform, 10)
        assert "revise_institution" in ROLE_TOOLS["god"]
        assert all("revise_institution" not in ROLE_TOOLS[role] for role in ROLE_TOOLS if role != "god")
        schema = next(item["function"] for item in tool_schemas_for_role("god") if item["function"]["name"] == "revise_institution")
        assert schema["parameters"]["required"] == ["institution", "reason"]

        executor = AgentToolExecutor(platform, role="god", actor_id="god")
        result = executor.execute("revise_institution", {
            "institution": INSTITUTION,
            "reason": "The recorded allocations show the minimum cannot be covered.",
            "policy": {"allocation": {"minimum_per_subject": 0.5, "contribution_share": 0.5}},
        })
        assert result["status"] == "accepted" and result["institution"]["revision"] == 1

        refused = AgentToolExecutor(platform, role="story", actor_id="story").execute("revise_institution", {"institution": INSTITUTION, "reason": "Story should not do this."})
        assert refused["status"] == "rejected" and "not allowed" in refused["message"]
    finally:
        platform.close()


def test_revision_is_a_configurable_wake_event() -> None:
    compiled = normalize_evolution_config({"wakes": [{"on": "institution.revised", "for": "story"}]})
    story = compiled["event_wake_policy"]["consumers"]["story"]
    assert story["direct_event_types"] == ["institution.revised"]
    assert story["external_event_types"] == ["institution.revised"]


# --- World configuration that keeps the loops open ------------------------------


def test_drinking_is_offered_only_when_the_configured_threshold_is_reached(tmp_path: Path) -> None:
    platform = _town(tmp_path, "drink-gate")
    try:
        def offered(hydration: float) -> bool:
            agent = platform.store.get_agent("mei_lan")
            agent["inventory"]["蓄水"] = 3
            agent["needs"]["hydration"] = hydration
            with platform.store.transaction():
                platform.store.update_agent("mei_lan", agent)
            snapshot = platform.view.option_snapshot("mei_lan")
            return "behavior:drink_water" in snapshot["options"]

        threshold = platform.store.get_meta("rules")["context_values"]["hydration_alert_threshold"]
        assert offered(threshold) is True
        assert offered(threshold + 1) is False
        assert offered(100) is False
    finally:
        platform.close()


def test_gathered_reeds_renew_so_the_gathering_loop_does_not_end(tmp_path: Path) -> None:
    platform = _town(tmp_path, "reed-renewal")
    try:
        reeds = platform.store.get_entity("reed_bundle")
        reeds["initial_state"]["quantity"] = 0.0
        with platform.store.transaction():
            platform.store.put_entity("reed_bundle", "resource", reeds["name"], {key: value for key, value in reeds.items() if key not in {"id", "entity_type", "name"}})
            platform._apply_minute_effects(1440, agents=[])
        assert platform.store.get_entity("reed_bundle")["initial_state"]["quantity"] == 6.0
        assert any(item["event_type"] == "resource.regenerated" and item["resource_id"] == "reed_bundle" for item in platform.store.recent_events(limit=20))
    finally:
        platform.close()


class ScriptedProvider:
    """Provider double that answers every call with one fixed response."""

    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[dict] = []

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.calls.append({"system": system, "context": context, "tools": tools, "messages": messages})
        return dict(self.response)


def test_outcome_review_wake_lets_god_revise_the_institution_it_reviewed(tmp_path: Path) -> None:
    platform = _town(tmp_path, "outcome-review-revises")
    try:
        _active_institution(platform)
        _record_operation(platform, 10)
        with platform.store.transaction():
            event_ids = [
                platform.store.append_event("institution.allocation_completed", "mei_lan", 20 + offset, {"institution": INSTITUTION})
                for offset in range(3)
            ]
        for offset, event_id in enumerate(event_ids):
            platform._wake_configured_evolution_consumers(event_id, "institution.allocation_completed", "mei_lan", 20 + offset)
        tasks = [item for item in platform.ready_evolution_tasks() if item["task_type"] == "event_group:institution_outcome_review"]
        assert len(tasks) == 1 and tasks[0]["role"] == "god"

        provider = ScriptedProvider({
            "intent_type": "revise_institution",
            "institution": INSTITUTION,
            "reason": "Three settlements left the pool below the minimum, so no settlement could cover everyone.",
            "policy": {"allocation": {"minimum_per_subject": 0.25, "contribution_share": 0.5, "category": "civic_work"}},
        })
        turn = AgentEngine(platform, provider).run(
            role="god", actor_id="god", task_type=tasks[0]["task_type"], source_events=tasks[0]["source_events"],
        )
        assert turn.result["status"] == "accepted", turn.result
        assert turn.result["institution"]["revision"] == 1
        prompt = provider.calls[0]["context"]
        assert "revise_institution" in prompt and "[terms: allocation: at least 1 per subject" in prompt
        stored = platform.store.list_institutions(status="active", limit=1)[0]
        assert stored["policy"]["allocation"]["minimum_per_subject"] == 0.25
    finally:
        platform.close()


# --- Blocked options name the failing check ---------------------------------------


def _blocked_needs(platform: Platform, agent_id: str, label: str) -> list[str]:
    snapshot = platform.view.option_snapshot(agent_id)
    return next(item["needs"] for item in snapshot["blocked"] if item["choice"] == label)


def _set_quantity(platform: Platform, entity_id: str, quantity: float) -> None:
    entity = platform.store.get_entity(entity_id)
    entity["initial_state"]["quantity"] = quantity
    with platform.store.transaction():
        platform.store.put_entity(entity_id, entity["entity_type"], entity["name"], {key: value for key, value in entity.items() if key not in {"id", "entity_type", "name"}})


def test_blocked_gathering_names_the_exhausted_resource(tmp_path: Path) -> None:
    platform = _town(tmp_path, "blocked-resource")
    try:
        _set_quantity(platform, "reed_bundle", 0.0)
        assert _blocked_needs(platform, "luo_shan", "Gather Lantern Reeds") == [
            "Action gather_resource cannot start: required resource reed_bundle is unavailable",
        ]
        assert "behavior:gather_reeds" not in platform.view.option_snapshot("luo_shan")["options"]

        _set_quantity(platform, "reed_bundle", 6.0)
        snapshot = platform.view.option_snapshot("luo_shan")
        assert "behavior:gather_reeds" in snapshot["options"]
        assert all(item["choice"] != "Gather Lantern Reeds" for item in snapshot["blocked"])
    finally:
        platform.close()


def test_blocked_settlement_names_the_pool_and_policy_minimum_until_the_policy_is_revised(tmp_path: Path) -> None:
    platform = _town(tmp_path, "blocked-settlement")
    try:
        _active_institution(platform)
        created = platform.submit_definition_proposal("god", {
            "definition_type": "behavior",
            "name": "Settle Reserve Allocation",
            "assign_to": ["梅岚"],
            "definition": {
                "summary": "Apply the active ledger to the reserve.",
                "entry_conditions": ["actor.can_inspect", "actor.alive"],
                "nodes": [{"type": "action", "action_id": "Settle Institution Allocation", "parameters": {
                    "institution": INSTITUTION, "pool_entity": "cistern_water", "item": "water", "period": "week",
                }}],
                "success_results": ["allocation_completed"],
                "failure_results": ["allocation_failed"],
            },
        })
        assert created["status"] == "accepted", created
        behavior_id = next(item["id"] for item in platform.store.get_definitions("bt_def") if item.get("agent_label") == "Settle Reserve Allocation")

        _set_quantity(platform, "cistern_water", 1.5)  # three characters x minimum 1 = 3
        assert _blocked_needs(platform, "mei_lan", "Settle Reserve Allocation") == [
            "Action settle_institution_allocation cannot start: the pool holds 1.5 but the policy minimum needs 3",
        ]
        assert f"behavior:{behavior_id}" not in platform.view.option_snapshot("mei_lan")["options"]

        _record_operation(platform, 10)
        assert _revise(platform, policy={"allocation": {"minimum_per_subject": 0.25, "contribution_share": 0.5, "category": "civic_work"}})["status"] == "accepted"
        snapshot = platform.view.option_snapshot("mei_lan")
        assert f"behavior:{behavior_id}" in snapshot["options"]
        assert all(item["choice"] != "Settle Reserve Allocation" for item in snapshot["blocked"])
    finally:
        platform.close()


# --- World-level prompts keep their evidence, institutions and task ----------------


def _god_prompt(platform: Platform, *, max_chars: int) -> str:
    view = platform.view.build_decision_view("god", role="god")
    return render_context(view, "Review the institution against its recorded results.", max_chars=max_chars)


def test_a_slightly_over_budget_god_prompt_drops_whole_reference_sections_first(tmp_path: Path) -> None:
    platform = _town(tmp_path, "compaction-drops-sections")
    try:
        _active_institution(platform)
        full = _god_prompt(platform, max_chars=10**7)
        limit = len(full) - 200
        prompt = _god_prompt(platform, max_chars=limit)
        assert len(prompt) <= limit
        assert "Omitted world_trends" in prompt and "<world_trends>" not in prompt
        assert "<world_outcomes>" in prompt  # removal stops as soon as the prompt fits
        assert "[terms: allocation: at least 1 per subject" in prompt
        assert prompt.index("<social_institutions>") < prompt.index("<decision>")
    finally:
        platform.close()


def test_a_far_over_budget_god_prompt_still_ends_with_the_institutions_and_the_task(tmp_path: Path) -> None:
    platform = _town(tmp_path, "compaction-keeps-task")
    try:
        _active_institution(platform)
        limit = len(_god_prompt(platform, max_chars=10**7)) // 2
        prompt = _god_prompt(platform, max_chars=limit)
        assert len(prompt) <= limit
        assert "Earlier context was compacted" in prompt
        assert "[terms: allocation: at least 1 per subject" in prompt
        assert prompt.index("<social_institutions>") < prompt.index("<decision>")
        assert prompt.rstrip().endswith("</decision>")
        assert prompt.startswith("<protocol>")
    finally:
        platform.close()


def test_god_history_skips_minute_ticks_and_does_not_repeat_the_trigger(tmp_path: Path) -> None:
    platform = _town(tmp_path, "history-without-ticks")
    try:
        _step(platform, 25)  # more minute ticks than the history window holds
        with platform.store.transaction():
            older = platform.store.append_event("cistern.reviewed", "mei_lan", 25, {"summary": "Older review of the cistern."})
            trigger = platform.store.append_event("cistern.reviewed", "mei_lan", 26, {"summary": "Trigger review of the cistern."})
        history = platform.view.build_decision_view("god", role="god")["recent_history"]
        assert all(item["event_type"] != "world_minute.advanced" for item in history)
        assert {older, trigger} <= {item["event_id"] for item in history}

        provider = WaitProvider()
        AgentEngine(platform, provider).run(role="god", actor_id="god", task_type="event:cistern.reviewed", source_events=[{"event_id": trigger}])
        prompt = provider.calls[0]["context"]
        assert "world_minute.advanced" not in prompt
        assert prompt.count("Trigger review of the cistern.") == 1  # evidence only, not again in the history
        assert prompt.count("Older review of the cistern.") == 1
    finally:
        platform.close()


def test_research_submission_note_follows_the_wake_evidence_not_its_wording(tmp_path: Path) -> None:
    platform = _town(tmp_path, "research-note")
    try:
        view = platform.view.build_decision_view("god", role="god")
        view["evolution_trigger"] = {
            "id": "event_group:research_synthesis",
            "condition": "configured evidence group",
            "required_result": "Create one new research source for the researchers.",
            "evidence_sources": [{"event_types": ["research.completed"]}],
        }
        assert "For this research continuation" in render_context(view, "Review the evidence.")

        # The same wording on a wake with other evidence lists the source as one lever among several.
        view["evolution_trigger"]["evidence_sources"] = [{"event_types": ["world.stagnation"]}]
        assert "For this research continuation" not in render_context(view, "Review the evidence.")
    finally:
        platform.close()
