"""Build the search index of a version: section-aware chunks, FTS5 rows and embedding vectors."""
from __future__ import annotations

import asyncio
import logging

from .embed import get_embedder
from .models import Block, Chunk, Section
from .store import Store, get_store

log = logging.getLogger(__name__)
CHUNK_CHARS = 1600
EMBED_BATCH = 32
_background: dict[str, asyncio.Task] = {}


def build_chunks(version_id: str, sections: list[Section], blocks: list[Block], max_chars: int = CHUNK_CHARS) -> list[Chunk]:
    by_section: dict[str, list[Block]] = {}
    for b in blocks:
        by_section.setdefault(b.section_id, []).append(b)
    chunks: list[Chunk] = []

    def emit(section: Section, group: list[Block]) -> None:
        n = len(chunks)
        body = "\n".join(b.text for b in group)
        chunks.append(Chunk(id=f"{version_id}-c{n:05d}", version_id=version_id, section_id=section.id,
                            block_ids=[b.id for b in group], heading_path=section.path,
                            content=f"{section.path}\n{body}", ordinal=n))

    for section in sections:
        group: list[Block] = []
        size = 0
        for b in by_section.get(section.id, []):
            if group and size + len(b.text) + 1 > max_chars:
                emit(section, group)
                group, size = [], 0
            group.append(b)
            size += len(b.text) + 1
        if group:
            emit(section, group)
    return chunks


def index_version(store: Store, version_id: str) -> str:
    """Rebuild chunks + FTS + vectors for a version. Returns the final index_status."""
    store.update_version(version_id, index_status="indexing", index_error=None)
    try:
        chunks = build_chunks(version_id, store.get_sections(version_id), store.get_blocks(version_id))
        store.save_chunks(version_id, chunks)
    except Exception as exc:
        log.exception("indexing failed for %s", version_id)
        store.update_version(version_id, index_status="failed", index_error=str(exc)[:500])
        return "failed"

    embedder = get_embedder()
    if embedder is None:
        store.update_version(version_id, index_status="fts_only")
        return "fts_only"
    try:
        import numpy as np

        parts = [embedder.embed_passages([c.content for c in chunks[i:i + EMBED_BATCH]])
                 for i in range(0, len(chunks), EMBED_BATCH)]
        if chunks:
            store.save_vectors(version_id, [c.id for c in chunks], np.vstack(parts))
    except Exception as exc:  # model missing, download failed, out of memory ...
        log.warning("embedding failed for %s: %s", version_id, exc)
        store.update_version(version_id, index_status="fts_only", index_error=f"embedding: {exc}"[:500])
        return "fts_only"
    store.update_version(version_id, index_status="ready")
    return "ready"


def schedule_index(version_id: str) -> asyncio.Task:
    """Index in a worker thread without blocking the request (one task per version)."""
    running = _background.get(version_id)
    if running is not None and not running.done():
        return running
    task = asyncio.get_running_loop().create_task(asyncio.to_thread(index_version, get_store(), version_id))
    _background[version_id] = task
    task.add_done_callback(lambda t, v=version_id: _background.pop(v, None) if _background.get(v) is t else None)
    return task


async def ensure_index(version_id: str) -> str:
    """Wait for a running index of this version, or build one when the version has none yet."""
    store = get_store()
    running = _background.get(version_id)
    if running is not None and not running.done() and running.get_loop() is asyncio.get_running_loop():
        await asyncio.shield(running)
    status = store.get_version(version_id).index_status
    if status in ("ready", "fts_only"):
        return status
    import agentorium.docpipe.index as me  # late lookup keeps index_version patchable

    return await asyncio.to_thread(me.index_version, store, version_id)
