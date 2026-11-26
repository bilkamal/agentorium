"""Per-section document review: worker → deterministic validator → supervisor (max N revise rounds)."""
from __future__ import annotations

import asyncio
import logging

from ..config import settings
from ..llm import LLMClient
from ..registry import AgentDef, registry
from . import prompts
from .jobs import runner
from .models import Block, Job, Section, Task
from .retrieval import search
from .store import get_store
from .validate import CATEGORIES, validate_fact, validate_finding

log = logging.getLogger(__name__)
MAX_TASK_CHARS = 24000
CONTEXT_K = 6


def make_llm(agent: AgentDef):
    return LLMClient(agent)


def plan_tasks(sections: list[Section], blocks: list[Block]) -> list[tuple[str, list[str]]]:
    by_section: dict[str, list[Block]] = {}
    for b in blocks:
        by_section.setdefault(b.section_id, []).append(b)
    out: list[tuple[str, list[str]]] = []
    for s in sections:
        group: list[str] = []
        size = 0
        for b in by_section.get(s.id, []):
            if group and size + len(b.text) > MAX_TASK_CHARS:
                out.append((s.id, group))
                group, size = [], 0
            group.append(b.id)
            size += len(b.text)
        if group:
            out.append((s.id, group))
    return out


def pick_agents() -> tuple[AgentDef, list[AgentDef]]:
    sups, workers = registry.by_role("supervisor"), registry.workers()
    if not sups:
        raise RuntimeError("Tim belum punya supervisor — tambahkan di ⚙ Pengaturan tim")
    if not workers:
        raise RuntimeError("Tim belum punya worker — tambahkan di ⚙ Pengaturan tim")
    return sups[0], workers


def _hooks():
    from . import office

    return office.bridge


def _row(job: Job, task: Task, f: dict, status: str, note: str | None = None) -> dict:
    return {**f, "job_id": job.id, "task_id": task.id, "version_id": job.version_id, "other_block_id": None,
            "status": status, "validator_note": note}


def _validate(raw_list, blocks_by_id, categories, job, task, rejected: list) -> list[dict]:
    ok = []
    for raw in raw_list if isinstance(raw_list, list) else []:
        f, reason = validate_finding(raw, blocks_by_id)
        if f is not None and f["category"] not in categories:
            f, reason = None, f"category {f['category']!r} not requested"
        if f is None:
            if isinstance(raw, dict) and raw.get("block_id") in blocks_by_id:
                safe = {k: str(raw.get(k) or "")[:2000] for k in ("block_id", "quote", "description", "suggestion")}
                cat = str(raw.get("category") or "")
                sev = str(raw.get("severity") or "")
                rejected.append(_row(job, task, {**safe, "category": cat if cat in CATEGORIES else "language",
                                                 "severity": sev if sev in ("low", "medium", "high") else "low",
                                                 "confidence": 0.0}, "rejected", f"validator: {reason}"))
            continue
        ok.append(f)
    return ok


async def review_task(job: Job, task: Task) -> dict:
    store = get_store()
    supervisor, workers = pick_agents()
    worker = workers[task.ordinal % len(workers)]
    categories = [c for c in (job.params.get("categories") or CATEGORIES) if c in CATEGORIES] or list(CATEGORIES)
    sections = store.get_sections(job.version_id)
    section = next(s for s in sections if s.id == task.section_id)
    wanted = set(task.block_ids)
    blocks = [b for b in store.get_blocks(job.version_id, section_id=task.section_id) if b.id in wanted]
    blocks_by_id = {b.id: b for b in blocks}
    doc = store.get_document(job.document_id)
    outline = [("  " * (s.level - 1)) + s.heading for s in sections]
    query = f"{section.path} " + " ".join(b.text for b in blocks)[:300]
    hits = await asyncio.to_thread(search, store, job.version_id, query, k=CONTEXT_K, exclude_section_id=section.id)
    hooks = _hooks()

    wllm, sllm = make_llm(worker), make_llm(supervisor)
    wsys = prompts.worker_system(worker.name, categories)
    rejected: list[dict] = []
    if hooks:
        hooks.task_started(job, task, worker, supervisor, section)
    reply = await wllm.complete_json(wsys, [{"role": "user", "content": prompts.worker_user(
        doc.title if doc else "", outline, section.path, blocks, hits)}], prompts.WORKER_SCHEMA, purpose="doc_review")
    candidates = _validate(reply.get("findings"), blocks_by_id, categories, job, task, rejected)
    facts = [f for f in (validate_fact(r, blocks_by_id) for r in reply.get("facts") or []) if f]

    valid: list[dict] = []
    needs_human: list[dict] = []
    rounds = 0
    while candidates:
        if hooks:
            hooks.submitted(job, task, worker, supervisor, len(candidates))
        verdict_reply = await sllm.complete_json(
            prompts.SUPERVISOR_SYSTEM,
            [{"role": "user", "content": prompts.supervisor_user(section.path, blocks_by_id, candidates)}],
            prompts.SUPERVISOR_SCHEMA, purpose="doc_review_verdict")
        verdicts = {}
        for v in verdict_reply.get("verdicts") or []:
            if isinstance(v, dict) and isinstance(v.get("index"), int):
                verdicts.setdefault(v["index"], v)
        to_revise: list[tuple[dict, str]] = []
        for i, f in enumerate(candidates):
            v = verdicts.get(i)
            kind = v.get("verdict") if v else None
            feedback = str(v.get("feedback") or "") if v else ""
            if kind == "approve":
                valid.append(_row(job, task, f, "valid"))
            elif kind == "reject":
                rejected.append(_row(job, task, f, "rejected", f"supervisor: {feedback}".strip()))
            elif kind == "revise":
                to_revise.append((f, feedback))
            else:  # fail-closed: no verdict is never an approval
                needs_human.append(_row(job, task, f, "needs_human", "supervisor gave no verdict"))
        if not to_revise:
            break
        if rounds >= settings.max_revisions:
            needs_human += [_row(job, task, f, "needs_human", f"supervisor: {fb}".strip()) for f, fb in to_revise]
            break
        rounds += 1
        reply = await wllm.complete_json(wsys, [{"role": "user", "content": prompts.worker_user(
            doc.title if doc else "", outline, section.path, blocks, hits, feedback=to_revise)}],
            prompts.WORKER_SCHEMA, purpose="doc_review_revise")
        candidates = _validate(reply.get("findings"), blocks_by_id, categories, job, task, rejected)

    store.add_findings(valid + needs_human + rejected)
    if facts:
        store.add_facts([{**f, "job_id": job.id, "version_id": job.version_id} for f in facts])
    if hooks:
        hooks.task_finished(job, task, worker, supervisor, len(valid))
    return {"findings": len(valid), "rejected": len(rejected), "needs_human": len(needs_human),
            "facts": len(facts), "worker": worker.id}


runner.register("section_review", review_task)


async def start_review(version_id: str, categories: list[str] | None = None) -> Job:
    """Create a review job for a ready version (indexing it first if needed) and start it."""
    from .index import index_version

    store = get_store()
    version = store.get_version(version_id)
    if version is None or version.status != "ready":
        raise ValueError("versi belum siap direview")
    pick_agents()  # fail fast when the team is incomplete
    if version.index_status not in ("ready", "fts_only"):
        await asyncio.to_thread(index_version, store, version_id)
    cats = [c for c in (categories or CATEGORIES) if c in CATEGORIES] or list(CATEGORIES)
    plan = plan_tasks(store.get_sections(version_id), store.get_blocks(version_id))
    job = store.create_job(version.document_id, version_id, "review", {"categories": cats})
    store.add_tasks(job.id, [(sid, bids, "section_review") for sid, bids in plan])
    await runner.start(job.id)
    return store.get_job(job.id)


async def _finalize_review(job: Job) -> None:
    from .consistency import check_consistency

    await check_consistency(job)


runner.register_finalizer("review", _finalize_review)
