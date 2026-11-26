"""REST API for the document pipeline, mounted at /api/docs."""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..config import settings
from .index import schedule_index
from .models import Version
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
    if not get_store().delete_document(doc_id):
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
    if variant not in ("current", "original"):
        raise HTTPException(400, "variant harus 'current' atau 'original'")
    path = version_file(get_store(), v, variant)
    if not path.exists():
        raise HTTPException(404, "file versi tidak ditemukan")
    doc = get_store().get_document(v.document_id)
    suffix = " (asli)" if variant == "original" and v.original_path else ""
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
