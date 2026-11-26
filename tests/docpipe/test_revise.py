import asyncio
import zipfile

import pytest

from agentorium.config import settings
from agentorium.docpipe import review
from agentorium.docpipe.jobs import runner
from agentorium.docpipe.revise import start_revision
from agentorium.docpipe.service import ingest, version_file
from agentorium.docpipe.store import get_store

from .fixtures import make_basic_docx, make_pdf
from .test_review import ScriptedLLM, team  # noqa: F401

OLD = "Kami telah menganalisa data penjualan bulan September."
NEW = "Kami telah menganalisis data penjualan bulan September."


@pytest.fixture
def llm(monkeypatch):
    calls, replies = [], {"worker": [], "supervisor": []}
    monkeypatch.setattr(review, "make_llm", ScriptedLLM(replies, calls).for_agent)
    return replies, calls


def finding(store, job, block, category, quote, desc="masalah", sugg=""):
    task = store.get_tasks(job.id)[0] if store.get_tasks(job.id) else store.add_tasks(job.id, [(block.section_id, [block.id], "section_review")])[0]
    store.add_findings([dict(job_id=job.id, task_id=task.id, version_id=job.version_id, block_id=block.id,
                             other_block_id=None, category=category, severity="medium", quote=quote,
                             description=desc, suggestion=sugg, confidence=0.9, status="valid", validator_note=None)])
    return store.get_findings(job.id)[-1]


@pytest.fixture
def reviewed(tmp_settings, tmp_path, fake_embedder):
    object.__setattr__(settings, "llm_concurrency", 1)
    store = get_store()
    _, v = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())
    job = store.create_job(v.document_id, v.id, "review", {"categories": list(review.CATEGORIES)})
    blocks = {b.text: b for b in store.get_blocks(v.id)}
    f1 = finding(store, job, blocks[OLD], "language", "menganalisa", "tidak baku", "menganalisis")
    f2 = finding(store, job, blocks["Penjualan naik 12% dibanding Agustus."], "substance", "naik 12%",
                 "tidak ada data pendukung", "sertakan sumber")
    store.update_job(job.id, status="done")
    return store, v, store.get_job(job.id), blocks, f1, f2


def run_revision(review_job_id, categories=None):
    async def go():
        job = await start_revision(review_job_id, categories)
        await runner.wait(job.id)
        return job
    return asyncio.run(go())


def test_revision_applies_approved_edits_new_version(reviewed, team, llm):  # noqa: F811
    store, v, rjob, blocks, f1, _ = reviewed
    replies, calls = llm
    replies["worker"].append({"edits": [{"block_id": blocks[OLD].id, "new_text": NEW, "finding_ids": [f1.id],
                                         "reason": "ejaan baku"}], "skipped": []})
    replies["supervisor"].append({"verdicts": [{"index": 0, "verdict": "approve", "feedback": ""}]})
    job = store.get_job(run_revision(rjob.id).id)
    assert job.status == "done", job.error
    nv = store.get_version(job.params["result_version_id"])
    assert nv.source == "revision" and nv.parent_version_id == v.id and nv.version_no == 2
    assert NEW in [b.text for b in store.get_blocks(nv.id)]
    assert b"w:ins" in zipfile.ZipFile(version_file(store, nv, "tracked")).read("word/document.xml")
    applied = [c for c in store.get_changes(job_id=job.id) if c.status == "applied"]
    assert len(applied) == 1 and applied[0].old_text == OLD and applied[0].new_text == NEW
    assert applied[0].source_ids == [f1.id] and applied[0].version_id == nv.id
    assert "menganalisa" in calls[0]["user"] and "[" + blocks[OLD].id + "]" in calls[0]["user"]
    assert "tidak ada data pendukung" not in calls[0]["user"]  # substance is never sent for editing


def test_substance_findings_become_suggestions(reviewed, team, llm):  # noqa: F811
    store, _, rjob, blocks, f1, f2 = reviewed
    replies, _ = llm
    replies["worker"].append({"edits": [], "skipped": [{"finding_id": f1.id, "reason": "tidak yakin"}]})
    job = store.get_job(run_revision(rjob.id).id)
    ch = store.get_changes(job_id=job.id)
    sug = [c for c in ch if c.status == "suggestion"]
    assert len(sug) == 1 and sug[0].source_ids == [f2.id] and "sertakan sumber" in sug[0].note
    nh = [c for c in ch if c.status == "needs_human"]
    assert len(nh) == 1 and nh[0].source_ids == [f1.id] and "tidak yakin" in nh[0].note


def test_rejected_edit_not_applied(reviewed, team, llm):  # noqa: F811
    store, _, rjob, blocks, f1, _ = reviewed
    replies, _ = llm
    replies["worker"].append({"edits": [{"block_id": blocks[OLD].id, "new_text": NEW, "finding_ids": [f1.id],
                                         "reason": "x"}], "skipped": []})
    replies["supervisor"].append({"verdicts": [{"index": 0, "verdict": "reject", "feedback": "mengubah makna"}]})
    job = store.get_job(run_revision(rjob.id).id)
    assert "result_version_id" not in job.params and job.status == "done"
    rej = [c for c in store.get_changes(job_id=job.id) if c.status == "rejected"]
    assert len(rej) == 1 and "mengubah makna" in rej[0].note


def test_validator_rejects_number_change(reviewed, team, llm):  # noqa: F811
    store, _, rjob, blocks, f1, _ = reviewed
    replies, calls = llm
    replies["worker"].append({"edits": [{"block_id": blocks[OLD].id, "new_text": NEW.replace("September", "Oktober"),
                                         "finding_ids": [f1.id], "reason": "x"}], "skipped": []})
    job = store.get_job(run_revision(rjob.id).id)
    rej = [c for c in store.get_changes(job_id=job.id) if c.status == "rejected"]
    assert len(rej) == 1 and "september" in rej[0].note.lower()
    assert [c["role"] for c in calls] == ["worker"]  # supervisor never sees invalid edits


def test_no_edits_no_version(reviewed, team, llm):  # noqa: F811
    store, v, rjob, _, _, _ = reviewed
    replies, _ = llm
    replies["worker"].append({"edits": [], "skipped": []})
    job = store.get_job(run_revision(rjob.id).id)
    assert job.status == "done" and "result_version_id" not in job.params
    assert [x.id for x in store.list_versions(v.document_id)] == [v.id]


def test_pdf_version_rejected(tmp_settings, tmp_path, team):  # noqa: F811
    store = get_store()
    _, v = ingest(store, filename="a.pdf", data=make_pdf(tmp_path).read_bytes())
    job = store.create_job(v.document_id, v.id, "review", {})
    store.update_job(job.id, status="done")
    with pytest.raises(ValueError, match="DOCX"):
        asyncio.run(start_revision(job.id, None))


def test_review_must_be_finished(reviewed, team):  # noqa: F811
    store, _, rjob, _, _, _ = reviewed
    store.update_job(rjob.id, status="running")
    with pytest.raises(ValueError, match="selesai"):
        asyncio.run(start_revision(rjob.id, None))


def test_office_events_for_revision(reviewed, team, llm):  # noqa: F811
    from agentorium.docpipe import office
    from agentorium.orchestrator import Orchestrator

    store, _, rjob, blocks, f1, _ = reviewed
    replies, _ = llm
    replies["worker"].append({"edits": [{"block_id": blocks[OLD].id, "new_text": NEW, "source_ids": [f1.id],
                                         "reason": "x"}], "skipped": []})
    replies["supervisor"].append({"verdicts": [{"index": 0, "verdict": "approve", "feedback": ""}]})
    o = Orchestrator()
    old = office.bridge
    office.install(o)
    try:
        job = store.get_job(run_revision(rjob.id).id)
    finally:
        office.bridge = old
    s = o.get(job.params["session_id"])
    assert s.title.startswith("Revisi: ") and s.stage == "done"
    assert any(e["type"] == "flow" for e in s.events) and any(e["type"] == "note" for e in s.events)


def test_server_registers_revision_handler(tmp_settings):
    from fastapi.testclient import TestClient

    import agentorium.server as server

    for k in ("revision_section", "instructed_section"):
        runner._handlers.pop(k, None)
    with TestClient(server.app):
        assert "revision_section" in runner._handlers and "instructed_section" in runner._handlers
