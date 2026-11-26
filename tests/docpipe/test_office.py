import asyncio

import pytest

from agentorium.docpipe import office, review
from agentorium.docpipe.jobs import runner
from agentorium.docpipe.office import OfficeBridge
from agentorium.docpipe.review import start_review
from agentorium.docpipe.service import ingest
from agentorium.docpipe.store import get_store
from agentorium.orchestrator import Orchestrator

from .fixtures import make_basic_docx
from .test_review import ScriptedLLM, team  # noqa: F401


@pytest.fixture
def orch(tmp_settings):
    o = Orchestrator()
    old = office.bridge
    office.install(o)
    yield o
    office.bridge = old


@pytest.fixture
def setup(tmp_settings, tmp_path, monkeypatch, fake_embedder):
    store = get_store()
    _, v = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())
    calls, replies = [], {"worker": [], "supervisor": []}
    monkeypatch.setattr(review, "make_llm", ScriptedLLM(replies, calls).for_agent)
    n = len(review.plan_tasks(store.get_sections(v.id), store.get_blocks(v.id)))
    b = store.get_blocks(v.id)[0]
    replies["worker"] += [{"findings": [{"block_id": b.id, "category": "language", "severity": "low",
                                         "quote": b.text[:8], "description": "x", "suggestion": "",
                                         "confidence": 0.5}], "facts": []}] + [{"findings": [], "facts": []}] * (n - 1)
    replies["supervisor"].append({"verdicts": [{"index": 0, "verdict": "approve", "feedback": ""}]})
    return store, v


def run_review(v):
    async def go():
        job = await start_review(v.id, None)
        await runner.wait(job.id)
        return job
    return asyncio.run(go())


def test_session_created_with_registry_agents(orch, setup, team):  # noqa: F811
    store, v = setup
    job = run_review(v)
    sid = store.get_job(job.id).params["session_id"]
    s = orch.get(sid)
    assert s.kind == "external" and s.title.startswith("Review: ") and s.title.endswith(" v1")
    assert {a["id"] for a in s.roster} == {"sup", "w1", "w2"}
    plan = next(e for e in s.events if e["type"] == "plan")
    assert len(plan["plan"]["steps"]) == store.get_job(job.id).total_tasks


def test_events_emitted_during_review(orch, setup, team):  # noqa: F811
    store, v = setup
    job = run_review(v)
    s = orch.get(store.get_job(job.id).params["session_id"])
    types = [e["type"] for e in s.events]
    assert "agent_status" in types and "flow" in types and "step" in types
    assert any(e["type"] == "flow" and e["source"] == "sup" for e in s.events)
    assert any(e["type"] == "flow" and e["target"] == "sup" for e in s.events)  # findings submitted
    assert s.stage == "done"
    assert any(e["type"] == "note" for e in s.events)
    done_steps = {e["step_id"] for e in s.events if e["type"] == "step" and e["status"] == "done"}
    assert len(done_steps) == store.get_job(job.id).total_tasks


def test_bridge_errors_do_not_break_review(orch, setup, team, monkeypatch):  # noqa: F811
    store, v = setup

    def boom(*a, **k):
        raise RuntimeError("visualiser down")

    monkeypatch.setattr(orch, "push_external", boom)
    monkeypatch.setattr(orch, "create_session", boom)
    job = run_review(v)
    assert store.get_job(job.id).status == "done"


def test_install_is_idempotent(orch):
    n = len(runner._listeners)
    office.install(orch)
    assert len(runner._listeners) == n


def test_server_startup_installs_bridge_and_review_handler(tmp_settings):
    from fastapi.testclient import TestClient

    import agentorium.server as server

    old = office.bridge
    try:
        office.bridge = None
        runner._handlers.pop("section_review", None)
        with TestClient(server.app):
            assert office.bridge is not None and office.bridge.orch is server.orchestrator
            assert "section_review" in runner._handlers
    finally:
        office.bridge = old
        runner.register("section_review", review.review_task)
