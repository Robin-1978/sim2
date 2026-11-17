"""Character lifecycle statuses and the configured recovery from incapacitation.

A character whose vital attribute reaches its minimum becomes ``unconscious``.
Without configuration that state is absorbing: nothing in the engine ends it,
so a world whose characters are all incapacitated stops evolving.  A world can
declare how long a character stays unconscious and how much of the vital range
it regains on waking:

.. code-block:: yaml

    incapacitation:
      recovery:
        after_minutes: 720
        vital_fraction: 0.25

The section is optional; a world without it keeps the absorbing behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

UNCONSCIOUS = "unconscious"
DEAD = "dead"
# Characters in these statuses take no decision turn and are left out of the
# option-stagnation evaluation.
INACTIVE_STATUSES = frozenset({UNCONSCIOUS, DEAD})
# A character in one of these statuses is already carrying out a behavior.
BUSY_STATUSES = frozenset({"executing", "paused"})
INCAPACITATED_CONDITION = "incapacitated"
INCAPACITATED_SINCE_KEY = "incapacitated_at"
_RECOVERY_FIELDS = ("after_minutes", "vital_fraction")


@dataclass(frozen=True)
class RecoveryRule:
    """How an unconscious character comes round on its own."""

    after_minutes: int
    vital_fraction: float

    def restored_vital(self, minimum: float, maximum: float, current: float) -> float:
        """Vital value after waking: the configured share of the range, never lower than now."""

        return max(float(current), float(minimum) + self.vital_fraction * (float(maximum) - float(minimum)))


def is_inactive(state: dict[str, Any] | None) -> bool:
    """Whether a character state is dead or unconscious."""

    return str((state or {}).get("status", "")).casefold() in INACTIVE_STATUSES


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_incapacitation_rule(rules: dict[str, Any]) -> list[str]:
    """Return the authoring errors of ``rules.incapacitation`` (empty when valid or absent)."""

    section = rules.get("incapacitation") if isinstance(rules, dict) else None
    if section is None:
        return []
    if not isinstance(section, dict):
        return ["rules.incapacitation must be an object"]
    errors: list[str] = []
    unknown = sorted(str(key) for key in section if key != "recovery")
    if unknown:
        errors.append("rules.incapacitation has unknown field(s): " + ", ".join(unknown) + "; use recovery")
    recovery = section.get("recovery")
    if recovery is None:
        return errors
    if not isinstance(recovery, dict):
        return errors + ["rules.incapacitation.recovery must be an object with after_minutes and vital_fraction"]
    if not rules.get("vital_attribute"):
        errors.append("rules.incapacitation.recovery needs rules.vital_attribute")
    unknown = sorted(str(key) for key in recovery if key not in _RECOVERY_FIELDS)
    if unknown:
        errors.append("rules.incapacitation.recovery has unknown field(s): " + ", ".join(unknown) + "; use after_minutes, vital_fraction")
    after = recovery.get("after_minutes")
    if isinstance(after, bool) or not isinstance(after, int) or after < 1:
        errors.append("rules.incapacitation.recovery.after_minutes must be a positive integer")
    fraction = recovery.get("vital_fraction")
    if not _is_number(fraction) or not 0 < float(fraction) <= 1:
        errors.append("rules.incapacitation.recovery.vital_fraction must be a number above 0 and at most 1")
    return errors


def recovery_rule(rules: dict[str, Any]) -> RecoveryRule | None:
    """The world's configured recovery, or ``None`` when incapacitation is absorbing."""

    if validate_incapacitation_rule(rules):
        return None
    section = rules.get("incapacitation") if isinstance(rules, dict) else None
    recovery = section.get("recovery") if isinstance(section, dict) else None
    if not isinstance(recovery, dict):
        return None
    return RecoveryRule(after_minutes=int(recovery["after_minutes"]), vital_fraction=float(recovery["vital_fraction"]))
