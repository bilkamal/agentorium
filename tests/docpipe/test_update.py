import asyncio
import re
import time

import pytest
from fastapi.testclient import TestClient

from agentorium.config import settings
from agentorium.docpipe import review
from agentorium.docpipe.jobs import runner
from agentorium.docpipe.service import ingest
from agentorium.docpipe.store import get_store
from agentorium.docpipe.update import start_update

from .fixtures import make_basic_docx, make_pdf
from .test_review import team  # noqa: F401

CSV = "Bulan;Penjualan\nSeptember;1200\nOktober;1500\n".encode()
GROWTH = "Penjualan naik 12% dibanding Agustus."
KASIR = "Data dikumpulkan dari sistem kasir."
ANALISA = "Kami telah menganalisa data penjualan bulan September."


class RoleLLM:
    """Worker answers by the blocks in its prompt; supervisor approves every edit it is shown."""

    def __init__(self, worker_fn, calls):
        self.worker_fn, self.calls, self.role = worker_fn, calls, None

    def for_agent(self, agent):
        c = RoleLLM(self.worker_fn, self.calls)
        c.role = agent.role
        return c

    async def complete_json(self, system, messages, schema, *, purpose=""):
        user = messages[-1]["content"]
        self.calls.append({"role": self.role, "user": user, "purpose": purpose})
        if self.role == "worker":
            return self.worker_fn(user)
        n = len(re.findall(r"^\[\d+\] blok", user, re.M))
        return {"verdicts": [{"index": i, "verdict": "approve", "feedback": ""} for i in range(n)]}


@pytest.fixture
def doc(tmp_settings, tmp_path, fake_embedder):
    object.__setattr__(settings, "llm_concurrency", 1)
    store = get_store()
    _, v = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())
    return store, v, {b.text: b for b in store.get_blocks(v.id)}


def worker_for(blocks):
    def fn(user):
        edits, skipped = [], []
        if f"[{blocks[GROWTH].id}]" in user:
            edits.append({"block_id": blocks[GROWTH].id, "source_ids": ["data"], "reason": "data Oktober",
                          "new_text": "Penjualan naik {{calc: (1500 - 1200) / 1200 * 100 | num:0}}% dibanding September."})
        if f"[{blocks[ANALISA].id}]" in user:
            edits.append({"block_id": blocks[ANALISA].id, "source_ids": ["data"], "reason": "periode",
                          "new_text": "Kami telah menganalisis data penjualan bulan Oktober sebesar 1777 unit."})
        if f"[{blocks['1.200'].id}]" in user:
            edits.append({"block_id": blocks["1.200"].id, "source_ids": [], "reason": "sel", "new_text": "1.500"})
        if f"[{blocks[KASIR].id}]" in user:
            skipped.append({"source_id": blocks[KASIR].id, "reason": "data sumber tidak disebut"})
        return {"edits": edits, "skipped": skipped}
    return fn


def run_update(vid, **kw):
    async def go():
        job = await start_update(vid, **kw)
        await runner.wait(job.id)
        return job
    return asyncio.run(go())


def test_update_end_to_end(doc, team, monkeypatch):  # noqa: F811
    store, v, blocks = doc
    calls = []
    monkeypatch.setattr(review, "make_llm", RoleLLM(worker_for(blocks), calls).for_agent)
    job = store.get_job(run_update(v.id, period_label="Oktober 2026", files=[("data.csv", CSV)]).id)
    assert job.status == "done", job.error
    nv = store.get_version(job.params["result_version_id"])
    assert nv.source == "update" and nv.parent_version_id == v.id and "Oktober 2026" in nv.note
    texts = [b.text for b in store.get_blocks(nv.id)]
    assert "Penjualan naik 25% dibanding September." in texts
    assert "1.500" in texts and ANALISA in texts  # unsourced 1777 → held, block unchanged
    ch = {c.block_id: c for c in store.get_changes(job_id=job.id)}
    assert ch[blocks[GROWTH].id].status == "applied" and "rumus: (1500 - 1200) / 1200 * 100 = 25" in ch[blocks[GROWTH].id].reason
    assert ch[blocks[ANALISA].id].status == "needs_human" and "1777" in ch[blocks[ANALISA].id].note
    assert ch[blocks[KASIR].id].status == "needs_human" and ch[blocks[KASIR].id].note.startswith("ditahan:")
    w = [c for c in calls if c["role"] == "worker"]
    assert "Bulan=Oktober | Penjualan=1500" in w[0]["user"] and "PERIODE BARU: Oktober 2026" in w[0]["user"]
    s = [c for c in calls if c["role"] == "supervisor"]
    assert all("1777" not in c["user"] for c in s)  # supervisor never sees edits the validator held


def test_update_requires_period_and_data(doc, team):  # noqa: F811
    _, v, _ = doc
    with pytest.raises(ValueError, match="periode"):
        asyncio.run(start_update(v.id, period_label=" ", files=[("d.csv", CSV)]))
    with pytest.raises(ValueError, match="kosong"):
        asyncio.run(start_update(v.id, period_label="Oktober", files=[]))


def test_update_api(doc, team, monkeypatch, tmp_path):  # noqa: F811
    import agentorium.server as server

    store, v, blocks = doc
    monkeypatch.setattr(review, "make_llm", RoleLLM(worker_for(blocks), []).for_agent)
    _, pv = ingest(store, filename="p.pdf", data=make_pdf(tmp_path).read_bytes())
    with TestClient(server.app) as c:
        url = f"/api/docs/versions/{v.id}/updates"
        assert c.post(url, data={"period_label": ""}, files=[("files", ("d.csv", CSV))]).status_code == 400
        assert c.post(url, data={"period_label": "Okt"}, files=[("files", ("d.exe", b"MZ"))]).status_code == 400
        assert c.post(f"/api/docs/versions/{pv.id}/updates", data={"period_label": "Okt"},
                      files=[("files", ("d.csv", CSV))]).status_code == 409
        r = c.post(url, data={"period_label": "Oktober 2026", "notes": "fokus penjualan"},
                   files=[("files", ("d.csv", CSV))])
        assert r.status_code == 202, r.text
        jid = r.json()["job"]["id"]
        end = time.time() + 10
        while time.time() < end and c.get(f"/api/docs/jobs/{jid}").json()["job"]["status"] not in ("done", "partial"):
            time.sleep(0.05)
        j = c.get(f"/api/docs/jobs/{jid}").json()["job"]
        assert j["status"] == "done" and j["result_version_id"] and j["type"] == "update"
        assert "update_section" in runner._handlers
