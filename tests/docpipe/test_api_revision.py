import io
import time

import docx
import pytest
from fastapi.testclient import TestClient

import agentorium.server as server
from agentorium.config import settings
from agentorium.docpipe import review
from agentorium.docpipe.service import ingest
from agentorium.docpipe.store import get_store

from .fixtures import make_pdf
from .test_materials import commented_docx
from .test_revise import NEW, OLD, finding
from .test_review import ScriptedLLM, team  # noqa: F401


@pytest.fixture
def env(tmp_settings, tmp_path, fake_embedder, monkeypatch, team):  # noqa: F811
    object.__setattr__(settings, "llm_concurrency", 1)
    calls, replies = [], {"worker": [], "supervisor": []}
    monkeypatch.setattr(review, "make_llm", ScriptedLLM(replies, calls).for_agent)
    store = get_store()
    _, v = ingest(store, filename="a.docx", data=commented_docx(tmp_path))
    with TestClient(server.app) as c:
        yield c, store, v, replies, tmp_path


def wait_done(c, job_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/docs/jobs/{job_id}").json()["job"]
        if j["status"] not in ("queued", "running", "paused", "finalizing"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def done_review(store, v):
    job = store.create_job(v.document_id, v.id, "review", {})
    blocks = {b.text: b for b in store.get_blocks(v.id)}
    f = finding(store, job, blocks[OLD], "language", "menganalisa", "tidak baku", "menganalisis")
    store.update_job(job.id, status="done")
    return job, blocks, f


def test_revision_endpoint_runs_and_reports_changes(env):
    c, store, v, replies, _ = env
    rjob, blocks, f = done_review(store, v)
    replies["worker"].append({"edits": [{"block_id": blocks[OLD].id, "new_text": NEW, "source_ids": [f.id],
                                         "reason": "baku"}], "skipped": []})
    replies["supervisor"].append({"verdicts": [{"index": 0, "verdict": "approve", "feedback": ""}]})
    r = c.post(f"/api/docs/jobs/{rjob.id}/revisions", json={})
    assert r.status_code == 202, r.text
    j = wait_done(c, r.json()["job"]["id"])
    assert j["status"] == "done" and j["result_version_id"]
    ch = c.get(f"/api/docs/jobs/{j['id']}/changes").json()
    assert [(x["status"], x["new_text"]) for x in ch] == [("applied", NEW)] and ch[0]["heading_path"].startswith("Bab 1")
    vch = c.get(f"/api/docs/versions/{j['result_version_id']}/changes").json()
    assert [x["status"] for x in vch] == ["applied"]
    cmp = c.get(f"/api/docs/versions/{v.id}/compare/{j['result_version_id']}").json()
    assert [b["status"] for b in cmp["blocks"]] == ["changed"]
    red = c.get(f"/api/docs/versions/{v.id}/compare/{j['result_version_id']}/redline")
    assert red.status_code == 200 and red.content[:2] == b"PK"
    tr = c.get(f"/api/docs/versions/{j['result_version_id']}/download?variant=tracked")
    assert tr.status_code == 200


def test_revision_of_unfinished_review_409(env):
    c, store, v, _, _ = env
    job = store.create_job(v.document_id, v.id, "review", {})
    assert c.post(f"/api/docs/jobs/{job.id}/revisions", json={}).status_code == 409
    assert c.post("/api/docs/jobs/nope/revisions", json={}).status_code == 404


def test_revision_pdf_409(env):
    c, store, _, _, tmp_path = env
    _, pv = ingest(store, filename="a.pdf", data=make_pdf(tmp_path).read_bytes())
    job = store.create_job(pv.document_id, pv.id, "review", {})
    store.update_job(job.id, status="done")
    r = c.post(f"/api/docs/jobs/{job.id}/revisions", json={})
    assert r.status_code == 409 and "DOCX" in r.json()["detail"]
    r = c.post(f"/api/docs/versions/{pv.id}/instructed-revisions", data={"notes": "x"})
    assert r.status_code == 409


def test_one_mutating_job_per_version(env):
    c, store, v, _, _ = env
    busy = store.create_job(v.document_id, v.id, "revision", {})
    store.update_job(busy.id, status="running")
    rjob, _, _ = done_review(store, v)
    assert c.post(f"/api/docs/jobs/{rjob.id}/revisions", json={}).status_code == 409
    r = c.post(f"/api/docs/versions/{v.id}/instructed-revisions", data={"notes": "ganti judul"})
    assert r.status_code == 409 and "berjalan" in r.json()["detail"]


def test_instructed_endpoint_followup_and_instructions(env):
    c, store, v, replies, _ = env
    replies["worker"].append({"edits": [], "skipped": [], "responses": []})
    d = docx.Document()
    d.add_paragraph("Tolong rapikan.")
    b = io.BytesIO()
    d.save(b)
    replies["supervisor"].append({"instructions": []})  # the uploaded file yields nothing
    r = c.post(f"/api/docs/versions/{v.id}/instructed-revisions", data={"notes": "", "use_comments": "true"},
               files=[("files", ("bahan.docx", b.getvalue()))])
    assert r.status_code == 202, r.text
    j = wait_done(c, r.json()["job"]["id"])
    assert j["status"] == "done"
    ins = c.get(f"/api/docs/jobs/{j['id']}/instructions").json()
    assert [i["text"] for i in ins] == ["Sebutkan nama aplikasinya"] and ins[0]["heading_path"].startswith("Bab 2")
    fu = c.get(f"/api/docs/jobs/{j['id']}/followup.docx")
    assert fu.status_code == 200 and "tindak lanjut" in fu.headers["content-disposition"].lower()
    assert docx.Document(io.BytesIO(fu.content)).tables[0].rows[1].cells[2].text == "Sebutkan nama aplikasinya"


def test_instructed_bad_inputs(env):
    c, _, v, _, _ = env
    assert c.post(f"/api/docs/versions/{v.id}/instructed-revisions", data={"notes": " "}).status_code == 400
    r = c.post(f"/api/docs/versions/{v.id}/instructed-revisions", data={"notes": "x"},
               files=[("files", ("a.exe", b"MZ"))])
    assert r.status_code == 400 and "Format" in r.json()["detail"]


def test_followup_only_for_instructed_jobs(env):
    c, store, v, _, _ = env
    job = store.create_job(v.document_id, v.id, "review", {})
    assert c.get(f"/api/docs/jobs/{job.id}/followup.docx").status_code == 404


def test_delete_document_refused_while_changes_are_applied(env):
    c, store, v, _, _ = env
    job = store.create_job(v.document_id, v.id, "revision", {})
    store.update_job(job.id, status="finalizing")
    r = c.delete(f"/api/docs/{v.document_id}")
    assert r.status_code == 409 and store.get_document(v.document_id) is not None
