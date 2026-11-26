"""Compare two versions of the same document: per-paragraph diff (by uid) and a Word redline."""
from __future__ import annotations

from pathlib import Path

from .service import version_file


def _pair(store, a_id: str, b_id: str):
    a, b = store.get_version(a_id), store.get_version(b_id)
    if a is None or b is None:
        raise LookupError("versi tidak ditemukan")
    if a.document_id != b.document_id:
        raise ValueError("kedua versi harus dari dokumen yang sama")
    if a.status != "ready" or b.status != "ready":
        raise ValueError("versi belum siap")
    return a, b


def block_diff(store, a_version_id: str, b_version_id: str) -> list[dict]:
    """Blocks of B in order, with A's removed blocks placed after their old predecessor."""
    _pair(store, a_version_id, b_version_id)
    paths = {s.id: s.path for s in store.get_sections(a_version_id)} | {
        s.id: s.path for s in store.get_sections(b_version_id)}
    old = store.get_blocks(a_version_id)
    new = store.get_blocks(b_version_id)
    old_by_uid = {b.uid: b for b in old}
    new_uids = {b.uid for b in new}
    out: list[dict] = []

    def row(status, ob, nb):
        ref = nb or ob
        return {"uid": ref.uid, "status": status, "old_text": ob.text if ob else "", "new_text": nb.text if nb else "",
                "section": paths.get(ref.section_id, ""), "old_block_id": ob.id if ob else None,
                "new_block_id": nb.id if nb else None}

    # removed blocks are emitted right after the last surviving block that preceded them in A
    removed_after: dict[str | None, list] = {}
    prev = None
    for ob in old:
        if ob.uid in new_uids:
            prev = ob.uid
        else:
            removed_after.setdefault(prev, []).append(ob)
    out += [row("removed", ob, None) for ob in removed_after.get(None, [])]
    for nb in new:
        ob = old_by_uid.get(nb.uid)
        if ob is None:
            out.append(row("added", None, nb))
        elif ob.text != nb.text:
            out.append(row("changed", ob, nb))
        out += [row("removed", r, None) for r in removed_after.get(nb.uid, [])]
    return out


def redline_docx(store, a_version_id: str, b_version_id: str) -> bytes:
    """B as tracked changes against A (both must be DOCX)."""
    from adeu.compare import build_redline_comparison

    a, b = _pair(store, a_version_id, b_version_id)
    if Path(a.filename).suffix.lower() != ".docx" or Path(b.filename).suffix.lower() != ".docx":
        raise ValueError("redline butuh dua versi DOCX")
    return build_redline_comparison(version_file(store, a).read_bytes(), version_file(store, b).read_bytes(),
                                    "DocPipe").data
