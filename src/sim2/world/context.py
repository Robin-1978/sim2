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
    # Values are read-only within a condition context. A shallow copy keeps
    # callers from mutating the stored mapping without paying a deep-copy cost
    # once per person and minute.
    result = dict(configured) if isinstance(configured, dict) else {}
    groups = rules.get("item_groups", {}) or {}
    inventory = agent.get("inventory", {}) or {}

    if not include_resolvers:
        return result
    resolvers = rules.get("context_resolvers", {}) or {}
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
    return result
