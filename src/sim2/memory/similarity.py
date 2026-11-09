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
import threading
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

    def __init__(self, model_path: str, providers: list[str] | None = None):
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
        available = set(ort.get_available_providers())
        requested = [str(item) for item in (providers or []) if str(item) in available]
        if not requested:
            requested = ["CUDAExecutionProvider", "CPUExecutionProvider"] if "CUDAExecutionProvider" in available else ["CPUExecutionProvider"]
        self.providers = requested
        self._session = ort.InferenceSession(str(onnx_path), providers=requested)
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

    _MODEL_CACHE: dict[tuple[str, str], tuple[Any, str]] = {}
    _MODEL_CACHE_LOCK = threading.Lock()

    def __init__(self, config: dict[str, Any] | None = None):
        config = config or {}
        self.enabled = bool(config.get("enabled", False))
        self.threshold = float(config.get("threshold", 0.88))
        self.retrieval_threshold = float(config.get("retrieval_threshold", 0.06))
        self.embedding_retrieval_threshold = float(config.get("embedding_retrieval_threshold", 0.18))
        self.backend = str(config.get("backend", "auto")).casefold()
        self.model_name = self._resolve_model_name(config)
        self._model: Any = None
        self._method = "token_jaccard"
        self._embedding_cache: dict[str, Any] = {}
        self._embedding_cache_limit = max(128, int(config.get("embedding_cache_limit", 2048) or 2048))
        provider = os.getenv("SIM2_BGE_PROVIDER", "auto").strip().casefold()
        self._providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if provider in {"auto", "cuda", "gpu"} else ["CPUExecutionProvider"]

    @property
    def method(self) -> str:
        """Current retrieval backend, useful for health and audit output."""

        return self._method

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "backend": self._method,
            "model": self.model_name or None,
            "loaded": bool(self._model),
            "embedding_cache_size": len(self._embedding_cache),
            "embedding_cache_limit": self._embedding_cache_limit,
            "providers": list(getattr(self._model, "providers", []) or []),
        }

    def preload(self) -> dict[str, Any]:
        """Load the configured local encoder before the first decision."""

        self._load_model()
        return self.status()

    @staticmethod
    def _resolve_model_name(config: dict[str, Any]) -> str:
        """Resolve a local BGE model without downloading or scanning the disk.

        ``SIM2_BGE_MODEL`` is the explicit sim2 setting.  The cache variables
        also understand the fastembed layout used by BieTheirBed, which lets
        both projects share one already-downloaded ONNX model.  Resolution is
        intentionally limited to the configured cache roots.
        """

        explicit = str(config.get("model") or os.getenv("SIM2_BGE_MODEL", "")).strip()
        if explicit:
            return explicit
        roots = [os.getenv("SIM2_BGE_CACHE", ""), os.getenv("KNOA_BGE_CACHE", "")]
        for raw_root in roots:
            root = Path(raw_root).expanduser() if raw_root else None
            if root is None or not root.exists():
                continue
            direct = root / "model_optimized.onnx"
            if direct.is_file() and (root / "tokenizer.json").is_file():
                return str(root)
            candidates = sorted(root.glob("models--*/snapshots/*/model_optimized.onnx"))
            for candidate in candidates:
                if (candidate.parent / "tokenizer.json").is_file():
                    return str(candidate.parent)
        home_model = Path.home() / ".sim2" / "bge" / "bge-small-zh-v1.5"
        if (home_model / "model_optimized.onnx").is_file() and (home_model / "tokenizer.json").is_file():
            return str(home_model)
        return ""

    def _load_model(self) -> None:
        if self._model is not None or not self.enabled or not self.model_name:
            return
        cache_key = (self.backend + ":" + ",".join(self._providers), self.model_name)
        with self._MODEL_CACHE_LOCK:
            cached = self._MODEL_CACHE.get(cache_key)
        if cached is not None:
            self._model, self._method = cached
            return
        try:
            path = Path(self.model_name).expanduser()
            if self.backend in {"onnx", "bge_onnx"} or path.is_file() or (path.is_dir() and (path / "model_optimized.onnx").is_file()):
                model = _BGEOnnxModel(self.model_name, self._providers)
                method = "embedding_cosine_onnx"
            else:
                from sentence_transformers import SentenceTransformer  # type: ignore

                model = SentenceTransformer(self.model_name)
                method = "embedding_cosine"
            self._model = model
            self._method = method
            with self._MODEL_CACHE_LOCK:
                self._MODEL_CACHE[cache_key] = (model, method)
        except Exception:
            # The core runtime stays available when the optional package or
            # model is absent.  The caller can expose this method in diagnostics.
            self._model = False
            self._method = "token_jaccard_fallback"

    def _encode_embeddings(self, texts: list[str]) -> Any:
        """Encode text while reusing durable candidate vectors in this process."""

        self._load_model()
        if not self._model or not self._method.startswith("embedding_cosine"):
            return None
        missing = [text for text in texts if text not in self._embedding_cache]
        if missing:
            try:
                try:
                    vectors = self._model.encode(missing, normalize_embeddings=True)
                except TypeError:
                    vectors = self._model.encode(missing)
            except Exception:
                return None
            for text, vector in zip(missing, vectors):
                self._embedding_cache[text] = vector
            if len(self._embedding_cache) > self._embedding_cache_limit:
                for key in list(self._embedding_cache)[: len(self._embedding_cache) - self._embedding_cache_limit]:
                    self._embedding_cache.pop(key, None)
        return [self._embedding_cache[text] for text in texts]

    def score(self, left: str, right: str) -> float:
        if not left.strip() or not right.strip():
            return 0.0
        if left.strip().casefold() == right.strip().casefold():
            return 1.0
        self._load_model()
        if self._model and self._method.startswith("embedding_cosine"):
            try:
                vectors = self._encode_embeddings([left, right])
                if vectors is not None:
                    return max(0.0, min(1.0, float(sum(a * b for a, b in zip(vectors[0], vectors[1])))))
            except Exception:
                pass
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
            vectors = self._encode_embeddings([query, *texts])
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
            # BGE treats templated summaries with different measured values as
            # near duplicates. Preserve the distinction when the prose carries
            # explicit numeric evidence; Reflection can later consolidate it
            # with a better lesson instead of silently discarding the record.
            left_numbers = set(re.findall(r"\d+(?:\.\d+)?", summary))
            right_numbers = set(re.findall(r"\d+(?:\.\d+)?", candidate))
            if left_numbers and right_numbers and left_numbers != right_numbers:
                continue
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
