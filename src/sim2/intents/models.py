from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class BehaviorIntent:
    actor_id: str
    behavior_ref: str
    target: dict[str, Any]
    parameters: dict[str, Any]
    reason: str
    confidence: float
    intent_id: str
    intent_type: str = "select_behavior"

    def __post_init__(self) -> None:
        if self.behavior_ref and not self.behavior_ref.startswith("behavior:"):
            object.__setattr__(self, "behavior_ref", f"behavior:{self.behavior_ref}")

    @classmethod
    def from_json(cls, data: dict[str, Any], *, actor_id: str | None = None) -> "BehaviorIntent":
        ref = data.get("behavior_ref") or data.get("behavior_id") or data.get("behavior")
        if not isinstance(ref, str) or not ref:
            raise ValueError("behavior_ref is required")
        if not ref.startswith("behavior:"):
            ref = f"behavior:{ref}"
        return cls(
            actor_id=str(actor_id or data.get("actor_id") or ""),
            behavior_ref=ref,
            target=dict(data.get("target") or {}),
            parameters=dict(data.get("parameters") or {}),
            reason=str(data.get("reason") or data.get("decision_summary") or ""),
            confidence=float(data.get("confidence", 0.0)),
            intent_id=str(data.get("intent_id") or uuid4()),
        )
