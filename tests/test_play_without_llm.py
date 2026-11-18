from __future__ import annotations

from pathlib import Path

from sim2.api.runtime_supervisor import RuntimeSupervisor
from sim2.api.service import ControlService
from sim2.bootstrap.service import create_instance


def test_manual_play_still_advances_when_no_llm_config_exists(tmp_path: Path, monkeypatch):
    """A fresh checkout can open the web client and complete a first action.

    The deterministic Platform loop still owns the clock, needs and long
    actions.  Only model-driven NPC turns stay idle until llm.json appears.
    """

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    config = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(config, "no-llm-play", tmp_path / "runtime")
    service = ControlService(db_path)
    supervisor = RuntimeSupervisor(service)
    try:
        assert supervisor.llm_configured() is False
        assert supervisor.available_providers() == []
        player_id = "offline-player"
        origin_id = service.player_entry()["origins"][0]["id"]
        assert service.enter_player(player_id, origin_id, name="离线玩家")["status"] == "accepted"
        target_id = next(
            agent["id"]
            for agent in service.platform.store.repositories.entities.list_agents()
            if agent["id"] != player_id
        )
        with service.platform.store.transaction():
            service.platform.store.repositories.events.append_event(
                "dialogue.completed",
                player_id,
                service.platform.status()["world_minute"],
                {"target_id": target_id, "message": "你好", "topic": "general"},
            )
        result = supervisor.advance_manual(3)
        assert result["status"] == "accepted"
        assert result["report"]["decisions"] == 0
        assert result["world_minute"] >= 3
    finally:
        service.close()
