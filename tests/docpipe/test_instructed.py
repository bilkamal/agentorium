import asyncio
import io

import docx
import pytest

from agentorium.config import settings
from agentorium.docpipe import review
from agentorium.docpipe.instructed import followup_docx, start_instructed_revision
from agentorium.docpipe.jobs import runner
from agentorium.docpipe.service import ingest
from agentorium.docpipe.store import get_store

from .fixtures import make_basic_docx
from .test_materials import commented_docx
from .test_review import ScriptedLLM, team  # noqa: F401

KASIR = "Data dikumpulkan dari sistem kasir."
ANALISA = "Kami telah menganalisa data penjualan bulan September."


@pytest.fixture
def llm(monkeypatch):
    calls, replies = [], {"worker": [], "supervisor": []}
    monkeypatch.setattr(review, "make_llm", ScriptedLLM(replies, calls).for_agent)
    return replies, calls


@pytest.fixture
def doc(tmp_settings, tmp_path, fake_embedder):
    object.__setattr__(settings, "llm_concurrency", 1)
    store = get_store()
    _, v = ingest(store, filename="a.docx", data=commented_docx(tmp_path))
    from agentorium.docpipe.index import index_version

    index_version(store, v.id)
    return store, v, {b.text: b for b in store.get_blocks(v.id)}


def run(coro_fn):
    async def go():
        job = await coro_fn()
        await runner.wait(job.id)
        return job
    return asyncio.run(go())


def test_instructed_revision_end_to_end(doc, team, llm):  # noqa: F811
    store, v, blocks = doc
    replies, calls = llm
    # supervisor splits the notes, worker edits section "Bab 1", supervisor approves
    replies["supervisor"].append({"instructions": [{"text": "Ganti menganalisa menjadi menganalisis",
                                                    "quote": "menganalisa"}]})

    def worker_reply():
        ins = store.get_instructions(store.list_jobs(v.id)[0].id)
        iid = next(i.id for i in ins if "menganalisis" in i.text)
        return iid

    job = None

    async def start():
        return await start_instructed_revision(v.id, notes="Tolong ganti kata menganalisa.", files=[],
                                               use_comments=False)

    # the worker reply needs the instruction id, which exists only after the preparer — script it lazily
    class Lazy(dict):
        def get(self, k, d=None):
            return super().get(k, d)

    orig = ScriptedLLM.complete_json

    async def complete_json(self, system, messages, schema, *, purpose=""):
        if self.role == "worker" and not self.replies["worker"]:
            iid = worker_reply()
            self.replies["worker"].append({"edits": [{"block_id": blocks[ANALISA].id,
                                                      "new_text": ANALISA.replace("menganalisa", "menganalisis"),
                                                      "source_ids": [iid], "reason": "baku"}], "skipped": [],
                                           "responses": [{"instruction_id": iid, "status": "done",
                                                          "response": "sudah diganti"}]})
            self.replies["supervisor"].append({"verdicts": [{"index": 0, "verdict": "approve", "feedback": ""}]})
        return await orig(self, system, messages, schema, purpose=purpose)

    ScriptedLLM.complete_json = complete_json
    try:
        job = store.get_job(run(start).id)
    finally:
        ScriptedLLM.complete_json = orig
    assert job.status == "done", job.error
    nv = store.get_version(job.params["result_version_id"])
    assert nv.source == "instructed_revision"
    assert ANALISA.replace("menganalisa", "menganalisis") in [b.text for b in store.get_blocks(nv.id)]
    ins = store.get_instructions(job.id)
    assert [(i.status, i.response) for i in ins] == [("done", "sudah diganti")]
    assert "Tolong ganti kata menganalisa." in calls[0]["user"]


def test_unlocated_instruction_reported(doc, team, llm):  # noqa: F811
    store, v, _ = doc
    replies, _ = llm
    replies["supervisor"].append({"instructions": [{"text": "zzqx wkwk", "quote": ""}]})

    import agentorium.docpipe.materials as m

    orig = m.locate
    m.locate = lambda store, vid, instr: {"block_ids": [], "candidate_block_ids": []}
    try:
        job = store.get_job(run(lambda: start_instructed_revision(v.id, notes="zzqx", files=[], use_comments=False)).id)
    finally:
        m.locate = orig
    ins = store.get_instructions(job.id)
    assert len(ins) == 1 and ins[0].status == "needs_human" and "tidak ditemukan" in ins[0].response
    assert job.status == "done" and job.total_tasks == 0


def test_comment_instruction_targets_anchor_block(doc, team, llm):  # noqa: F811
    store, v, blocks = doc
    replies, calls = llm
    replies["worker"].append({"edits": [], "skipped": [], "responses": []})
    job = store.get_job(run(lambda: start_instructed_revision(v.id, notes="", files=[], use_comments=True)).id)
    ins = store.get_instructions(job.id)
    assert len(ins) == 1 and ins[0].source == "comment" and ins[0].author == "Atasan"
    assert ins[0].block_ids == [blocks[KASIR].id] and ins[0].text == "Sebutkan nama aplikasinya"
    assert ins[0].status == "needs_human"  # worker did nothing with it
    assert [c["role"] for c in calls] == ["worker"]  # comments need no splitting call
    assert f"[{blocks[KASIR].id}]" in calls[0]["user"] and "Sebutkan nama aplikasinya" in calls[0]["user"]


def test_not_applicable_response_kept(doc, team, llm):  # noqa: F811
    store, v, blocks = doc
    replies, _ = llm

    orig = ScriptedLLM.complete_json

    async def complete_json(self, system, messages, schema, *, purpose=""):
        if self.role == "worker" and not self.replies["worker"]:
            iid = store.get_instructions(store.list_jobs(v.id)[0].id)[0].id
            self.replies["worker"].append({"edits": [], "skipped": [], "responses": [
                {"instruction_id": iid, "status": "not_applicable", "response": "nama aplikasi tidak ada di data"}]})
        return await orig(self, system, messages, schema, purpose=purpose)

    ScriptedLLM.complete_json = complete_json
    try:
        job = store.get_job(run(lambda: start_instructed_revision(v.id, notes="", files=[], use_comments=True)).id)
    finally:
        ScriptedLLM.complete_json = orig
    i = store.get_instructions(job.id)[0]
    assert (i.status, i.response) == ("not_applicable", "nama aplikasi tidak ada di data")


def test_empty_materials_rejected(doc, team):  # noqa: F811
    store, v, _ = doc
    with pytest.raises(ValueError, match="kosong"):
        asyncio.run(start_instructed_revision(v.id, notes="  ", files=[], use_comments=False))


def test_bad_material_file_rejected(doc, team):  # noqa: F811
    store, v, _ = doc
    with pytest.raises(ValueError, match="Format"):
        asyncio.run(start_instructed_revision(v.id, notes="x", files=[("a.exe", b"MZ")], use_comments=False))


def test_followup_docx_has_all_instructions(doc, team):  # noqa: F811
    store, v, blocks = doc
    job = store.create_job(v.document_id, v.id, "instructed_revision", {})
    store.add_instructions([
        dict(job_id=job.id, source="notes", text="Ganti istilah", quote="", block_ids=[blocks[ANALISA].id],
             candidate_block_ids=[], status="done", response="sudah"),
        dict(job_id=job.id, source="comment", author="Atasan", text="Sebutkan aplikasi", quote="",
             block_ids=[], candidate_block_ids=[], status="needs_human", response="perlu data")])
    d = docx.Document(io.BytesIO(followup_docx(job.id)))
    rows = [[c.text for c in r.cells] for r in d.tables[0].rows]
    assert rows[0][:4] == ["No", "Sumber", "Instruksi", "Status"]
    assert rows[1][2] == "Ganti istilah" and rows[1][3] == "selesai" and "Bab 1" in rows[1][5]
    assert rows[2][1] == "Komentar Word (Atasan)" and rows[2][3] == "perlu dicek manual"


def test_failed_apply_marks_instruction_needs_human(doc, team, llm, monkeypatch):  # noqa: F811
    store, v, blocks = doc
    replies, _ = llm
    import agentorium.docpipe.revise as rv
    from agentorium.docpipe.edits import EditResult

    monkeypatch.setattr(rv, "apply_block_edits",
                        lambda data, edits, author="DocPipe": (None, None,
                                                               [EditResult(e.block_id, "needs_human", "ambigu") for e in edits]))
    orig = ScriptedLLM.complete_json

    async def complete_json(self, system, messages, schema, *, purpose=""):
        if self.role == "worker" and not self.replies["worker"]:
            iid = store.get_instructions(store.list_jobs(v.id)[0].id)[0].id
            self.replies["worker"].append({"edits": [{"block_id": blocks[KASIR].id,
                                                      "new_text": "Data dikumpulkan dari aplikasi kasir.",
                                                      "source_ids": [iid], "reason": "x"}], "skipped": [],
                                           "responses": [{"instruction_id": iid, "status": "done", "response": "ok"}]})
            self.replies["supervisor"].append({"verdicts": [{"index": 0, "verdict": "approve", "feedback": ""}]})
        return await orig(self, system, messages, schema, purpose=purpose)

    ScriptedLLM.complete_json = complete_json
    try:
        job = store.get_job(run(lambda: start_instructed_revision(v.id, notes="", files=[], use_comments=True)).id)
    finally:
        ScriptedLLM.complete_json = orig
    i = store.get_instructions(job.id)[0]
    assert i.status == "needs_human" and "ambigu" in i.response
    assert "result_version_id" not in job.params
