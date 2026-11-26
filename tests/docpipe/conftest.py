import pytest


@pytest.fixture
def fake_embedder():
    from agentorium.docpipe import embed

    old = embed._embedder, embed._embedder_set
    e = embed.HashEmbedder()
    embed.set_embedder(e)
    yield e
    embed._embedder, embed._embedder_set = old
