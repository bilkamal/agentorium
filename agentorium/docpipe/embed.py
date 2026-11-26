"""Sentence embeddings for document search (local, CPU, no torch)."""
from __future__ import annotations

import hashlib
import re
import threading
from typing import Protocol

import numpy as np

from ..config import settings


class Embedder(Protocol):
    name: str
    dim: int

    def embed_passages(self, texts: list[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


def _normalize(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    norms = np.linalg.norm(m, axis=-1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


class FastEmbedder:
    """fastembed (ONNX) model, loaded on first use; e5 models get their passage/query prefixes."""

    def __init__(self, model: str):
        self.name = model
        self._model = None
        self._dim: int | None = None
        self._lock = threading.Lock()
        self._e5 = "e5" in model.lower()

    def _load(self):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                cache = settings.data_dir / "models"
                cache.mkdir(parents=True, exist_ok=True)
                self._model = TextEmbedding(self.name, cache_dir=str(cache))
        return self._model

    def _run(self, texts: list[str]) -> np.ndarray:
        vecs = list(self._load().embed(texts, batch_size=32))
        m = _normalize(np.vstack(vecs)) if vecs else np.zeros((0, self._dim or 0), dtype=np.float32)
        if vecs:
            self._dim = m.shape[1]
        return m

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._run(["dim"])
        return self._dim or 0

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return self._run([f"passage: {t}" if self._e5 else t for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._run([f"query: {text}" if self._e5 else text])[0]


class HashEmbedder:
    """Deterministic bag-of-words hashing embedder for tests (no model download)."""

    name = "hash"
    dim = 256

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for tok in re.findall(r"\w+", text.lower()):
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim] += 1.0
        return v

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return _normalize(np.vstack([self._vec(t) for t in texts])) if texts else np.zeros((0, self.dim), np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return _normalize(self._vec(text))


_embedder: Embedder | None = None
_embedder_set = False
_lock = threading.Lock()


def get_embedder() -> Embedder | None:
    global _embedder, _embedder_set
    with _lock:
        if not _embedder_set:
            name = settings.embed_model
            _embedder = None if name.lower() == "none" else FastEmbedder(name)
            _embedder_set = True
        return _embedder


def set_embedder(e: Embedder | None) -> None:
    global _embedder, _embedder_set
    with _lock:
        _embedder, _embedder_set = e, True
