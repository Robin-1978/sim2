"""Optional semantic similarity checks for experience retention.

The simulation must remain usable without downloading an embedding model.  A
small token based score is therefore the deterministic fallback.  If a world
configures a local SentenceTransformers model (for example a BGE model), it is
loaded lazily on the first comparison and only used to suggest that a new
experience is redundant.  It never performs a world mutation by itself.
"""

from __future__ import annotations

import re
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Any


_STOPWORDS = {
    "a", "an", "and", "are", "at", "be", "before", "by", "for", "from", "in", "is", "it", "of", "on", "one", "or", "the", "to", "was", "were", "with", "you",
}


def _tokens(text: str) -> set[str]:
    """Return small multilingual lexical units for the no-model fallback."""

    lowered = text.casefold()
    tokens = {token for token in re.findall(r"[a-z0-9]+", lowered) if len(token) > 1 and token not in _STOPWORDS}
    # A character fallback keeps Chinese/Japanese/Korean memory usable when
    # the optional embedding model is not installed.  Bigrams reduce matches
    # on isolated common characters while remaining deterministic.
    cjk = "".join(re.findall(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]", lowered))
    tokens.update(cjk[index:index + 2] for index in range(max(0, len(cjk) - 1)))
    return tokens


@dataclass(frozen=True)
class SimilarityMatch:
    score: float
    method: str
    matched_memory_id: int | None = None


class _BGEOnnxModel:
    """Small local BGE encoder for the cached ONNX distribution.

    The runtime keeps this adapter optional: importing sim2 does not require
    either ``onnxruntime`` or ``tokenizers``. The model is loaded only when a
    world explicitly enables semantic similarity and points at a local model
    directory (or an ONNX file).
    """

    def __init__(self, model_path: str):
        import numpy as np  # type: ignore
        import onnxruntime as ort  # type: ignore
        from tokenizers import Tokenizer  # type: ignore

        path = Path(model_path).expanduser()
        if path.is_dir():
            onnx_path = path / "model_optimized.onnx"
            tokenizer_path = path / "tokenizer.json"
        else:
            onnx_path = path
            tokenizer_path = path.with_name("tokenizer.json")
        if not onnx_path.is_file() or not tokenizer_path.is_file():
            raise FileNotFoundError(f"BGE ONNX model needs {onnx_path} and {tokenizer_path}")
        self._np = np
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._tokenizer.enable_truncation(max_length=512)
        self._session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        self._inputs = {item.name for item in self._session.get_inputs()}

    def encode(self, texts: list[str]):
        encoded = self._tokenizer.encode_batch([str(text) for text in texts])
        max_length = max(1, max(len(item.ids) for item in encoded))
        ids = []
        masks = []
        types = []
        for item in encoded:
            pad = max_length - len(item.ids)
            ids.append(list(item.ids) + [0] * pad)
            masks.append(list(item.attention_mask) + [0] * pad)
            types.append(list(item.type_ids) + [0] * pad)
        inputs = {
            "input_ids": self._np.asarray(ids, dtype=self._np.int64),
            "attention_mask": self._np.asarray(masks, dtype=self._np.int64),
            "token_type_ids": self._np.asarray(types, dtype=self._np.int64),
        }
        outputs = self._session.run(None, {key: value for key, value in inputs.items() if key in self._inputs})
        hidden = self._np.asarray(outputs[0], dtype=self._np.float32)
        mask = inputs["attention_mask"].astype(self._np.float32)[..., None]
        pooled = (hidden * mask).sum(axis=1) / self._np.maximum(mask.sum(axis=1), 1e-9)
        norms = self._np.linalg.norm(pooled, axis=1, keepdims=True)
        return pooled / self._np.maximum(norms, 1e-9)


class ExperienceSimilarity:
    """Lazy BGE-compatible checker with a deterministic fallback."""

    def __init__(self, config: dict[str, Any] | None = None):
        config = config or {}
        self.enabled = bool(config.get("enabled", False))
        self.threshold = float(config.get("threshold", 0.88))
        self.retrieval_threshold = float(config.get("retrieval_threshold", 0.06))
        self.embedding_retrieval_threshold = float(config.get("embedding_retrieval_threshold", 0.18))
        self.backend = str(config.get("backend", "auto")).casefold()
        self.model_name = str(config.get("model") or os.getenv("SIM2_BGE_MODEL", ""))
        self._model: Any = None
        self._method = "token_jaccard"

    def _load_model(self) -> None:
        if self._model is not None or not self.enabled or not self.model_name:
            return
        try:
            path = Path(self.model_name).expanduser()
            if self.backend in {"onnx", "bge_onnx"} or path.is_file() or (path.is_dir() and (path / "model_optimized.onnx").is_file()):
                self._model = _BGEOnnxModel(self.model_name)
                self._method = "embedding_cosine_onnx"
            else:
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
        if self._model and self._method.startswith("embedding_cosine"):
            try:
                vectors = self._model.encode([left, right], normalize_embeddings=True)
            except TypeError:
                # The local ONNX adapter always normalizes; Sentence
                # Transformers exposes the keyword explicitly.
                vectors = self._model.encode([left, right])
            return max(0.0, min(1.0, float(sum(a * b for a, b in zip(vectors[0], vectors[1])))))
        a, b = _tokens(left), _tokens(right)
        if not a or not b:
            return 0.0
        # Cosine over binary lexical features is less sensitive to a long
        # task query than Jaccard: a short memory can still match strongly
        # when it contains the task's decisive terms.
        return max(0.0, min(1.0, len(a & b) / max(1.0, (len(a) * len(b)) ** 0.5)))

    def relevance(self, query: str, memory: dict[str, Any]) -> float:
        """Return the task-conditioned score used by retrieval gates."""

        if not query.strip():
            return 1.0
        text = self.memory_text(memory)
        semantic = self.score(query, text)
        query_tokens, memory_tokens = _tokens(query), _tokens(text)
        lexical = len(query_tokens & memory_tokens) / max(1.0, (len(query_tokens) * len(memory_tokens)) ** 0.5) if query_tokens and memory_tokens else 0.0
        return max(semantic, lexical)

    def _semantic_scores(self, query: str, texts: list[str]) -> list[float] | None:
        """Encode one query and a candidate batch in one local model call."""

        if not query.strip() or not texts:
            return None
        self._load_model()
        if not self._model or not self._method.startswith("embedding_cosine"):
            return None
        try:
            try:
                vectors = self._model.encode([query, *texts], normalize_embeddings=True)
            except TypeError:
                vectors = self._model.encode([query, *texts])
            query_vector = vectors[0]
            return [max(0.0, min(1.0, float(sum(a * b for a, b in zip(query_vector, vector))))) for vector in vectors[1:]]
        except Exception:
            return None

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

    @staticmethod
    def memory_text(memory: dict[str, Any]) -> str:
        tags = memory.get("tags") or {}
        values = [memory.get("summary"), memory.get("lesson"), memory.get("fact"), memory.get("topic"), memory.get("message"), memory.get("location"), memory.get("behavior"), memory.get("goal"), memory.get("goal_tags"), tags]
        return " ".join(str(value) for value in values if value not in (None, ""))

    def rank(self, query: str, memories: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
        """Rank memories by task-conditioned relevance plus stable quality.

        Importance and prior use break ties, but they cannot outrank a strong
        semantic match.  There is deliberately no age decay for durable
        memory: a useful lesson remains retrievable until Platform compaction
        or an explicit revision removes it.
        """

        if not query.strip():
            return memories[: max(1, int(limit))]
        ranked: list[tuple[float, int, dict[str, Any]]] = []
        texts = [self.memory_text(memory) for memory in memories]
        semantic_scores = self._semantic_scores(query, texts)
        for index, memory in enumerate(memories):
            text = self.memory_text(memory)
            semantic = semantic_scores[index] if semantic_scores is not None else self.score(query, text)
            query_tokens, memory_tokens = _tokens(query), _tokens(text)
            lexical = len(query_tokens & memory_tokens) / max(1.0, (len(query_tokens) * len(memory_tokens)) ** 0.5) if query_tokens and memory_tokens else 0.0
            quality = 0.55 * semantic + 0.20 * lexical
            quality += 0.12 * float(memory.get("importance", 0.5) or 0)
            quality += 0.10 * float(memory.get("salience", 0.5) or 0)
            quality += 0.08 * min(1.0, float(memory.get("uses", 0) or 0) / 10.0)
            projected = dict(memory)
            projected["_retrieval_relevance"] = max(semantic, lexical)
            ranked.append((quality, -index, projected))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in ranked[: max(1, int(limit))]]
