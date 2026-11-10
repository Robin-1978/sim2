from __future__ import annotations

import json
import importlib.util
from pathlib import Path


_SPEC = importlib.util.spec_from_file_location("audit_real_world", Path(__file__).parents[1] / "scripts" / "audit_real_world.py")
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_quality_counts = _MODULE._quality_counts
_write_json_atomic = _MODULE._write_json_atomic


def test_audit_checkpoint_is_atomic_and_provider_errors_are_separate(tmp_path: Path):
    path = tmp_path / "audit.checkpoint.json"
    _write_json_atomic(path, {"status": "running", "sessions": [{"role": "agent_decision"}]})
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "running"
    assert not path.with_name(path.name + ".tmp").exists()

    sessions = [
        {"quality": {"passed": True, "grade": "grounded"}},
        {"quality": {"passed": False, "grade": "provider_provider_error"}},
        {"quality": {"passed": False, "grade": "invalid_choice"}},
        {"quality": {"passed": False, "pending": ["followup"], "grade": "pending"}},
    ]
    assert _quality_counts(sessions) == {"passed": 1, "pending": 1, "provider_errors": 1, "failed": 1}
