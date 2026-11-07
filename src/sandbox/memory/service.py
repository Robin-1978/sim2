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

    def compact(self, agent_id: str, *, keep: int = 24) -> dict[str, Any]:
        """Create a bounded deterministic summary and forget old raw items."""

        all_items = self.store.list_memories(agent_id, limit=1000)
        if len(all_items) <= keep:
            return {"status": "unchanged", "count": len(all_items)}
        retained = all_items[:keep]
        forgotten = all_items[keep:]
        snippets = []
        for item in forgotten[:32]:
            text = item.get("summary") or item.get("fact") or item.get("topic") or str(item.get("type", "memory"))
            snippets.append(str(text).strip())
        with self.store.transaction():
            summary_id = self.store.put_memory(agent_id, "summary", {"summary": " | ".join(snippets), "source_count": len(forgotten), "retained_count": len(retained)})
            deleted = self.store.delete_memories(agent_id, [int(item["id"]) for item in forgotten if item.get("id")])
        return {"status": "compacted", "summary_id": summary_id, "forgotten": deleted, "retained": len(retained)}

    def forget(self, agent_id: str, *, maximum: int = 64) -> dict[str, Any]:
        """Apply a hard memory bound while retaining newest items."""

        items = self.store.list_memories(agent_id, limit=1000)
        if len(items) <= maximum:
            return {"status": "unchanged", "count": len(items)}
        old = items[maximum:]
        with self.store.transaction():
            deleted = self.store.delete_memories(agent_id, [int(item["id"]) for item in old if item.get("id")])
        return {"status": "forgotten", "deleted": deleted, "remaining": maximum}
