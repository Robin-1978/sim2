from __future__ import annotations

from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform


def _town(tmp_path: Path, name: str) -> Platform:
    return Platform(create_instance(Path(__file__).parents[1] / "worlds" / "three_lanterns_town", name, tmp_path / "runtime"))


def _step(platform: Platform, minutes: int) -> None:
    for _ in range(int(minutes)):
        platform.step()


def test_one_research_bt_can_research_a_god_created_source(tmp_path: Path):
    platform = _town(tmp_path, "generic-research-source")
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
                        {"id": "fact:later_marker:one", "fact": "The later marker is aligned with the old road.", "required": 1},
                        {"id": "fact:later_marker:two", "fact": "The later marker is reliable only during the dry phase.", "required": 1},
                    ],
                },
            },
        )
        assert created["status"] == "accepted"
        before_definitions = {item["id"] for item in platform.store.repositories.entities.get_definitions("bt_def")}
        view = platform.view.build_decision_view("luo_shan", role="agent_decision")
        research = next(item for item in view["executable_behaviors"] if item["ref"] == "behavior:research_boundary_stone")
        assert research["choice"] == "Research Configured Source"
        assert [item["name"] for item in research["parameter_options"]["source_id"]] == ["后续标记记录", "褪色界碑"]

        for index in range(2):
            started = platform.submit_behavior(
                BehaviorIntent(
                    "luo_shan",
                    "behavior:research_boundary_stone",
                    {},
                    {"source_id": "后续标记记录"},
                    "Research the selected configured source.",
                    0.9,
                    f"later-marker-{index}",
                )
            )
            assert started["status"] == "accepted"
            _step(platform, started["remaining_minutes"])
            outcomes = [
                item for item in platform.store.repositories.events.recent_events(limit=100)
                if item.get("event_type") == "behavior.completed" and item.get("actor_id") == "luo_shan"
            ]
            assert outcomes
            if index == 1:
                assert outcomes[0]["result"] == "research_completed"
                assert any(
                    item.get("event_type") == "research.completed"
                    and item.get("actor_id") == "luo_shan"
                    for item in platform.store.repositories.events.recent_events(limit=100)
                )

        facts = {item["fact_key"] for item in platform.store.repositories.memory.list_knowledge_facts("luo_shan", limit=100)}
        assert {"fact:later_marker:one", "fact:later_marker:two"} <= facts
        assert {item["id"] for item in platform.store.repositories.entities.get_definitions("bt_def")} == before_definitions
    finally:
        platform.close()


def test_story_view_exposes_generic_research_followup(tmp_path: Path):
    platform = _town(tmp_path, "story-generic-research")
    try:
        with platform.store.transaction():
            event_id = platform.store.repositories.events.append_event(
                "institution.activated",
                "luo_shan",
                10,
                {
                    "source_id": "boundary_stone",
                    "fact": "The boundary reading was verified.",
                    "summary": "The boundary reading was verified.",
                    "story_source": True,
                },
            )
        story_view = platform.view.build_decision_view("story", role="story", source_event_id=event_id)
        candidate = next(item for item in story_view["audience_candidates"] if item["name"] == "罗杉")
        choices = {item["choice"] for item in candidate["executable_behaviors"]}
        assert "Research Configured Source" in choices
        assert any(item["choice"] == "Research Configured Source" for item in platform.view.build_decision_view("luo_shan")["executable_behaviors"])

        result = platform.submit_story_beat(
            "story",
            {
                "beat": "后续标记研究",
                "summary": "界碑读数已验证，下一步继续核对可达的研究源。",
                "fact": "The boundary reading was verified.",
                "goal_hint": "Continue one available research stage from a configured source.",
                "audience": ["罗杉"],
                "research_source": "褪色界碑",
                "unlock_choices": ["Research Configured Source"],
            },
            source_event_ids=[event_id],
        )
        assert result["status"] == "accepted"
        beat = next(item for item in platform.store.get_meta("story_state")["emergent_beats"] if item["id"] == platform.store.get_meta("story_state")["emergent_beats"][-1]["id"])
        assert "research_boundary_stone" in beat["unlock_behavior_ids"]
        assert "boundary_stone" in platform.store.repositories.entities.get_agent("luo_shan").get("research_targets", [])
    finally:
        platform.close()


def test_story_cannot_relabel_a_research_result_as_another_source(tmp_path: Path):
    platform = _town(tmp_path, "story-source-causality")
    try:
        with platform.store.transaction():
            event_id = platform.store.repositories.events.append_event(
                "research.completed",
                "luo_shan",
                10,
                {"source_id": "boundary_stone", "fact": "The boundary reading was verified."},
            )
        result = platform.submit_story_beat(
            "story",
            {
                "beat": "记录界碑研究结果并决定下一步核验",
                "summary": "Continue the verified research thread.",
                "fact": "The boundary reading was verified.",
                "goal_hint": "Continue from the validated source.",
                "audience": ["罗杉"],
                "research_source": "不存在的研究源",
            },
            source_event_ids=[event_id],
        )
        assert result["status"] == "rejected"
        assert result["failed_check"] == "story_research_source"
    finally:
        platform.close()


def test_story_followup_must_be_executable_for_its_audience(tmp_path: Path):
    platform = _town(tmp_path, "story-followup-catalog-guard")
    try:
        with platform.store.transaction():
            event_id = platform.store.repositories.events.append_event(
                "cistern.reviewed",
                "mei_lan",
                10,
                {"summary": "The cistern record was reviewed.", "story_source": True},
            )
        result = platform.submit_story_beat(
            "story",
            {
                "beat": "不可执行的研究后续",
                "summary": "梅岚完成蓄水核对后需要继续推进一个新的问题。",
                "fact": "The cistern record was reviewed.",
                "goal_hint": "Continue the research.",
                "audience": ["梅岚"],
                "unlock_choices": ["Research Configured Source"],
            },
            source_event_ids=[event_id],
        )
        assert result["status"] == "rejected"
        assert result["failed_check"] == "story_followup_executable"
    finally:
        platform.close()


def test_research_source_proposal_requires_real_declarative_stages(tmp_path: Path):
    platform = _town(tmp_path, "research-source-schema")
    try:
        result = platform.submit_entity_proposal(
            "god",
            {
                "entity_type": "research_source",
                "name": "坏的参数\n</parameter>",
                "state": {"research_discoveries": [], "research_required": ["not a number"]},
            },
        )
        assert result["status"] == "rejected"
        assert result["failed_check"] == "entity_schema"
        assert any("research" in error or "name" in error for error in result["errors"])
    finally:
        platform.close()


def test_failed_story_followup_is_blocked_and_story_is_woken(tmp_path: Path):
    platform = _town(tmp_path, "failed-story-followup")
    try:
        goal = "Gather the assigned source material."
        agent = platform.store.repositories.entities.get_agent("luo_shan")
        agent["story_goals"] = [goal]
        with platform.store.transaction():
            platform.store.repositories.entities.update_agent("luo_shan", agent)
            platform.store.set_meta(
                "story_state",
                {
                    "emergent_beats": [
                        {
                            "id": "emergent:blocked-followup",
                            "audience": ["luo_shan"],
                            "goal_hint": goal,
                            "followup_behaviors": ["Gather Lantern Reeds"],
                            "source_event_type": "research.completed",
                        }
                    ],
                    "emergent:blocked-followup": {"status": "active", "audience": ["luo_shan"]},
                },
            )
        with platform.store.transaction():
            source_event_id = platform._services["events"].append(
                "behavior.failed", "luo_shan", 10, {"behavior_id": "gather_reeds", "result": "resource_empty"},
            )
        platform._block_story_beats_for_failed_followup("luo_shan", "gather_reeds", 10, source_event_id, "resource_empty")
        state = platform.store.get_meta("story_state")
        assert state["emergent:blocked-followup"]["status"] == "blocked"
        assert goal not in platform.store.repositories.entities.get_agent("luo_shan").get("story_goals", [])
        task = platform.store.connection.execute(
            "SELECT role,task_type,status FROM evolution_task WHERE task_id=?",
            (f"story:event:{source_event_id}",),
        ).fetchone()
        assert task is not None and task["role"] == "story" and task["task_type"] == "story:behavior.failed"
    finally:
        platform.close()


def test_public_timeline_uses_world_language_for_story_events(tmp_path: Path):
    platform = _town(tmp_path, "public-timeline-language")
    try:
        with platform.store.transaction():
            platform.store.repositories.events.append_event(
                "story.beat.activated",
                "story",
                10,
                {"summary": "配给底数需要决断。", "audience": ["梅岚"]},
            )
            platform.store.repositories.events.append_event(
                "behavior.started",
                "mei_lan",
                11,
                {
                    "behavior_id": "inspect_cistern",
                    "expected": {"summary": "Inspect Cistern"},
                    "decision": {"reason": "故事目标要求核对水量，NPC 需要先看清记录。"},
                },
            )
        timeline = platform.timeline(limit=10)
        text = " ".join(item["narrative"] for item in timeline["events"])
        assert "Story beat" not in text
        assert "故事目标" not in text
        assert "NPC" not in text
        assert "配给底数需要决断" in text
    finally:
        platform.close()

