from __future__ import annotations

from pathlib import Path

from sim2.agents.engine import AgentEngine
from sim2.bootstrap.service import (
    create_instance,
    describe_config,
    validate_world_template,
    write_world_package_from_template,
)
from sim2.platform.runtime import Platform
from sim2.world_builder import WorldBuilderService


class OneChoiceProvider:
    def complete_json(self, *, system, context, tools=None, messages=None):
        return {"choice": "Stabilize Reactor", "reason": "The reactor is unstable and this is my assigned purpose."}


class BuilderProvider:
    def __init__(self, template: dict):
        self.template = template
        self.calls = []

    def complete_json(self, *, system, context, tools=None, messages=None):
        self.calls.append({"system": system, "context": context, "tools": tools, "messages": list(messages or [])})
        name = "validate_world_template" if len(self.calls) == 1 else "submit_world_template"
        return {
            "tool_calls": [{
                "id": f"builder-call-{len(self.calls)}",
                "type": "function",
                "function": {"name": name, "arguments": __import__("json").dumps({"template": self.template}, ensure_ascii=False)},
            }],
            "_usage": {"prompt_tokens": 100, "completion_tokens": 200},
        }


def orbital_template() -> dict:
    """A deliberately non-village world with unrelated ontology and needs."""

    return {
        "template_version": "orbital-lab-1",
        "world": {
            "id": "orbital_lab",
            "title": "Kepler Relay",
            "premise": "An orbital relay must survive a radiation storm.",
            "genre": "science_fiction",
            "properties": {"reactor_stability": 0.4, "solar_flux": 0.8},
            "random_seed": 17,
        },
        "attributes": [
            {"id": "integrity", "kind": "state", "label": "Integrity", "unit": "%", "min": 0, "max": 100, "default": 80, "vital": True, "cost_mode": "decrease", "prompt": "Higher means the chassis is intact; zero means disabled."},
            {"id": "charge", "kind": "state", "label": "Charge", "unit": "%", "min": 0, "max": 100, "default": 100, "capacity": True, "cost_mode": "decrease", "prompt": "Higher means more electrical energy is available."},
            {"id": "signal_clarity", "kind": "need", "label": "Signal clarity", "unit": "%", "min": 0, "max": 100, "default": 55, "prompt": "Higher means communication is clear and reliable."},
        ],
        "locations": [
            {"id": "docking_ring", "name": "Docking Ring", "public": True, "safe": True, "tags": ["entry"], "routes": ["reactor_bay"]},
            {"id": "reactor_bay", "name": "Reactor Bay", "public": True, "tags": ["engineering"], "routes": ["docking_ring"]},
        ],
        "objects": [
            {"id": "fusion_core", "type": "machine", "name": "Fusion Core", "initial_state": {"location_id": "reactor_bay", "status": "unstable"}},
        ],
        "capabilities": [
            {"id": "systems_engineering", "name": "Systems engineering", "description": "Diagnose and stabilize station machinery."},
        ],
        "actions": [
            {
                "id": "stabilize_reactor",
                "version": 1,
                "description": "Tune the field coils and stabilize the fusion core.",
                "parameters": {},
                "preconditions": ["actor.alive"],
                "duration": {"minutes": 4},
                "costs": {"reserve": {}, "per_minute": {"charge": 2}, "on_success": {}, "on_failure": {}, "refund": {}},
                "effects": [
                    {"type": "world_property_delta", "property": "reactor_stability", "delta": 0.1, "maximum": 1.0},
                ],
                "success": {"condition": "actor.alive", "result": "reactor_stabilized"},
                "failure": {"condition": "actor.incapacitated", "result": "unit_disabled"},
                "kind": "atomic",
                "executor": "effect_pipeline",
            },
        ],
        "behavior_trees": [
            {
                "id": "stabilize_reactor",
                "version": 1,
                "agent_label": "Stabilize Reactor",
                "summary": "Stabilize the orbital relay reactor.",
                "entry_conditions": ["actor.alive"],
                "estimated_minutes": 4,
                "success_results": ["reactor_stabilized"],
                "failure_results": ["unit_disabled"],
                "nodes": [{"type": "action", "action_id": "stabilize_reactor", "parameters": {}}],
            },
        ],
        "agents": [
            {
                "id": "unit_seven",
                "name": "Unit Seven",
                "role": "maintenance_synthetic",
                "ultimate_goal": "Keep Kepler Relay operational through the storm.",
                "traits": {"methodical": 0.9},
                "beliefs": [],
                "needs": {"signal_clarity": 55},
                "capabilities": {"systems_engineering": 0.8},
                "initial_bt_ids": ["stabilize_reactor"],
                "initial_state": {"location_id": "reactor_bay", "status": "idle", "integrity": 82, "charge": 100},
                "inventory": {},
            },
        ],
        "rules": {
            "vital_attribute": "integrity",
            "capacity_attribute": "charge",
            "fallback_behavior_ids": [],
            "needs": [{"id": "signal_clarity", "target": 80, "minimum": 0, "gamma": 1.3, "critical_threshold": 0.7, "critical_gain": 1.0}],
            "motivation_model": {"family": "hybrid_need_utility_appraisal", "no_fixed_hierarchy": True, "dimensions": ["system_integrity", "mission_progress"]},
            "minute_effects": [{"id": "signal_noise", "scope": "agent", "every_minutes": 1, "effect": {"type": "need_delta", "need": "signal_clarity", "delta": -0.01}}],
            "memory_quality": {"semantic_similarity": {"enabled": False}},
        },
        "evolution": {},
        "story": {},
        "player_template": {},
        "initial_events": [{"id": "storm_arrival", "type": "storm.detected", "world_minute": 0, "payload": {"summary": "A radiation storm reaches the relay."}}],
    }


def test_completely_different_world_builds_and_runs_without_platform_changes(tmp_path: Path):
    template = orbital_template()
    assert validate_world_template(template) == {"valid": True, "errors": []}
    package = write_world_package_from_template(template, tmp_path / "orbital-package")
    description = describe_config(package)
    assert description["valid"] is True
    assert description["world_id"] == "orbital_lab"
    db_path = create_instance(package, "orbital-instance", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        agent = platform.store.get_agent("unit_seven")
        assert "health" not in agent["initial_state"]
        assert "energy" not in agent["initial_state"]
        assert "satiety" not in agent["needs"]
        prompt_turn = AgentEngine(platform, OneChoiceProvider()).run(role="agent_decision", actor_id="unit_seven")
        assert prompt_turn.result["status"] == "accepted"
        for _ in range(prompt_turn.result["remaining_minutes"]):
            platform.step()
        updated = platform.store.get_agent("unit_seven")
        assert updated["initial_state"]["charge"] == 92
        assert platform.store.get_meta("world_properties")["reactor_stability"] == 0.5
        assert updated["needs"]["signal_clarity"] < 55
        assert any(event["event_type"] == "behavior.completed" for event in platform.store.recent_events(limit=50))
    finally:
        platform.close()


def test_world_template_reports_cross_reference_errors_before_writing(tmp_path: Path):
    template = orbital_template()
    template["locations"][0]["routes"] = ["missing_module"]
    report = validate_world_template(template)
    assert report["valid"] is False
    assert any("missing_module" in item for item in report["errors"])
    try:
        write_world_package_from_template(template, tmp_path / "invalid")
    except ValueError as exc:
        assert "missing_module" in str(exc)
    else:  # pragma: no cover - protects the no-write invariant
        raise AssertionError("invalid world template was written")
    assert not (tmp_path / "invalid").exists()


def test_world_builder_agent_validates_exact_template_before_submission():
    provider = BuilderProvider(orbital_template())
    draft = WorldBuilderService(provider).build("Create an orbital relay during a radiation storm.")
    assert draft.template["world"]["id"] == "orbital_lab"
    assert draft.iterations == 2
    assert draft.usage == {"prompt_tokens": 200, "completion_tokens": 400, "cached_tokens": 0}
    assert provider.calls[0]["tools"]
    assert any(item["function"]["name"] == "validate_world_template" for item in provider.calls[0]["tools"])
    second_messages = provider.calls[1]["messages"]
    assert any(message.get("role") == "tool" and "Template is valid" in message.get("content", "") for message in second_messages)
