import asyncio
import json

import pytest

from agentorium.config import settings
from agentorium.docpipe import review
from agentorium.docpipe.index import index_version
from agentorium.docpipe.models import Block, ParsedDoc, Section
from agentorium.docpipe.review import MAX_TASK_CHARS, pick_agents, plan_tasks, review_task
from agentorium.docpipe.store import get_store
from agentorium.llm import LLMError
from agentorium.registry import AgentDef, registry


class ScriptedLLM:
    """Returns queued JSON replies per role and records every call."""

    def __init__(self, replies: dict[str, list], calls: list):
        self.replies, self.calls, self.role = replies, calls, None

    def for_agent(self, agent):
        clone = ScriptedLLM(self.replies, self.calls)
        clone.role = agent.role
        return clone

    async def complete_json(self, system, messages, schema, *, purpose=""):
        self.calls.append({"role": self.role, "system": system, "user": messages[-1]["content"], "schema": schema})
        queue = self.replies[self.role]
        if not queue:
            raise AssertionError(f"no scripted reply left for {self.role}")
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def team():
    old = registry.agents
    registry.agents = [AgentDef(id="sup", name="Supervisor", role="supervisor", provider="claude_code"),
                       AgentDef(id="w1", name="Worker 1", role="worker", provider="claude_code"),
                       AgentDef(id="w2", name="Worker 2", role="worker", provider="claude_code"),
                       AgentDef(id="cl", name="Client", role="client")]
    yield registry.agents
    registry.agents = old


@pytest.fixture
def llm(monkeypatch):
    calls: list = []
    replies = {"worker": [], "supervisor": []}
    base = ScriptedLLM(replies, calls)
    monkeypatch.setattr(review, "make_llm", base.for_agent)
    return replies, calls


@pytest.fixture
def doc(tmp_settings, fake_embedder):
    store = get_store()
    d = store.create_document("Laporan Uji")
    v = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=None)
    sections = [Section(id="s0000", ordinal=0, level=1, heading="Pendahuluan", path="Pendahuluan", parent_id=None),
                Section(id="s0001", ordinal=1, level=1, heading="Penjualan", path="Penjualan", parent_id=None),
                Section(id="s0002", ordinal=2, level=1, heading="Kosong", path="Kosong", parent_id=None)]
    blocks = [Block(id="b00000", uid="u0", ordinal=0, section_id="s0000", type="paragraph",
                    text="Laporan ini menganalisa penjualan toko dengan 120 responden."),
              Block(id="b00001", uid="u1", ordinal=1, section_id="s0001", type="paragraph",
                    text="Penjualan bulan September naik 12 persen."),
              Block(id="b00002", uid="u2", ordinal=2, section_id="s0001", type="cell",
                    text="Rp 1.200 juta", table_index=0, row=1, col=2)]
    store.save_structure(v.id, ParsedDoc(sections, blocks))
    store.update_version(v.id, status="ready")
    index_version(store, v.id)
    job = store.create_job(d.id, v.id, "review", {"categories": list(review.CATEGORIES)})
    tasks = store.add_tasks(job.id, [(sid, bids, "section_review") for sid, bids in plan_tasks(sections, blocks)])
    return store, store.get_job(job.id), tasks


def finding(**kw):
    return {"block_id": "b00001", "category": "language", "severity": "low", "quote": "naik 12 persen",
            "description": "Kata kurang baku.", "suggestion": "meningkat 12 persen", "confidence": 0.9, **kw}


def run(coro):
    return asyncio.run(coro)


def test_plan_tasks_one_per_section():
    secs = [Section(id=f"s{i:04d}", ordinal=i, level=1, heading=str(i), path=str(i), parent_id=None) for i in range(3)]
    blks = [Block(id="b00000", uid="", ordinal=0, section_id="s0000", type="paragraph", text="a"),
            Block(id="b00001", uid="", ordinal=1, section_id="s0002", type="paragraph", text="b")]
    assert plan_tasks(secs, blks) == [("s0000", ["b00000"]), ("s0002", ["b00001"])]


def test_long_section_split_covers_all_blocks():
    secs = [Section(id="s0000", ordinal=0, level=1, heading="A", path="A", parent_id=None)]
    blks = [Block(id=f"b{i:05d}", uid="", ordinal=i, section_id="s0000", type="paragraph", text="x" * 5000)
            for i in range(12)]
    tasks = plan_tasks(secs, blks)
    assert len(tasks) > 1 and all(sid == "s0000" for sid, _ in tasks)
    assert [b for _, ids in tasks for b in ids] == [b.id for b in blks]
    assert all(sum(5000 for _ in ids) <= MAX_TASK_CHARS for _, ids in tasks)


def test_review_task_valid_and_rejected_findings(doc, team, llm):
    store, job, tasks = doc
    replies, calls = llm
    replies["worker"].append({"findings": [finding(), finding(quote="turun 5 persen")], "facts": []})
    replies["supervisor"].append({"verdicts": [{"index": 0, "verdict": "approve", "feedback": ""}]})
    out = run(review_task(job, tasks[1]))
    assert out["findings"] == 1 and out["rejected"] == 1 and out["needs_human"] == 0
    valid = store.get_findings(job.id, status="valid")
    assert len(valid) == 1 and valid[0].block_id == "b00001" and valid[0].task_id == tasks[1].id
    rej = store.get_findings(job.id, status="rejected")
    assert len(rej) == 1 and "quote" in rej[0].validator_note
    assert [c["role"] for c in calls] == ["worker", "supervisor"]
    assert "turun 5 persen" not in calls[1]["user"]  # supervisor only sees validated findings


def test_supervisor_reject_marks_rejected(doc, team, llm):
    store, job, tasks = doc
    replies, _ = llm
    replies["worker"].append({"findings": [finding()], "facts": []})
    replies["supervisor"].append({"verdicts": [{"index": 0, "verdict": "reject", "feedback": "bukan kesalahan"}]})
    out = run(review_task(job, tasks[1]))
    assert out["findings"] == 0 and out["rejected"] == 1
    assert "bukan kesalahan" in store.get_findings(job.id, status="rejected")[0].validator_note


def test_supervisor_revise_then_approve(doc, team, llm):
    store, job, tasks = doc
    replies, calls = llm
    replies["worker"] += [{"findings": [finding(severity="high")], "facts": []},
                          {"findings": [finding(severity="low", description="Lebih baku: meningkat.")], "facts": []}]
    replies["supervisor"] += [{"verdicts": [{"index": 0, "verdict": "revise", "feedback": "tingkatnya terlalu tinggi"}]},
                              {"verdicts": [{"index": 0, "verdict": "approve", "feedback": ""}]}]
    out = run(review_task(job, tasks[1]))
    assert out["findings"] == 1
    v = store.get_findings(job.id, status="valid")[0]
    assert v.severity == "low" and v.description == "Lebih baku: meningkat."
    assert "tingkatnya terlalu tinggi" in calls[2]["user"] and calls[2]["role"] == "worker"


def test_revise_exhausted_becomes_needs_human(doc, team, llm):
    object.__setattr__(settings, "max_revisions", 2)
    store, job, tasks = doc
    replies, calls = llm
    replies["worker"] += [{"findings": [finding()], "facts": []}] * 3
    replies["supervisor"] += [{"verdicts": [{"index": 0, "verdict": "revise", "feedback": "kurang jelas"}]}] * 3
    out = run(review_task(job, tasks[1]))
    assert out["needs_human"] == 1 and out["findings"] == 0
    assert len(store.get_findings(job.id, status="needs_human")) == 1
    assert [c["role"] for c in calls].count("worker") == 3


def test_missing_verdict_is_not_approved(doc, team, llm):
    store, job, tasks = doc
    replies, _ = llm
    replies["worker"].append({"findings": [finding()], "facts": []})
    replies["supervisor"].append({"verdicts": []})
    out = run(review_task(job, tasks[1]))
    assert out["findings"] == 0 and out["needs_human"] == 1


def test_supervisor_invalid_json_fails_task(doc, team, llm):
    store, job, tasks = doc
    replies, _ = llm
    replies["worker"].append({"findings": [finding()], "facts": []})
    replies["supervisor"].append(LLMError("invalid JSON"))
    with pytest.raises(LLMError):
        run(review_task(job, tasks[1]))
    assert store.get_findings(job.id) == []


def test_worker_prompt_contains_blocks_outline_and_context(doc, team, llm):
    store, job, tasks = doc
    replies, calls = llm
    replies["worker"].append({"findings": [], "facts": []})
    run(review_task(job, tasks[1]))
    user = calls[0]["user"]
    assert "[b00001] Penjualan bulan September naik 12 persen." in user
    assert "[b00002] (tabel 1, baris 2, kolom 3) Rp 1.200 juta" in user
    assert "Pendahuluan" in user and "Kosong" in user  # outline
    assert "menganalisa penjualan" in user  # context from another section via retrieval
    ctx = user.split("KONTEKS")[1].split("BAGIAN YANG DIREVIEW")[0]
    assert "September naik" not in ctx  # own section never appears as context
    assert len(calls) == 1  # no findings → no supervisor call


def test_findings_outside_requested_categories_rejected(doc, team, llm):
    store, job, tasks = doc
    store.update_job(job.id, params={"categories": ["language"]})
    job = store.get_job(job.id)
    replies, calls = llm
    replies["worker"].append({"findings": [finding(category="substance")], "facts": []})
    out = run(review_task(job, tasks[1]))
    assert out["rejected"] == 1 and "category" in store.get_findings(job.id)[0].validator_note
    assert "language" in calls[0]["system"] and "substance:" not in calls[0]["system"]


def test_facts_saved(doc, team, llm):
    store, job, tasks = doc
    replies, _ = llm
    replies["worker"].append({"findings": [], "facts": [
        {"block_id": "b00001", "kind": "number", "key": "kenaikan penjualan", "value": "12", "unit": "persen",
         "quote": "naik 12 persen"},
        {"block_id": "b00001", "kind": "number", "key": "x", "value": "1", "unit": "", "quote": "tidak ada"}]})
    out = run(review_task(job, tasks[1]))
    facts = store.get_facts(job.id)
    assert out["facts"] == 1 and len(facts) == 1 and facts[0].key == "kenaikan penjualan"


def test_workers_round_robin(doc, team, llm):
    store, job, tasks = doc
    replies, _ = llm
    replies["worker"] += [{"findings": [], "facts": []}] * 2
    assert run(review_task(job, tasks[0]))["worker"] == "w1"
    assert run(review_task(job, tasks[1]))["worker"] == "w2"


def test_pick_agents_requires_supervisor_and_worker(tmp_settings):
    old = registry.agents
    try:
        registry.agents = [AgentDef(id="w", name="W", role="worker")]
        with pytest.raises(RuntimeError, match="supervisor"):
            pick_agents()
        registry.agents = [AgentDef(id="s", name="S", role="supervisor")]
        with pytest.raises(RuntimeError, match="worker"):
            pick_agents()
    finally:
        registry.agents = old
