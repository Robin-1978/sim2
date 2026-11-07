from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..persistence.store import WorldStore


class MemoryService:
    """Small role-scoped memory projection; writes remain Platform Intents."""

    def __init__(self, store: "WorldStore"):
        self.store = store

    def recall(self, agent_id: str, question: str = "", limit: int = 8) -> dict[str, Any]:
        return {"status": "ok", "question": question, "memories": self.store.list_memories(agent_id, limit=max(1, min(int(limit), 8)), query=question), "knowledge": self.store.list_knowledge_facts(agent_id, limit=8), "source": "agent_memory"}
