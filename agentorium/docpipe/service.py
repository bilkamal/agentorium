"""Ingest uploads into versions, and restore old versions (rollback)."""
from __future__ import annotations

import shutil
from pathlib import Path

from ..config import settings
from .models import Document, ParsedDoc, Version
from .parse import ParseError, accept_tracked_changes, parse_docx, parse_pdf
from .store import Store
from .versions import assign_uids

ALLOWED_EXTENSIONS = {".docx", ".pdf"}
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
TRACKED_WARNING = "tracked_changes_accepted"
NO_TEXT_WARNING = "no_text_extracted"


class IngestError(Exception):
    """Upload rejected; message is safe to show to the user."""


def _abs(rel: str) -> Path:
    return settings.data_dir / rel


def ingest(store: Store, *, filename: str, data: bytes, document_id: str | None = None, title: str | None = None,
           label: str = "", source: str = "upload", note: str = "") -> tuple[Document, Version]:
    name = Path(filename or "upload").name
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise IngestError(f"Format tidak didukung ({ext or 'tanpa ekstensi'}). Gunakan: {', '.join(sorted(ALLOWED_EXTENSIONS))}")
    if len(data) > MAX_UPLOAD_BYTES:
        raise IngestError(f"File terlalu besar (maks {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")

    created = document_id is None
    if created:
        doc = store.create_document(title or Path(name).stem, label)
    else:
        doc = store.get_document(document_id)
        if doc is None:
            raise IngestError("Dokumen tidak ditemukan")
    prev = store.latest_ready_version(doc.id)
    version = store.add_version(doc.id, source=source, filename=name,
                                parent_version_id=prev.id if prev else None, note=note)
    written: list[Path] = []
    try:
        path = _abs(version.file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append(path)
        warnings: list[str] = []
        original_rel = None
        if ext == ".docx":
            accepted = path.with_suffix(".accepted.docx")
            if accept_tracked_changes(path, accepted):
                written.append(accepted)
                original_rel = version.file_path[: -len(ext)] + ".original.docx"
                path.rename(_abs(original_rel))
                written.append(_abs(original_rel))
                accepted.rename(path)
                warnings.append(TRACKED_WARNING)
            parsed = parse_docx(path)
        else:
            parsed = parse_pdf(path)
        if not parsed.blocks:
            warnings.append(NO_TEXT_WARNING)
        assign_uids(parsed.blocks, store.get_blocks(prev.id) if prev else None)
        store.save_structure(version.id, parsed)
        version = store.update_version(version.id, status="ready", warnings=warnings + parsed.warnings,
                                       original_path=original_rel)
    except Exception as exc:
        for f in written:
            f.unlink(missing_ok=True)
        store.delete_version(version.id)
        if created:
            store.delete_document(doc.id)
        if isinstance(exc, ParseError):
            raise IngestError(str(exc)) from exc
        raise
    return store.get_document(doc.id), version


def restore(store: Store, version_id: str) -> Version:
    old = store.get_version(version_id)
    if old is None or old.status != "ready":
        raise IngestError("Versi tidak ditemukan atau belum siap")
    new = store.add_version(old.document_id, source="rollback", filename=old.filename,
                            parent_version_id=old.id, note=f"Rollback ke v{old.version_no}")
    dst = _abs(new.file_path)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_abs(old.file_path), dst)
        store.save_structure(new.id, ParsedDoc(sections=store.get_sections(old.id), blocks=store.get_blocks(old.id)))
        return store.update_version(new.id, status="ready")
    except Exception:
        dst.unlink(missing_ok=True)
        store.delete_version(new.id)
        raise


def create_revised_version(store: Store, base_version_id: str, tracked: bytes, clean: bytes, *, source: str,
                           note: str) -> Version:
    """New version from an edited copy of `base`: clean file (changes accepted) + tracked copy for Word."""
    base = store.get_version(base_version_id)
    if base is None or base.status != "ready":
        raise IngestError("Versi dasar tidak ditemukan atau belum siap")
    new = store.add_version(base.document_id, source=source, filename=base.filename, parent_version_id=base.id,
                            note=note)
    path = _abs(new.file_path)
    tracked_rel = new.file_path[: -len(path.suffix)] + ".tracked.docx"
    written: list[Path] = []
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(clean)
        written.append(path)
        _abs(tracked_rel).write_bytes(tracked)
        written.append(_abs(tracked_rel))
        parsed = parse_docx(path)
        prev_blocks = store.get_blocks(base.id)
        if len(prev_blocks) == len(parsed.blocks):  # edits replace text in place: same block layout
            for b, old in zip(parsed.blocks, prev_blocks):
                b.uid = old.uid
        else:
            assign_uids(parsed.blocks, prev_blocks)
        store.save_structure(new.id, parsed)
        return store.update_version(new.id, status="ready", tracked_path=tracked_rel, warnings=parsed.warnings)
    except Exception:
        for f in written:
            f.unlink(missing_ok=True)
        store.delete_version(new.id)
        raise


def version_file(store: Store, version: Version, variant: str = "current") -> Path:
    if variant == "original" and version.original_path:
        return _abs(version.original_path)
    if variant == "tracked":
        return _abs(version.tracked_path) if version.tracked_path else _abs(version.file_path + ".missing")
    return _abs(version.file_path)
