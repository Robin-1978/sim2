"""Pure helpers for generic contested-resource behavior definitions."""

from __future__ import annotations

from typing import Any

from .bindings import resolve_behavior_effect


def resource_take_effect(
    behavior: dict[str, Any],
    action_defs: dict[str, dict[str, Any]],
    behavior_defs: dict[str, dict[str, Any]] | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the first configured ``resource_take`` effect in one behavior.

    The effect is resolved through Action-node literals and Agent parameters so
    an admission check sees the same target that execution will use.  Nested
    behavior nodes are followed only when the caller supplies the full
    behavior catalog.  Platform effect handlers stay independent of any world
    resource name.
    """

    return resolve_behavior_effect(
        behavior,
        "resource_take",
        action_defs,
        behavior_defs,
        parameters,
    )
