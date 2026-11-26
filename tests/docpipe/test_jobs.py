import asyncio

import pytest

from agentorium.config import settings
from agentorium.docpipe.jobs import JobRunner, resume_jobs
from agentorium.docpipe.store import get_store
from agentorium.llm import LLMRateLimited


@pytest.fixture
def store(tmp_settings):
    return get_store()


def new_job(store, n=3, kind="t"):
    d = store.create_document("Uji")
    v = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=None)
    job = store.create_job(d.id, v.id, "review", {"x": 1})
    store.add_tasks(job.id, [(f"s{i:04d}", [f"b{i:05d}"], kind) for i in range(n)])
    return store.get_job(job.id)


def run(coro):
    return asyncio.run(coro)


def test_job_runs_all_tasks_and_finishes_done(store):
    job = new_job(store)
    r = JobRunner()
    seen = []

    async def handler(job, task):
        seen.append(task.section_id)
        return {"ok": task.ordinal}

    r.register("t", handler)

    async def go():
        await r.start(job.id)
        await r.wait(job.id)

    run(go())
    j = store.get_job(job.id)
    assert j.status == "done" and j.done_tasks == 3 and j.total_tasks == 3 and j.finished_at
    assert sorted(seen) == ["s0000", "s0001", "s0002"]
    assert [t.output for t in store.get_tasks(job.id)] == [{"ok": 0}, {"ok": 1}, {"ok": 2}]
    assert j.params == {"x": 1}


def test_failed_task_retried_once_then_partial(store):
    job = new_job(store, 2)
    r = JobRunner()
    calls = {}

    async def handler(job, task):
        calls[task.section_id] = calls.get(task.section_id, 0) + 1
        if task.section_id == "s0001":
            raise RuntimeError("boom")
        return {}

    r.register("t", handler)

    async def go():
        await r.start(job.id)
        await r.wait(job.id)

    run(go())
    j = store.get_job(job.id)
    assert j.status == "partial" and j.failed_tasks == 1 and j.done_tasks == 1
    assert calls == {"s0000": 1, "s0001": 2}
    bad = [t for t in store.get_tasks(job.id) if t.section_id == "s0001"][0]
    assert bad.status == "failed" and bad.attempts == 2 and "boom" in bad.error


def test_concurrency_respected(store, monkeypatch):
    object.__setattr__(settings, "llm_concurrency", 2)
    job = new_job(store, 6)
    r = JobRunner()
    active = peak = 0

    async def handler(job, task):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {}

    r.register("t", handler)

    async def go():
        await r.start(job.id)
        await r.wait(job.id)

    run(go())
    assert peak == 2 and store.get_job(job.id).status == "done"


def test_rate_limit_pauses_then_resumes(store):
    object.__setattr__(settings, "rate_limit_wait", 0.2)
    job = new_job(store, 2)
    r = JobRunner()
    state = {"limited": False}
    statuses = []

    async def handler(job, task):
        if not state["limited"]:
            state["limited"] = True
            raise LLMRateLimited("usage limit")
        return {}

    r.register("t", handler)
    r.on_event(lambda job, event, payload: statuses.append(event))

    async def go():
        await r.start(job.id)
        for _ in range(100):
            if store.get_job(job.id).status == "paused":
                break
            await asyncio.sleep(0.01)
        paused = store.get_job(job.id)
        await r.wait(job.id)
        return paused

    paused = run(go())
    assert paused.status == "paused" and paused.resume_at
    j = store.get_job(job.id)
    assert j.status == "done" and j.failed_tasks == 0
    assert all(t.attempts <= 1 for t in store.get_tasks(job.id))
    assert "paused" in statuses and "resumed" in statuses


def test_resume_requeues_unfinished_tasks(store):
    job = new_job(store, 3)
    tasks = store.get_tasks(job.id)
    store.update_task(tasks[0].id, status="done", output={"old": True})
    store.increment_job(job.id, "done_tasks")
    store.update_task(tasks[1].id, status="running")
    store.update_job(job.id, status="running")
    seen = []

    import agentorium.docpipe.jobs as jobs

    r = JobRunner()

    async def handler(job, task):
        seen.append(task.section_id)
        return {}

    r.register("t", handler)

    async def go():
        old = jobs.runner
        jobs.runner = r
        try:
            started = await resume_jobs()
            await r.wait(job.id)
            return started
        finally:
            jobs.runner = old

    started = run(go())
    assert started == [job.id]
    assert sorted(seen) == ["s0001", "s0002"]
    j = store.get_job(job.id)
    assert j.status == "done" and j.done_tasks == 3


def test_cancel_stops_remaining_tasks(store):
    object.__setattr__(settings, "llm_concurrency", 1)
    job = new_job(store, 5)
    r = JobRunner()
    seen = []

    async def handler(job, task):
        seen.append(task.section_id)
        await asyncio.sleep(0.05)
        return {}

    r.register("t", handler)

    async def go():
        await r.start(job.id)
        await asyncio.sleep(0.07)
        await r.cancel(job.id)
        await r.wait(job.id)

    run(go())
    j = store.get_job(job.id)
    assert j.status == "cancelled" and j.finished_at
    assert len(seen) < 5
    assert all(t.status != "running" for t in store.get_tasks(job.id))


def test_finalizer_called_once(store):
    job = new_job(store, 2)
    r = JobRunner()
    calls = []

    async def handler(job, task):
        return {}

    async def fin(job):
        calls.append(job.id)
        assert store.get_job(job.id).status == "finalizing"

    r.register("t", handler)
    r.register_finalizer("review", fin)

    async def go():
        await r.start(job.id)
        await r.wait(job.id)

    run(go())
    assert calls == [job.id] and store.get_job(job.id).status == "done"


def test_finalizer_error_makes_job_partial(store):
    job = new_job(store, 1)
    r = JobRunner()

    async def handler(job, task):
        return {}

    async def fin(job):
        raise RuntimeError("consistency failed")

    r.register("t", handler)
    r.register_finalizer("review", fin)

    async def go():
        await r.start(job.id)
        await r.wait(job.id)

    run(go())
    j = store.get_job(job.id)
    assert j.status == "partial" and "consistency failed" in j.error


def test_unknown_task_kind_fails_task(store):
    job = new_job(store, 1, kind="nope")
    r = JobRunner()

    async def go():
        await r.start(job.id)
        await r.wait(job.id)

    run(go())
    assert store.get_job(job.id).status == "partial"


def test_findings_and_facts_roundtrip(store):
    job = new_job(store, 1)
    t = store.get_tasks(job.id)[0]
    store.add_findings([dict(job_id=job.id, task_id=t.id, version_id=job.version_id, block_id="b00000",
                             other_block_id=None, category="language", severity="low", quote="q",
                             description="d", suggestion="s", confidence=0.7, status="valid", validator_note=None)])
    store.add_findings([dict(job_id=job.id, task_id=t.id, version_id=job.version_id, block_id="b00000",
                             other_block_id=None, category="substance", severity="high", quote="q",
                             description="d2", suggestion="", confidence=0.4, status="rejected", validator_note="x")])
    assert len(store.get_findings(job.id)) == 2
    valid = store.get_findings(job.id, status="valid")
    assert len(valid) == 1 and valid[0].category == "language" and valid[0].id
    store.add_facts([dict(job_id=job.id, version_id=job.version_id, block_id="b00000", kind="number",
                          key="jumlah responden", value="120", unit="orang", quote="120 orang")])
    f = store.get_facts(job.id)
    assert f[0].key == "jumlah responden" and f[0].value == "120"
    assert [j.id for j in store.list_jobs(job.version_id)] == [job.id]


def test_server_startup_resumes_jobs(store):
    import time

    from fastapi.testclient import TestClient

    import agentorium.docpipe.jobs as jobs
    from agentorium.server import app

    job = new_job(store, 2, kind="startup_test")
    store.update_job(job.id, status="running")

    async def handler(job, task):
        return {}

    jobs.runner.register("startup_test", handler)
    with TestClient(app):
        for _ in range(100):
            if store.get_job(job.id).status == "done":
                break
            time.sleep(0.02)
    assert store.get_job(job.id).status == "done"


def test_required_preparer_failure_fails_job(store):
    job = new_job(store, 2)
    r = JobRunner()
    ran = []
    r.register("t", lambda job, task: ran.append(task.id) or asyncio.sleep(0, {}))

    async def prep(job):
        raise RuntimeError("bahan tidak terbaca")

    r.register_preparer("review", prep, required=True)

    async def go():
        await r.start(job.id)
        await r.wait(job.id)

    run(go())
    j = store.get_job(job.id)
    assert j.status == "failed" and "bahan tidak terbaca" in j.error and ran == []


def test_rate_limited_preparer_pauses_then_retries(store):
    object.__setattr__(settings, "rate_limit_wait", 0.05)
    job = new_job(store, 1)
    r = JobRunner()
    calls = []

    async def handler(job, task):
        return {}

    async def prep(job):
        calls.append(1)
        if len(calls) == 1:
            raise LLMRateLimited("usage limit")

    r.register("t", handler)
    r.register_preparer("review", prep, required=True)
    events = []
    r.on_event(lambda job, event, payload: events.append(event))

    async def go():
        await r.start(job.id)
        await r.wait(job.id)

    run(go())
    assert len(calls) == 2 and "paused" in events and store.get_job(job.id).status == "done"


def test_cancel_refused_while_finalizing(store):
    job = new_job(store, 1)
    store.update_job(job.id, status="finalizing")
    assert run(JobRunner().cancel(job.id)) is False
    assert store.get_job(job.id).status == "finalizing"
