from __future__ import annotations

from typing import Any, TYPE_CHECKING

from .similarity import ExperienceSimilarity

if TYPE_CHECKING:  # pragma: no cover
    from ..persistence.store import WorldStore


class MemoryService:
    """Small role-scoped memory projection; writes remain Platform Intents."""

    def __init__(self, store: "WorldStore", similarity: ExperienceSimilarity | None = None):
        self.store = store
        rules = store.get_meta("rules", {}) or {}
        config = rules.get("memory_quality", {}).get("semantic_similarity", {}) if isinstance(rules, dict) else {}
        self.similarity = similarity or ExperienceSimilarity(config)

    def recall(self, agent_id: str, question: str = "", limit: int = 8, *, scope: str = "all") -> dict[str, Any]:
        scope = scope if scope in {"all", "short_term", "long_term"} else "all"
        memories = self.retrieve(agent_id, query=question, limit=limit, scope=scope, touch=True)
        return {"status": "ok", "question": question, "scope": scope, "memories": memories, "knowledge": self.store.list_knowledge_facts(agent_id, limit=8), "source": "agent_memory"}

    def retrieve(
        self,
        agent_id: str,
        *,
        query: str = "",
        limit: int = 8,
        scope: str = "all",
        touch: bool = False,
    ) -> list[dict[str, Any]]:
        """Retrieve task-relevant short and long memory without age decay.

        Short-term records preserve the current working context, while
        long-term records contain consolidated experience.  Both are ranked
        against the current task/goal/location/behavior query when supplied.
        """

        limit = max(1, min(int(limit), 20))
        if scope in {"short_term", "long_term"}:
            candidates = self.store.list_memories(agent_id, limit=1000, tier=scope, touch=False)
            ranked = self.similarity.rank(query, candidates, limit=limit)
        else:
            # Preserve both working context and durable lessons when the
            # caller asks for the default layered view.  Relevance ranking is
            # still performed independently inside each tier.
            short = self.store.list_memories(agent_id, limit=1000, tier="short_term", touch=False)
            long = self.store.list_memories(agent_id, limit=1000, tier="long_term", touch=False)
            short_limit = min(len(short), max(1, (limit + 1) // 2)) if short else 0
            long_limit = min(len(long), max(0, limit - short_limit)) if long else 0
            ranked = self.similarity.rank(query, short, limit=short_limit) if short_limit else []
            ranked.extend(self.similarity.rank(query, long, limit=long_limit) if long_limit else [])
            if len(ranked) < limit and long and short_limit < len(short):
                ranked.extend(self.similarity.rank(query, short, limit=limit)[short_limit:limit - len(ranked) + short_limit])
        if touch and ranked:
            # Touch only what is exposed; retrieval strengthens accessibility
            # without changing durable memory by elapsed time.
            self.store.touch_memories([int(item["id"]) for item in ranked if item.get("id")])
        return ranked

    def experience_match(self, agent_id: str, summary: str) -> dict[str, Any]:
        """Return a quality hint; the Reflection Agent still decides what to retain."""

        memories = self.store.list_memories(agent_id, limit=1000, tier="long_term")
        match = self.similarity.find_match(summary, memories)
        return {"duplicate": match.score >= self.similarity.threshold, "score": round(match.score, 4), "method": match.method, "memory_id": match.matched_memory_id}

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
