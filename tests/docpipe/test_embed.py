import numpy as np

from agentorium.config import settings
from agentorium.docpipe import embed
from agentorium.docpipe.embed import FastEmbedder, HashEmbedder, get_embedder


def test_hash_embedder_similarity():
    e = HashEmbedder()
    a, b, c = e.embed_passages(["penjualan naik bulan september", "penjualan bulan september naik tajam", "kucing tidur di sofa"])
    assert float(a @ b) > float(a @ c)


def test_vectors_normalized():
    v = HashEmbedder().embed_passages(["satu dua tiga", "empat"])
    assert v.dtype == np.float32
    assert np.allclose(np.linalg.norm(v, axis=1), 1.0)
    q = HashEmbedder().embed_query("satu")
    assert q.shape == (HashEmbedder().dim,) and abs(float(np.linalg.norm(q)) - 1) < 1e-5


def test_get_embedder_none_when_disabled(monkeypatch):
    old = embed._embedder, embed._embedder_set, settings.embed_model
    embed._embedder, embed._embedder_set = None, False
    object.__setattr__(settings, "embed_model", "none")
    try:
        assert get_embedder() is None
    finally:
        embed._embedder, embed._embedder_set = old[0], old[1]
        object.__setattr__(settings, "embed_model", old[2])


def test_e5_prefixes(monkeypatch, tmp_settings):
    seen = []

    class FakeTE:
        def __init__(self, name, cache_dir=None):
            self.name = name

        def embed(self, texts, batch_size=32):
            for t in texts:
                seen.append(t)
                yield np.ones(4, dtype=np.float32)

    import fastembed

    monkeypatch.setattr(fastembed, "TextEmbedding", FakeTE)
    e = FastEmbedder("intfloat/multilingual-e5-large")
    v = e.embed_passages(["x"])
    e.embed_query("y")
    assert seen == ["passage: x", "query: y"]
    assert abs(float(np.linalg.norm(v[0])) - 1) < 1e-5
    assert e.dim == 4


def test_no_prefix_for_non_e5(monkeypatch, tmp_settings):
    seen = []

    class FakeTE:
        def __init__(self, name, cache_dir=None):
            pass

        def embed(self, texts, batch_size=32):
            for t in texts:
                seen.append(t)
                yield np.ones(3, dtype=np.float32)

    import fastembed

    monkeypatch.setattr(fastembed, "TextEmbedding", FakeTE)
    FastEmbedder("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2").embed_passages(["x"])
    assert seen == ["x"]
