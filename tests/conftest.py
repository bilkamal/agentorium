import pytest

from agentorium.config import settings


@pytest.fixture
def tmp_settings(tmp_path):
    """Point settings.data_dir at a temporary folder for the duration of a test."""
    old = dict(vars(settings))
    object.__setattr__(settings, "data_dir", tmp_path / "data")
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    yield settings
    for k, v in old.items():  # tests may also tweak other settings
        object.__setattr__(settings, k, v)


@pytest.fixture(autouse=True)
def _no_real_embedding_model():
    """Never load/download the real embedding model in tests; fake_embedder opts in to a test double."""
    from agentorium.docpipe import embed

    old = embed._embedder, embed._embedder_set
    embed.set_embedder(None)
    yield
    embed._embedder, embed._embedder_set = old


@pytest.fixture(autouse=True)
def _never_call_real_claude(request):
    """Unit tests must never reach the real Claude Code CLI (it spends the user's quota)."""
    if request.node.get_closest_marker("live"):
        yield
        return
    old = settings.claude_bin
    object.__setattr__(settings, "claude_bin", "/nonexistent/claude-disabled-in-unit-tests")
    yield
    object.__setattr__(settings, "claude_bin", old)
