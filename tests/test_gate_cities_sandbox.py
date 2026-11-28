from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from sim2.actions.conditions import ConditionEngine
from sim2.api.catalog import bundled_world_catalog
from sim2.api.map_view import build_map_view
from sim2.api.player_requirements import explain_requirement
from sim2.api.runtime_supervisor import RuntimeSupervisor
from sim2.api.service import ControlService
from sim2.bootstrap.service import create_instance
from sim2.intents.models import BehaviorIntent
from worlds import world_path


WORLD = world_path("gate_cities_sandbox")


def play(service: ControlService, ref: str, **parameters) -> dict:
    result = service.player_act({"player_id": "hero", "behavior_ref": f"behavior:{ref}", "parameters": parameters})
    assert result["status"] == "accepted", result
    service.player_wait("hero", minutes=60)
    view = service.player_view("hero")
    assert not view["active_task"]["bt"], view["active_task"]
    return view


def test_calculated_urgency_is_available_to_dsl() -> None:
    condition = "actor.urgency.livelihood >= 0.6"
    engine = ConditionEngine({
        "needs": [{"id": "livelihood", "inventory_item": "coin", "inventory_scale": 25}],
    })
    assert engine.evaluate(condition, agent={"inventory": {"coin": 0}})
    assert not engine.evaluate(condition, agent={"inventory": {"coin": 4}})
    assert not engine.evaluate("actor.urgency.unknown >= 0", agent={})


def test_player_sees_the_way_to_unlock_an_action() -> None:
    old_reason = "Action handover gate cannot start: precondition not met: location tag gate dock"
    places = {"high_gate": {"name": "高地枢门", "tags": ["gate_dock"]}}
    assert explain_requirement(old_reason, places, {}, {}) == "前往高地枢门"
    appoint = "Action authorize appointment cannot start: precondition not met: actor state location id == 'high gate'"
    assert explain_requirement(appoint, places, {}, {}) == "前往高地枢门"
    classified = "Action fit terminal cannot start: precondition not met: world.market_permit == 1"
    hints = {"world.market_permit == 1": "先登记建造许可"}
    assert explain_requirement(classified, places, {}, hints) == "先登记建造许可"


def test_north_bridge_enters_topology_only_after_survey(tmp_path: Path) -> None:
    assert any(item["id"] == "gate_cities_sandbox" for item in bundled_world_catalog())
    with closing(ControlService(create_instance(WORLD, "bridge", tmp_path))) as service:
        assert service.enter_player("hero", "builder", "林晚")["status"] == "accepted"
        initial_map = build_map_view(service.platform.store, observer=True)
        assert len(initial_map["nodes"]) == 12
        assert not any({e["from"], e["to"]} == {"north_trestle", "mine_pit"} for e in initial_map["edges"])
        agents = service.platform.store.repositories.entities.list_agents()
        assert len([agent for agent in agents if agent.get("role") != "player"]) == 60
        assert sum(agent.get("controller") == "llm" for agent in agents) == 4

        result = service.player_act({"player_id": "hero", "behavior_ref": "behavior:survey_bridge", "parameters": {}})
        assert result["status"] == "accepted", result
        advance = RuntimeSupervisor(service).advance_manual(480, until_actor_idle="hero")
        assert advance["report"]["end_minute"] == 20
        assert service.player_view("hero")["last_action_outcome"]["status"] == "completed"
        view = play(service, "improve_bridge")
        assert view["inventory"]["frame"] == 2
        assert view["world_properties"]["bridge_improved"] == 1
        edge = next(e for e in view["map"]["edges"] if {e["from"], e["to"]} == {"north_trestle", "mine_pit"})
        assert edge["minutes"] == 8
        assert any(action["ref"] == "behavior:bridge_trade" for action in view["actions"])
        assert view["inventory"].get("ore", 0) == 0
        result = service.player_act({"player_id": "hero", "behavior_ref": "behavior:bridge_trade"})
        assert result["status"] == "accepted", result
        service.player_wait("hero", minutes=120)
        view = service.player_view("hero")
        assert view["last_action_outcome"]["status"] == "completed"
        assert view["world_properties"]["market_sales"] == 1
        assert view["world_properties"]["connected_count"] == 0
        assert view["inventory"]["gate_core"] == 1


def test_appointment_changes_controller_without_replacing_the_resident(tmp_path: Path) -> None:
    database = create_instance(WORLD, "appointment", tmp_path)
    with closing(ControlService(database)) as service:
        platform = service.platform
        store = platform.store
        assert service.enter_player("hero", "builder", "林晚")["status"] == "accepted"
        original = store.repositories.entities.get_agent("porter_bai")
        assert original["controller"] == "bt"
        assert service.player_act({"player_id": "hero", "behavior_ref": "behavior:authorize_appointment",
                                   "parameters": {}})["status"] == "rejected"
        with store.transaction():
            props = store.repositories.metadata.get("world_properties")
            props.update(merit_count=3, service_votes=2)
            store.repositories.metadata.set("world_properties", props)
        play(service, "authorize_appointment")
        platform.run(45, providers={})
        resident = store.repositories.entities.get_agent("porter_bai")
        assert resident["controller"] == "llm"
        assert resident["controller_fallback"] == "bt"
        assert resident["initial_state"]["office"] == 1
        assert resident["initial_state"]["home_location"] == original["initial_state"]["home_location"]
        play(service, "close_office")
        platform.run(40, providers={})
    with closing(ControlService(database)) as reloaded:
        resident = reloaded.platform.store.repositories.entities.get_agent("porter_bai")
        assert resident["controller"] == "bt"
        assert resident["initial_state"]["office"] == 0
        assert resident["routine_bt_id"] == original["routine_bt_id"]


def test_cities_can_resume_gates_after_an_embargo(tmp_path: Path) -> None:
    with closing(ControlService(create_instance(WORLD, "embargo", tmp_path))) as service:
        platform = service.platform
        store = platform.store
        assert service.enter_player("hero", "builder", "林晚")["status"] == "accepted"
        with store.transaction():
            properties = store.repositories.metadata.get("world_properties")
            properties.update(connected_count=1, market_link=1, trade_policy=3, embargo_countdown=2, lockdown=0)
            store.repositories.metadata.set("world_properties", properties)
            platform._services["tick"].set_world_property("market_maintenance", 6)
        assert any(edge.get("kind") == "gate" for edge in build_map_view(store, observer=True)["edges"])
        platform.step()
        assert service.player_view("hero")["world_properties"]["lockdown"] == 0
        for _ in range(2):
            platform.step()
        assert service.player_view("hero")["world_properties"]["lockdown"] == 1
        assert not any(edge.get("kind") == "gate" for edge in build_map_view(store, observer=True)["edges"])
        view = play(service, "sign_open_market")
        assert view["world_properties"]["trade_policy"] == 2
        assert view["world_properties"]["lockdown"] == 0
        assert any(edge.get("kind") == "gate" for edge in view["map"]["edges"])


def test_mismatched_terminals_can_be_retargeted_without_spending_more_frames(tmp_path: Path) -> None:
    with closing(ControlService(create_instance(WORLD, "mixed-ends", tmp_path))) as service:
        assert service.enter_player("hero", "builder", "林晚")["status"] == "accepted"
        platform = service.platform
        store = platform.store
        with store.transaction():
            tick = platform._services["tick"]
            for key, value in {"site_surveyed": 1, "market_permit": 1, "yard_permit": 1,
                               "market_mine": 1, "market_mine_slot": 0, "yard_city": 1, "yard_city_slot": 0}.items():
                tick.set_world_property(key, value)
            player = store.repositories.entities.get_agent("hero")
            player["initial_state"]["location_id"] = "mine_exchange"
            player["inventory"]["frame"] = 0
            store.repositories.entities.update_agent("hero", player)
        view = service.player_view("hero")
        assert view["goals"][0]["id"] == "fix_market_yard"
        assert "behavior:retarget_market_to_yard" in view["goals"][0]["ready_behavior_refs"]
        view = play(service, "retarget_market_to_yard")
        assert view["world_properties"]["market_mine"] == 0
        assert view["world_properties"]["market_mine_slot"] == 1
        assert view["world_properties"]["yard_mine"] == 1
        assert view["world_properties"]["yard_mine_slot"] == 0
        assert view["inventory"].get("frame", 0) == 0
        view = play(service, "pair_yard")
        assert view["world_properties"]["yard_link"] == 1
        assert view["inventory"].get("gate_core", 0) == 0
        assert service.player_act({"player_id": "hero", "behavior_ref": "behavior:reclaim_yard_mine"})["status"] == "rejected"


def test_workshop_collects_real_revenue_and_reinvests_physical_output(tmp_path: Path) -> None:
    with closing(ControlService(create_instance(WORLD, "first-contract", tmp_path))) as service:
        assert service.enter_player("hero", "builder", "林晚")["status"] == "accepted"
        store = service.platform.store
        with store.transaction():
            tick = service.platform._services["tick"]
            tick.set_world_property("site_surveyed", 1)
            tick.set_world_property("market_ore", 4)
            player = store.repositories.entities.get_agent("hero")
            player["initial_state"]["location_id"] = "low_market"
            store.repositories.entities.update_agent("hero", player)
        before = service.player_view("hero")
        view = play(service, "open_paving_shop")
        assert view["inventory"]["coin"] == before["inventory"]["coin"] - 2
        assert view["world_properties"]["shop_cash"] == 2
        assert service.player_act({"player_id": "hero", "behavior_ref": "behavior:shop_pay_dividend"})["status"] == "rejected"
        play(service, "shop_process")
        view = play(service, "shop_process")
        props = view["world_properties"]
        assert props["paving_stock"] == 4 and props["market_ore"] == 0
        assert props["shop_batches"] == 2 and props["shop_sales"] == 6 and props["shop_spent"] == 2
        assert props["low_wages"] == before["world_properties"]["low_wages"] - 6
        assert view["inventory"]["coin"] + props["shop_cash"] + props["low_wages"] == before["inventory"]["coin"] + before["world_properties"]["low_wages"]
        view = play(service, "shop_pay_dividend")
        assert view["world_properties"]["shop_dividends"] == 1
        assert view["world_properties"]["shop_cash"] == props["shop_cash"] - 1
        view = play(service, "shop_pave_market_road")
        assert view["world_properties"]["shop_road"] == 1 and view["world_properties"]["paving_stock"] == 0
        edge = next(edge for edge in view["map"]["edges"] if {edge["from"], edge["to"]} == {"low_city", "low_market"})
        assert edge["minutes"] == 2


def test_commissioned_workshop_resident_earns_the_wage_and_delivers_paid_work(tmp_path: Path) -> None:
    with closing(ControlService(create_instance(WORLD, "commissioned-shop", tmp_path))) as service:
        assert service.enter_player("hero", "builder", "林晚")["status"] == "accepted"
        platform = service.platform
        store = platform.store
        mason_id = "low_resident_03"
        with store.transaction():
            platform._services["tick"].set_world_property("market_ore", 2)
            player = store.repositories.entities.get_agent("hero")
            player["initial_state"]["location_id"] = "low_market"
            store.repositories.entities.update_agent("hero", player)
        play(service, "open_paving_shop")
        play(service, "appoint_shop_crew")
        start_wage = store.repositories.entities.get_agent(mason_id)["inventory"].get("coin", 0)
        platform.run(25, providers={})
        events = store.repositories.events.recent_events_by_types(["gatebuild.shop_batch_sold"], limit=5)
        assert [event["actor_id"] for event in events] == [mason_id]
        assert store.repositories.entities.get_agent(mason_id)["inventory"]["coin"] == start_wage + 1
        view = service.player_view("hero")
        assert view["world_properties"]["connected_count"] == 0
        assert view["world_properties"]["shop_cash"] >= 3
        assert view["world_properties"]["shop_batches"] == 1
        assert "behavior:shop_pay_dividend" in {action["ref"] for action in view["actions"]}


def test_market_job_with_a_bed_changes_the_resident_home(tmp_path: Path) -> None:
    with closing(ControlService(create_instance(WORLD, "new-home", tmp_path))) as service:
        platform = service.platform
        store = platform.store
        with store.transaction():
            properties = store.repositories.metadata.get("world_properties")
            properties.update(market_link=1, connected_count=1)
            store.repositories.metadata.set("world_properties", properties)
            platform._services["tick"].set_world_property("market_orders", 1)
            candidate = store.repositories.entities.get_agent("yard_resident_01")
            candidate["initial_state"]["location_id"] = "low_market"
            candidate["needs"].update(hydration=90, satiety=90)
            store.repositories.entities.update_agent(candidate["id"], candidate)
        platform.run(20, providers={})
        resident = store.repositories.entities.get_agent(candidate["id"])
        assert resident["initial_state"]["home_location"] == "low_market"
        assert resident["initial_state"]["job_region"] == "market"
        properties = store.repositories.metadata.get("world_properties")
        assert properties["low_beds"] == 7 and properties["yard_beds"] == 5
        assert properties["market_rent"] == 1 and properties["market_orders"] == 0
        events = store.repositories.events.recent_events_by_types(["gatebuild.resident_moved"], limit=2)
        assert [event["actor_id"] for event in events] == [candidate["id"]]


def test_arrival_pays_for_a_real_bed_and_provisions(tmp_path: Path) -> None:
    with closing(ControlService(create_instance(WORLD, "new-resident", tmp_path))) as service:
        platform = service.platform
        store = platform.store
        props = store.repositories.metadata.get("world_properties")
        result = platform.submit_entity_proposal("god", {
            "name": "返乡的修桥工", "entity_type": "agent",
            "description": "回高地借宿并寻找修桥工作。",
            "state": {"controller": "llm", "role": "修桥工",
                      "ultimate_goal": "投宿后找一份扛料的工作。",
                      "inventory": {"coin": 500},
                      "initial_bt_ids": ["routine_resident"],
                      "initial_state": {"location_id": "high_gate"}},
        })
        assert result["status"] == "accepted", result
        newcomer = store.repositories.entities.get_agent(result["entity_id"])
        assert newcomer["controller"] == "bt" and newcomer["routine_bt_id"] == "routine_resident"
        assert newcomer["inventory"] == {"water": 1, "meal": 1}
        assert newcomer["initial_state"]["home_location"] == "high_lodges"
        after = store.repositories.metadata.get("world_properties")
        assert all(after[stock] == props[stock] - 1 for stock in ("high_beds", "high_water", "high_food"))


def test_resident_model_budget_is_shared_by_decisions_and_reflections(tmp_path: Path) -> None:
    with closing(ControlService(create_instance(WORLD, "resident-budget", tmp_path))) as service:
        platform = service.platform
        store = platform.store
        with store.transaction():
            rules = store.repositories.metadata.get("rules")
            rules["decision_policy"]["resident_sessions_per_day"] = 1
            store.repositories.metadata.set("rules", rules)
            for role in ("agent_decision", "agent_reflection"):
                store.repositories.evolution.put_evolution_task(role, role, "warden", "review", 0, 0, status="ready")
        assert platform.claim_evolution_task("agent_decision")
        platform.complete_evolution_task("agent_decision")
        assert platform.claim_evolution_task("agent_reflection") is None
        with store.transaction():
            store.repositories.metadata.set("world_minute", 1440)
        assert platform.claim_evolution_task("agent_reflection")
        platform.complete_evolution_task("agent_reflection")


def test_street_life_shows_resident_work_and_a_persistent_local_conversation(tmp_path: Path) -> None:
    db = create_instance(WORLD, "street-life", tmp_path)
    with closing(ControlService(db)) as service:
        assert service.enter_player("hero", "builder", "林晚")["status"] == "accepted"
        store = service.platform.store
        entities = store.repositories.entities
        with store.transaction():
            player = entities.get_agent("hero")
            player["initial_state"]["location_id"] = "low_market"
            entities.update_agent("hero", player)
            mason = entities.get_agent("low_resident_03")
            mason["initial_state"].update(
                location_id="low_market", status="executing", active_action="shop_fabricate_paving",
                remaining_minutes=7, action_progress=0.5,
            )
            entities.update_agent(mason["id"], mason)
            tick = service.platform._services["tick"]
            tick.set_world_property("shop_open", 1)
            tick.set_world_property("market_ore", 0)
        view = service.player_view("hero")
        resident = next(person for person in view["nearby_participants"] if person["id"] == mason["id"])
        assert resident["active_behavior_label"] == "在工坊打制路石"
        assert resident["initial_state"]["action_progress"] == 0.5
        assert resident["can_talk"]
        with store.transaction():
            mason["initial_state"].update(active_action="wait", remaining_minutes=4)
            entities.update_agent(mason["id"], mason)
        view = service.player_view("hero")
        resident = next(person for person in view["nearby_participants"] if person["id"] == mason["id"])
        assert resident["can_talk"] and resident["active_behavior_label"] == "在这儿歇脚"
        assert any(topic["id"] == "home" for topic in resident["topics"])
        assert any("缺矿料" in signal["text"] for signal in view["city_watch"]["signals"])
        assert service.dialogue("hero", "yard_resident_02", "work", "忙吗？")["failed_check"] == "location"
        result = service.dialogue("hero", mason["id"], "work", "工坊现在怎么样？")
        assert result["reply"]["status"] == "completed"
        assert entities.get_agent(mason["id"])["initial_state"]["active_action"] == "wait"
        assert service.dialogue("hero", mason["id"], "home", "收工以后想做什么？")["reply"]["status"] == "completed"
        assert "支张桌" in service.player_dialogue_history("hero", mason["id"])["exchanges"][-1]["message"]
        view = play(service, "set_up_crew_table")
        assert view["world_properties"]["crew_table"] == 1
        assert view["inventory"]["coin"] == 4
        assert view["world_properties"]["low_wages"] == 62
        assert any(entry["person"] == mason["id"] and entry["relation"] == "trust" for entry in view["relationships"])
        assert view["city_watch"]["milestones"][0]["title"] == "工人在坊市支起长桌"
        assert service.player_act({"player_id": "hero", "behavior_ref": "behavior:set_up_crew_table"})["status"] == "rejected"
        with store.transaction():
            mason = entities.get_agent(mason["id"])
            mason["initial_state"].update(status="idle", active_action=None, remaining_minutes=0)
            mason["needs"].update(satiety=60, belonging=45, hydration=90)
            entities.update_agent(mason["id"], mason)
            tick.set_world_property("low_food", 8)
        intent = BehaviorIntent.from_json({"behavior_ref": "routine_shop_mason"}, actor_id=mason["id"])
        assert service.platform.submit_behavior(intent, configured_routine=True)["status"] == "accepted"
        for _ in range(6):
            service.platform.step()
        fed = entities.get_agent(mason["id"])
        assert fed["needs"]["satiety"] > 90 and fed["needs"]["belonging"] > 45
        assert store.repositories.metadata.get("world_properties")["low_food"] == 7
    with closing(ControlService(db)) as service:
        assert service.player_dialogue_history("hero", "yard_resident_02")["exchanges"] == []
        transcript = service.player_dialogue_history("hero", "low_resident_03")["exchanges"]
        assert len(transcript) == 4
        assert "没料" in transcript[1]["message"] or "矿料不够" in transcript[1]["message"]
        assert transcript[1]["in_reply_to"] == transcript[0]["dialogue_id"]
        assert service.player_view("hero")["world_properties"]["crew_table"] == 1


@pytest.mark.parametrize(("buyer", "advance", "source", "stock"), [
    ("court", 3, "low_wages", "court_tender_stones"),
    ("yard", 4, "yard_wages", "yard_tender_stones"),
])
def test_municipal_tenders_use_real_funds_and_pay_only_for_deliveries(
    tmp_path: Path, buyer: str, advance: int, source: str, stock: str,
) -> None:
    with closing(ControlService(create_instance(WORLD, "tender", tmp_path))) as service:
        assert service.enter_player("hero", "builder", "林晚")["status"] == "accepted"
        store = service.platform.store
        entities = store.repositories.entities
        tick = service.platform._services["tick"]
        with store.transaction():
            player = entities.get_agent("hero")
            player["initial_state"]["location_id"] = "low_market"
            entities.update_agent("hero", player)
            for key, value in {"shop_road": 1, "shop_open": 1, "shop_cash": 2, "market_ore": 6, "shop_batches": 1}.items():
                tick.set_world_property(key, value)
        view = service.player_view("hero")
        assert [row["id"] for row in view["goals"]][0] == "tender_offer"

        with store.transaction():
            tick.set_world_property(source, 0)
        assert service.player_act({"player_id": "hero", "behavior_ref": f"behavior:sign_{buyer}_tender"})["status"] == "rejected"
        with store.transaction():
            tick.set_world_property(source, 60)

        view = play(service, f"sign_{buyer}_tender")
        props = view["world_properties"]
        assert props["shop_cash"] == 2 + advance and props["shop_sales"] == 0
        assert props[source] == 60 - (10 if buyer == "court" else 2)
        assert props["tender_escrow"] == 6 and props["tender_bonus"] == 1
        assert props["tender_delivered"] == 0 and props[stock] == 0
        other = "yard" if buyer == "court" else "court"
        assert service.player_act({"player_id": "hero", "behavior_ref": f"behavior:sign_{other}_tender"})["status"] == "rejected"
        for batch in range(1, 4):
            if buyer == "yard":
                view = play(service, "tender_mason_yard")
                assert view["world_properties"]["tender_delivered"] == batch - 1
                assert view["world_properties"]["tender_yard_ready"] == 1
                result = service.player_act({"player_id": "hero", "behavior_ref": "behavior:tender_haul"})
                assert result["status"] == "accepted", result
                service.player_wait("hero", minutes=240)
                view = service.player_view("hero")
            else:
                view = play(service, "tender_mason_court")
            props = view["world_properties"]
            assert props["tender_delivered"] == batch, props
            assert props["tender_escrow"] == 6 - (batch - 1) * 3
            assert props["shop_sales"] == advance + (batch - 1) * 3
            assert props[stock] == batch * 2
        assert props["tender_batches_due"] == 0 and props["tender_receipt_slot"] == 1
        with store.transaction():
            tick.set_world_property("tender_minutes", 0)
            player = entities.get_agent("hero")
            player["initial_state"]["location_id"] = "low_market"
            entities.update_agent("hero", player)
        view = play(service, "tender_close")
        assert view["world_properties"]["tender_bonus"] == 0
        assert view["world_properties"]["tender_mode"] == 0
        assert view["world_properties"]["shop_sales"] == advance + 6


@pytest.mark.parametrize(("source", "charter", "policy"), [
    ("relief_buy_yard", "relief_ratify_trade", 1),
    ("relief_early_harvest", "relief_ratify_growers", 2),
])
def test_food_council_changes_the_same_towns_in_different_ways(tmp_path: Path, source: str, charter: str, policy: int) -> None:
    db = create_instance(WORLD, f"council-{policy}", tmp_path)
    with closing(ControlService(db)) as service:
        assert service.enter_player("hero", "builder", "林晚")["status"] == "accepted"
        platform = service.platform
        repo = platform.store.repositories
        tick = platform._services["tick"]
        with platform.store.transaction():
            player = repo.entities.get_agent("hero")
            player["initial_state"]["location_id"] = "low_city"
            repo.entities.update_agent("hero", player)
            for key, value in {"shop_road": 1, "shop_open": 1, "low_food": 2, "yard_food": 26}.items():
                tick.set_world_property(key, value)
        initial = service.player_view("hero")["world_properties"]
        assert service.player_view("hero")["goals"][0]["id"] == "relief_source"
        view = play(service, source)
        props = view["world_properties"]
        assert props["relief_food"] == (10 if policy == 1 else 6)
        assert props["relief_desk"] == 1 and props["relief_cases"] == 1
        if policy == 1:
            assert props["yard_food"] == initial["yard_food"] - 10
            assert props["yard_wages"] == initial["yard_wages"] + 6
            assert props["low_wages"] == initial["low_wages"] - 6
        else:
            assert props["yard_wages"] == initial["yard_wages"] and props["low_wages"] == initial["low_wages"]
            assert props["field_fuel"] == initial["field_fuel"] - 2
            assert props["low_water"] == initial["low_water"] - 4
            assert props["farm_water"] == initial["farm_water"] + 2
        assert service.player_act({"player_id": "hero", "behavior_ref": "behavior:relief_buy_yard"})["status"] == "rejected"
        view = play(service, "relief_feed_neighborhood")
        props = view["world_properties"]
        assert props["low_food"] == initial["low_food"] + 6
        if policy == 1:
            assert "behavior:relief_commission_path" in view["goals"][0]["ready_behavior_refs"]
            view = play(service, "relief_commission_path")
            assert view["world_properties"]["relief_jobs"] == 2
        else:
            assert "behavior:relief_commission_path" not in view["goals"][0]["ready_behavior_refs"]

        view = play(service, charter)
        props = view["world_properties"]
        assert props["relief_policy"] == policy and props["relief_phase"] == 0
        assert props["relief_reopen"] > 0
        assert service.player_act({"player_id": "hero", "behavior_ref": f"behavior:{source}"})["status"] == "rejected"
        if policy == 1:
            for actor_id in ("low_resident_06", "low_resident_07"):
                with platform.store.transaction():
                    resident = repo.entities.get_agent(actor_id)
                    resident["initial_state"].update(location_id="low_market", status="idle")
                    resident["needs"].update(hydration=95, satiety=80)
                    repo.entities.update_agent(actor_id, resident)
                intent = BehaviorIntent.from_json({"behavior_ref": "routine_resident"}, actor_id=actor_id)
                assert platform.submit_behavior(intent, configured_routine=True)["status"] == "accepted"
                for _ in range(5):
                    platform.step()
            view = service.player_view("hero")
            assert view["world_properties"]["relief_path_shifts"] == 2
            assert view["world_properties"]["relief_crew_food"] == 0
            assert view["goals"][0]["id"] == "relief_followup"
            view = play(service, "relief_path_market")
            assert view["inventory"]["coin"] == 4
            assert view["world_properties"]["relief_path_use"] == 1
        else:
            assert view["world_properties"]["relief_path"] == 0

    with closing(ControlService(db)) as service:
        view = service.player_view("hero")
        edges = [edge for edge in view["map"]["edges"] if {edge["from"], edge["to"]} == {"low_market", "low_fields"}]
        assert [edge["minutes"] for edge in edges] == ([2] if policy == 1 else [])
        tick = service.platform._services["tick"]
        store = service.platform.store
        with store.transaction():
            tick.set_world_property("low_food", 2)
            store.repositories.metadata.set("world_minute", 89)
        service.platform.step()
        props = service.player_view("hero")["world_properties"]
        assert props["relief_followups"] == 1
        assert props["relief_path_trades"] == (1 if policy == 1 else 0)
        receipt = store.repositories.events.recent_events_by_types(["gatebuild.relief_supply"], limit=1)[0]
        meal_change = next(entry for entry in receipt["effects"] if entry.get("resource_id") == "low_food")
        assert meal_change["after"] - meal_change["before"] == (3 if policy == 1 else 4)
        assert service.player_view("hero")["city_watch"]["report"]["summary"] == receipt["summary"]
        if policy == 1:
            assert props["low_food"] == 8 and props["yard_food"] == 10
        else:
            assert props["low_food"] == 6 and props["field_fuel"] == 9
        with store.transaction():
            tick.set_world_property("low_food", 2)
            tick.set_world_property("yard_food" if policy == 1 else "field_fuel", 10 if policy == 1 else 0)
            tick.set_world_property("relief_reopen", 1)
            if policy == 1:
                tick.set_world_property("low_water", 13)
            store.repositories.metadata.set("world_minute", 179)
        service.platform.step()
        view = service.player_view("hero")
        assert view["world_properties"]["relief_followups"] == 1
        assert view["world_properties"]["relief_reopen"] == 0
        assert view["goals"][0]["id"] == "relief_source"


def test_food_council_rechecks_destination_capacity_at_settlement(tmp_path: Path) -> None:
    with closing(ControlService(create_instance(WORLD, "council-capacity", tmp_path))) as service:
        assert service.enter_player("hero", "builder", "林晚")["status"] == "accepted"
        platform = service.platform
        store = platform.store
        tick = platform._services["tick"]
        with store.transaction():
            player = store.repositories.entities.get_agent("hero")
            player["initial_state"]["location_id"] = "low_city"
            store.repositories.entities.update_agent("hero", player)
            for key, value in {"shop_road": 1, "low_food": 2, "yard_food": 26, "yard_wages": 164}.items():
                tick.set_world_property(key, value)
        assert service.player_act({"player_id": "hero", "behavior_ref": "behavior:relief_buy_yard"})["status"] == "accepted"
        platform.step()
        reserved = store.repositories.metadata.get("world_properties")
        assert reserved["relief_desk"] == 0 and reserved["yard_food"] == 16
        with store.transaction():
            tick.set_world_property("yard_wages", 167)
        service.player_wait("hero", minutes=5)
        view = service.player_view("hero")
        assert view["last_action_outcome"]["status"] == "failed"
        props = view["world_properties"]
        assert props["yard_wages"] == 167 and props["low_wages"] == 60
        assert props["yard_food"] == 26 and props["relief_desk"] == 1
        assert props["relief_food"] == 0 and props["relief_cases"] == 0


def test_new_path_can_supply_the_workshop_instead_of_the_market(tmp_path: Path) -> None:
    with closing(ControlService(create_instance(WORLD, "council-freight", tmp_path))) as service:
        assert service.enter_player("hero", "builder", "林晚")["status"] == "accepted"
        platform = service.platform
        store = platform.store
        tick = platform._services["tick"]
        with store.transaction():
            player = store.repositories.entities.get_agent("hero")
            player["initial_state"]["location_id"] = "low_market"
            store.repositories.entities.update_agent("hero", player)
            for key, value in {"relief_path": 1, "shop_open": 1, "shop_cash": 5, "yard_ore": 8, "market_ore": 1}.items():
                tick.set_world_property(key, value)
        view = play(service, "relief_path_freight")
        assert view["world_properties"]["shop_cash"] == 4 and view["world_properties"]["low_wages"] == 61
        assert view["world_properties"]["relief_path_use"] == 2
        assert service.player_act({"player_id": "hero", "behavior_ref": "behavior:relief_path_market"})["status"] == "rejected"
        with store.transaction():
            store.repositories.metadata.set("world_minute", 89)
        platform.step()
        view = service.player_view("hero")
        props = view["world_properties"]
        assert (props["yard_ore"], props["market_ore"], props["relief_path_trades"]) == (6, 3, 1)
        receipt = store.repositories.events.recent_events_by_types(["gatebuild.relief_path_supply"], limit=1)[0]
        assert receipt["rule_id"] == "relief_path_supply_workshop"
        with store.transaction():
            store.repositories.metadata.set("world_minute", 179)
        platform.step()
        assert service.player_view("hero")["world_properties"]["relief_path_trades"] == 1


def test_play_updates_a_known_bundled_save_and_keeps_player_and_resident_progress(tmp_path: Path) -> None:
    from sim2.api.instances import InstanceRegistry

    db = create_instance(WORLD, "old-bundled-save", tmp_path)
    with closing(ControlService(db)) as service:
        assert service.enter_player("hero", "builder", "旅人")["status"] == "accepted"
        store = service.platform.store
        with store.transaction():
            store.repositories.metadata.set("config_version", "gate-cities-build-0.4")
            store.repositories.metadata.set("world_minute", 1063)
            props = service.platform._services["tick"]
            props.set_world_property("shop_road", 1)
            props.set_world_property("low_food", 6)
            player = store.repositories.entities.get_agent("hero")
            player["inventory"]["coin"] = 7
            store.repositories.entities.update_agent("hero", player)
            resident = store.repositories.entities.get_agent("porter_bai")
            resident.update(controller="llm", controller_fallback="bt")
            store.repositories.entities.update_agent("porter_bai", resident)
    registry = InstanceRegistry(tmp_path)
    result = registry.upgrade_bundled_for_play(db)
    assert result["from"] == "gate-cities-build-0.4" and result["to"] == "gate-cities-build-0.6"
    backup = Path(result["snapshot"])
    assert registry.describe(backup)["world_minute"] == 1063
    assert registry.describe(backup)["config_version"] == "gate-cities-build-0.4"
    assert registry.upgrade_bundled_for_play(db) is None
    with closing(ControlService(db)) as service:
        view = service.player_view("hero")
        assert view["inventory"]["coin"] == 7 and view["server_minute"] == 1063
        assert view["goals"][0]["id"] == "relief_source"
        resident = service.platform.store.repositories.entities.get_agent("porter_bai")
        assert resident["controller"] == "llm" and resident["controller_fallback"] == "bt"
