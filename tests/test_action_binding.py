from __future__ import annotations

from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform


WORLD = Path(__file__).parents[1] / "worlds" / "three_lanterns_town"


def test_action_without_followup_behavior_is_rejected(tmp_path: Path) -> None:
    platform = Platform(create_instance(WORLD, "action-needs-binding", tmp_path / "runtime"))
    try:
        result = platform.submit_definition_proposal(
            "god",
            {
                "definition_type": "action",
                "name": "Unbound Action",
                "definition": {
                    "duration": {"minutes": 1},
                    "preconditions": ["actor.alive"],
                    "effects": [],
                    "success": {"condition": "done", "result": "done"},
                    "failure": {"condition": "failed", "result": "failed"},
                },
            },
        )
        assert result["status"] == "rejected"
        assert any("followup_behavior" in error for error in result["errors"])
        assert not any(item.get("name") == "Unbound Action" for item in platform.store.get_definitions("action_def"))
    finally:
        platform.close()

