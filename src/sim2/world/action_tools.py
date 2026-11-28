"""Declarative time bonuses for Actions that use carried tools."""

from __future__ import annotations

import math
from typing import Any


def validate_tool_duration(action: dict[str, Any]) -> str | None:
    duration = action.get("duration")
    if not isinstance(duration, dict) or "tool_bonuses" not in duration:
        return None
    bonuses = duration["tool_bonuses"]
    label = f"action {action.get('id')} duration.tool_bonuses"
    minutes = duration.get("minutes")
    if type(minutes) not in (int, float) or not 0 < minutes < math.inf or duration.get("mode"):
        return f"{label} requires positive fixed minutes"
    if not isinstance(bonuses, list) or not bonuses:
        return f"{label} must be a non-empty list"
    for index, bonus in enumerate(bonuses):
        entry = f"{label}[{index}]"
        if not isinstance(bonus, dict) or bonus.keys() - {"item", "multiplier", "resource_tags"}:
            return f"{entry} accepts item, multiplier and optional resource_tags"
        if not isinstance(bonus.get("item"), str) or not bonus["item"].strip():
            return f"{entry}.item must name a carried tool"
        multiplier = bonus.get("multiplier")
        if type(multiplier) not in (int, float) or not 0 < multiplier < 1:
            return f"{entry}.multiplier must be between 0 and 1"
        tags = bonus.get("resource_tags")
        if "resource_tags" in bonus and (
            not isinstance(tags, list) or not tags
            or any(not isinstance(tag, str) or not tag.strip() for tag in tags)
        ):
            return f"{entry}.resource_tags must be a non-empty list of resource tags"
    return None


def best_time_tool(
    action: dict[str, Any], parameters: dict[str, Any], inventory: dict[str, Any],
) -> tuple[str, float] | None:
    bonuses = (action.get("duration") or {}).get("tool_bonuses")
    if not bonuses:
        return None
    resource_tag = parameters.get("resource_tag")
    matches = (
        (bonus["item"], bonus["multiplier"])
        for bonus in bonuses
        if (inventory.get(bonus["item"]) or 0) >= 1
        and ("resource_tags" not in bonus or resource_tag in bonus["resource_tags"])
    )
    return min(matches, key=lambda tool: tool[1], default=None)
