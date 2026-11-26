import asyncio

import pytest

from agentorium.docpipe import review
from agentorium.docpipe.consistency import candidate_conflicts, check_consistency, normalize_value
from agentorium.docpipe.jobs import runner
from agentorium.docpipe.models import Fact
from agentorium.docpipe.review import start_review
from agentorium.docpipe.service import ingest
from agentorium.docpipe.store import get_store

from .fixtures import make_basic_docx
from .test_review import ScriptedLLM, team  # noqa: F401  (fixture)


def fact(i, block, key, value, kind="number", unit="orang"):
    return Fact(id=f"f{i}", job_id="j", version_id="v", block_id=block, kind=kind, key=key, value=value, unit=unit,
                quote=value)


def test_candidate_conflicts_same_key_different_value():
    facts = [fact(1, "b1", "Jumlah responden", "120"), fact(2, "b7", "jumlah  responden.", "125"),
             fact(3, "b8", "jumlah karyawan", "14"), fact(4, "b9", "jumlah responden", "120", kind="term")]
    pairs = candidate_conflicts(facts)
    assert [(a.id, b.id) for a, b in pairs] == [("f1", "f2")]


def test_same_block_is_not_a_conflict():
    assert candidate_conflicts([fact(1, "b1", "x", "1"), fact(2, "b1", "x", "2")]) == []


def test_number_normalization_treats_equal_values_as_same():
    assert normalize_value("1.200", "number") == normalize_value("1200", "number") == "1200"
    assert normalize_value("12,5", "number") == normalize_value("12.5", "number") == "12.5"
    assert normalize_value("Rp 1.234.567,00", "number") == "1234567"
    assert candidate_conflicts([fact(1, "b1", "x", "1.200"), fact(2, "b2", "x", "1200")]) == []
    assert normalize_value("  Bapak  Ahmad ", "name") == "bapak ahmad"


def test_different_units_not_compared():
    assert candidate_conflicts([fact(1, "b1", "x", "12", unit="persen"), fact(2, "b2", "x", "14", unit="orang")]) == []
    assert len(candidate_conflicts([fact(1, "b1", "x", "12", unit=""), fact(2, "b2", "x", "14", unit="orang")])) == 1


def test_pairs_capped():
    facts = [fact(i, f"b{i}", "x", str(i)) for i in range(30)]
    assert len(candidate_conflicts(facts)) == 50


@pytest.fixture
def llm(monkeypatch):
    calls, replies = [], {"worker": [], "supervisor": []}
    monkeypatch.setattr(review, "make_llm", ScriptedLLM(replies, calls).for_agent)
    return replies, calls


def test_check_consistency_creates_paired_finding(tmp_settings, team, llm):  # noqa: F811
    store = get_store()
    d = store.create_document("x")
    v = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=None)
    job = store.create_job(d.id, v.id, "review", {})
    store.add_facts([dict(job_id=job.id, version_id=v.id, block_id="b00001", kind="number", key="jumlah responden",
                          value="120", unit="orang", quote="120 responden"),
                     dict(job_id=job.id, version_id=v.id, block_id="b00009", kind="number", key="jumlah responden",
                          value="125", unit="orang", quote="125 responden")])
    replies, calls = llm
    replies["supervisor"].append({"conflicts": [{"pair": 0, "is_conflict": True, "severity": "high",
                                                 "description": "Jumlah responden berbeda (120 vs 125)."}]})
    n = asyncio.run(check_consistency(store.get_job(job.id)))
    assert n == 1
    f = store.get_findings(job.id)[0]
    assert (f.category, f.block_id, f.other_block_id, f.status, f.severity) == \
        ("consistency", "b00001", "b00009", "valid", "high")
    assert "120 responden" in calls[0]["user"] and "125 responden" in calls[0]["user"]


def test_check_consistency_no_pairs_no_call(tmp_settings, team, llm):  # noqa: F811
    store = get_store()
    d = store.create_document("x")
    v = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=None)
    job = store.create_job(d.id, v.id, "review", {})
    assert asyncio.run(check_consistency(job)) == 0 and llm[1] == []


def test_start_review_end_to_end_with_fake_llm(tmp_settings, tmp_path, team, llm, fake_embedder):  # noqa: F811
    store = get_store()
    _, v = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())
    replies, calls = llm
    blocks = store.get_blocks(v.id)
    n_tasks = len(review.plan_tasks(store.get_sections(v.id), blocks))
    target = blocks[0]
    replies["worker"] += [{"findings": [{"block_id": target.id, "category": "language", "severity": "low",
                                         "quote": target.text[:10], "description": "contoh", "suggestion": "",
                                         "confidence": 0.6}], "facts": []}] + \
                         [{"findings": [], "facts": []}] * (n_tasks - 1)
    replies["supervisor"].append({"verdicts": [{"index": 0, "verdict": "approve", "feedback": ""}]})

    async def go():
        job = await start_review(v.id, ["language"])
        await runner.wait(job.id)
        return job

    job = asyncio.run(go())
    j = store.get_job(job.id)
    assert j.status == "done" and j.total_tasks == n_tasks == j.done_tasks
    assert all(t.status == "done" for t in store.get_tasks(job.id))
    assert len(store.get_findings(job.id, status="valid")) == 1
    assert j.params["categories"] == ["language"]
    assert store.get_version(v.id).index_status == "ready"


def test_start_review_rejects_unready_version(tmp_settings, team):  # noqa: F811
    store = get_store()
    d = store.create_document("x")
    v = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=None)
    with pytest.raises(ValueError):
        asyncio.run(start_review(v.id, None))


def test_check_consistency_skips_conflict_already_reported_by_worker(tmp_settings, team, llm):  # noqa: F811
    store = get_store()
    d = store.create_document("x")
    v = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=None)
    job = store.create_job(d.id, v.id, "review", {})
    store.add_facts([dict(job_id=job.id, version_id=v.id, block_id="b00001", kind="number", key="jumlah responden",
                          value="120", unit="orang", quote="120"),
                     dict(job_id=job.id, version_id=v.id, block_id="b00009", kind="number", key="jumlah responden",
                          value="125", unit="orang", quote="125")])
    store.add_findings([dict(job_id=job.id, task_id=None, version_id=v.id, block_id="b00009", other_block_id=None,
                             category="consistency", severity="high", quote="125", description="beda dengan 120",
                             suggestion="", confidence=0.9, status="valid", validator_note=None)])
    assert asyncio.run(check_consistency(store.get_job(job.id))) == 0
    assert llm[1] == [] and len(store.get_findings(job.id)) == 1


def test_worker_prompt_forbids_meta_commentary():
    from agentorium.docpipe.prompts import worker_system

    s = worker_system("W", ["consistency"])
    assert "KONTEKS" in s and "aturan" in s.lower() and "ID blok" in s
