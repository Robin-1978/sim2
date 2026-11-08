from __future__ import annotations

from typing import Any


def check_action_capacity(
    agent: dict[str, Any],
    action: dict[str, Any],
    duration_minutes: int,
    attributes: list[dict[str, Any]] | None = None,
) -> tuple[bool, str]:
    """Check configured per-minute costs against current state limits.

    Energy and health are consumed. Other numeric state or need costs are
    treated as load and must fit below the configured maximum. The same
    function is used when compiling the Agent view and when Platform starts an
    Action, so a shown behavior cannot exceed a state limit silently.
    """

    duration = max(1, int(duration_minutes))
    state = agent.get("initial_state", {}) or {}
    needs = agent.get("needs", {}) or {}
    maxima = {
        str(item.get("id")): float(item.get("max", 100))
        for item in (attributes or [])
        if isinstance(item, dict) and item.get("id")
    }
    for key, rate in (action.get("costs", {}).get("per_minute", {}) or {}).items():
        if key == "time" or not isinstance(rate, (int, float)) or rate <= 0:
            continue
        required = float(rate) * duration
        if key in {"energy", "health"} or key in needs:
            available = float(state.get(key, needs.get(key, 0)) or 0)
            if required > available + 1e-9:
                return False, f"{key} cost {required:g} exceeds available {available:g}"
            continue
        if key not in state:
            continue
        current = float(state.get(key, 0) or 0)
        maximum = maxima.get(str(key), 100.0)
        if current + required > maximum + 1e-9:
            return False, f"{key} load {current + required:g} exceeds maximum {maximum:g}"
    return True, ""
