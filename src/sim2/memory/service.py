from __future__ import annotations

import os
import math
from typing import Any, TYPE_CHECKING

from .similarity import ExperienceSimilarity

if TYPE_CHECKING:  # pragma: no cover
    from ..persistence.store import WorldStore


class MemoryService:
    """Small role-scoped memory projection; writes remain Platform Intents."""

    def __init__(self, store: "WorldStore", similarity: ExperienceSimilarity | None = None):
        self.store = store
        rules = store.repositories.metadata.get("rules", {}) or {}
        config = rules.get("memory_quality", {}).get("semantic_similarity", {}) if isinstance(rules, dict) else {}
        self.similarity = similarity or ExperienceSimilarity(config, embedding_store=store)
        ranking = config.get("ranking") if isinstance(config.get("ranking"), dict) else {}
        self.ranking = {
            "relevance": float(ranking.get("relevance", 0.62)),
            "importance": float(ranking.get("importance", 0.18)),
            "recency": float(ranking.get("recency", 0.12)),
            "access": float(ranking.get("access", 0.08)),
            "half_life_minutes": max(1.0, float(ranking.get("half_life_minutes", 1440))),
            "diversity": max(0.0, min(1.0, float(ranking.get("diversity", 0.12)))),
        }
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

    @staticmethod
    def _memory_text(item: dict[str, Any]) -> str:
        return " ".join(
            str(item.get(key) or "").strip()
            for key in ("summary", "fact", "topic", "text", "goal", "reason")
            if str(item.get(key) or "").strip()
        ).casefold()

    @staticmethod
    def _token_overlap(left: str, right: str) -> float:
        left_tokens = {token for token in left.split() if len(token) > 1}
        right_tokens = {token for token in right.split() if len(token) > 1}
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    def _rank(self, items: list[dict[str, Any]], query: str, limit: int, dimensions: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Rank permitted memories by relevance, importance, recency and access.

        Tag metadata is a soft alignment feature, not a hard filter.  A small
        diversity pass prevents the top of the prompt from filling with near
        duplicates of the same experience.
        """

        if not items:
            return []
        ranked = self.similarity.rank(query, items, limit=len(items))
        now = int(self.store.repositories.metadata.get("world_minute", 0) or 0)
        weights = self.ranking

        def score(item: dict[str, Any]) -> tuple[float, float, float]:
            relevance = 1.0 if not str(query or "").strip() else float(item.get("_retrieval_relevance", 0.0))
            importance = max(0.0, min(1.0, float(item.get("importance", 0.5) or 0.0)))
            last_access = item.get("last_access_minute")
            if isinstance(last_access, (int, float)) and not isinstance(last_access, bool):
                age = max(0.0, float(now - int(last_access)))
                recency = math.exp(-age / weights["half_life_minutes"])
            else:
                recency = 0.5
            uses = max(0, int(item.get("access_count", item.get("uses", 0)) or 0))
            access = min(1.0, math.log1p(uses) / math.log1p(100))
            tag_boost = self._dimension_score(item, dimensions)
            total = (
                weights["relevance"] * relevance
                + weights["importance"] * importance
                + weights["recency"] * recency
                + weights["access"] * access
                + 0.10 * tag_boost
            )
            return (total, importance, uses)

        ranked.sort(key=score, reverse=True)
        target = max(1, int(limit))
        diversity = float(weights.get("diversity", 0.0) or 0.0)
        if diversity <= 0 or target <= 1:
            return ranked[:target]
        selected: list[dict[str, Any]] = []
        selected_text: list[str] = []
        for item in ranked:
            text = self._memory_text(item)
            if any(self._token_overlap(text, other) >= max(0.55, 1.0 - diversity) for other in selected_text):
                continue
            selected.append(item)
            selected_text.append(text)
            if len(selected) >= target:
                break
        if len(selected) < target:
            chosen = {id(item) for item in selected}
            selected.extend(item for item in ranked if id(item) not in chosen)
        return selected[:target]

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
            candidates = self.store.repositories.memory.list_memories(agent_id, limit=1000, tier=scope, touch=False)
            ranked = relevant(candidates, limit)
        else:
            # Preserve both working context and durable lessons when the
            # caller asks for the default layered view.  Relevance ranking is
            # still performed independently inside each tier.
            short = self.store.repositories.memory.list_memories(agent_id, limit=1000, tier="short_term", touch=False)
            long = self.store.repositories.memory.list_memories(agent_id, limit=1000, tier="long_term", touch=False)
            short_limit = min(len(short), max(1, (limit + 1) // 2)) if short else 0
            long_limit = min(len(long), max(0, limit - short_limit)) if long else 0
            ranked = relevant(short, short_limit) if short_limit else []
            ranked.extend(relevant(long, long_limit) if long_limit else [])
            if len(ranked) < limit and long and short_limit < len(short):
                ranked.extend(relevant(short, limit)[short_limit:limit - len(ranked) + short_limit])
        if touch and ranked:
            # Touch only what is exposed; retrieval strengthens accessibility
            # without changing durable memory by elapsed time.
            self.store.repositories.memory.touch_memories([int(item["id"]) for item in ranked if item.get("id")])
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

        facts = self.store.repositories.memory.list_knowledge_facts(agent_id, limit=1000)
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

        memories = self.store.repositories.memory.list_memories(agent_id, limit=1000, tier="long_term")
        match = self.similarity.find_match(summary, memories)
        return {"duplicate": match.score >= self.similarity.threshold, "score": round(match.score, 4), "method": match.method, "memory_id": match.matched_memory_id}

    @staticmethod
    def _consolidation_bucket(item: dict[str, Any]) -> str:
        memory_type = str(item.get("memory_type") or item.get("type") or "").casefold()
        if memory_type in {"knowledge", "fact", "observation"}:
            return "facts"
        if memory_type in {"experience", "capability", "summary"}:
            return "lessons"
        if memory_type in {"goal", "commitment", "open_loop", "promise"}:
            return "open_loops"
        return "other"

    def compact(self, agent_id: str, *, keep: int = 24) -> dict[str, Any]:
        """Create a structured, bounded summary and forget old raw items."""

        all_items = self.store.repositories.memory.list_memories(agent_id, limit=1000)
        if len(all_items) <= keep:
            return {"status": "unchanged", "count": len(all_items)}
        retained = all_items[:keep]
        forgotten = all_items[keep:]
        buckets: dict[str, list[str]] = {"facts": [], "lessons": [], "open_loops": [], "other": []}
        seen: set[str] = set()
        for item in forgotten[:64]:
            text = str(
                item.get("summary") or item.get("fact") or item.get("topic")
                or item.get("goal") or item.get("reason") or item.get("type", "memory")
            ).strip()
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            bucket = self._consolidation_bucket(item)
            if len(buckets[bucket]) < 8:
                buckets[bucket].append(text)
        snippets = [text for values in buckets.values() for text in values]
        with self.store.transaction():
            summary_id = self.store.repositories.memory.put_memory(
                agent_id,
                "summary",
                {
                    "summary": " | ".join(snippets),
                    "sections": buckets,
                    "source_count": len(forgotten),
                    "retained_count": len(retained),
                    "consolidated_types": sorted({str(item.get("memory_type") or item.get("type") or "unknown") for item in forgotten}),
                },
            )
            deleted = self.store.repositories.memory.delete_memories(agent_id, [int(item["id"]) for item in forgotten if item.get("id")])
        return {
            "status": "compacted",
            "summary_id": summary_id,
            "forgotten": deleted,
            "retained": len(retained),
            "sections": {key: len(value) for key, value in buckets.items()},
        }

    def forget(self, agent_id: str, *, maximum: int = 64) -> dict[str, Any]:
        """Apply a hard memory bound while retaining newest items."""

        items = self.store.repositories.memory.list_memories(agent_id, limit=1000)
        if len(items) <= maximum:
            return {"status": "unchanged", "count": len(items)}
        old = items[maximum:]
        with self.store.transaction():
            deleted = self.store.repositories.memory.delete_memories(agent_id, [int(item["id"]) for item in old if item.get("id")])
        return {"status": "forgotten", "deleted": deleted, "remaining": maximum}
