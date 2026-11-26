"""Automatic revision: turn approved review findings into a new DOCX version (tracked + clean)."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .edits import BlockEdit, apply_block_edits
from .editloop import edit_loop
from .jobs import ACTIVE, runner
from .models import Job, Task
from . import review
from .review import _hooks, pick_agents, plan_tasks
from .service import create_revised_version, version_file
from .store import get_store

log = logging.getLogger(__name__)
EDITABLE = ("language", "consistency", "structure")
MUTATING_JOBS = ("revision", "instructed_revision")


def require_docx(version) -> None:
    if version is None or version.status != "ready":
        raise ValueError("versi belum siap")
    if Path(version.filename).suffix.lower() != ".docx":
        raise ValueError("revisi butuh dokumen DOCX (versi ini PDF)")


async def start_revision(review_job_id: str, categories: list[str] | None = None) -> Job:
    store = get_store()
    rjob = store.get_job(review_job_id)
    if rjob is None or rjob.type != "review":
        raise ValueError("job review tidak ditemukan")
    if rjob.status not in ("done", "partial"):
        raise ValueError("review belum selesai")
    version = store.get_version(rjob.version_id)
    require_docx(version)
    pick_agents()
    cats = [c for c in (categories or EDITABLE) if c in EDITABLE] or list(EDITABLE)
    findings = [f for f in store.get_findings(rjob.id, status="valid") if f.category in cats]
    wanted = {f.block_id for f in findings} | {f.other_block_id for f in findings if f.other_block_id}
    plan = [(sid, ids) for sid, ids in plan_tasks(store.get_sections(version.id), store.get_blocks(version.id))
            if wanted & set(ids)]
    job = store.create_job(version.document_id, version.id, "revision",
                           {"review_job_id": rjob.id, "categories": cats})
    store.add_tasks(job.id, [(sid, ids, "revision_section") for sid, ids in plan])
    await runner.start(job.id)
    return store.get_job(job.id)


def _finding_text(f) -> str:
    return f"{f.description} | kutipan: {f.quote}" + (f" | saran: {f.suggestion}" if f.suggestion else "")


async def revision_task(job: Job, task: Task) -> dict:
    store = get_store()
    supervisor, workers = pick_agents()
    worker = workers[task.ordinal % len(workers)]
    section = next(s for s in store.get_sections(job.version_id) if s.id == task.section_id)
    ids = set(task.block_ids)
    blocks = [b for b in store.get_blocks(job.version_id, section_id=task.section_id) if b.id in ids]
    blocks_by_id = {b.id: b for b in blocks}
    cats = job.params.get("categories") or list(EDITABLE)
    findings = [f for f in store.get_findings(job.params["review_job_id"], status="valid")
                if f.category in cats and f.block_id in blocks_by_id]
    basis = {f.id: _finding_text(f) for f in findings}
    fdicts = [{"id": f.id, "block_id": f.block_id, "category": f.category, "description": f.description,
               "quote": f.quote, "suggestion": f.suggestion} for f in findings]
    doc_texts = [b.text for b in store.get_blocks(job.version_id)]
    hooks = _hooks()
    if hooks:
        hooks.task_started(job, task, worker, supervisor, section)
    from . import prompts

    out = await edit_loop(
        wllm=review.make_llm(worker), sllm=review.make_llm(supervisor), worker_system=prompts.revision_worker_system(worker.name),
        user_for=lambda fb: prompts.revision_worker_user(section.path, blocks, fdicts, fb),
        section_path=section.path, blocks_by_id=blocks_by_id, basis=basis, doc_texts=doc_texts,
        purpose="doc_revision", hooks=hooks, job=job, task=task, worker=worker, supervisor=supervisor)
    handled = {s for e in out.approved + out.rejected + out.needs_human for s in e["source_ids"]}
    skipped = {s["source_id"]: s["reason"] for s in out.skipped if s["source_id"] in basis}
    for fid in basis:
        if fid not in handled and fid not in skipped:
            skipped[fid] = "tidak ada edit dari worker"
    if hooks:
        hooks.task_finished(job, task, worker, supervisor, len(out.approved))
    return {"edits": out.approved, "rejected": out.rejected, "needs_human": out.needs_human,
            "skipped": [{"source_id": k, "reason": v} for k, v in skipped.items()], "worker": worker.id}


def _change(job, block, e: dict, status: str, version_id: str, note: str | None = None, kind="finding") -> dict:
    return {"version_id": version_id, "job_id": job.id, "block_uid": block.uid, "block_id": block.id,
            "old_text": block.text, "new_text": e.get("new_text", ""), "reason": e.get("reason", ""),
            "source_kind": kind, "source_ids": e.get("source_ids", []), "status": status, "note": note}


async def apply_job_edits(job: Job, *, source: str, kind: str, extra_changes: list[dict] | None = None,
                          note: str | None = None) -> str | None:
    """Apply every approved edit of the job's tasks; store the new version and the change log. Returns the new
    version id or None when nothing could be applied."""
    from .index import schedule_index

    store = get_store()
    base = store.get_version(job.version_id)
    blocks = {b.id: b for b in store.get_blocks(base.id)}
    approved, rows = [], []
    for t in store.get_tasks(job.id):
        o = t.output or {}
        approved += [e for e in o.get("edits", []) if e["block_id"] in blocks]
        rows += [_change(job, blocks[e["block_id"]], e, "rejected", base.id, e.get("note"), kind)
                 for e in o.get("rejected", []) if e["block_id"] in blocks]
        rows += [_change(job, blocks[e["block_id"]], e, "needs_human", base.id, e.get("note"), kind)
                 for e in o.get("needs_human", []) if e["block_id"] in blocks]
    edits = [BlockEdit(e["block_id"], blocks[e["block_id"]].text, e["new_text"], e["reason"], e["source_ids"])
             for e in approved]
    tracked, clean, results = await asyncio.to_thread(apply_block_edits, version_file(store, base).read_bytes(), edits)
    new_id = None
    if clean is not None:
        nv = await asyncio.to_thread(create_revised_version, store, base.id, tracked, clean, source=source,
                                     note=note or f"Revisi dari v{base.version_no}")
        new_id = nv.id
        schedule_index(nv.id)
    for e, r in zip(approved, results):
        if r.status == "applied":
            rows.append(_change(job, blocks[e["block_id"]], e, "applied", new_id, None, kind))
        else:
            rows.append(_change(job, blocks[e["block_id"]], e, "needs_human", base.id,
                                f"gagal diterapkan ke DOCX: {r.error}", kind))
    rows += extra_changes or []
    if rows:
        store.add_changes(rows)
    if new_id:
        fresh = store.get_job(job.id)
        store.update_job(job.id, params={**fresh.params, "result_version_id": new_id})
    return new_id


async def _finalize_revision(job: Job) -> None:
    store = get_store()
    base = store.get_version(job.version_id)
    blocks = {b.id: b for b in store.get_blocks(base.id)}
    findings = {f.id: f for f in store.get_findings(job.params["review_job_id"], status="valid")}
    extra = []
    for t in store.get_tasks(job.id):
        for s in (t.output or {}).get("skipped", []):
            f = findings.get(s["source_id"])
            if f and f.block_id in blocks:
                extra.append(_change(job, blocks[f.block_id], {"source_ids": [f.id], "reason": f.description},
                                     "needs_human", base.id, f"tidak diubah otomatis: {s['reason']}"))
    for f in findings.values():
        if f.category == "substance" and f.block_id in blocks:
            extra.append(_change(job, blocks[f.block_id], {"source_ids": [f.id], "reason": f.description},
                                 "suggestion", base.id, f.suggestion or f.description))
    await apply_job_edits(job, source="revision", kind="finding", extra_changes=extra)


runner.register("revision_section", revision_task)
runner.register_finalizer("revision", _finalize_revision)
