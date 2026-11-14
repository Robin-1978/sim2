"""Audit every round of the deterministic ten-round evolution contract.

This is intentionally provider-free: it checks the Platform and Story/God
tool boundary with fixed, semantically meaningful proposals, then prints one
sanitized row per round for human review.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from sim2.agents.engine import AgentEngine
from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform


class SequenceProvider:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete_json(self, *, system, context, tools=None, messages=None):
        return dict(self.responses.pop(0))


def step_n(platform: Platform, minutes: int) -> None:
    for _ in range(int(minutes)):
        platform.step()


def tool(name: str, arguments: dict) -> dict:
    return {"tool_call": {"name": name, "arguments": arguments}}


def main() -> int:
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="sim2-ten-round-audit-") as runtime:
        platform = Platform(create_instance(config, "ten-round-audit", Path(runtime)))
        try:
            entities = platform.store.repositories.entities
            events = platform.store.repositories.events
            with platform.store.transaction():
                for minute in (1, 2, 3):
                    events.append_event("cistern.reviewed", "mei_lan", minute, {"behavior_id": "inspect_cistern", "result": "record_inspected"})
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
            activation_task = next(item for item in platform.ready_evolution_tasks() if item["task_type"] == "event:institution.activated")
            activation_story = AgentEngine(platform, SequenceProvider([tool("propose_story_beat", {
                "beat": "公开配给议事", "audience": ["梅岚"],
                "summary": "制度激活后，梅岚需要组织首次公开配给议事。",
                "fact": "Test Public Ledger 已经激活。",
                "goal_hint": "组织首次结算，决定本期公共资源分配。",
                "reason": "The activated institution needs a concrete first settlement step.",
            })]), max_iterations=1).run(role="story", actor_id="story", task_type=activation_task["task_type"], source_events=activation_task.get("source_events") or [])
            assert activation_story.result["status"] == "accepted"
            platform.complete_evolution_task(activation_task["task_id"])

            for cycle in range(10):
                state = dict(platform.store.get_meta("god:event_wake_group_state", {}) or {})
                state.setdefault("institution_ledger_review", {})["last_wake_minute"] = -100000
                platform.store.set_meta("god:event_wake_group_state", state)
                base = 10 + cycle * 3
                for minute in (base, base + 1, base + 2):
                    event_id = events.append_event("cistern.reviewed", "mei_lan", minute, {"behavior_id": "inspect_cistern", "result": "record_inspected"})
                    platform._record_active_institution_entries(event_id, "cistern.reviewed", "mei_lan", minute, {"behavior_id": "inspect_cistern", "result": "record_inspected"})
                review = next(item for item in platform.ready_evolution_tasks() if item["task_type"] == "event_group:institution_ledger_review")
                label = ("首期", "二期", "三期", "四期", "五期", "六期", "七期", "八期", "九期", "十期")[cycle]
                god = AgentEngine(platform, SequenceProvider([tool("propose_definition", {
                    "definition_type": "behavior", "name": f"Settle Public Ledger {cycle}",
                    "definition": {
                        "summary": "Settle one active institution allocation from the verified ledger.",
                        "entry_conditions": ["actor.can_inspect", "actor.alive"],
                        "nodes": [{"type": "action", "action_id": "Settle Institution Allocation", "parameters": {"institution": "Test Public Ledger", "pool_entity": "cistern_water", "item": "water", "period": "week"}}],
                        "success_results": ["allocation_completed"], "failure_results": ["allocation_failed"],
                        "outcome_events": [{"event_type": "institution.allocation_completed", "wake": ["story"], "summary": "The active institution allocation was completed."}],
                    }, "assign_to": ["梅岚"],
                    "evidence": "Three new verified ledger entries require an executable settlement follow-up.",
                })]), max_iterations=1).run(role="god", actor_id="god", task_type=review["task_type"], source_events=review.get("source_events") or [])
                assert god.result["status"] == "accepted"
                platform.complete_evolution_task(review["task_id"])
                definition_task = next(item for item in platform.ready_evolution_tasks() if item["task_type"] == "event:definition.registered")
                god_review = AgentEngine(platform, SequenceProvider([tool("wait", {"reason": "The new settlement behavior is registered and ready for execution."})]), max_iterations=1).run(role="god", actor_id="god", task_type=definition_task["task_type"], source_events=definition_task.get("source_events") or [])
                assert god_review.result["status"] == "wait"
                platform.complete_evolution_task(definition_task["task_id"])
                behavior = next(item for item in entities.get_definitions("bt_def") if item.get("agent_label") == f"Settle Public Ledger {cycle}")
                before = entities.get_entity("cistern_water")["initial_state"]["quantity"]
                started = platform.submit_behavior(BehaviorIntent("mei_lan", f"behavior:{behavior['id']}", {}, {}, "Execute the newly registered settlement behavior.", 0.9, f"audit-settle-{cycle}"))
                assert started["status"] == "accepted"
                step_n(platform, int(started["remaining_minutes"]))
                after = entities.get_entity("cistern_water")["initial_state"]["quantity"]
                story_task = next(item for item in platform.ready_evolution_tasks() if item["task_type"] == "event:institution.allocation_completed")
                story = AgentEngine(platform, SequenceProvider([tool("propose_story_beat", {
                    "beat": f"结算进展{label}", "audience": ["梅岚"],
                    "summary": f"账本{label}结算已经完成，下一期需要继续依据新记录复核公共分配。",
                    "fact": f"Test Public Ledger {label}结算已完成。",
                    "goal_hint": "继续记录下一期公共分配结果并复核制度效果。",
                    "reason": "The validated allocation result supports the next ledger review.",
                })]), max_iterations=1).run(role="story", actor_id="story", task_type=story_task["task_type"], source_events=story_task.get("source_events") or [])
                assert story.result["status"] == "accepted"
                platform.complete_evolution_task(story_task["task_id"])
                ledger = platform.store.repositories.social.institution_ledger_totals(platform.store.repositories.social.list_institutions(status="active")[0]["institution_id"])
                allocation_events = [item for item in platform.store.repositories.events.recent_events(limit=500) if item["event_type"] == "institution.allocation_completed"]
                row = {
                    "round": cycle + 1,
                    "god_proposal": god.result.get("tool"),
                    "god_review": god_review.result.get("status"),
                    "story_summary": story.result.get("beat"),
                    "ledger_entries": sum(int(item.get("entries", 0) or 0) for item in ledger),
                    "reserve_before": before,
                    "reserve_after": after,
                    "water_given_to_mei_lan": entities.get_agent("mei_lan").get("inventory", {}).get("water", 0),
                    "allocation_events": len(allocation_events),
                }
                assert row["god_proposal"] == "propose_definition"
                assert row["story_summary"]
                assert row["reserve_after"] < row["reserve_before"]
                assert row["allocation_events"] == cycle + 1
                rows.append(row)
            print(json.dumps({"status": "passed", "rounds": rows}, ensure_ascii=False, indent=2))
            return 0
        finally:
            platform.close()


if __name__ == "__main__":
    raise SystemExit(main())
