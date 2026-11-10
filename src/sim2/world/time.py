from __future__ import annotations

from typing import Any


def world_time_view(store: Any, minute: int | None = None) -> dict[str, Any]:
    """Compile configured calendar time into one model/runtime projection."""

    setting = store.get_meta("setting", {}) or {}
    calendar = setting.get("calendar", {}) if isinstance(setting, dict) else {}
    day_minutes = max(1, int(calendar.get("minutes_per_day", 1440) or 1440))
    week_minutes = max(day_minutes, int(calendar.get("minutes_per_week", day_minutes * 7) or day_minutes * 7))
    month_minutes = max(week_minutes, int(calendar.get("minutes_per_month", week_minutes * 4) or week_minutes * 4))
    clock = setting.get("world_clock", {}) if isinstance(setting, dict) else {}
    current = int(store.get_meta("world_minute", 0) if minute is None else minute)
    start_day = int(clock.get("start_day", 1) or 1)
    start_minute = int(clock.get("start_minute", 0) or 0)
    absolute = max(0, start_minute + current)
    day = start_day + absolute // day_minutes
    minute_of_day = absolute % day_minutes
    hour, minute_part = divmod(minute_of_day, 60)
    season = str(clock.get("season") or setting.get("season") or "unknown")
    phase = configured_day_phase(setting, minute_of_day, day_minutes=day_minutes)
    phase_text = f", {phase}" if phase != "unknown" else ""
    return {
        "minute": current,
        "day": day,
        "week": absolute // week_minutes + 1,
        "month": absolute // month_minutes + 1,
        "minute_of_day": minute_of_day,
        "time_of_day": f"{hour:02d}:{minute_part:02d}",
        "day_phase": phase,
        "season": season,
        "label": f"Day {day}, {hour:02d}:{minute_part:02d}{phase_text}, {season}",
    }


def configured_day_phase(setting: dict[str, Any], minute_of_day: int, *, day_minutes: int = 1440) -> str:
    """Resolve a package-defined phase, including ranges that cross midnight."""

    calendar = setting.get("calendar", {}) if isinstance(setting, dict) else {}
    phases = calendar.get("day_phases", []) if isinstance(calendar, dict) else []
    for item in phases or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        start = int(item.get("start_minute", 0) or 0) % day_minutes
        end = int(item.get("end_minute", day_minutes) or day_minutes) % day_minutes
        active = start <= minute_of_day < end if start < end else minute_of_day >= start or minute_of_day < end
        if active:
            return str(item["id"])
    return "unknown"


def minutes_until_next_day_phase(store: Any, minute: int | None = None) -> int | None:
    """Return the next configured phase boundary in world minutes."""

    setting = store.get_meta("setting", {}) or {}
    calendar = setting.get("calendar", {}) if isinstance(setting, dict) else {}
    phases = [item for item in (calendar.get("day_phases", []) or []) if isinstance(item, dict) and item.get("id")]
    if not phases:
        return None
    day_minutes = max(1, int(calendar.get("minutes_per_day", 1440) or 1440))
    current = world_time_view(store, minute).get("minute_of_day", 0)
    deltas = [
        (int(item.get("start_minute", 0) or 0) % day_minutes - int(current)) % day_minutes
        for item in phases
    ]
    positive = [value for value in deltas if value > 0]
    return min(positive) if positive else day_minutes
