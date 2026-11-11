"""Resolve world-defined condition and Action context values.

The Runtime must not know that a particular package has food, oxygen, charge,
or another domain concept.  A package may expose literal context values and a
small set of generic resolvers; Actions and BT predicates then reference those
values through ``context.*``.
"""

from __future__ import annotations

from typing import Any


def configured_context_values(
    rules: dict[str, Any] | None,
    agent: dict[str, Any] | None = None,
    *,
    include_resolvers: bool = True,
) -> dict[str, Any]:
    rules = rules or {}
    agent = agent or {}
    configured = rules.get("context_values", {}) or {}
    # Older imported instances stored package context at the rules root
    # (for example ``satiety_alert_threshold`` and ``food_items``).  Treat
    # those scalar threshold values and the legacy food group as a declarative
    # migration, rather than making an existing world lose its recovery BTs
    # after a runtime upgrade.  New packages continue to use the explicit
    # context_values/item_groups/context_resolvers sections.
    if not configured:
        configured = {
            str(key): value
            for key, value in rules.items()
            if str(key).endswith("_threshold") and isinstance(value, (int, float)) and not isinstance(value, bool)
        }
    # Values are read-only within a condition context. A shallow copy keeps
    # callers from mutating the stored mapping without paying a deep-copy cost
    # once per person and minute.
    result = dict(configured) if isinstance(configured, dict) else {}
    groups = rules.get("item_groups", {}) or {}
    if not groups and isinstance(rules.get("food_items"), list):
        groups = {"food": list(rules.get("food_items") or [])}
    inventory = agent.get("inventory", {}) or {}

    if not include_resolvers:
        return result
    resolvers = rules.get("context_resolvers", {}) or {}
    if not resolvers and "food" in groups:
        resolvers = {"item_id": {"type": "first_inventory_item", "group": "food"}}
    for name, raw in resolvers.items():
        if not isinstance(raw, dict):
            continue
        resolver = str(raw.get("type") or raw.get("resolver") or "")
        if resolver == "first_inventory_item":
            group = str(raw.get("group") or "")
            candidates = groups.get(group, []) if isinstance(groups, dict) else []
            result[str(name)] = next(
                (
                    str(item)
                    for item in (candidates or [])
                    if isinstance(inventory.get(str(item)), (int, float))
                    and float(inventory.get(str(item), 0) or 0) > 0
                ),
                None,
            )
    # The first imported starter package used ``first_food`` as the action
    # default name before context resolvers were named explicitly. Preserve
    # that harmless alias whenever a package exposes an item resolver; the
    # Platform still resolves the value from the configured inventory group.
    if "item_id" in result and "first_food" not in result:
        result["first_food"] = result.get("item_id")
    return result
