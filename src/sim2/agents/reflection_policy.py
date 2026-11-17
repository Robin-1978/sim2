"""Evidence an Agent needs before a reflection may record a durable lesson."""

from __future__ import annotations

# Event types that count as something observable to reflect on, besides a
# completed outcome or a stored memory.  Both reflection gates read this one
# set: the view check in ``AgentIntentGateway.reflection_has_evidence`` and the
# ``record_experience`` tool in ``AgentToolExecutor``.
MEANINGFUL_REFLECTION_EVENTS = frozenset({
    "behavior.completed",
    "behavior.failed",
    "behavior.interrupted",
    "dialogue.completed",
    "trade.completed",
    "trade.offer_created",
    "trade.countered",
    "exploration.completed",
    "research.completed",
    "object.recovered",
    "relationship.changed",
    "world.change.world_property",
})
