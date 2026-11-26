import asyncio

import pytest

from agentorium.config import settings
from agentorium.docpipe import index as index_mod
from agentorium.docpipe import review
from agentorium.docpipe.consistency import check_consistency
from agentorium.docpipe.jobs import JobRunner, runner
from agentorium.docpipe.review import pick_agents, start_review
from agentorium.docpipe.service import ingest
from agentorium.docpipe.store import get_store
from agentorium.llm import LLMRateLimited
from agentorium.registry import AgentDef, registry

from .fixtures import make_basic_docx
from .test_review import ScriptedLLM, team  # noqa: F401


def test_mock_agents_cannot_review(tmp_settings):
    old = registry.agents
    try:
        registry.agents = [AgentDef(id="s", name="S", role="supervisor", provider="mock"),
                           AgentDef(id="w", name="W", role="worker", provider="claude_code")]
        with pytest.raises(RuntimeError, match="mock"):
            pick_agents()
        registry.agents = [AgentDef(id="s", name="S", role="supervisor", provider="claude_code"),
                           AgentDef(id="w", name="W", role="worker", provider="mock")]
        with pytest.raises(RuntimeError, match="mock"):
            pick_agents()
        registry.agents.append(AgentDef(id="w2", name="W2", role="worker", provider="claude_code"))
        sup, workers = pick_agents()
        assert [w.id for w in workers] == ["w2"]
    finally:
        registry.agents = old


def test_usage_limit_in_finalizer_pauses_then_completes(tmp_settings):
    object.__setattr__(settings, "rate_limit_wait", 0.1)
    store = get_store()
    d = store.create_document("x")
    v = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=None)
    job = store.create_job(d.id, v.id, "review", {})
    store.add_tasks(job.id, [("s0000", ["b00000"], "t")])
    r = JobRunner()
    events, calls = [], []

    async def handler(job, task):
        return {}

    async def fin(job):
        calls.append(1)
        if len(calls) == 1:
            raise LLMRateLimited("usage limit")

    r.register("t", handler)
    r.register_finalizer("review", fin)
    r.on_event(lambda j, e, p: events.append(e))

    async def go():
        await r.start(job.id)
        await r.wait(job.id)

    asyncio.run(go())
    assert store.get_job(job.id).status == "done" and store.get_job(job.id).error is None
    assert len(calls) == 2 and "paused" in events


@pytest.fixture
def llm(monkeypatch):
    calls, replies = [], {"worker": [], "supervisor": []}
    monkeypatch.setattr(review, "make_llm", ScriptedLLM(replies, calls).for_agent)
    return replies, calls


def facts_job(store):
    d = store.create_document("x")
    v = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=None)
    job = store.create_job(d.id, v.id, "review", {"categories": ["consistency"]})
    store.add_facts([dict(job_id=job.id, version_id=v.id, block_id="b1", kind="number", key="k", value="1", quote="1"),
                     dict(job_id=job.id, version_id=v.id, block_id="b2", kind="number", key="k", value="2", quote="2")])
    return store.get_job(job.id)


def test_consistency_check_is_idempotent(tmp_settings, team, llm):  # noqa: F811
    store = get_store()
    job = facts_job(store)
    replies, _ = llm
    ok = {"conflicts": [{"pair": 0, "is_conflict": True, "severity": "low", "description": "beda"}]}
    replies["supervisor"] += [ok, ok]
    asyncio.run(check_consistency(job))
    asyncio.run(check_consistency(job))
    assert len(store.get_findings(job.id)) == 1


def test_consistency_skipped_when_not_requested(tmp_settings, team, llm):  # noqa: F811
    store = get_store()
    job = facts_job(store)
    store.update_job(job.id, params={"categories": ["language"]})
    assert asyncio.run(review._finalize_review(store.get_job(job.id))) is None
    assert llm[1] == [] and store.get_findings(job.id) == []


def test_review_indexes_inside_job_not_request(tmp_settings, tmp_path, team, llm, monkeypatch, fake_embedder):  # noqa: F811
    object.__setattr__(settings, "llm_concurrency", 1)
    store = get_store()
    _, v = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())
    real, seen = index_mod.index_version, []

    def counting(s, vid):
        seen.append(vid)
        return real(s, vid)

    monkeypatch.setattr(index_mod, "index_version", counting)
    replies, _ = llm
    replies["worker"] += [{"findings": [], "facts": []}] * 10

    async def go():
        job = await start_review(v.id, ["language"])
        at_return = len(seen)
        await runner.wait(job.id)
        return job, at_return

    job, at_return = asyncio.run(go())
    assert at_return == 0 and seen == [v.id]
    assert store.get_job(job.id).status == "done" and store.get_version(v.id).index_status == "ready"


def test_concurrent_review_requests_create_one_job(tmp_settings, tmp_path, team, monkeypatch):  # noqa: F811
    from fastapi import HTTPException

    from agentorium.docpipe.api import create_review

    store = get_store()
    _, v = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())

    async def slow_llm_job():
        results = await asyncio.gather(create_review(v.id, {}), create_review(v.id, {}), return_exceptions=True)
        for j in store.list_jobs(v.id):
            await runner.cancel(j.id)
        return results

    monkeypatch.setattr(index_mod, "index_version", lambda s, vid: __import__("time").sleep(0.3) or "fts_only")
    results = asyncio.run(slow_llm_job())
    assert sum(isinstance(r, HTTPException) and r.status_code == 409 for r in results) == 1
    assert len(store.list_jobs(v.id)) == 1
