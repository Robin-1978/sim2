from contextlib import closing
from pathlib import Path
from shutil import copytree

import pytest

from sim2.agents.engine import AgentEngine
from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.platform.runtime import Platform
from sim2.simulation.runner import SimulationRunner


WORLDS = Path(__file__).parents[1] / "worlds"


def test_live_claim_survives_another_runner_start_and_recovers_after_close(tmp_path):
    db = create_instance(WORLDS / "windward_skyfall", "claims", tmp_path)
    with closing(Platform(db)) as first, closing(Platform(db)) as second:
        with first.store.transaction():
            first.store.repositories.evolution.put_evolution_task("story-review", "story", "story", "story:review", 0, 0, status="ready")
        assert first.claim_evolution_task("story-review")
        SimulationRunner(first, {})
        SimulationRunner(second, {})
        assert not second.claim_evolution_task("story-review")
        second.retry_evolution_task("story-review", 60, "not owner")
        second.complete_evolution_task("story-review")
        assert second.recover_claimed_evolution_tasks() == []
        first._services["evolution"].close()
        assert second.recover_claimed_evolution_tasks() == ["story-review"]
        assert second.claim_evolution_task("story-review")


def test_failed_behavior_outcome_effect_rolls_back_earlier_effect_and_wake(tmp_path):
    db = create_instance(WORLDS / "three_lanterns_town", "outcome-rollback", tmp_path)
    with closing(Platform(db)) as platform:
        entities = platform.store.repositories.entities
        behavior = next(item for item in entities.get_definitions("bt_def") if item["id"] == "inspect_cistern")
        behavior["outcome_effects"] = [
            {"type": "world_property_set", "property": "trade_confidence", "value": 0.91},
            {"type": "public_event", "event": "review.partial"},
            {"type": "world_resource_delta", "resource_id": "removed_stock", "delta": 1},
        ]
        # An old saved definition can contain a stale resource reference.
        with platform.store.transaction():
            entities.put_definition("bt_def", behavior["id"], behavior)
        submitted = platform.submit_behavior(BehaviorIntent(
            actor_id="mei_lan", behavior_ref="behavior:inspect_cistern", target={}, parameters={},
            reason="Review the cistern.", confidence=1.0, intent_id="effect-rollback",
        ))
        assert submitted["status"] == "accepted"
        for _ in range(60):
            platform.step()
        events = platform.store.repositories.events
        outcome, _ = events.behavior_outcome_evidence("mei_lan", "effect-rollback")
        assert outcome["event_type"] == "behavior.failed"
        assert "existing world resource" in outcome["result"]
        assert platform.store.repositories.metadata.get("world_properties")["trade_confidence"] == 0.42
        assert events.event_type_counts().get("review.partial", 0) == 0


class DisabledProvider:
    def complete_json(self, **kwargs):
        pytest.fail("disabled role contacted the provider")

    def cooldown_remaining(self):
        return 5.0


def test_package_can_omit_evolution_and_continue_the_world(tmp_path):
    pack = tmp_path / "pack"
    copytree(WORLDS / "windward_skyfall", pack)
    (pack / "evolution.yaml").unlink()
    manifest = pack / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace("- evolution.yaml\n", ""))
    with closing(Platform(create_instance(pack, "no-roles", tmp_path))) as platform:
        providers = {role: DisabledProvider() for role in ("story", "god", "director")}
        assert platform.enter_player("hero", "mechanic", name="林晚")["status"] == "accepted"
        result = platform.submit_behavior(BehaviorIntent(
            actor_id="hero", behavior_ref="behavior:rescue_kite_scout", target={}, parameters={},
            reason="答复求救信号", confidence=1.0, intent_id="rescue-without-evolution",
        ))
        assert result["status"] == "accepted"
        report = SimulationRunner(platform, providers).run(31)
        assert report.end_minute == 31
        assert report.bt_decisions > 0
        assert platform.store.repositories.metadata.get("world_properties")["dawn_salvage"] == 1
        assert platform.ready_evolution_tasks() == []
        for role in ("god", "story"):
            assert AgentEngine(platform, providers[role]).run(role=role, actor_id=role).result["reason"] == "role_disabled"
        assert platform.plan_structure_with_provider(providers["director"], 1) is None


def test_story_can_run_without_god_and_director_and_ignore_old_disabled_tasks(tmp_path):
    with closing(Platform(create_instance(WORLDS / "windward_skyfall", "story-only", tmp_path))) as platform:
        store = platform.store
        from sim2.evolution.config import normalize_evolution_config

        with store.transaction():
            store.repositories.evolution.put_evolution_task("old-god", "god", "god", "god_structural", 0, 0, status="ready")
            config = normalize_evolution_config({"roles": {"story": True}, "cadence": {"god": "1m"}})
            store.repositories.metadata.set("evolution", config)
            store.repositories.evolution.put_evolution_task("new-story", "story", "story", "story:review", 0, 0, status="ready")
            store.repositories.evolution.put_evolution_task("new-god", "god", "god", "god_structural", 0, 0, status="ready")
        assert [task["task_id"] for task in platform.ready_evolution_tasks()] == ["new-story"]
        assert platform.claim_evolution_task("old-god") is None
        assert store.connection.execute("SELECT 1 FROM evolution_task WHERE task_id='new-god'").fetchone() is None
        assert platform.claim_evolution_task("new-story")
