from __future__ import annotations

from pathlib import Path

from sim2.agents.engine import AgentEngine
from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform


class SequenceProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.contexts = []

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.contexts.append({"system": system, "context": context, "tools": tools, "messages": messages})
        return dict(self.responses.pop(0))


def _call(tool_name: str, **arguments):
    return {"tool_call": {"name": tool_name, "arguments": arguments}}


def _step_behavior(platform: Platform, actor_id: str) -> int:
    remaining = int(platform.store.repositories.entities.get_agent(actor_id)["initial_state"].get("remaining_minutes", 0))
    for _ in range(remaining):
        platform.step()
    return remaining


def _town(tmp_path: Path) -> Platform:
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    return Platform(create_instance(config, "npc-evolution-loop", tmp_path / "runtime"))


def _base_behavior(platform: Platform) -> None:
    result = platform.submit_definition_proposal("god", {
        "definition_type": "behavior",
        "name": "Review Public Cistern",
        "assign_to": ["梅岚"],
        "definition": {
            "summary": "Review the public cistern and publish the verified result.",
            "entry_conditions": ["actor.can_inspect", "actor.alive"],
            "nodes": [
                {"type": "action", "action_id": "Travel To", "parameters": {"location_id": "cistern_house"}},
                {"type": "action", "action_id": "Inspect Object", "parameters": {"object_id": "cistern_water"}},
            ],
            "success_results": ["record_inspected"],
            "failure_results": ["denied", "interrupted"],
            "outcome_events": [{"event_type": "cistern.reviewed", "wake": ["story"], "summary": "A public cistern review was completed."}],
        },
    })
    assert result["status"] == "accepted", result


def _base_institution(platform: Platform) -> None:
    with platform.store.transaction():
        for minute in (1, 2, 3):
            event_id = platform.store.repositories.events.append_event(
                "cistern.reviewed", "mei_lan", minute,
                {"behavior_id": "inspect_cistern", "fact": "Three public cistern reviews were persisted."},
            )
            platform._record_active_institution_entries(
                event_id, "cistern.reviewed", "mei_lan", minute,
                {"behavior_id": "inspect_cistern"},
            )
    result = platform.submit_institution_proposal("god", {
        "name": "Public Water Ledger",
        "purpose": "Record verified public work before shared water is allocated.",
        "evidence_sources": [{"event_types": ["cistern.reviewed"]}],
        "minimum_evidence": 3,
        "ledger": {"period": "week", "selectors": [{"event_types": ["behavior.completed"], "category": "public_work", "points": 1}]},
        "policy": {"allocation": {"minimum_per_subject": 1, "contribution_share": 0.5}},
    })
    assert result["status"] == "accepted", result
    assert platform.activate_institution("god", "Public Water Ledger", "Verified reviews established a public need.")["status"] == "accepted"


def test_npc_story_god_loop_returns_to_npc_for_ten_rounds(tmp_path: Path):
    platform = _town(tmp_path)
    try:
        _base_behavior(platform)
        _base_institution(platform)
        opening = platform.submit_story_beat("story", {
            "beat": "Public cistern review",
            "summary": "The verified cistern record requires regular public review.",
            "fact": "The public cistern review process is active.",
            "goal_hint": "Review the cistern and carry the verified result into the next public decision.",
            "audience": ["梅岚"],
            "unlock_choices": ["Review Public Cistern"],
        }, source_event_ids=[platform.store.repositories.events.recent_events(limit=1)[0]["event_id"]])
        assert opening["status"] == "accepted", opening

        audit = []
        labels = ("First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth", "Tenth")
        for round_no, label in enumerate(labels, start=1):
            view = platform.decision_view("mei_lan")
            assert view["active_story_beats"], f"story missing before round {round_no}"
            assert any(item["choice"] == "Review Public Cistern" for item in view["executable_behaviors"])
            npc = AgentEngine(platform, SequenceProvider([_call(
                "request_behavior",
                choice="Review Public Cistern",
                reason="The active story asks for a cistern review, and this behavior is currently executable.",
                confidence=0.9,
            )]), max_iterations=1).run(role="agent_decision", actor_id="mei_lan", task_type="agent_plan_selection")
            assert npc.result["status"] == "accepted", npc.result
            minutes = _step_behavior(platform, "mei_lan")
            assert platform.store.repositories.events.recent_outcomes_for_actor("mei_lan", limit=1)[0]["event_type"] == "behavior.completed"

            story_tasks = [item for item in platform.ready_evolution_tasks() if item["role"] == "story"]
            story_task = max(
                story_tasks,
                key=lambda item: max(
                    [int(event.get("event_id", 0) or 0) for event in (item.get("source_events") or [])]
                    or [int(item.get("source_event_id", 0) or 0)]
                ),
                default=None,
            )
            assert story_task, f"NPC result did not wake Story in round {round_no}"
            story_summary = f"Public review {label} creates the next grounded water decision."
            story = AgentEngine(platform, SequenceProvider([_call(
                "propose_story_beat",
                beat=f"Public water review {label}",
                audience=["梅岚"],
                summary=story_summary,
                fact=f"The {label.lower()} public review result was verified from the NPC outcome.",
                goal_hint="Continue from the verified public-water review with an executable behavior.",
                reason="A persisted NPC outcome provides the next decision point.",
            )]), max_iterations=1).run(role="story", actor_id="story", task_type=story_task["task_type"], source_events=story_task.get("source_events") or [])
            assert story.result["status"] == "accepted", {"result": story.result, "transcript": story.transcript}
            platform.complete_evolution_task(story_task["task_id"])

            god_created = None
            if round_no % 3 == 0:
                god_tasks = [item for item in platform.ready_evolution_tasks() if item["role"] == "god"]
                god_task = max(
                    god_tasks,
                    key=lambda item: max(
                        [int(event.get("event_id", 0) or 0) for event in (item.get("source_events") or [])]
                        or [int(item.get("source_event_id", 0) or 0)],
                    ),
                    default=None,
                )
                assert god_task, "three NPC outcomes did not wake God"
                god_created = f"Deep Cistern Review {round_no}"
                god = AgentEngine(platform, SequenceProvider([_call(
                    "propose_definition",
                    definition_type="behavior",
                    name=god_created,
                    assign_to=["梅岚"],
                    definition={
                        "summary": "Continue the verified public-water work with a deeper cistern review.",
                        "entry_conditions": ["actor.can_inspect", "actor.alive"],
                        "nodes": [
                            {"type": "action", "action_id": "Travel To", "parameters": {"location_id": "cistern_house"}},
                            {"type": "action", "action_id": "Inspect Object", "parameters": {"object_id": "cistern_water"}},
                        ],
                        "success_results": ["record_inspected"],
                        "failure_results": ["denied", "interrupted"],
                        "outcome_events": [{"event_type": "cistern.reviewed", "wake": ["story"], "summary": story_summary}],
                    },
                    evidence="Three verified public-work outcomes support a deeper review behavior.",
                )]), max_iterations=1).run(role="god", actor_id="god", task_type=god_task["task_type"], source_events=god_task.get("source_events") or [])
                assert god.result["status"] == "accepted", god.result
                platform.complete_evolution_task(god_task["task_id"])

            next_view = platform.decision_view("mei_lan")
            next_choices = [item["choice"] for item in next_view["executable_behaviors"]]
            assert next_choices
            if god_created:
                assert god_created in next_choices
            audit.append({
                "round": round_no,
                "npc_reason": npc.response["tool_call"]["arguments"]["reason"],
                "behavior_minutes": minutes,
                "story_summary": story_summary,
                "god_created": god_created,
                "next_choices": next_choices,
            })
        assert len(audit) == 10
        assert sum(item["god_created"] is not None for item in audit) == 3
    finally:
        platform.close()
