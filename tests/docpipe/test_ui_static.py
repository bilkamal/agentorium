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


def test_review_controls_present(client):
    html = client.get("/").text
    for marker in ('id="doc-review"', 'id="findings-table"', 'id="doc-search"', 'id="review-status"',
                   'id="review-cancel"', 'id="review-office"', 'name="review-cat"'):
        assert marker in html, marker


def test_docs_js_wires_review_and_search(client):
    js = client.get("/static/docs.js").text
    assert "/reviews" in js and "/findings" in js and "/search?q=" in js and "Office.openSession" in js
    assert "innerHTML = `" not in js  # text only — no template HTML injection


def test_app_js_exposes_open_session(client):
    assert "window.Office" in client.get("/static/app.js").text


def test_revision_controls_present(client):
    html = client.get("/").text
    for marker in ('id="doc-revise"', 'id="materials-form"', 'id="rev-notes"', 'id="rev-files"', 'id="rev-comments"',
                   'id="rev-status"', 'id="changes-table"', 'id="followup-table"', 'id="followup-download"',
                   'id="compare-form"', 'id="cmp-a"', 'id="cmp-b"', 'id="cmp-redline"'):
        assert marker in html, marker


def test_docs_js_wires_revision_and_compare(client):
    js = client.get("/static/docs.js").text
    for marker in ("/revisions", "/instructed-revisions", "/changes", "/instructions", "/followup.docx",
                   "/compare/", "/redline", "variant=tracked"):
        assert marker in js, marker
    assert "innerHTML = `" not in js


def test_update_controls_present_and_wired(client):
    html = client.get("/").text
    for marker in ('id="update-form"', 'id="upd-period"', 'id="upd-files"', 'id="upd-notes"', 'id="upd-start"'):
        assert marker in html, marker
    js = client.get("/static/docs.js").text
    assert "/updates" in js and "kind_update" in js and "'update'" in js
