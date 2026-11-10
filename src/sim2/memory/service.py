from __future__ import annotations

import os
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
        self.similarity = similarity or ExperienceSimilarity(config, embedding_store=store)
        preload = bool(config.get("preload", False)) or os.getenv("SIM2_BGE_PRELOAD", "").strip().casefold() in {"1", "true", "yes", "on"}
        if preload:
            self.similarity.preload()

    def status(self) -> dict[str, Any]:
        """Expose the selected retrieval backend without forcing model load."""

        return self.similarity.status()

    @staticmethod
    def _dimension_score(item: dict[str, Any], dimensions: dict[str, Any] | None) -> float:
        """Return a soft metadata match used as a small ranking feature."""

        if not dimensions:
            return 0.0
        tags = item.get("tags") if isinstance(item.get("tags"), dict) else {}
        matched = 0
        total = 0
        for key, wanted in dimensions.items():
            if wanted in (None, "", []):
                continue
            total += 1
            wanted_values = wanted if isinstance(wanted, (list, tuple, set)) else [wanted]
            actual = tags.get(key)
            actual_values = actual if isinstance(actual, (list, tuple, set)) else [actual]
            if {str(value).casefold() for value in wanted_values if value not in (None, "", [])} & {str(value).casefold() for value in actual_values if value not in (None, "", [])}:
                matched += 1
        return matched / max(1, total)

    def _rank(self, items: list[dict[str, Any]], query: str, limit: int, dimensions: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Rank every permitted candidate, with tags as a soft tie-breaker.

        Scope and permission filtering happen before this method. Metadata does
        not remove a candidate: BGE/lexical relevance is evaluated over the
        whole permitted set, then a small tag boost improves task alignment.
        """

        if not items:
            return []
        ranked = self.similarity.rank(query, items, limit=len(items))
        if dimensions:
            def score(item: dict[str, Any]) -> tuple[float, float, float]:
                relevance = float(item.get("_retrieval_relevance", 1.0 if not query.strip() else 0.0))
                tag_boost = self._dimension_score(item, dimensions)
                return (relevance + 0.12 * tag_boost, float(item.get("importance", 0.5) or 0), float(item.get("uses", 0) or 0))
            ranked.sort(key=score, reverse=True)
        return ranked[: max(1, int(limit))]

    def recall(self, agent_id: str, question: str = "", limit: int = 8, *, scope: str = "all") -> dict[str, Any]:
        scope = scope if scope in {"all", "short_term", "long_term"} else "all"
        memories = self.retrieve(agent_id, query=question, limit=limit, scope=scope, touch=True)
        knowledge = self.relevant_facts(agent_id, query=question, limit=limit)
        return {"status": "ok", "question": question, "scope": scope, "memories": memories, "knowledge": knowledge, "source": "agent_memory"}

    def retrieve(
        self,
        agent_id: str,
        *,
        query: str = "",
        limit: int = 8,
        scope: str = "all",
        touch: bool = False,
        dimensions: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve task-relevant short and long memory without age decay.

        Short-term records preserve the current working context, while
        long-term records contain consolidated experience.  Both are ranked
        against the current task/goal/location/behavior query when supplied.
        """

        limit = max(1, min(int(limit), 20))
        def relevant(items: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
            ranked_items = self._rank(items, query, budget, dimensions)
            if not query.strip():
                return ranked_items
            # Do not fill the prompt with the highest-scoring irrelevant row.
            # Pending interaction records remain visible because they require
            # an immediate response even when their wording shares no terms
            # with the current goal.
            projected = []
            threshold = self.similarity.embedding_retrieval_threshold if self.similarity._method.startswith("embedding_cosine") else self.similarity.retrieval_threshold
            for item in ranked_items:
                relevance = float(item["_retrieval_relevance"]) if "_retrieval_relevance" in item else self.similarity.relevance(query, item)
                if relevance >= threshold or bool(item.get("pending_decision")):
                    projected.append(item)
            for item in projected:
                item.pop("_retrieval_relevance", None)
            return projected

        if scope in {"short_term", "long_term"}:
            candidates = self.store.list_memories(agent_id, limit=1000, tier=scope, touch=False)
            ranked = relevant(candidates, limit)
        else:
            # Preserve both working context and durable lessons when the
            # caller asks for the default layered view.  Relevance ranking is
            # still performed independently inside each tier.
            short = self.store.list_memories(agent_id, limit=1000, tier="short_term", touch=False)
            long = self.store.list_memories(agent_id, limit=1000, tier="long_term", touch=False)
            short_limit = min(len(short), max(1, (limit + 1) // 2)) if short else 0
            long_limit = min(len(long), max(0, limit - short_limit)) if long else 0
            ranked = relevant(short, short_limit) if short_limit else []
            ranked.extend(relevant(long, long_limit) if long_limit else [])
            if len(ranked) < limit and long and short_limit < len(short):
                ranked.extend(relevant(short, limit)[short_limit:limit - len(ranked) + short_limit])
        if touch and ranked:
            # Touch only what is exposed; retrieval strengthens accessibility
            # without changing durable memory by elapsed time.
            self.store.touch_memories([int(item["id"]) for item in ranked if item.get("id")])
        return ranked

    def relevant_facts(
        self,
        agent_id: str,
        *,
        query: str = "",
        limit: int = 8,
        dimensions: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve task-relevant knowledge facts using the same hybrid ranker.

        Facts are authoritative observations, while memory items are retained
        experience.  They share the retrieval envelope but remain separate in
        the response so an Agent cannot confuse a belief or lesson with a
        world observation.
        """

        facts = self.store.list_knowledge_facts(agent_id, limit=1000)
        if not facts:
            return []
        requested = max(1, min(int(limit), 20))
        ranked = self._rank(facts, query, requested, dimensions)
        if query.strip():
            threshold = self.similarity.embedding_retrieval_threshold if self.similarity._method.startswith("embedding_cosine") else self.similarity.retrieval_threshold
            ranked = [item for item in ranked if float(item.get("_retrieval_relevance", self.similarity.relevance(query, item))) >= threshold]
        for item in ranked:
            item.pop("_retrieval_relevance", None)
        return ranked[: max(1, min(int(limit), 20))]

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
