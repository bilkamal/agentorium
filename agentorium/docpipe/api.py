"""REST API for the document pipeline, mounted at /api/docs."""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..config import settings
from .index import schedule_index
from .jobs import ACTIVE, runner
from .models import Job, Version
from .retrieval import search
from .review import pick_agents, start_review
from .service import MAX_UPLOAD_BYTES, IngestError, ingest, restore, version_file
from .store import get_store

router = APIRouter(prefix="/api/docs", tags=["docs"])


def _version_json(v: Version) -> dict:
    data = asdict(v)
    data["block_count"] = len(get_store().get_blocks(v.id))
    return data


def _version_or_404(version_id: str) -> Version:
    v = get_store().get_version(version_id)
    if v is None:
        raise HTTPException(404, "versi tidak ditemukan")
    return v


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File terlalu besar (maks {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")
    return data


async def _ingest(**kwargs):
    try:
        return await asyncio.to_thread(ingest, get_store(), **kwargs)
    except IngestError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("", status_code=201)
async def upload_document(file: UploadFile = File(...), title: str | None = Form(None), label: str = Form("")):
    data = await _read_upload(file)
    doc, version = await _ingest(filename=file.filename or "upload", data=data, title=(title or "").strip() or None,
                                 label=label.strip())
    schedule_index(version.id)
    return {"document": asdict(doc), "version": _version_json(version)}


@router.get("")
async def list_documents():
    store = get_store()
    out = []
    for d in store.list_documents():
        latest = store.get_version(d.latest_version_id) if d.latest_version_id else None
        out.append({**asdict(d), "latest_version": _version_json(latest) if latest else None,
                    "version_count": len(store.list_versions(d.id))})
    return out


@router.get("/{doc_id}")
async def get_document(doc_id: str):
    store = get_store()
    d = store.get_document(doc_id)
    if d is None:
        raise HTTPException(404, "dokumen tidak ditemukan")
    return {"document": asdict(d), "versions": [_version_json(v) for v in store.list_versions(doc_id)]}


@router.delete("/{doc_id}")
async def delete_document(doc_id: str):
    store = get_store()
    for job in store.list_jobs(statuses=ACTIVE):
        if job.document_id == doc_id:
            await runner.cancel(job.id)
    if not store.delete_document(doc_id):
        raise HTTPException(404, "dokumen tidak ditemukan")
    shutil.rmtree(settings.data_dir / "docs" / doc_id, ignore_errors=True)
    return {"ok": True}


@router.post("/{doc_id}/versions", status_code=201)
async def upload_version(doc_id: str, file: UploadFile = File(...), note: str = Form("")):
    if get_store().get_document(doc_id) is None:
        raise HTTPException(404, "dokumen tidak ditemukan")
    data = await _read_upload(file)
    _, version = await _ingest(filename=file.filename or "upload", data=data, document_id=doc_id, note=note.strip())
    schedule_index(version.id)
    return {"version": _version_json(version)}


@router.get("/versions/{version_id}/outline")
async def outline(version_id: str):
    _version_or_404(version_id)
    store = get_store()
    counts: dict[str, int] = {}
    for b in store.get_blocks(version_id):
        counts[b.section_id] = counts.get(b.section_id, 0) + 1
    return [{**asdict(s), "block_count": counts.get(s.id, 0)} for s in store.get_sections(version_id)]


@router.get("/versions/{version_id}/blocks")
async def blocks(version_id: str, section_id: str | None = None):
    _version_or_404(version_id)
    return [asdict(b) for b in get_store().get_blocks(version_id, section_id=section_id)]


@router.get("/versions/{version_id}/download")
async def download(version_id: str, variant: str = "current"):
    v = _version_or_404(version_id)
    if variant not in ("current", "original", "tracked"):
        raise HTTPException(400, "variant harus 'current', 'original' atau 'tracked'")
    path = version_file(get_store(), v, variant)
    if not path.exists():
        raise HTTPException(404, "file versi tidak ditemukan")
    doc = get_store().get_document(v.document_id)
    suffix = {"original": " (asli)" if v.original_path else "", "tracked": " (tracked)"}.get(variant, "")
    name = f"{doc.title if doc else 'dokumen'} v{v.version_no}{suffix}{Path(path).suffix}"
    return FileResponse(path, filename=name)


@router.post("/versions/{version_id}/restore", status_code=201)
async def restore_version(version_id: str):
    _version_or_404(version_id)
    try:
        version = await asyncio.to_thread(restore, get_store(), version_id)
    except IngestError as exc:
        raise HTTPException(422, str(exc)) from exc
    schedule_index(version.id)
    return {"version": _version_json(version)}


@router.get("/versions/{version_id}/search")
async def search_version(version_id: str, q: str = "", k: int = 8) -> list[dict]:
    store = get_store()
    if store.get_version(version_id) is None:
        raise HTTPException(404, "version not found")
    hits = await asyncio.to_thread(search, store, version_id, q, k=max(1, min(k, 50)))
    return [{**asdict(h.chunk), "score": round(h.score, 5), "fts_rank": h.fts_rank, "vec_rank": h.vec_rank}
            for h in hits]


# ----------------------------------------------------------------- reviews
def _job_or_404(job_id: str) -> Job:
    job = get_store().get_job(job_id)
    if job is None:
        raise HTTPException(404, "job tidak ditemukan")
    return job


@router.post("/versions/{version_id}/reviews", status_code=202)
async def create_review(version_id: str, body: dict | None = Body(default=None)):
    version = _version_or_404(version_id)
    store = get_store()
    if version.status != "ready":
        raise HTTPException(409, "versi belum siap direview")
    if any(j.type == "review" for j in store.list_jobs(version_id, statuses=ACTIVE)):
        raise HTTPException(409, "review untuk versi ini masih berjalan")
    try:
        pick_agents()
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    categories = (body or {}).get("categories")
    if categories is not None and not isinstance(categories, list):
        raise HTTPException(422, "categories harus berupa daftar")
    job = await start_review(version_id, categories)
    return {"job": asdict(job)}


@router.get("/versions/{version_id}/jobs")
async def version_jobs(version_id: str):
    _version_or_404(version_id)
    return [asdict(j) for j in get_store().list_jobs(version_id)]


@router.get("/jobs/{job_id}")
async def job_detail(job_id: str):
    job = _job_or_404(job_id)
    store = get_store()
    sections = {s.id: s for s in store.get_sections(job.version_id)}
    tasks = [{"id": t.id, "ordinal": t.ordinal, "section_id": t.section_id,
              "heading": sections[t.section_id].heading if t.section_id in sections else t.section_id,
              "status": t.status, "attempts": t.attempts, "error": t.error, "output": t.output}
             for t in store.get_tasks(job_id)]
    counts = {"valid": 0, "rejected": 0, "needs_human": 0, **store.count_findings(job_id)}
    return {"job": asdict(job), "tasks": tasks, "counts": counts}


@router.get("/jobs/{job_id}/findings")
async def job_findings(job_id: str, status: str | None = None, category: str | None = None):
    job = _job_or_404(job_id)
    store = get_store()
    blocks = {b.id: b for b in store.get_blocks(job.version_id)}
    paths = {s.id: s.path for s in store.get_sections(job.version_id)}
    out = []
    for f in store.get_findings(job_id, status=status or None, category=category or None):
        b, o = blocks.get(f.block_id), blocks.get(f.other_block_id) if f.other_block_id else None
        out.append({**asdict(f), "section_id": b.section_id if b else None,
                    "heading_path": paths.get(b.section_id) if b else None, "block_text": b.text if b else None,
                    "other_section_id": o.section_id if o else None,
                    "other_heading_path": paths.get(o.section_id) if o else None,
                    "other_block_text": o.text if o else None})
    return out


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    _job_or_404(job_id)
    if not await runner.cancel(job_id):
        raise HTTPException(409, "job sudah selesai")
    return {"ok": True}
