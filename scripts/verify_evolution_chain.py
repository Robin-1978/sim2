"""Run a short real-provider Story/God continuation chain.

This check deliberately uses a fresh SQLite instance. Existing instances keep
the world-package snapshot that was present when they were created.

    python scripts/verify_evolution_chain.py --provider remote
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from sim2.agents.engine import AgentEngine
from sim2.bootstrap.service import create_instance
from sim2.llm.config import LLMConfig
from sim2.llm.gateway import LLMRouter
from sim2.platform.runtime import Platform


def _summary(turn: Any) -> dict[str, Any]:
    return {
        "status": turn.result.get("status"),
        "tool": turn.result.get("tool"),
        "failed_check": turn.result.get("failed_check"),
        "iterations": turn.iterations,
        "tool_names": [step.get("name") for step in turn.transcript if step.get("step") == "tool"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="remote")
    args = parser.parse_args()
    config = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"
    router = LLMRouter(LLMConfig.load())
    with tempfile.TemporaryDirectory(prefix="sim2-evolution-chain-") as runtime:
        platform = Platform(create_instance(config, "real-evolution-chain", Path(runtime)))
        try:
            entities = platform.store.repositories.entities
            events = platform.store.repositories.events
            social = platform.store.repositories.social
            with platform.store.transaction():
                for minute in (1, 2, 3):
                    events.append_event(
                        "cistern.reviewed", "mei_lan", minute,
                        {"behavior_id": "inspect_cistern", "result": "record_inspected"},
                    )
            properties = dict(platform.store.get_meta("world_properties", {}) or {})
            properties["cistern_reviews"] = 3
            with platform.store.transaction():
                platform.store.set_meta("world_properties", properties)
            platform.step()
            god_provider = router.new_provider_for_name(args.provider, role="god")
            story_provider = router.new_provider_for_name(args.provider, role="story")

            trigger = next(item for item in platform.ready_evolution_tasks() if str(item["task_id"]).startswith("trigger:cistern_review_threshold"))
            god_proposal = AgentEngine(platform, god_provider).run(
                role="god", actor_id="god", task_type=(
                    f"trigger:{trigger['trigger_id']}"
                    if trigger.get("trigger_id")
                    else str(trigger.get("task_type") or "god_local")
                ),
            )
            if god_proposal.result.get("status") != "accepted":
                raise RuntimeError(f"God did not create the institution: {god_proposal.result}")
            platform.complete_evolution_task(trigger["task_id"])

            proposed = next(
                item for item in platform.ready_evolution_tasks()
                if item.get("role") == "god"
                and item.get("task_type") in {"event:institution.proposed", "event_group:institution_proposal_review"}
            )
            god_activation = AgentEngine(platform, god_provider).run(
                role="god", actor_id="god", task_type=proposed["task_type"], source_events=proposed.get("source_events") or [],
            )
            if god_activation.result.get("status") != "accepted":
                raise RuntimeError(f"God did not activate the institution: {god_activation.result}")
            platform.complete_evolution_task(proposed["task_id"])

            activated = next(item for item in platform.ready_evolution_tasks() if item["task_type"] == "event:institution.activated")
            story = AgentEngine(platform, story_provider).run(
                role="story", actor_id="story", task_type=activated["task_type"], source_events=activated.get("source_events") or [],
            )
            if story.result.get("status") != "accepted":
                raise RuntimeError(f"Story did not create a continuation: {story.result}")
            platform.complete_evolution_task(activated["task_id"])

            active = social.list_institutions(status="active")[0]
            for minute in (4, 5, 6):
                event_id = events.append_event(
                    "cistern.reviewed", "mei_lan", minute,
                    {"behavior_id": "inspect_cistern", "result": "record_inspected"},
                )
                platform._record_active_institution_entries(
                    event_id, "cistern.reviewed", "mei_lan", minute,
                    {"behavior_id": "inspect_cistern", "result": "record_inspected"},
                )
            ledger_review = next(item for item in platform.ready_evolution_tasks() if item["task_type"] == "event_group:institution_ledger_review")
            god_behavior = AgentEngine(platform, god_provider).run(
                role="god", actor_id="god", task_type=ledger_review["task_type"], source_events=ledger_review.get("source_events") or [],
            )
            if god_behavior.result.get("status") != "accepted":
                raise RuntimeError(f"God did not create the settlement behavior: {god_behavior.result}")

            report = {
                "provider": args.provider,
                "institution": active.get("name"),
                "god_proposal": _summary(god_proposal),
                "god_activation": _summary(god_activation),
                "story_continuation": _summary(story),
                "god_settlement_behavior": _summary(god_behavior),
                "registered_god_behaviors": [
                    item.get("agent_label") for item in entities.get_definitions("bt_def")
                    if item.get("definition_source") == "god_proposal"
                ],
                "status": "passed",
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        finally:
            platform.close()


if __name__ == "__main__":
    raise SystemExit(main())
