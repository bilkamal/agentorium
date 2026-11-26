import sqlite3

import numpy as np
import pytest

from agentorium.docpipe import embed
from agentorium.docpipe.index import build_chunks, index_version
from agentorium.docpipe.models import Block, Section
from agentorium.docpipe.service import ingest
from agentorium.docpipe.store import SCHEMA, Store, get_store

from .fixtures import make_basic_docx


@pytest.fixture
def store(tmp_settings):
    return get_store()


def sec(i, heading):
    return Section(id=f"s{i:04d}", ordinal=i, level=1, heading=heading, path=f"Dok > {heading}", parent_id=None)


def blk(n, sid, text):
    return Block(id=f"b{n:05d}", uid=f"u{n}", ordinal=n, section_id=sid, type="paragraph", text=text)


def test_chunks_respect_section_boundary():
    sections = [sec(0, "A"), sec(1, "B")]
    blocks = [blk(0, "s0000", "satu"), blk(1, "s0000", "dua"), blk(2, "s0001", "tiga")]
    chunks = build_chunks("v", sections, blocks)
    assert [(c.section_id, c.block_ids) for c in chunks] == [("s0000", ["b00000", "b00001"]), ("s0001", ["b00002"])]


def test_chunk_size_limit_and_block_ids_cover_all_blocks():
    sections = [sec(0, "A")]
    blocks = [blk(i, "s0000", "x" * 500) for i in range(10)]
    chunks = build_chunks("v", sections, blocks, max_chars=1600)
    assert all(len(c.content) <= 1600 + len("Dok > A\n") + 10 for c in chunks)
    assert [b for c in chunks for b in c.block_ids] == [b.id for b in blocks]
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_oversized_single_block_kept_whole():
    chunks = build_chunks("v", [sec(0, "A")], [blk(0, "s0000", "y" * 5000)])
    assert len(chunks) == 1 and "y" * 5000 in chunks[0].content


def test_chunk_content_prefixed_with_heading_path():
    c = build_chunks("v", [sec(0, "A")], [blk(0, "s0000", "isi")])[0]
    assert c.heading_path == "Dok > A"
    assert c.content.startswith("Dok > A\n") and c.content.endswith("isi")


def test_index_version_ready_with_vectors(store, tmp_path, fake_embedder):
    _, v = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())
    assert index_version(store, v.id) == "ready"
    assert store.get_version(v.id).index_status == "ready"
    ids, mat = store.load_vectors(v.id)
    assert len(ids) == len(store.get_chunks(v.id)) == mat.shape[0] and mat.shape[1] == fake_embedder.dim


def test_fts_search_finds_word_and_ignores_case(store, tmp_path, fake_embedder):
    _, v = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())
    index_version(store, v.id)
    ids = store.fts_search(v.id, "KASIR", 5)
    chunks = {c.id: c for c in store.get_chunks(v.id)}
    assert ids and "kasir" in chunks[ids[0]].content


def test_index_falls_back_to_fts_when_embedder_fails(store, tmp_path):
    class Broken:
        name, dim = "broken", 3

        def embed_passages(self, texts):
            raise RuntimeError("model download failed")

        def embed_query(self, text):
            raise RuntimeError("x")

    old = embed._embedder, embed._embedder_set
    embed.set_embedder(Broken())
    try:
        _, v = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())
        assert index_version(store, v.id) == "fts_only"
        assert "model download failed" in store.get_version(v.id).index_error
        assert store.fts_search(v.id, "kasir", 5)
    finally:
        embed._embedder, embed._embedder_set = old


def test_index_without_embedder_is_fts_only(store, tmp_path):
    old = embed._embedder, embed._embedder_set
    embed.set_embedder(None)
    try:
        _, v = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())
        assert index_version(store, v.id) == "fts_only"
    finally:
        embed._embedder, embed._embedder_set = old


def test_reindex_replaces_chunks(store, tmp_path, fake_embedder):
    _, v = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())
    index_version(store, v.id)
    n = len(store.get_chunks(v.id))
    index_version(store, v.id)
    assert len(store.get_chunks(v.id)) == n
    assert len(store.fts_search(v.id, "kasir", 50)) == 1


def test_old_database_gets_new_columns(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA.split("-- phase2")[0])
    db.execute("INSERT INTO documents VALUES('d','t','',0)")
    db.execute("INSERT INTO versions(id,document_id,version_no,source,filename,file_path,status,created_at) "
               "VALUES('v','d',1,'upload','a.docx','docs/d/v1.docx','ready',0)")
    db.commit()
    db.close()
    s = Store(path)
    assert s.get_version("v").index_status == "none"


def test_upload_triggers_background_index(tmp_settings, tmp_path, fake_embedder):
    import time

    from fastapi.testclient import TestClient

    from agentorium.server import app

    with TestClient(app) as c:
        p = make_basic_docx(tmp_path)
        doc = c.post("/api/docs", files={"file": (p.name, p.read_bytes(), "application/octet-stream")}).json()["document"]
        status = None
        for _ in range(50):
            status = c.get(f"/api/docs/{doc['id']}").json()["versions"][0]["index_status"]
            if status in ("ready", "fts_only", "failed"):
                break
            time.sleep(0.05)
        assert status == "ready"
        v1 = c.get(f"/api/docs/{doc['id']}").json()["versions"][0]["id"]
        v2 = c.post(f"/api/docs/versions/{v1}/restore").json()["version"]["id"]
        for _ in range(50):
            if get_store().get_version(v2).index_status == "ready":
                break
            time.sleep(0.05)
        assert get_store().get_version(v2).index_status == "ready"
