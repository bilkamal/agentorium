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
