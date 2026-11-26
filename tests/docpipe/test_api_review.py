import time

import pytest

from agentorium.config import settings
from fastapi.testclient import TestClient

from agentorium.docpipe import review
from agentorium.docpipe.store import get_store
from agentorium.registry import AgentDef, registry

from .fixtures import make_basic_docx
from .test_review import ScriptedLLM, team  # noqa: F401


class SlowLLM(ScriptedLLM):
    def for_agent(self, agent):
        return SlowLLM({}, [])

    async def complete_json(self, system, messages, schema, *, purpose=""):
        import asyncio

        await asyncio.sleep(0.3)
        return {"findings": [], "facts": []}


@pytest.fixture
def client(tmp_settings, fake_embedder):
    from agentorium.server import app

    with TestClient(app) as c:
        yield c


def upload(c, tmp_path):
    p = make_basic_docx(tmp_path)
    doc = c.post("/api/docs", files={"file": (p.name, p.read_bytes(), "application/octet-stream")}).json()["document"]
    vid = c.get(f"/api/docs/{doc['id']}").json()["versions"][0]["id"]
    return doc["id"], vid


def wait_job(c, job_id, statuses=("done", "partial", "failed", "cancelled")):
    for _ in range(200):
        body = c.get(f"/api/docs/jobs/{job_id}").json()
        if body["job"]["status"] in statuses:
            return body
        time.sleep(0.02)
    raise AssertionError(body)


def script(monkeypatch, store, vid):
    object.__setattr__(settings, "llm_concurrency", 1)  # scripted replies are consumed in task order
    calls, replies = [], {"worker": [], "supervisor": []}
    monkeypatch.setattr(review, "make_llm", ScriptedLLM(replies, calls).for_agent)
    blocks = store.get_blocks(vid)
    n = len(review.plan_tasks(store.get_sections(vid), blocks))
    b = blocks[0]
    good = {"block_id": b.id, "category": "language", "severity": "medium", "quote": b.text[:8],
            "description": "contoh masalah", "suggestion": "perbaikan", "confidence": 0.7}
    bad = {**good, "quote": "teks karangan"}
    replies["worker"] += [{"findings": [good, bad], "facts": []}] + [{"findings": [], "facts": []}] * (n - 1)
    replies["supervisor"].append({"verdicts": [{"index": 0, "verdict": "approve", "feedback": ""}]})
    return b


def test_start_review_returns_202_and_job_completes(client, tmp_path, team, monkeypatch):  # noqa: F811
    _, vid = upload(client, tmp_path)
    script(monkeypatch, get_store(), vid)
    r = client.post(f"/api/docs/versions/{vid}/reviews", json={"categories": ["language"]})
    assert r.status_code == 202
    job = r.json()["job"]
    body = wait_job(client, job["id"])
    assert body["job"]["status"] == "done"
    assert body["counts"] == {"valid": 1, "rejected": 1, "needs_human": 0}
    assert body["tasks"] and all(t["status"] == "done" and t["heading"] for t in body["tasks"])
    assert [j["id"] for j in client.get(f"/api/docs/versions/{vid}/jobs").json()] == [job["id"]]


def test_second_review_while_running_409(client, tmp_path, team, monkeypatch):  # noqa: F811
    _, vid = upload(client, tmp_path)
    monkeypatch.setattr(review, "make_llm", SlowLLM({}, []).for_agent)
    first = client.post(f"/api/docs/versions/{vid}/reviews", json={})
    assert first.status_code == 202, first.text
    assert client.post(f"/api/docs/versions/{vid}/reviews", json={}).status_code == 409
    client.post(f"/api/docs/jobs/{first.json()['job']['id']}/cancel")


def test_missing_agents_400(client, tmp_path):
    _, vid = upload(client, tmp_path)
    old = registry.agents
    registry.agents = [AgentDef(id="w", name="W", role="worker")]
    try:
        r = client.post(f"/api/docs/versions/{vid}/reviews", json={})
        assert r.status_code == 400 and "supervisor" in r.json()["detail"]
    finally:
        registry.agents = old


def test_review_unknown_version_404(client):
    assert client.post("/api/docs/versions/nope/reviews", json={}).status_code == 404
    assert client.get("/api/docs/jobs/nope").status_code == 404


def test_findings_filter_and_block_text(client, tmp_path, team, monkeypatch):  # noqa: F811
    _, vid = upload(client, tmp_path)
    b = script(monkeypatch, get_store(), vid)
    job = client.post(f"/api/docs/versions/{vid}/reviews", json={}).json()["job"]
    wait_job(client, job["id"])
    allf = client.get(f"/api/docs/jobs/{job['id']}/findings").json()
    assert len(allf) == 2
    valid = client.get(f"/api/docs/jobs/{job['id']}/findings", params={"status": "valid"}).json()
    assert len(valid) == 1
    f = valid[0]
    assert f["block_text"] == b.text and f["heading_path"] and f["section_id"] == b.section_id
    assert f["other_block_text"] is None
    assert client.get(f"/api/docs/jobs/{job['id']}/findings", params={"category": "structure"}).json() == []


def test_cancel(client, tmp_path, team, monkeypatch):  # noqa: F811
    _, vid = upload(client, tmp_path)
    monkeypatch.setattr(review, "make_llm", SlowLLM({}, []).for_agent)
    job = client.post(f"/api/docs/versions/{vid}/reviews", json={}).json()["job"]
    r = client.post(f"/api/docs/jobs/{job['id']}/cancel")
    assert r.status_code == 200
    assert wait_job(client, job["id"])["job"]["status"] == "cancelled"
    assert client.post(f"/api/docs/jobs/{job['id']}/cancel").status_code == 409


def test_delete_document_cancels_running_review(client, tmp_path, team, monkeypatch):  # noqa: F811
    doc_id, vid = upload(client, tmp_path)
    monkeypatch.setattr(review, "make_llm", SlowLLM({}, []).for_agent)
    job = client.post(f"/api/docs/versions/{vid}/reviews", json={}).json()["job"]
    assert client.delete(f"/api/docs/{doc_id}").status_code == 200
    time.sleep(0.4)
    from agentorium.docpipe.jobs import runner
    assert not runner.is_running(job["id"])
