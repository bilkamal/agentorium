import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_settings):
    from agentorium.server import app

    return TestClient(app)


def test_index_has_docs_tab_and_script(client):
    html = client.get("/").text
    assert 'id="tab-docs"' in html and 'id="tab-office"' in html
    assert 'id="docs-view"' in html and "/static/docs.js" in html


def test_docs_js_served(client):
    r = client.get("/static/docs.js")
    assert r.status_code == 200 and "DocsView" in r.text
