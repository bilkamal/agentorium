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


def _node_diff(old: str, new: str):
    import json
    import shutil
    import subprocess
    from pathlib import Path

    if not shutil.which("node"):
        pytest.skip("node not installed")
    src = Path(__file__).resolve().parents[2] / "web" / "worddiff.js"
    code = f"const W = require({json.dumps(str(src))}); console.log(JSON.stringify(W.diff({json.dumps(old)}, {json.dumps(new)})));"
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)


def test_word_diff_marks_only_changed_words():
    ops = _node_diff("Sebanyak 240 tiket memuat Incident ID dan terdapat 255 ID unik.",
                     "Sebanyak 240 tiket memuat Incident ID, dengan total 255 Incident ID unik.")
    assert "".join(t for k, t in ops if k != "ins") == "Sebanyak 240 tiket memuat Incident ID dan terdapat 255 ID unik."
    assert "".join(t for k, t in ops if k != "del") == "Sebanyak 240 tiket memuat Incident ID, dengan total 255 Incident ID unik."
    changed = "".join(t for k, t in ops if k != "eq")
    assert "Sebanyak 240 tiket memuat" not in changed and len(changed) < 45


def test_word_diff_long_paragraph_stays_small():
    base = " ".join(f"kata{i}" for i in range(3000))
    ops = _node_diff(base, base.replace("kata1500", "KATA"))
    assert [t for k, t in ops if k != "eq"] == ["kata1500", "KATA"]


def test_tables_use_inline_diff(client):
    html = client.get("/").text
    assert "/static/worddiff.js" in html and html.index("/static/worddiff.js") < html.index("/static/docs.js")
    js = client.get("/static/docs.js").text
    assert js.count("WordDiff.render(") >= 2


def _node_pick(jobs, types):
    import json
    import shutil
    import subprocess
    from pathlib import Path

    if not shutil.which("node"):
        pytest.skip("node not installed")
    src = Path(__file__).resolve().parents[2] / "web" / "jobpick.js"
    code = (f"const P = require({json.dumps(str(src))}); "
            f"console.log(JSON.stringify(P.pick({json.dumps(jobs)}, {json.dumps(types)})));")
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)


def test_cancelled_review_does_not_hide_the_finished_one():
    jobs = [{"id": "new", "type": "review", "status": "cancelled"}, {"id": "old", "type": "review", "status": "done"}]
    assert _node_pick(jobs, ["review"])["id"] == "old"
    running = [{"id": "r", "type": "review", "status": "running"}] + jobs
    assert _node_pick(running, ["review"])["id"] == "r"
    assert _node_pick([{"id": "c", "type": "review", "status": "cancelled"}], ["review"])["id"] == "c"
    assert _node_pick([{"id": "x", "type": "revision", "status": "done"}], ["review"]) is None


def test_docs_js_uses_job_pick(client):
    html = client.get("/").text
    assert html.index("/static/jobpick.js") < html.index("/static/docs.js")
    assert client.get("/static/docs.js").text.count("JobPick.pick(") >= 2


def test_rereview_asks_first(client):
    js = client.get("/static/docs.js").text
    assert "confirm_rereview" in js and js.count("confirm_rereview") >= 3
