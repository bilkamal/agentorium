import pytest

from agentorium.config import settings


@pytest.fixture
def tmp_settings(tmp_path):
    """Point settings.data_dir at a temporary folder for the duration of a test."""
    old = settings.data_dir
    object.__setattr__(settings, "data_dir", tmp_path / "data")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    yield settings
    object.__setattr__(settings, "data_dir", old)


@pytest.fixture(autouse=True)
def _no_real_embedding_model():
    """Never load/download the real embedding model in tests; fake_embedder opts in to a test double."""
    from agentorium.docpipe import embed

    old = embed._embedder, embed._embedder_set
    embed.set_embedder(None)
    yield
    embed._embedder, embed._embedder_set = old
