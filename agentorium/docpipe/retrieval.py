"""Hybrid retrieval over one version: FTS5 (bm25) + embedding cosine, fused with Reciprocal Rank Fusion."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import numpy as np

from .embed import get_embedder
from .models import Chunk
from .store import Store

log = logging.getLogger(__name__)
RRF_K = 60


@dataclass
class Hit:
    chunk: Chunk
    score: float
    fts_rank: int | None
    vec_rank: int | None


def search(store: Store, version_id: str, query: str, *, k: int = 8, exclude_section_id: str | None = None,
           pool: int = 30) -> list[Hit]:
    if not re.search(r"\w", query):
        return []
    chunks = {c.id: c for c in store.get_chunks(version_id)}
    allowed = {cid for cid, c in chunks.items() if c.section_id != exclude_section_id}
    fts = [cid for cid in store.fts_search(version_id, query, pool + len(chunks) - len(allowed)) if cid in allowed][:pool]

    vec: list[str] = []
    version = store.get_version(version_id)
    embedder = get_embedder()
    if version and version.index_status == "ready" and embedder is not None:
        try:
            ids, mat = store.load_vectors(version_id)
            if ids:
                sims = mat @ embedder.embed_query(query)
                order = [ids[i] for i in np.argsort(-sims)]
                vec = [cid for cid in order if cid in allowed][:pool]
        except Exception as exc:  # degrade to keyword search
            log.warning("vector search failed for %s: %s", version_id, exc)
            vec = []

    fts_rank = {cid: i + 1 for i, cid in enumerate(fts)}
    vec_rank = {cid: i + 1 for i, cid in enumerate(vec)}
    scores: dict[str, float] = {}
    for ranks in (fts_rank, vec_rank):
        for cid, r in ranks.items():
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + r)
    best = sorted(scores, key=lambda cid: (-scores[cid], chunks[cid].ordinal))[:k]
    return [Hit(chunks[cid], scores[cid], fts_rank.get(cid), vec_rank.get(cid)) for cid in best]
