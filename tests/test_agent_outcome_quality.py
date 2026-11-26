import json
from contextlib import closing
from pathlib import Path

import pytest

from scripts.verify_agent_outcomes import DEFENSE, RELIEF, WORLDS, run_case
from sim2.agents.engine import AgentEngine
from sim2.agents.outcome_quality import evaluate_behavior_outcome
from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform


class ChoiceProvider:
    def __init__(self, behavior_ref: str):
        self.behavior_ref = behavior_ref

    def complete_json(self, *, system, context):
        assert "<executable_behaviors>" in context
        return {"intent_type": "select_behavior", "behavior_ref": self.behavior_ref, "reason": "分配这一批应急物资。"}


@pytest.mark.parametrize("world", ["windward_skyfall", "tidebound_commons"])
@pytest.mark.parametrize(("relief_due", "choice"), [(False, DEFENSE), (True, RELIEF)])
def test_outcome_scenarios_grade_committed_allocations(tmp_path: Path, world: str, relief_due: bool, choice: str):
    with closing(Platform(create_instance(WORLDS / world, "outcome", tmp_path))) as platform:
        report = run_case(platform, world, ChoiceProvider(choice), relief_due=relief_due)
        assert report["passed"], report
        assert report["outcome"]["outcome_event_id"]


def test_acceptance_and_unrelated_completion_cannot_pass_the_outcome_eval(tmp_path: Path):
    with closing(Platform(create_instance(WORLDS / "windward_skyfall", "cancelled-outcome", tmp_path))) as platform:
        with platform.store.transaction():
            platform.store.repositories.metadata.set("world_minute", 719)
        platform.step()
        turn = AgentEngine(platform, ChoiceProvider(DEFENSE)).run(role="agent_decision", actor_id="alan")
        assert turn.result["status"] == "accepted", turn.result
        events = platform.store.repositories.events
        with platform.store.transaction():
            events.append_event("behavior.completed", "alan", 720, {"execution_id": "some-other-execution"})
        options = {"actor_id": "alan", "deadline_minute": 970, "resource_deltas": {"emergency_copper_cache": -3}}
        pending = evaluate_behavior_outcome(turn, events, **options)
        assert not pending["passed"] and pending["grade"] == "outcome_pending"
        assert evaluate_behavior_outcome(turn, events, observed_minute=970, **options)["grade"] == "deadline_missed"
        platform.interrupt_behavior("alan", "Allocation cancelled before completion.", hard=True)
        cancelled = evaluate_behavior_outcome(turn, events, **options)
        assert not cancelled["passed"] and cancelled["grade"] == "outcome_mismatch"
        assert not cancelled["checks"]["behavior_completed"]
        assert not cancelled["checks"]["resource_deltas_match"]


def test_native_behavior_selection_keeps_internal_evidence_and_executes(tmp_path: Path):
    class ToolProvider:
        def complete_json(self, *, system, context, tools=None, messages=None):
            return {"tool_calls": [{"id": "select", "type": "function", "function": {
                "name": "request_behavior",
                "arguments": json.dumps({"choice": "把应急铜料用于保船", "reason": "保留铜料以备修船。", "confidence": 0.9}),
            }}]}

    with closing(Platform(create_instance(WORLDS / "windward_skyfall", "native-outcome", tmp_path))) as platform:
        report = run_case(platform, "windward_skyfall", ToolProvider(), relief_due=False)
        assert report["passed"], report
        assert report["selected"] == DEFENSE


@pytest.mark.parametrize(("world", "owner", "other", "claimant"), [
    ("windward_skyfall", "alan", "ke", "阿筝"),
    ("tidebound_commons", "mara", "ivo", "伊沃"),
])
def test_normal_npc_turn_sees_deadline_claimants_and_cost_of_delay(tmp_path, world, owner, other, claimant):
    class CaptureProvider:
        def complete_json(self, *, system, context):
            assert "<decision_briefs>" in context
            assert "250 world minutes" in context
            assert claimant in context
            assert "If unresolved:" in context
            return {"intent_type": "wait", "reason": "Wait for more information."}

    with closing(Platform(create_instance(WORLDS / world, "deadline", tmp_path))) as platform:
        metadata = platform.store.repositories.metadata
        with platform.store.transaction():
            metadata.set("world_minute", 719)
        platform.step()
        turn = AgentEngine(platform, CaptureProvider()).run(role="agent_decision", actor_id=owner)
        assert turn.result["status"] == "wait", turn.result
        assert not platform.decision_view(other)["decision_briefs"]
        platform.step()
        assert platform.decision_view(owner)["decision_briefs"][0]["remaining_minutes"] == 249
        with platform.store.transaction():
            state = metadata.get("world_properties")
            state["supply_consultation"] = 3
            metadata.set("world_properties", state)
        assert not platform.decision_view(owner)["decision_briefs"]
