"""Optional semantic similarity checks for experience retention.

The simulation must remain usable without downloading an embedding model.  A
small token based score is therefore the deterministic fallback.  If a world
configures a local SentenceTransformers model (for example a BGE model), it is
loaded lazily on the first comparison and only used to suggest that a new
experience is redundant.  It never performs a world mutation by itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 1}


@dataclass(frozen=True)
class SimilarityMatch:
    score: float
    method: str
    matched_memory_id: int | None = None


class ExperienceSimilarity:
    """Lazy BGE-compatible checker with a deterministic fallback."""

    def __init__(self, config: dict[str, Any] | None = None):
        config = config or {}
        self.enabled = bool(config.get("enabled", False))
        self.threshold = float(config.get("threshold", 0.88))
        self.model_name = str(config.get("model", ""))
        self._model: Any = None
        self._method = "token_jaccard"

    def _load_model(self) -> None:
        if self._model is not None or not self.enabled or not self.model_name:
            return
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            self._model = SentenceTransformer(self.model_name)
            self._method = "embedding_cosine"
        except Exception:
            # The core runtime stays available when the optional package or
            # model is absent.  The caller can expose this method in diagnostics.
            self._model = False
            self._method = "token_jaccard_fallback"

    def score(self, left: str, right: str) -> float:
        if not left.strip() or not right.strip():
            return 0.0
        if left.strip().casefold() == right.strip().casefold():
            return 1.0
        self._load_model()
        if self._model and self._method == "embedding_cosine":
            vectors = self._model.encode([left, right], normalize_embeddings=True)
            return max(0.0, min(1.0, float(sum(a * b for a, b in zip(vectors[0], vectors[1])))))
        a, b = _tokens(left), _tokens(right)
        return len(a & b) / max(1, len(a | b))

    def find_match(self, summary: str, memories: list[dict[str, Any]]) -> SimilarityMatch:
        best = SimilarityMatch(0.0, self._method)
        for memory in memories:
            if memory.get("type") != "experience":
                continue
            candidate = str(memory.get("summary") or "")
            score = self.score(summary, candidate)
            if score > best.score:
                best = SimilarityMatch(score, self._method, int(memory["id"]) if memory.get("id") else None)
        return best
