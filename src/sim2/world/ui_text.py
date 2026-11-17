"""World-owned presentation text for clients and public projections.

A projection never embeds wording that belongs to one world.  A world declares
the phrases it wants in ``setting.yaml`` (``world.ui_text`` in a template):

.. code-block:: yaml

    ui_text:
      travel_choice: "前往 {name}"
      travel_time: "预计 {minutes} 分钟"
      world_label: 世界

Every key is optional and falls back to the neutral English below, so a world
that declares nothing still renders.  A phrase may only use the placeholders
listed for its key, written as plain ``{name}`` fields.
"""

from __future__ import annotations

from collections.abc import Mapping
from string import Formatter
from typing import Any

# key -> (neutral default, placeholders the phrase may use)
UI_TEXT_KEYS: dict[str, tuple[str, frozenset[str]]] = {
    "travel_choice": ("Travel to {name}", frozenset({"name"})),
    "travel_summary": ("Follow the known route to {name}.", frozenset({"name"})),
    "travel_purpose": ("Move to another known place", frozenset()),
    "travel_time": ("{minutes} minutes", frozenset({"minutes"})),
    "travel_impact": ("Arrive at the selected location", frozenset()),
    "world_label": ("The world", frozenset()),
}


def _phrase_errors(key: str, value: Any) -> list[str]:
    """Authoring errors of one declared phrase (empty when it can be used)."""

    if not isinstance(value, str) or not value.strip():
        return [f"ui_text.{key} must be a non-empty string"]
    allowed = UI_TEXT_KEYS[key][1]
    try:
        fields = list(Formatter().parse(value))
    except ValueError as exc:
        return [f"ui_text.{key} is not a valid phrase: {exc}"]
    errors: list[str] = []
    for _literal, field, spec, conversion in fields:
        if field is None:
            continue
        if field not in allowed or spec or conversion:
            usable = ", ".join("{" + name + "}" for name in sorted(allowed)) or "no placeholders"
            errors.append(f"ui_text.{key} may only use {usable}; found {{{field}}}")
    return errors


def validate_ui_text(declared: Any) -> list[str]:
    """Return the authoring errors of a world's ``ui_text`` (empty when valid or absent)."""

    if declared is None:
        return []
    if not isinstance(declared, Mapping):
        return ["ui_text must be an object"]
    errors: list[str] = []
    for key, value in declared.items():
        if key not in UI_TEXT_KEYS:
            errors.append(f"ui_text.{key} is not a known key; use " + ", ".join(sorted(UI_TEXT_KEYS)))
        else:
            errors.extend(_phrase_errors(str(key), value))
    return errors


class UiText:
    """The phrases a world declared, with the neutral default for everything else."""

    def __init__(self, declared: Mapping[str, Any] | None = None) -> None:
        self._declared = {
            str(key): value
            for key, value in (declared or {}).items()
            if key in UI_TEXT_KEYS and not _phrase_errors(str(key), value)
        }

    @classmethod
    def from_setting(cls, setting: Mapping[str, Any] | None) -> "UiText":
        declared = (setting or {}).get("ui_text")
        return cls(declared if isinstance(declared, Mapping) else None)

    def text(self, key: str, **values: Any) -> str:
        """The phrase for ``key`` with its placeholders filled."""

        return str(self._declared.get(key) or UI_TEXT_KEYS[key][0]).format(**values)
