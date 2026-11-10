from __future__ import annotations

from typing import Any


def check_action_capacity(
    agent: dict[str, Any],
    action: dict[str, Any],
    duration_minutes: int,
    attributes: list[dict[str, Any]] | None = None,
) -> tuple[bool, str]:
    """Check configured per-minute costs against current state limits.

    Cost direction and bounds come from configured attribute definitions. The
    same function is used when compiling the Agent view and when Platform
    starts an Action, so a shown behavior cannot exceed a state limit silently.
    """

    duration = max(1, int(duration_minutes))
    state = agent.get("initial_state", {}) or {}
    needs = agent.get("needs", {}) or {}
    definitions = {
        str(item.get("id")): item
        for item in (attributes or [])
        if isinstance(item, dict) and item.get("id")
    }
    maxima = {
        key: float(item.get("max", 100))
        for key, item in definitions.items()
    }
    minima = {
        key: float(item.get("min", 0))
        for key, item in definitions.items()
    }
    modes = {
        key: str(item.get("cost_mode"))
        for key, item in definitions.items()
        if item.get("cost_mode") in {"increase", "decrease"}
    }
    for key, rate in (action.get("costs", {}).get("per_minute", {}) or {}).items():
        if key == "time" or not isinstance(rate, (int, float)) or rate <= 0:
            continue
        required = float(rate) * duration
        if key in state or key in needs:
            current = float(state.get(key, needs.get(key, 0)) or 0)
            mode = modes.get(str(key), "decrease" if key in needs else "increase")
            if mode == "decrease" and current - required < minima.get(str(key), 0.0) - 1e-9:
                return False, f"{key} cost {required:g} exceeds available {current:g}"
            if mode == "increase" and current + required > maxima.get(str(key), 100.0) + 1e-9:
                return False, f"{key} load {current + required:g} exceeds maximum {maxima.get(str(key), 100.0):g}"
            continue
        if key not in state:
            continue
        current = float(state.get(key, 0) or 0)
        maximum = maxima.get(str(key), 100.0)
        if current + required > maximum + 1e-9:
            return False, f"{key} load {current + required:g} exceeds maximum {maximum:g}"
    return True, ""
