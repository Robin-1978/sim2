from __future__ import annotations

import json
import importlib.util
import sqlite3
from pathlib import Path

from sim2.bootstrap.service import create_instance
from sim2.platform.runtime import Platform


_SPEC = importlib.util.spec_from_file_location("audit_real_world", Path(__file__).parents[1] / "scripts" / "audit_real_world.py")
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_quality_counts = _MODULE._quality_counts
_write_json_atomic = _MODULE._write_json_atomic
_merge_reports = _MODULE._merge_reports
_unresolved_provider_errors = _MODULE._unresolved_provider_errors

_REVIEW_SPEC = importlib.util.spec_from_file_location("review_audit", Path(__file__).parents[1] / "scripts" / "review_audit.py")
assert _REVIEW_SPEC and _REVIEW_SPEC.loader
_REVIEW_MODULE = importlib.util.module_from_spec(_REVIEW_SPEC)
_REVIEW_SPEC.loader.exec_module(_REVIEW_MODULE)
_database_checks = _REVIEW_MODULE._database_checks
_review_unresolved_provider_errors = _REVIEW_MODULE._unresolved_provider_errors


def test_audit_checkpoint_is_atomic_and_provider_errors_are_separate(tmp_path: Path):
    path = tmp_path / "audit.checkpoint.json"
    _write_json_atomic(path, {"status": "running", "sessions": [{"role": "agent_decision"}]})
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "running"
    assert not path.with_name(path.name + ".tmp").exists()

    sessions = [
        {"quality": {"passed": True, "grade": "grounded"}},
        {"quality": {"passed": False, "grade": "provider_provider_error"}},
        {"quality": {"passed": False, "grade": "invalid_protocol"}, "result": {"failed_check": "llm_response"}},
        {"quality": {"passed": False, "grade": "invalid_choice"}},
        {"quality": {"passed": False, "pending": ["followup"], "grade": "pending"}},
    ]
    assert _quality_counts(sessions) == {"passed": 1, "pending": 1, "provider_errors": 2, "failed": 1}


def test_segment_reports_are_aggregated_without_double_counting_world_totals():
    class Report:
        def __init__(self, start, end, decisions, errors, stop_reason, events):
            self.start_minute, self.end_minute = start, end
            self.decisions, self.llm_errors = decisions, errors
            self.reflections = self.god_sessions = self.story_sessions = self.waits = 0
            self.rejected_turns = self.interaction_replies_suppressed = self.interaction_threads_closed = 0
            self.stop_reason = stop_reason
            self.degeneration_flags = []
            self._events = events

        def as_dict(self):
            return {"event_types": self._events, "selected_behaviors": {"test": self.end_minute}, "diversity": {"events": sum(self._events.values())}}

    merged = _merge_reports([
        Report(0, 1, 1, 1, "provider_cooldown:agent_decision", {"tick": 1}),
        Report(1, 10, 3, 0, None, {"tick": 10}),
    ], target_minute=10)
    assert merged["decisions"] == 4
    assert merged["llm_errors"] == 1
    assert merged["event_types"] == {"tick": 10}
    assert merged["stop_reason"] is None
    assert merged["segments"] == 2


def test_only_same_boundary_success_recovers_provider_error():
    error = {
        "role": "agent_decision", "actor_id": "a", "task_type": "decision",
        "world_time": {"minute": 4},
        "result": {"status": "rejected", "failed_check": "llm_response"},
        "quality": {"grade": "provider_error"},
    }
    recovered = {
        "role": "agent_decision", "actor_id": "a", "task_type": "decision",
        "world_time": {"minute": 4},
        "result": {"status": "accepted"}, "quality": {"grade": "grounded"},
    }
    late = {**recovered, "world_time": {"minute": 5}}
    assert _unresolved_provider_errors([error, recovered]) == []
    assert _unresolved_provider_errors([error, late]) == [error]
    assert _review_unresolved_provider_errors([error, recovered]) == []
    assert _review_unresolved_provider_errors([error, late]) == [error]


def test_audit_verifies_capability_update_sources(tmp_path: Path):
    world = Path(__file__).parents[1] / "worlds" / "starter_village"
    db_path = create_instance(world, "audit-capability-world", tmp_path / "runtime")
    platform = Platform(db_path)
    try:
        with platform.store.transaction():
            source_ids = [
                platform.store.append_event(
                    "capability.evidence", "liang_he", minute,
                    {"capability": "cooking", "source": "recipe:craft_meal"},
                )
                for minute in (1, 2, 3)
            ]
        result = platform.submit_capability_update(
            "liang_he", "cooking", 0.04,
            "Three configured cooking outcomes support one bounded update.",
        )
        assert result["status"] == "accepted"
    finally:
        platform.close()

    valid = _database_checks({"db_path": str(db_path), "world": {}})
    assert valid["capability_updates"] == 1
    assert not [item for item in valid["issues"] if str(item["kind"]).startswith("capability_")]

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "UPDATE event_log SET data_json=? WHERE event_id=?",
            (json.dumps({"capability": "forging", "source": "recipe:craft_meal"}), source_ids[0]),
        )
        connection.commit()
    finally:
        connection.close()
    invalid = _database_checks({"db_path": str(db_path), "world": {}})
    assert any(item["kind"] == "capability_evidence_mismatch" for item in invalid["issues"])
