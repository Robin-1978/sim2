"""Story beat validation gates: every rejection leaves state untouched, every accept commits once."""

from __future__ import annotations

from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform

WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
BOUNDARY_BEAT = "记录界碑研究结果并决定下一步核验"


def _town(tmp_path: Path, name: str) -> Platform:
    return Platform(create_instance(WORLD, name, tmp_path / "runtime"))


def _event(platform: Platform, event_type: str, minute: int, payload: dict) -> int:
    with platform.store.transaction():
        return platform.store.repositories.events.append_event(event_type, "luo_shan", minute, payload)


def _channel_event(platform: Platform, minute: int, number: int, source: str) -> int:
    return _event(platform, "exploration.completed", minute, {
        "summary": f"Luo Shan counted {number} dry channels north of the lanterns.",
        "fact": f"{number} dry channels run north.",
        "source_id": source,
    })


def _channel_beat(number: int, label: str, **extra: object) -> dict:
    return {
        "beat": label,
        "summary": f"Luo Shan counted {number} dry channels north of the lanterns.",
        "fact": f"{number} dry channels run north.",
        "audience": ["罗杉"],
        **extra,
    }


def _intents(platform: Platform) -> list[tuple[str, str]]:
    rows = platform.store.connection.execute(
        "SELECT intent_type, status FROM intent WHERE intent_type='propose_story_beat' ORDER BY rowid"
    ).fetchall()
    return [(row["intent_type"], row["status"]) for row in rows]


def _activations(platform: Platform) -> list[dict]:
    return [
        item for item in platform.store.repositories.events.recent_events(limit=200)
        if item["event_type"] == "story.beat.activated"
    ]


def _story_state(platform: Platform) -> dict:
    return dict(platform.store.repositories.metadata.get("story_state", {}) or {})


def _assert_rejected_untouched(platform: Platform, result: dict, failed_check: str) -> None:
    assert result["status"] == "rejected", result
    assert result["failed_check"] == failed_check, result
    assert _intents(platform)[-1] == ("propose_story_beat", "rejected")
    assert _story_state(platform) == {}
    assert _activations(platform) == []


def test_emergent_summary_must_be_between_eight_and_five_hundred_characters(tmp_path: Path) -> None:
    platform = _town(tmp_path, "story-summary-bounds")
    try:
        for summary in ("short", "y" * 501):
            result = platform.submit_story_beat("story", {"beat": "Anything", "summary": summary})
            _assert_rejected_untouched(platform, result, "story_summary")
            assert result["retryable"] is True
    finally:
        platform.close()


def test_emergent_beat_needs_a_validated_source_event(tmp_path: Path) -> None:
    platform = _town(tmp_path, "story-needs-evidence")
    try:
        result = platform.submit_story_beat(
            "story", {"beat": "A new thing", "summary": "A valid emergent summary about water."}
        )
        _assert_rejected_untouched(platform, result, "story_evidence")
    finally:
        platform.close()


def test_unknown_choice_reports_no_configured_beats_before_any_evidence(tmp_path: Path) -> None:
    platform = _town(tmp_path, "story-choice-scope")
    try:
        for choice in ("nope", BOUNDARY_BEAT):
            result = platform.submit_story_beat("story", {"beat": choice})
            _assert_rejected_untouched(platform, result, "story_beat_choice")
            # The configured beat only exists in the scope of its triggering evidence.
            assert result["available_choices"] == []
    finally:
        platform.close()


def test_emergent_audience_and_followups_must_name_visible_things(tmp_path: Path) -> None:
    platform = _town(tmp_path, "story-visible-names")
    try:
        source = _channel_event(platform, 5, 3, "north_channel")
        unknown_audience = platform.submit_story_beat(
            "story", _channel_beat(3, "Count", audience=["Nobody Here"]), source_event_ids=[source]
        )
        _assert_rejected_untouched(platform, unknown_audience, "story_audience")
        unknown_followup = platform.submit_story_beat(
            "story", _channel_beat(3, "Count", unlock_choices=["Not A Behavior"]), source_event_ids=[source]
        )
        _assert_rejected_untouched(platform, unknown_followup, "story_followup_choice")
        assert unknown_followup["invalid_choices"] == ["Not A Behavior"]
        assert [status for _, status in _intents(platform)] == ["rejected", "rejected"]
    finally:
        platform.close()


def test_research_result_cannot_be_credited_to_another_source(tmp_path: Path) -> None:
    platform = _town(tmp_path, "story-source-evidence")
    try:
        created = platform.submit_entity_proposal(
            "god",
            {
                "entity_type": "document",
                "entity_id": "later_marker",
                "name": "后续标记记录",
                "description": "A source introduced after the opening story.",
                "location_id": "north_watch",
                "state": {
                    "location_id": "north_watch",
                    "readable": True,
                    "research_assignees": ["luo_shan"],
                    "research_discoveries": [
                        {"id": "fact:later_marker:one", "fact": "The later marker is aligned with the old road.", "required": 1}
                    ],
                },
            },
        )
        assert created["status"] == "accepted"
        source = _event(platform, "research.completed", 10, {"source_id": "boundary_stone", "fact": "The boundary reading was verified."})
        proposal = {
            "beat": BOUNDARY_BEAT,
            "summary": "Continue the verified research thread.",
            "fact": "The boundary reading was verified.",
            "audience": ["罗杉"],
        }
        result = platform.submit_story_beat("story", {**proposal, "research_source": "后续标记记录"}, source_event_ids=[source])
        _assert_rejected_untouched(platform, result, "story_research_source_evidence")

        accepted = platform.submit_story_beat("story", {**proposal, "research_source": "褪色界碑"}, source_event_ids=[source])
        assert accepted["status"] == "accepted", accepted
        assert _story_state(platform)["boundary_stone_research"]["research_source_id"] == "boundary_stone"
    finally:
        platform.close()


def test_emergent_beat_commits_once_per_validated_result(tmp_path: Path) -> None:
    platform = _town(tmp_path, "story-commit-once")
    try:
        first_source = _channel_event(platform, 5, 3, "north_channel")
        accepted = platform.submit_story_beat("story", _channel_beat(3, "Channel count"), source_event_ids=[first_source])
        assert accepted["status"] == "accepted" and accepted["activated"] is True

        state = _story_state(platform)
        (emergent,) = state["emergent_beats"]
        beat_id = emergent["id"]
        assert state[beat_id]["status"] == "active"
        assert emergent["audience"] == ["luo_shan"]
        assert emergent["source_event_id"] == first_source
        assert emergent["source_event_type"] == "exploration.completed"
        assert "unlock_choices" not in emergent
        keys = {item["fact_key"] for item in platform.store.repositories.memory.list_knowledge_facts("luo_shan", limit=100)}
        assert f"story:{beat_id}" in keys
        assert [item["beat_id"] for item in _activations(platform)] == [beat_id]
        assert _intents(platform) == [("propose_story_beat", "accepted")]

        repeated = platform.submit_story_beat("story", _channel_beat(3, "Channel count again"), source_event_ids=[first_source])
        assert repeated["status"] == "wait"
        assert "already has an active emergent story beat" in repeated["reason"]

        same_fact = _channel_event(platform, 9, 3, "north_channel_2")
        duplicate = platform.submit_story_beat("story", _channel_beat(3, "Channel count third"), source_event_ids=[same_fact])
        assert duplicate["status"] == "wait"
        assert "materially identical" in duplicate["reason"]

        # Waiting is a no-op: nothing else was recorded or activated.
        assert _intents(platform) == [("propose_story_beat", "accepted")]
        assert len(_story_state(platform)["emergent_beats"]) == 1
        assert len(_activations(platform)) == 1
    finally:
        platform.close()


def test_changing_only_numbers_does_not_open_a_second_continuation(tmp_path: Path) -> None:
    platform = _town(tmp_path, "story-digit-duplicate")
    try:
        first = _channel_event(platform, 5, 3, "north_channel")
        assert platform.submit_story_beat("story", _channel_beat(3, "Channel count"), source_event_ids=[first])["status"] == "accepted"

        # Same source, stage and audience; only the counted number differs.
        second = _channel_event(platform, 9, 4, "north_channel")
        repeated = platform.submit_story_beat("story", _channel_beat(4, "Channel count again"), source_event_ids=[second])
        assert repeated["status"] == "wait"
        assert "same audience and continuation" in repeated["reason"]

        # A different source is a different result and may carry its own beat.
        other = _channel_event(platform, 12, 5, "other_channel")
        assert platform.submit_story_beat("story", _channel_beat(5, "Channel count other"), source_event_ids=[other])["status"] == "accepted"
        assert len(_story_state(platform)["emergent_beats"]) == 2
    finally:
        platform.close()
