"""Turn player-facing condition failures into actions the player can take."""

from __future__ import annotations

import re
from typing import Any


_ACTION_PREFIX = re.compile(r"^Action .+? cannot start:\s*", re.IGNORECASE)
_CHECK_PREFIX = re.compile(r"^(?:precondition (?:not met|failed)|condition failed):\s*", re.IGNORECASE)
_LOCATION_TAG = re.compile(r"^location[. ]tag[. ]([\w -]+)$", re.IGNORECASE)
_LOCATION_ID = re.compile(r"""^actor(?:\.state\.| state )(?:location_id|location id) == ['"]([^'"]+)['"]$""", re.IGNORECASE)
_RESERVE = re.compile(r"^required (?:carried item|resource) (.+) is unavailable$", re.IGNORECASE)
_INVENTORY_MINIMUM = re.compile(r"^actor\.inventory\.([\w]+) >= (\d+(?:\.\d+)?)$")
_WORLD_LIMIT = re.compile(r"^world\.([\w]+) (<=|>=|==|<|>) (-?\d+(?:\.\d+)?)$")
_LIMIT_LABELS = {
    "<=": "不高于 {limit:g} 时可执行",
    ">=": "至少要 {limit:g}",
    "==": "需要达到 {limit:g}",
    "<": "低于 {limit:g} 时可执行",
    ">": "需要高于 {limit:g}",
}


def explain_requirement(
    reason: str,
    locations: dict[str, dict[str, Any]],
    item_labels: dict[str, str],
    hints: dict[str, str],
    *,
    inventory: dict[str, Any] | None = None,
    world_properties: dict[str, Any] | None = None,
    world_labels: dict[str, dict[str, Any]] | None = None,
) -> str:
    unwrapped = _CHECK_PREFIX.sub("", _ACTION_PREFIX.sub("", reason.strip().removeprefix("Configured condition: "))).rstrip(".")
    if hint := hints.get(unwrapped):
        return hint
    if inventory is not None and (match := _INVENTORY_MINIMUM.fullmatch(unwrapped)):
        item, amount = match.groups()
        name = item_labels.get(item)
        if name:
            have, required = float(inventory.get(item) or 0), float(amount)
            return f"{name}还差 {max(0, required - have):g}（现有 {have:g} / 需要 {required:g}）"
    if world_properties is not None and (match := _WORLD_LIMIT.fullmatch(unwrapped)):
        key, operator, threshold = match.groups()
        name = (world_labels or {}).get(key, {}).get("label")
        have = world_properties.get(key)
        if name and isinstance(have, (int, float)):
            return f"{name}现为 {have:g}，" + _LIMIT_LABELS[operator].format(limit=float(threshold))
    if match := _LOCATION_TAG.fullmatch(unwrapped):
        tag = match[1].strip().replace(" ", "_")
        names = [str(node["name"]) for node in locations.values() if tag in node.get("tags", ()) and node.get("open", True)]
        return "前往" + "或".join(names[:3]) if names else "前往符合条件的地点"
    if match := _LOCATION_ID.fullmatch(unwrapped):
        target = match[1]
        node = locations.get(target) or locations.get(target.replace(" ", "_")) or next(
            (node for node in locations.values() if node.get("name") == target), {},
        )
        return f"前往{node['name']}" if node.get("name") else "前往这项行动所在的地点"
    if match := _RESERVE.fullmatch(unwrapped):
        item = match[1].strip()
        key = item.replace(" ", "_")
        label = item_labels.get(key) or (world_labels or {}).get(key, {}).get("label")
        if not label and re.search(r"[\u4e00-\u9fff]", item):
            label = item
        return f"补足{label}" if label else "补足所需库存或材料"
    return unwrapped


def player_goal_blocker(reason: str) -> bool:
    """Keep localized conditions with enough detail to guide the player."""
    return bool(re.search(r"[\u4e00-\u9fff]", reason)) and reason not in {
        "前往符合条件的地点", "前往这项行动所在的地点", "补足所需库存或材料",
    }
