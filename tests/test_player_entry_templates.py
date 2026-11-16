from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sim2.api.cli import _dialogue_reply_handler, _ensure_player
from sim2.api.service import ControlService, build_player_view
from sim2.api.text import TextWorldSession
from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from sim2.llm.gateway import LLMRouter
from sim2.platform.runtime import Platform
from sim2.simulation.runner import SimulationRunner
from sim2.world.player_template import DEFAULT_PLAYER_GOAL, PlayerTemplateError, parse_player_template

WORLDS = Path(__file__).parents[1] / "worlds"


def test_player_template_uses_one_canonical_shape():
    template = parse_player_template({
        "entry_location_id": "lantern_square",
        "controller": "manual",
        "capabilities": {"gathering": 0.5},
        "inventory": {"food_ration": 2},
        "origins": [{"id": "traveler"}, {"id": "resident"}],
    })

    assert template["entry_location_id"] == "lantern_square"
    assert template["controller"] == "manual"
    assert template["capabilities"] == {"gathering": 0.5}
    assert template["inventory"] == {"food_ration": 2}
    assert [origin["id"] for origin in template["origins"]] == ["traveler", "resident"]
    assert all(origin["starting_goal"] == DEFAULT_PLAYER_GOAL for origin in template["origins"])

    with pytest.raises(PlayerTemplateError, match="legacy player template"):
        parse_player_template({"player": {"entry_location_id": "lantern_square", "origin_choices": []}})
    with pytest.raises(PlayerTemplateError, match="must be an object"):
        parse_player_template({"entry_location_id": "lantern_square", "origins": ["traveler"]})
    with pytest.raises(PlayerTemplateError, match="controller must"):
        parse_player_template({"entry_location_id": "lantern_square", "origins": [{"id": "traveler"}], "controller": "auto"})
    with pytest.raises(PlayerTemplateError, match="must not be negative"):
        parse_player_template({"entry_location_id": "lantern_square", "origins": [{"id": "traveler"}], "inventory": {"food_ration": -1}})


def test_tidebound_canonical_origins_can_enter_the_world(tmp_path: Path):
    db_path = create_instance(WORLDS / "tidebound_commons", "tidebound-player-entry", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        result = platform.enter_player("player_one", "forager", name="Ari")
        assert result["status"] == "accepted"
        agent = platform.store.repositories.entities.get_agent("player_one")
        assert agent["initial_state"]["location_id"] == "commons_quay"
        assert agent["ultimate_goal"] == "让食物和净水持续流动，同时不把沼泽采空。"
        assert agent["name"] == "Ari"
    finally:
        platform.close()


def test_tidebound_player_gets_configured_capabilities_and_supplies(tmp_path: Path):
    db_path = create_instance(WORLDS / "tidebound_commons", "tidebound-player-profile", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        result = platform.enter_player("player_one", "forager", name="Ari")
        assert result["status"] == "accepted"
        agent = platform.store.repositories.entities.get_agent("player_one")
        assert agent["controller"] == "manual"
        assert agent["capabilities"]["gathering"] == 0.6
        assert agent["capabilities"]["crafting"] == 0.5
        assert agent["capabilities"]["conversation"] == 0.6
        assert agent["inventory"]["fresh_water"] == 1
        assert agent["inventory"]["food_ration"] == 3
        assert agent["inventory"]["shellfish"] == 2
        assert "gather_shellfish" in agent["initial_bt_ids"]

        view = build_player_view(platform, "player_one")
        choices = {item.get("choice") for item in view["actions"]}
        assert {"取淡水", "采贝类", "交换公社情报"}.issubset(choices)
        assert all(item.get("id") != "driftwood" for item in view["nearby_participants"])
        assert all(item.get("type") in {"agent", "player"} for item in view["nearby_participants"])
        assert view["unavailable_actions"]
        assert all(item.get("ref") != "behavior:travel" for item in view["unavailable_actions"])
    finally:
        platform.close()


def test_resuming_an_old_player_backfills_the_template_profile(tmp_path: Path):
    db_path = create_instance(WORLDS / "tidebound_commons", "tidebound-player-resume", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        assert platform.enter_player("player_one", "forager", name="Ari")["status"] == "accepted"
        agent = platform.store.repositories.entities.get_agent("player_one")
        agent["capabilities"] = {}
        agent["inventory"] = {}
        agent["initial_bt_ids"] = []
        agent.pop("controller", None)
        with platform.store.transaction():
            platform.store.repositories.entities.put_agent("player_one", agent, "Ari")

        resumed = platform.enter_player("player_one", "", name="Ari")
        assert resumed["status"] == "accepted"
        assert resumed["resumed"] is True and resumed["migrated"] is True
        restored = platform.store.repositories.entities.get_agent("player_one")
        assert restored["controller"] == "manual"
        assert restored["capabilities"]["gathering"] == 0.5
        assert restored["inventory"]["food_ration"] == 3

        rejected = platform.enter_player("mara", "forager")
        assert rejected["status"] == "rejected"
        assert rejected["failed_check"] == "player_id"
    finally:
        platform.close()


def test_three_lanterns_canonical_origins_can_enter_the_world(tmp_path: Path):
    db_path = create_instance(WORLDS / "three_lanterns_town", "three-lanterns-player-entry", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        result = platform.enter_player("player_one", "resident", name="Ari")
        assert result["status"] == "accepted"
        agent = platform.store.repositories.entities.get_agent("player_one")
        assert agent["initial_state"]["location_id"] == "lantern_square"
        assert agent["ultimate_goal"] == DEFAULT_PLAYER_GOAL
    finally:
        platform.close()


def test_sync_player_template_backfills_an_existing_instance(tmp_path: Path):
    db_path = create_instance(WORLDS / "tidebound_commons", "tidebound-player-sync", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        assert platform.enter_player("player_one", "forager", name="Ari")["status"] == "accepted"
        agent = platform.store.repositories.entities.get_agent("player_one")
        agent["capabilities"] = {}
        agent["inventory"] = {}
        agent["initial_bt_ids"] = []
        agent.pop("controller", None)
        with platform.store.transaction():
            platform.store.repositories.entities.put_agent("player_one", agent, "Ari")
            platform.store.repositories.metadata.set("player_template", {
                "entry_location_id": "commons_quay",
                "origins": [{"id": "forager"}],
            })

        raw_template = yaml.safe_load((WORLDS / "tidebound_commons" / "player_template.yaml").read_text(encoding="utf-8"))
        result = platform.sync_player_template(raw_template)
        assert result["status"] == "ok"
        assert result["updated_players"] == ["player_one"]
        restored = platform.store.repositories.entities.get_agent("player_one")
        assert restored["controller"] == "manual"
        assert restored["capabilities"]["gathering"] == 0.5
        assert restored["inventory"]["food_ration"] == 3
        assert "gather_water" in restored["initial_bt_ids"]
    finally:
        platform.close()


def test_player_travel_registers_missing_generic_behavior(tmp_path: Path):
    db_path = create_instance(WORLDS / "tidebound_commons", "tidebound-player-travel", tmp_path / "runtime")
    service = ControlService(db_path)
    try:
        assert service.enter_player("player_one", "forager", name="Ari")["status"] == "accepted"
        # Existing instances created before the generic travel behavior existed
        # must still be able to use the route cards exposed by the player view.
        with service.platform.store.transaction():
            service.platform.store.connection.execute("DELETE FROM bt_def WHERE id='travel'")
        view = build_player_view(service.platform, "player_one")
        travel = next(item for item in view["actions"] if item.get("kind") == "travel" and item.get("parameters", {}).get("location_id") == "workshop")
        result = service.platform.submit_behavior(BehaviorIntent(
            actor_id="player_one",
            behavior_ref=travel["ref"],
            target={},
            parameters=dict(travel["parameters"]),
            reason="Test route travel.",
            confidence=1.0,
            intent_id="test-travel-1",
        ))
        assert result["status"] == "accepted"
        definitions = {item["id"] for item in service.platform.store.repositories.entities.get_definitions("bt_def")}
        assert "travel" in definitions

        service.player_wait("player_one", minutes=30)
        agent = service.platform.store.repositories.entities.get_agent("player_one")
        assert agent["initial_state"]["location_id"] == "workshop"
    finally:
        service.close()


def test_player_entry_projection_lists_canonical_origins(tmp_path: Path):
    db_path = create_instance(WORLDS / "tidebound_commons", "tidebound-player-projection", tmp_path / "runtime")
    service = ControlService(db_path)
    try:
        entry = service.player_entry()
        assert entry["entry_location"] == {"id": "commons_quay", "name": "公共码头"}
        assert [origin["id"] for origin in entry["origins"]] == [
            "steward",
            "forager",
            "chart_keeper",
            "ferry_runner",
        ]
        assert all(origin["goal"] for origin in entry["origins"])
        assert [origin["name"] for origin in entry["origins"]] == [
            "潮务学徒",
            "沼泽采集者",
            "海图学徒",
            "渡船信使",
        ]
    finally:
        service.close()


def test_cli_entry_lists_valid_origins_after_a_bad_choice(tmp_path: Path):
    db_path = create_instance(WORLDS / "tidebound_commons", "tidebound-cli-guidance", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        with pytest.raises(SystemExit, match=r"steward, forager, chart_keeper, ferry_runner"):
            _ensure_player(platform, "player_bad", "traveler", "Ari")
        _ensure_player(platform, "player_good", "forager", "Ari")
        assert platform.store.repositories.entities.get_agent("player_good")["name"] == "Ari"
    finally:
        platform.close()


def test_autonomous_runtime_does_not_drive_a_manual_player(tmp_path: Path):
    db_path = create_instance(WORLDS / "tidebound_commons", "tidebound-player-manual", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        assert platform.enter_player("player_one", "forager", name="Ari")["status"] == "accepted"

        class WaitProvider:
            def __init__(self):
                self.contexts: list[str] = []

            def complete_json(self, *, system, context, tools=None, messages=None):
                self.contexts.append(str(context))
                return {"intent_type": "wait", "reason": "Test provider keeps the world idle."}

        provider = WaitProvider()
        SimulationRunner(platform, {"agent_decision": provider}).run(1)
        assert provider.contexts
        assert all('"player_one"' not in context for context in provider.contexts)
    finally:
        platform.close()


def test_text_client_say_accepts_nearby_name_and_defaults_topic(tmp_path: Path):
    db_path = create_instance(WORLDS / "tidebound_commons", "tidebound-say", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        assert platform.enter_player("player_one", "forager", name="Ari")["status"] == "accepted"
        reply_calls: list[bool] = []
        session = TextWorldSession(
            platform,
            player_id="player_one",
            reply_handler=lambda: reply_calls.append(True) or "Reply:\n[0] 玛拉 · general: Hello back.",
        )
        scene = session.execute("look")
        assert "玛拉" in scene
        assert "id=mara" in scene
        result = session.execute("say 玛拉 Hello")
        assert result.startswith("status=accepted")
        assert "topic: general" in result
        assert "Hello back." in result
        assert "type reply" not in result
        assert reply_calls == [True]
    finally:
        platform.close()


def test_text_reply_handler_invokes_agent_interaction_without_advancing_time(tmp_path: Path, monkeypatch):
    db_path = create_instance(WORLDS / "tidebound_commons", "tidebound-reply", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        assert platform.enter_player("player_one", "forager", name="Ari")["status"] == "accepted"
        assert platform.submit_dialogue("player_one", "mara", "general", "Hello")["status"] == "accepted"

        class ReplyProvider:
            def complete_json(self, *, system, context, tools=None, messages=None):
                return {
                    "tool_calls": [{
                        "id": "reply-1",
                        "type": "function",
                        "function": {
                            "name": "send_message",
                            "arguments": {
                                "recipient": "Ari",
                                "topic": "general",
                                "message": "Hello back from Mara.",
                            },
                        },
                    }],
                }

        class Router:
            def provider_for_role(self, role):
                assert role == "agent_decision"
                return ReplyProvider()

        monkeypatch.setattr(LLMRouter, "from_default_config", lambda: Router())
        result = _dialogue_reply_handler(platform, "player_one")()
        assert "Reply:" in result
        assert "Hello back from Mara." in result
        assert platform.store.repositories.metadata.get("world_minute") == 0
        transcript = platform.store.repositories.social.list_dialogue_exchanges("player_one")
        assert any(item["speaker_id"] == "mara" and item["message"] == "Hello back from Mara." for item in transcript)
    finally:
        platform.close()


def test_explicit_player_duplicate_message_still_reaches_the_recipient(tmp_path: Path):
    db_path = create_instance(WORLDS / "tidebound_commons", "tidebound-player-repeat", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        assert platform.enter_player("player_one", "forager", name="Ari")["status"] == "accepted"

        class WaitProvider:
            def __init__(self):
                self.calls = 0

            def complete_json(self, *, system, context, tools=None, messages=None):
                self.calls += 1
                return {"intent_type": "wait", "reason": "The greeting needs no further information."}

        provider = WaitProvider()
        runner = SimulationRunner(platform, {"agent_decision": provider})
        first = platform.submit_dialogue("player_one", "mara", "general", "Hello")
        assert first["status"] == "accepted"
        runner.resolve_pending_interactions()

        second = platform.submit_dialogue("player_one", "mara", "general", "hello")
        assert second["status"] == "accepted"
        report = runner.resolve_pending_interactions()

        assert provider.calls == 2
        assert report.interaction_replies_suppressed == 0
        first_exchange = platform.store.repositories.social.get_dialogue_exchange(first["dialogue_id"])
        assert first_exchange["source"] == "direct_text"
        assert first_exchange["origin"] == "player"
    finally:
        platform.close()


def test_text_scene_and_messages_show_player_dialogue(tmp_path: Path):
    db_path = create_instance(WORLDS / "tidebound_commons", "tidebound-transcript", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        assert platform.enter_player("player_one", "forager", name="Ari")["status"] == "accepted"
        assert platform.submit_dialogue("mara", "player_one", "general", "Welcome to the quay.")["status"] == "accepted"
        session = TextWorldSession(platform, player_id="player_one")
        scene = session.execute("look")
        assert "== Conversation ==" in scene
        assert "玛拉 -> Ari" in scene
        assert "Welcome to the quay." in scene
        messages = session.execute("messages")
        assert "玛拉 -> Ari" in messages
        assert "Welcome to the quay." in messages
        with pytest.raises(ValueError, match="No reply provider"):
            session.execute("reply")
        with pytest.raises(ValueError, match="does not take a recipient"):
            session.execute("reply Mara")
    finally:
        platform.close()


def test_dialogue_resolves_names_and_reports_missing_participant(tmp_path: Path):
    db_path = create_instance(WORLDS / "tidebound_commons", "tidebound-names", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        assert platform.enter_player("player_one", "forager", name="Ari")["status"] == "accepted"
        by_names = platform.submit_dialogue("Ari", "玛拉", "general", "Hello by names")
        assert by_names["status"] == "accepted"
        stored = platform.store.repositories.social.get_dialogue_exchange(by_names["dialogue_id"])
        assert stored["speaker_id"] == "player_one"
        assert stored["listener_id"] == "mara"

        missing = platform.submit_dialogue("player_one", "nobody_xyz", "general", "Hi")
        assert missing["status"] == "rejected"
        assert missing["missing"] == "nobody_xyz"
        assert "mara" in missing["allowed_values"]

        god = platform.submit_dialogue("god", "mara", "general", "Hi")
        assert god["status"] == "rejected"
        assert "inject_event" in god["message"]

        bad_trade = platform.create_trade_offer("player_one", "nobody_xyz", {"bread": 1}, {"water": 1})
        assert bad_trade["status"] == "rejected"
        assert bad_trade["missing"] == "nobody_xyz"
    finally:
        platform.close()
