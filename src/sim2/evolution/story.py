from __future__ import annotations

from typing import Any, Iterable


def story_evidence_event_types(event: dict[str, Any] | None) -> set[str]:
    """Return the event types that identify the evidence behind a Story session.

    A progress or milestone event summarizes an earlier validated result and
    records that result's type in ``source_event_type``.  Configured beats name
    the original evidence type, so the summarizing event must inherit it.
    Otherwise a configured continuation is visible in the session that holds
    the evidence but hidden in the later milestone session, where the model is
    free to record only an emergent beat instead.
    """

    if not isinstance(event, dict):
        return set()
    types = {str(event.get("event_type") or ""), str(event.get("source_event_type") or "")}
    types.discard("")
    return types


def eligible_story_beats(
    beats: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    source_event_type: str | Iterable[str] = "",
    observed_events: set[str] | None = None,
    observed_facts: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return configured beats supported by the current causal evidence.

    AgentView and intent validation must use the same scope.  Otherwise a
    model can be shown no configured continuation and then be rejected merely
    because an unrelated beat exists elsewhere in the world package.

    ``source_event_type`` is one event type or every type that identifies the
    session evidence (see :func:`story_evidence_event_types`).
    """

    observed_events = {str(item) for item in (observed_events or set())}
    observed_facts = {str(item) for item in (observed_facts or set())}
    if isinstance(source_event_type, str):
        source_types = {source_event_type} if source_event_type else set()
    else:
        source_types = {str(item) for item in source_event_type if str(item)}
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
        if trigger_events and source_types and not source_types & trigger_events:
            continue
        if trigger_events and not source_types and not trigger_events & observed_events:
            continue
        if trigger_facts and not trigger_facts <= observed_facts:
            continue
        result.append(beat)
    return result
