import pytest
from fastapi.testclient import TestClient

from agentorium.docpipe.index import index_version
from agentorium.docpipe.models import ParsedDoc, Section, Block
from agentorium.docpipe.retrieval import search
from agentorium.docpipe.store import get_store


@pytest.fixture
def store(tmp_settings):
    return get_store()


def make_version(store, texts_by_section):
    d = store.create_document("Uji")
    v = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=None)
    sections, blocks, n = [], [], 0
    for i, (h, texts) in enumerate(texts_by_section.items()):
        sid = f"s{i:04d}"
        sections.append(Section(id=sid, ordinal=i, level=1, heading=h, path=h, parent_id=None))
        for t in texts:
            blocks.append(Block(id=f"b{n:05d}", uid=f"u{n}", ordinal=n, section_id=sid, type="paragraph", text=t))
            n += 1
    store.save_structure(v.id, ParsedDoc(sections, blocks))
    store.update_version(v.id, status="ready")
    return v


DOC = {
    "Pendahuluan": ["Laporan ini membahas kinerja toko."],
    "Penjualan": ["Penjualan bulan September naik 12 persen dibanding Agustus."],
    "Karyawan": ["Jumlah karyawan tetap sebanyak 14 orang."],
}


def test_hybrid_ranks_relevant_chunk_first(store, fake_embedder):
    v = make_version(store, DOC)
    index_version(store, v.id)
    hits = search(store, v.id, "penjualan september naik")
    assert hits[0].chunk.section_id == "s0001"
    assert hits[0].fts_rank == 1 and hits[0].vec_rank == 1
    assert hits[0].score > hits[-1].score


def test_exclude_section(store, fake_embedder):
    v = make_version(store, DOC)
    index_version(store, v.id)
    hits = search(store, v.id, "penjualan september", exclude_section_id="s0001")
    assert all(h.chunk.section_id != "s0001" for h in hits)


def test_fts_only_when_no_vectors(store):
    v = make_version(store, DOC)
    assert index_version(store, v.id) == "fts_only"
    hits = search(store, v.id, "karyawan")
    assert hits[0].chunk.section_id == "s0002" and hits[0].vec_rank is None


def test_query_with_fts_syntax_chars_does_not_crash(store, fake_embedder):
    v = make_version(store, DOC)
    index_version(store, v.id)
    assert search(store, v.id, '"naik" AND (12%') != []
    assert search(store, v.id, "***") == []


def test_k_limits_results(store, fake_embedder):
    v = make_version(store, DOC)
    index_version(store, v.id)
    assert len(search(store, v.id, "toko penjualan karyawan", k=2)) == 2


def test_search_endpoint(store, fake_embedder):
    from agentorium.server import app

    v = make_version(store, DOC)
    index_version(store, v.id)
    c = TestClient(app)
    r = c.get(f"/api/docs/versions/{v.id}/search", params={"q": "karyawan", "k": 3})
    assert r.status_code == 200
    first = r.json()[0]
    assert first["section_id"] == "s0002" and "score" in first and "fts_rank" in first and first["block_ids"]
    assert c.get("/api/docs/versions/nope/search", params={"q": "x"}).status_code == 404
