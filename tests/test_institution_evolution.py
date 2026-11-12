from __future__ import annotations

import json
from pathlib import Path

from sim2.agents.engine import AgentEngine
from sim2.agents.tools import tool_schemas_for_role
from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform


class SequenceProvider:
    """Small provider double that exercises the same tool-call boundary as a model."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.calls.append({"system": system, "context": context, "tools": tools, "messages": messages})
        return dict(self.responses.pop(0))


def _town(tmp_path: Path, instance: str) -> Platform:
    return Platform(create_instance(Path(__file__).parents[1] / "worlds" / "three_lanterns_town", instance, tmp_path / "runtime"))


def step_n(platform: Platform, minutes: int) -> None:
    for _ in range(int(minutes)):
        platform.step()


def test_only_god_can_create_institution_and_npc_tools_do_not_expose_it(tmp_path: Path):
    platform = _town(tmp_path, "institution-authority")
    try:
        assert "propose_institution" not in {item["function"]["name"] for item in tool_schemas_for_role("agent_decision")}
        assert "propose_institution" not in {item["function"]["name"] for item in tool_schemas_for_role("story")}
        assert "propose_institution" in {item["function"]["name"] for item in tool_schemas_for_role("god")}
        rejected = platform.submit_institution_proposal("mei_lan", {"name": "Public Ration", "purpose": "Review a scarce public reserve fairly."})
        assert rejected["status"] == "rejected"
        assert rejected["failed_check"] == "institution_authority"
    finally:
        platform.close()


def test_institution_evidence_selectors_support_behavior_only_and_deduplicate(tmp_path: Path):
    platform = _town(tmp_path, "institution-evidence-selectors")
    try:
        with platform.store.transaction():
            first = platform.store.append_event("behavior.completed", "mei_lan", 1, {"behavior_id": "inspect_cistern"})
            second = platform.store.append_event("behavior.completed", "mei_lan", 2, {"behavior_id": "inspect_cistern"})
            platform.store.append_event("behavior.completed", "mei_lan", 3, {"behavior_id": "other_behavior"})
            platform.store.append_event("behavior.completed", "mei_lan", 4, {"behavior_id": "inspect_cistern"})
            platform.store.append_event("behavior.completed", "mei_lan", 5, {"malformed": True})
        result = platform.submit_institution_proposal("god", {
            "name": "Verified Work Register",
            "purpose": "Count verified public work before a later allocation decision.",
            "evidence_sources": [
                {"behavior_ids": ["inspect_cistern"]},
                {"event_types": ["behavior.completed"], "behavior_ids": ["inspect_cistern"]},
            ],
            "minimum_evidence": 3,
        })
        assert result["status"] == "accepted"
        institution = platform.store.list_institutions(limit=1)[0]
        assert institution["evidence_count_at_creation"] == 3
    finally:
        platform.close()


def test_story_npc_evidence_and_god_institution_chain_uses_llm_tools(tmp_path: Path):
    """A short three-role ReAct chain proves feedback, authority and ledger wiring."""

    platform = _town(tmp_path, "institution-llm-chain")
    try:
        with platform.store.transaction():
            source_event_id = platform.store.append_event(
                "cistern.reviewed",
                "mei_lan",
                1,
                {"summary": "The cistern record was audited; a public ration baseline is unresolved.", "story_source": True},
            )

        story_provider = SequenceProvider([{
            "tool_call": {"name": "propose_story_beat", "arguments": {
                "beat": "公开配给决断",
                "audience": ["梅岚"],
                "summary": "蓄水记录已核对，公共配给决断进入下一幕。",
                "fact": "蓄水记录已核对，公共配给底数仍未决断。",
                "goal_hint": "把配给问题带到公开讨论中，等待世界层制度决定。",
                "reason": "The validated cistern review needs a public continuation.",
            }},
        }])
        story_turn = AgentEngine(platform, story_provider, max_iterations=1).run(
            role="story", actor_id="story", task_type="event:cistern.reviewed", source_event_id=source_event_id,
        )
        assert story_turn.result["status"] == "accepted"

        npc_provider = SequenceProvider([{
            "tool_call": {"name": "request_behavior", "arguments": {
                "choice": "Inspect Cistern", "confidence": 0.9,
                "reason": "The story makes the unresolved public ration decision the next issue; audit the configured record.",
            }},
        }])
        npc_view = platform.decision_view("mei_lan")
        assert npc_view["active_story_beats"]
        assert "公共配给" in str(npc_view["active_story_beats"])
        npc_turn = AgentEngine(platform, npc_provider, max_iterations=1).run(
            role="agent_decision", actor_id="mei_lan", task_type="agent_plan_selection",
        )
        assert npc_turn.result["status"] == "accepted"
        started = platform.store.get_agent("mei_lan")["initial_state"].get("active_bt_instance")
        assert started

        # The behavior itself is the NPC evidence; no NPC institution write is needed.
        execution = platform.store.get_agent("mei_lan")["initial_state"].get("remaining_minutes", 0)
        for _ in range(int(execution) + 1):
            platform.step()
        props = dict(platform.store.get_meta("world_properties", {}) or {})
        props["cistern_reviews"] = 3
        with platform.store.transaction():
            platform.store.set_meta("world_properties", props)
        platform.step()
        god_tasks = [item for item in platform.ready_evolution_tasks() if str(item["task_id"]).startswith("trigger:cistern_review_threshold")]
        assert god_tasks and god_tasks[0]["role"] == "god"

        god_provider = SequenceProvider([
            {"tool_call": {"name": "propose_institution", "arguments": {
                "name": "Public Ration Ledger",
                "purpose": "Record verified public work before a scarce reserve is allocated.",
                "scope": "town",
                "evidence": "Three configured cistern reviews left the public ration baseline unresolved.",
                "evidence_sources": [{"event_types": ["cistern.reviewed"]}],
                "minimum_evidence": 1,
                "ledger": {"period": "week", "selectors": [{"event_types": ["behavior.completed"], "category": "public_work", "points": 1}]},
                "policy": {"allocation": {"minimum_per_subject": 1, "contribution_share": 0.5, "category": "public_work"}},
            }}},
            {"tool_call": {"name": "approve_institution", "arguments": {
                "institution": "Public Ration Ledger",
                "reason": "The threshold is backed by repeated verified reviews.",
            }}},
        ])
        god_turn = AgentEngine(platform, god_provider, max_iterations=1).run(
            role="god", actor_id="god", task_type="trigger:cistern_review_threshold",
        )
        assert god_turn.result["status"] == "accepted"
        assert "evidence selectors" in god_provider.calls[0]["context"]
        assert "observed: 1" in god_provider.calls[0]["context"]
        assert {item["function"]["name"] for item in god_provider.calls[0]["tools"]} == {"propose_institution", "wait"}
        pending_god_tasks = [item for item in platform.ready_evolution_tasks() if item["role"] == "god" and item["task_type"] == "event:institution.proposed"]
        assert pending_god_tasks
        approve_turn = AgentEngine(platform, god_provider, max_iterations=1).run(
            role="god", actor_id="god", task_type="event:institution.proposed",
        )
        assert approve_turn.result["status"] == "accepted"
        active = platform.store.list_institutions(status="active")
        assert [item["name"] for item in active] == ["Public Ration Ledger"]
        assert any(item["role"] == "story" and item["task_type"] == "event:institution.activated" for item in platform.ready_evolution_tasks())
        assert any(item["function"]["name"] == "propose_institution" for item in god_provider.calls[0]["tools"])
        assert "<social_institutions>" in god_provider.calls[-1]["context"]
        assert "Public Ration Ledger" in god_provider.calls[-1]["context"]

        event_id = platform.store.append_event(
            "behavior.completed", "mei_lan", 40, {"behavior_id": "inspect_cistern", "result": "record_inspected"}
        )
        platform._record_active_institution_entries(event_id, "behavior.completed", "mei_lan", 40, {"behavior_id": "inspect_cistern", "result": "record_inspected"})
        ledger = platform.store.institution_ledger_totals(active[0]["institution_id"])
        assert ledger and ledger[0]["subject_id"] == "mei_lan"
        allocation = platform.calculate_institution_allocation("Public Ration Ledger", ["mei_lan", "luo_shan"], 10, "week:0")
        assert allocation["status"] == "accepted"
        assert allocation["allocations"][0]["verified_weight"] == 1
    finally:
        platform.close()


def test_god_and_story_continue_after_institution_activation(tmp_path: Path):
    platform = _town(tmp_path, "institution-continuation")
    try:
        source_ids = []
        with platform.store.transaction():
            for minute in (1, 2, 3):
                source_ids.append(platform.store.append_event(
                    "behavior.completed", "mei_lan", minute,
                    {"behavior_id": "inspect_cistern", "result": "record_inspected"},
                ))
        proposal = platform.submit_institution_proposal("god", {
            "name": "Public Ration Ledger",
            "purpose": "Record verified public work before a scarce reserve is allocated.",
            "scope": "town",
            "evidence_sources": [{"event_types": ["behavior.completed"], "behavior_ids": ["inspect_cistern"]}],
            "minimum_evidence": 3,
            "ledger": {"period": "week", "selectors": [{"event_types": ["behavior.completed"], "category": "public_work", "points": 1}]},
            "policy": {"allocation": {"minimum_per_subject": 1, "contribution_share": 0.5}},
        })
        assert proposal["status"] == "accepted"
        assert platform.activate_institution("god", "Public Ration Ledger", "The evidence threshold was reached.")["status"] == "accepted"
        active = platform.store.list_institutions(status="active")[0]

        for source_id, minute in zip(source_ids, (10, 11, 12)):
            platform._record_active_institution_entries(
                source_id, "behavior.completed", "mei_lan", minute,
                {"behavior_id": "inspect_cistern", "result": "record_inspected"},
            )
        review_tasks = [
            item for item in platform.ready_evolution_tasks()
            if item["role"] == "god" and item["task_type"] == "event_group:institution_ledger_review"
        ]
        assert review_tasks
        ledger = platform.store.institution_ledger_totals(active["institution_id"])
        assert ledger[0]["total"] == 3

        god_provider = SequenceProvider([{"intent": "wait", "reason": "The active ledger has been reviewed; no further structural change is justified yet."}])
        god_turn = AgentEngine(platform, god_provider, max_iterations=1).run(
            role="god", actor_id="god", task_type="event_group:institution_ledger_review",
        )
        assert god_turn.result["status"] == "wait"
        assert "Public Ration Ledger" in god_provider.calls[0]["context"]
        assert "verified ledger totals" in god_provider.calls[0]["context"]
        story_tasks = [item for item in platform.ready_evolution_tasks() if item["role"] == "story" and item["task_type"] == "event:institution.activated"]
        assert story_tasks
    finally:
        platform.close()


def test_god_creates_new_settlement_behavior_after_ledger_review(tmp_path: Path):
    platform = _town(tmp_path, "institution-settlement")
    try:
        with platform.store.transaction():
            for minute in (1, 2, 3):
                platform.store.append_event("behavior.completed", "mei_lan", minute, {"behavior_id": "inspect_cistern", "result": "record_inspected"})
        assert platform.submit_institution_proposal("god", {
            "name": "Public Ration Ledger",
            "purpose": "Record verified public work before a scarce reserve is allocated.",
            "scope": "town",
            "evidence_sources": [{"event_types": ["behavior.completed"], "behavior_ids": ["inspect_cistern"]}],
            "minimum_evidence": 3,
            "ledger": {"period": "week", "selectors": [{"event_types": ["behavior.completed"], "category": "public_work", "points": 1}]},
            "policy": {"allocation": {"minimum_per_subject": 1, "contribution_share": 0.5}},
        })["status"] == "accepted"
        assert platform.activate_institution("god", "Public Ration Ledger", "Evidence threshold reached.")["status"] == "accepted"
        active = platform.store.list_institutions(status="active")[0]
        for source_id, minute in zip((10, 11, 12), (10, 11, 12)):
            platform._record_active_institution_entries(source_id, "behavior.completed", "mei_lan", minute, {"behavior_id": "inspect_cistern", "result": "record_inspected"})
        review_task = next(item for item in platform.ready_evolution_tasks() if item["task_type"] == "event_group:institution_ledger_review")
        god_provider = SequenceProvider([{"tool_call": {"name": "propose_definition", "arguments": {
            "definition_type": "behavior",
            "name": "Settle Active Institution Allocation",
            "definition": {
                "summary": "Apply the active institution allocation to its eligible subjects.",
                "entry_conditions": ["actor.can_inspect", "actor.alive"],
                "success_results": ["allocation_completed"],
                "failure_results": ["allocation_failed"],
                    "nodes": [{"type": "action", "action_id": "Settle Institution Allocation", "parameters": {
                        "institution": "context.institution", "pool_entity": "context.pool_entity", "item": "context.item", "period": "context.period",
                    }}],
                "outcome_events": [{"event_type": "institution.allocation_applied", "wake": ["story"]}],
            },
            "assign_to": ["梅岚"],
            "evidence": "Three verified ledger entries justify a repeatable settlement behavior.",
        }}}])
        turn = AgentEngine(platform, god_provider, max_iterations=1).run(role="god", actor_id="god", task_type="event_group:institution_ledger_review", source_events=review_task.get("source_events") or [])
        assert turn.result["status"] == "accepted"
        behavior = next(item for item in platform.store.get_definitions("bt_def") if item.get("agent_label") == "Settle Active Institution Allocation")
        assert behavior["id"] in platform.store.get_agent("mei_lan").get("unlocked_bt_ids", [])
        before = platform.store.get_entity("cistern_water")["initial_state"]["quantity"]
        started = platform.submit_behavior(BehaviorIntent(
            "mei_lan", f"behavior:{behavior['id']}",
            {"institution": "Public Ration Ledger", "pool_entity": "cistern_water", "item": "water", "period": "week"},
            {}, "Apply the new behavior created from the ledger review.", 0.9, "settlement-run-1",
        ))
        assert started["status"] == "accepted"
        step_n(platform, started["remaining_minutes"])
        assert platform.store.get_entity("cistern_water")["initial_state"]["quantity"] < before
        assert platform.store.get_agent("mei_lan")["inventory"].get("water", 0) > 0
        assert any(item["event_type"] == "institution.allocation_completed" for item in platform.store.recent_events(limit=100))
    finally:
        platform.close()


def test_research_source_can_gain_new_stages_and_supersede_old_facts(tmp_path: Path):
    """Research remains open-ended through ordinary God entity proposals.

    The runtime does not contain a quota or town-specific continuation.  A
    God-created source publishes two declarative questions; the existing
    research Action executes each one, and the second result revises the first
    through the generic knowledge lifecycle.
    """

    platform = _town(tmp_path, "research-evolution")
    try:
        source = platform.submit_entity_proposal("god", {
            "entity_type": "document",
            "entity_id": "later_research_source",
            "name": "后续观测记录",
            "description": "A newly proposed source for the next research cycle.",
            "location_id": "north_watch",
            "state": {
                "location_id": "north_watch",
                "readable": True,
                "research_discoveries": [
                    {"id": "researched:later_source:initial", "fact": "The first reading suggests the old route remains usable."},
                    {"id": "researched:later_source:revision", "fact": "A second reading finds the route is safe only during the dry phase.", "supersedes": "researched:later_source:initial"},
                ],
            },
        })
        assert source["status"] == "accepted"
        assert any(item["role"] == "god" and item["task_type"] == "event:entity.created" for item in platform.ready_evolution_tasks())
        behavior = platform.submit_definition_proposal("god", {
            "definition_type": "behavior",
            "name": "Continue Later Source Research",
            "definition": {
                "summary": "Continue research on a source proposed during world evolution.",
                "entry_conditions": ["actor.can_research", "actor.alive"],
                "nodes": [
                    {"type": "action", "action_id": "travel_to", "parameters": {"location_id": "north_watch"}},
                    {"type": "action", "action_id": "research_source", "parameters": {"source_id": "later_research_source"}},
                ],
                "success_results": ["research_completed"],
                "failure_results": ["failed"],
            },
            "assign_to": ["罗杉"],
            "evidence": "A new source was created from a validated research continuation.",
        })
        assert behavior["status"] == "accepted"
        assert any(item["role"] == "god" and item["task_type"] == "event:definition.registered" for item in platform.ready_evolution_tasks())
        behavior_def = next(item for item in platform.store.get_definitions("bt_def") if item.get("agent_label") == "Continue Later Source Research")

        for stage in range(2):
            started = platform.submit_behavior(BehaviorIntent(
                "luo_shan", f"behavior:{behavior_def['id']}", {}, {},
                "Continue the currently available research stage.", 0.9, f"research-stage-{stage}-a",
            ))
            assert started["status"] == "accepted"
            step_n(platform, int(started["remaining_minutes"]))
            started = platform.submit_behavior(BehaviorIntent(
                "luo_shan", f"behavior:{behavior_def['id']}", {}, {},
                "Complete the currently available research stage.", 0.9, f"research-stage-{stage}-b",
            ))
            assert started["status"] == "accepted"
            step_n(platform, int(started["remaining_minutes"]))

        facts = {item["fact_key"]: item for item in platform.store.list_knowledge_facts("luo_shan", limit=100)}
        assert facts["researched:later_source:initial"]["status"] == "superseded"
        assert facts["researched:later_source:revision"]["status"] == "active"
        assert facts["researched:later_source:revision"]["supersedes"] == "researched:later_source:initial"
        rows = platform.store.connection.execute(
            "SELECT event_id,event_type,actor_id,world_minute,data_json FROM event_log WHERE event_type='research.completed' ORDER BY event_id"
        ).fetchall()
        completed = [
            {"event_id": row["event_id"], "event_type": row["event_type"], "actor_id": row["actor_id"], **json.loads(row["data_json"] or "{}")}
            for row in rows
            if json.loads(row["data_json"] or "{}").get("source_id") == "later_research_source"
        ]
        assert len(completed) == 2
        assert {item.get("stage_id") for item in completed} == {"researched:later_source:initial", "researched:later_source:revision"}
        assert any(item["role"] == "story" and item["task_type"] == "event:research.completed" for item in platform.ready_evolution_tasks())
        assert not any(item.get("ref") == f"behavior:{behavior_def['id']}" for item in platform.decision_view("luo_shan")["executable_behaviors"])
    finally:
        platform.close()


def test_ten_round_story_god_chain_keeps_creating_followups(tmp_path: Path):
    """A bounded ten-turn contract test catches silent evolution dead ends."""

    platform = _town(tmp_path, "ten-round-evolution")
    try:
        with platform.store.transaction():
            for minute in (1, 2, 3):
                platform.store.append_event("cistern.reviewed", "mei_lan", minute, {"behavior_id": "inspect_cistern", "result": "record_inspected"})
        proposal = platform.submit_institution_proposal("god", {
            "name": "Test Public Ledger",
            "purpose": "Record verified public work before a shared reserve is allocated.",
            "evidence_sources": [{"event_types": ["cistern.reviewed"], "behavior_ids": ["inspect_cistern"]}],
            "minimum_evidence": 3,
            "ledger": {"period": "week", "selectors": [{"event_types": ["cistern.reviewed"], "behavior_ids": ["inspect_cistern"], "category": "civic_work", "points": 1}]},
            "policy": {"allocation": {"minimum_per_subject": 1, "contribution_share": 0.5}},
        })
        assert proposal["status"] == "accepted"
        assert platform.activate_institution("god", "Test Public Ledger", "Three verified reviews.")["status"] == "accepted"

        # The first three turns are the initial God/God/Story handoff.
        story_event = next(item for item in platform.ready_evolution_tasks() if item["task_type"] == "event:institution.activated")
        story_provider = SequenceProvider([{"tool_call": {"name": "propose_story_beat", "arguments": {
            "beat": "公开配给议事",
            "audience": ["梅岚"],
            "summary": "制度激活后，梅岚需要组织首次公开配给议事。",
            "fact": " Test Public Ledger 已经激活。",
            "goal_hint": "组织首次结算，决定本期公共资源分配。",
            "reason": "The activated institution needs a concrete first settlement step.",
        }}}])
        initial_story_turn = AgentEngine(platform, story_provider, max_iterations=1).run(
            role="story", actor_id="story", task_type=story_event["task_type"], source_events=story_event.get("source_events") or [],
        )
        assert initial_story_turn.result["status"] == "accepted"
        platform.complete_evolution_task(story_event["task_id"])

        rounds = 10
        completed_turns = 3
        for cycle in range(rounds):
            # Each cycle represents a later evidence window. Reset only the
            # configured cooldown in this test; the runtime still requires
            # three new persisted ledger events before God is woken.
            state = dict(platform.store.get_meta("god:event_wake_group_state", {}) or {})
            state.setdefault("institution_ledger_review", {})["last_wake_minute"] = -100000
            platform.store.set_meta("god:event_wake_group_state", state)
            base = 10 + cycle * 3
            for minute in (base, base + 1, base + 2):
                event_id = platform.store.append_event("cistern.reviewed", "mei_lan", minute, {"behavior_id": "inspect_cistern", "result": "record_inspected"})
                platform._record_active_institution_entries(event_id, "cistern.reviewed", "mei_lan", minute, {"behavior_id": "inspect_cistern", "result": "record_inspected"})
            review = next(item for item in platform.ready_evolution_tasks() if item["task_type"] == "event_group:institution_ledger_review")
            behavior_id = f"settle_public_ledger_{cycle}"
            god_provider = SequenceProvider([{"tool_call": {"name": "propose_definition", "arguments": {
                "definition_type": "behavior",
                "name": f"Settle Public Ledger {cycle}",
                "definition": {
                    "summary": "Settle one active institution allocation from the verified ledger.",
                    "entry_conditions": ["actor.can_inspect", "actor.alive"],
                    "nodes": [{"type": "action", "action_id": "Settle Institution Allocation", "parameters": {"institution": "Test Public Ledger", "pool_entity": "cistern_water", "item": "water", "period": "week"}}],
                    "success_results": ["allocation_completed"],
                    "failure_results": ["allocation_failed"],
                    "outcome_events": [{"event_type": "institution.allocation_completed", "wake": ["story"]}],
                },
                "assign_to": ["梅岚"],
                "evidence": "Three new verified ledger entries require an executable settlement follow-up.",
            }}}])
            god_turn = AgentEngine(platform, god_provider, max_iterations=1).run(
                role="god", actor_id="god", task_type=review["task_type"], source_events=review.get("source_events") or [],
            )
            assert god_turn.result["status"] == "accepted"
            platform.complete_evolution_task(review["task_id"])
            completed_turns += 1

            definition_task = next(item for item in platform.ready_evolution_tasks() if item["task_type"] == "event:definition.registered")
            wait_provider = SequenceProvider([{"tool_call": {"name": "wait", "arguments": {"reason": "The new settlement behavior is registered and can now be executed by a character."}}}])
            wait_turn = AgentEngine(platform, wait_provider, max_iterations=1).run(
                role="god", actor_id="god", task_type=definition_task["task_type"], source_events=definition_task.get("source_events") or [],
            )
            assert wait_turn.result["status"] == "wait"
            platform.complete_evolution_task(definition_task["task_id"])
            completed_turns += 1

            behavior = next(item for item in platform.store.get_definitions("bt_def") if item.get("agent_label") == f"Settle Public Ledger {cycle}")
            started = platform.submit_behavior(BehaviorIntent(
                "mei_lan", f"behavior:{behavior['id']}", {}, {}, "Execute the newly registered settlement behavior.", 0.9, f"settle-round-{cycle}",
            ))
            assert started["status"] == "accepted"
            step_n(platform, int(started["remaining_minutes"]))
            story_task = next(item for item in platform.ready_evolution_tasks() if item["task_type"] == "event:institution.allocation_completed")
            period_label = ("首期", "二期", "三期", "四期", "五期", "六期", "七期", "八期", "九期", "十期")[cycle]
            story_provider = SequenceProvider([{"tool_call": {"name": "propose_story_beat", "arguments": {
                "beat": f"结算进展{period_label}",
                "audience": ["梅岚"],
                "summary": f"账本{period_label}结算已经完成，下一期需要继续依据新记录复核公共分配。",
                "fact": f"Test Public Ledger {period_label}结算已完成。",
                "goal_hint": "继续记录下一期公共分配结果并复核制度效果。",
                "reason": "The validated allocation result supports the next ledger review.",
            }}}])
            story_turn = AgentEngine(platform, story_provider, max_iterations=1).run(
                role="story", actor_id="story", task_type=story_task["task_type"], source_events=story_task.get("source_events") or [],
            )
            assert story_turn.result["status"] == "accepted"
            platform.complete_evolution_task(story_task["task_id"])
            completed_turns += 1

        assert completed_turns == 33
        assert len([item for item in platform.store.recent_events(limit=500) if item["event_type"] == "institution.allocation_completed"]) >= rounds
        assert len([item for item in platform.store.list_institutions(status="active")]) == 1
    finally:
        platform.close()

