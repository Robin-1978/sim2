"""Turn player-facing condition failures into actions the player can take."""

from __future__ import annotations

import re
from typing import Any


_ACTION_PREFIX = re.compile(r"^Action .+? cannot start:\s*", re.IGNORECASE)
_CHECK_PREFIX = re.compile(r"^(?:precondition (?:not met|failed)|condition failed):\s*", re.IGNORECASE)
_LOCATION_TAG = re.compile(r"^location[. ]tag[. ]([\w -]+)$", re.IGNORECASE)
_LOCATION_ID = re.compile(r"""^actor(?:\.state\.| state )(?:location_id|location id) == ['"]([^'"]+)['"]$""", re.IGNORECASE)
_RESERVE = re.compile(r"^required (?:carried item|resource) (.+) is unavailable$", re.IGNORECASE)


def explain_requirement(
    reason: str,
    locations: dict[str, dict[str, Any]],
    item_labels: dict[str, str],
    hints: dict[str, str],
) -> str:
    unwrapped = _CHECK_PREFIX.sub("", _ACTION_PREFIX.sub("", reason.strip().removeprefix("Configured condition: "))).rstrip(".")
    if hint := hints.get(unwrapped):
        return hint
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
        label = item_labels.get(item.replace(" ", "_")) or (item if re.search(r"[\u4e00-\u9fff]", item) else None)
        return f"补足{label}" if label else "补足所需库存或材料"
    if unwrapped != reason and not re.search(r"[\u4e00-\u9fff]", unwrapped):
        if re.match(r"^(?:world\.|actor\.(?:state|need|inventory)\.)[\w.]+ [<>=!]+ -?\d+(?:\.\d+)?$", unwrapped):
            return unwrapped
        return "查看行动所需的地点、材料和权限"
    return unwrapped
