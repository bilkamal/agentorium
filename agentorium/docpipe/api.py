"""REST API for the document pipeline, mounted at /api/docs."""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

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
    mine = [j for j in store.list_jobs(statuses=ACTIVE) if j.document_id == doc_id]
    if any(j.status == "finalizing" for j in mine):
        raise HTTPException(409, "perubahan sedang diterapkan ke dokumen — coba lagi sebentar")
    for job in mine:
        await runner.cancel(job.id)
    sessions = [j.params.get("session_id") for j in store.list_jobs() if j.document_id == doc_id]
    if not store.delete_document(doc_id):
        raise HTTPException(404, "dokumen tidak ditemukan")
    shutil.rmtree(settings.data_dir / "docs" / doc_id, ignore_errors=True)
    from . import office

    if office.bridge is not None:  # the office sessions that showed this document's jobs go with it
        for sid in filter(None, sessions):
            office.bridge.orch.delete(sid)
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


def _compare_pair(version_id: str, base: str):
    v = _version_or_404(version_id)
    b = _version_or_404(base)
    if v.document_id != b.document_id:
        raise HTTPException(400, "kedua versi harus dari dokumen yang sama")
    if v.status != "ready" or b.status != "ready":
        raise HTTPException(409, "versi belum siap")
    return v, b


@router.get("/versions/{base}/compare/{version_id}")
async def compare(base: str, version_id: str):
    from .compare import block_diff

    v, b = _compare_pair(version_id, base)
    blocks = await asyncio.to_thread(block_diff, get_store(), b.id, v.id)
    return {"base": asdict(b), "version": asdict(v), "blocks": blocks}


@router.get("/versions/{base}/compare/{version_id}/redline")
async def compare_docx(base: str, version_id: str):
    from .compare import redline_docx

    v, b = _compare_pair(version_id, base)
    try:
        data = await asyncio.to_thread(redline_docx, get_store(), b.id, v.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    doc = get_store().get_document(v.document_id)
    name = f"{doc.title if doc else 'dokumen'} v{b.version_no} banding v{v.version_no}.docx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": _attachment(name)})


def _attachment(name: str) -> str:
    from urllib.parse import quote

    ascii_name = name.encode("ascii", "replace").decode().replace('"', "'")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


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
    return {"job": _job_json(job)}


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
    return {"job": _job_json(job), "tasks": tasks, "counts": counts}


def _job_json(job: Job) -> dict:
    return {**asdict(job), "result_version_id": job.params.get("result_version_id")}


def _start_error(exc: Exception) -> HTTPException:
    from .revise import Conflict

    return HTTPException(409 if isinstance(exc, Conflict) else 400, str(exc))  # RuntimeError: no usable team


@router.post("/jobs/{job_id}/revisions", status_code=202)
async def create_revision(job_id: str, body: dict | None = Body(default=None)):
    from .revise import start_revision

    _job_or_404(job_id)
    categories = (body or {}).get("categories")
    if categories is not None and not isinstance(categories, list):
        raise HTTPException(422, "categories harus berupa daftar")
    try:
        job = await start_revision(job_id, categories)
    except (ValueError, RuntimeError) as exc:
        raise _start_error(exc) from exc
    return {"job": _job_json(job)}


@router.post("/versions/{version_id}/instructed-revisions", status_code=202)
async def create_instructed_revision(version_id: str, notes: str = Form(""), use_comments: bool = Form(False),
                                     files: list[UploadFile] = File(default=[])):
    from .instructed import start_instructed_revision

    _version_or_404(version_id)
    loaded = [((f.filename or "bahan"), await _read_upload(f)) for f in files or []]
    try:
        job = await start_instructed_revision(version_id, notes=notes, files=loaded, use_comments=use_comments)
    except (ValueError, RuntimeError) as exc:
        raise _start_error(exc) from exc
    return {"job": _job_json(job)}


@router.post("/versions/{version_id}/updates", status_code=202)
async def create_update(version_id: str, period_label: str = Form(""), notes: str = Form(""),
                        files: list[UploadFile] = File(default=[])):
    from .update import start_update

    _version_or_404(version_id)
    loaded = [((f.filename or "data"), await _read_upload(f)) for f in files or []]
    try:
        job = await start_update(version_id, period_label=period_label, files=loaded, notes=notes)
    except (ValueError, RuntimeError) as exc:
        raise _start_error(exc) from exc
    return {"job": _job_json(job)}


def _with_location(store, version_id: str, rows: list[dict], block_key: str) -> list[dict]:
    blocks = {b.id: b for b in store.get_blocks(version_id)}
    paths = {s.id: s.path for s in store.get_sections(version_id)}
    for r in rows:
        ids = r[block_key] if isinstance(r[block_key], list) else [r[block_key]]
        b = next((blocks[i] for i in ids if i in blocks), None)
        r["section_id"] = b.section_id if b else None
        r["heading_path"] = paths.get(b.section_id) if b else None
    return rows


@router.get("/jobs/{job_id}/changes")
async def job_changes(job_id: str):
    job = _job_or_404(job_id)
    store = get_store()
    return _with_location(store, job.version_id, [asdict(c) for c in store.get_changes(job_id=job_id)], "block_id")


@router.get("/versions/{version_id}/changes")
async def version_changes(version_id: str):
    """Changes that produced this version (applied), plus the log of jobs run on it."""
    v = _version_or_404(version_id)
    store = get_store()
    rows = [asdict(c) for c in store.get_changes(version_id=version_id)]
    base = v.parent_version_id if v.source in ("revision", "instructed_revision") else version_id
    return _with_location(store, base or version_id, rows, "block_id")


@router.get("/jobs/{job_id}/instructions")
async def job_instructions(job_id: str):
    job = _job_or_404(job_id)
    store = get_store()
    rows = [{**asdict(i), "_loc": i.block_ids or i.candidate_block_ids} for i in store.get_instructions(job_id)]
    out = _with_location(store, job.version_id, rows, "_loc")
    for r in out:
        r.pop("_loc")
    return out


@router.get("/jobs/{job_id}/followup.docx")
async def job_followup(job_id: str):
    from .instructed import followup_docx

    job = _job_or_404(job_id)
    if job.type != "instructed_revision":
        raise HTTPException(404, "tabel tindak lanjut hanya untuk revisi dari bahan")
    data = await asyncio.to_thread(followup_docx, job_id)
    doc = get_store().get_document(job.document_id)
    v = get_store().get_version(job.version_id)
    name = f"Tindak lanjut {doc.title if doc else 'dokumen'} v{v.version_no if v else ''}.docx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": _attachment(name)})


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
        raise HTTPException(409, "job sudah selesai atau sedang menerapkan perubahan")
    return {"ok": True}
