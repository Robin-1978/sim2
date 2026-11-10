from __future__ import annotations

from typing import Any


def eligible_story_beats(
    beats: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    source_event_type: str = "",
    observed_events: set[str] | None = None,
    observed_facts: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return configured beats supported by the current causal evidence.

    AgentView and intent validation must use the same scope.  Otherwise a
    model can be shown no configured continuation and then be rejected merely
    because an unrelated beat exists elsewhere in the world package.
    """

    observed_events = {str(item) for item in (observed_events or set())}
    observed_facts = {str(item) for item in (observed_facts or set())}
    source_event_type = str(source_event_type or "")
    result: list[dict[str, Any]] = []
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        beat_id = str(beat.get("id") or "")
        current = state.get(beat_id) if isinstance(state.get(beat_id), dict) else {}
        if current.get("status") in {"active", "completed"}:
            continue
        trigger_events = {str(item) for item in (beat.get("trigger_events") or [])}
        trigger_facts = {str(item) for item in (beat.get("trigger_facts") or [])}
        if trigger_events and source_event_type and source_event_type not in trigger_events:
            continue
        if trigger_events and not source_event_type and not trigger_events & observed_events:
            continue
        if trigger_facts and not trigger_facts <= observed_facts:
            continue
        result.append(beat)
    return result
