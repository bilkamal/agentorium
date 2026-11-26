"""Revision materials: Word comments, notes and files → instructions located in the document."""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from lxml import etree

from .models import Block
from .parse import _W, _para_text
from .validate import normalize_ws

MATERIAL_EXTENSIONS = (".txt", ".md", ".docx", ".pdf")
MAX_MATERIAL_CHARS = 20000
CANDIDATE_LIMIT = 12

INSTRUCTIONS_SCHEMA = {
    "type": "object",
    "properties": {"instructions": {"type": "array", "items": {
        "type": "object", "properties": {"text": {"type": "string"}, "quote": {"type": "string"}},
        "required": ["text", "quote"]}}},
    "required": ["instructions"],
}

INSTRUCTIONS_SYSTEM = """Anda asisten penyunting. Anda menerima bahan revisi dari atasan/klien (catatan, daftar koreksi,
notulen). Pecah menjadi daftar instruksi revisi yang berdiri sendiri: satu instruksi = satu perubahan yang diminta.
- text: instruksinya, ringkas tetapi lengkap (jangan menghilangkan nilai, nama, atau istilah yang disebut).
- quote: potongan teks dokumen yang dirujuk instruksi bila bahan menyebutnya persis (salin apa adanya); kosongkan bila tidak ada.
Abaikan salam, basa-basi, dan hal yang bukan permintaan perubahan. Jangan menambah instruksi sendiri."""


_COMMENTS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _comments_part(z: zipfile.ZipFile) -> str | None:
    """Path of the main comments part (its name varies: comments.xml, comments1.xml …)."""
    names = set(z.namelist())
    try:
        rels = etree.fromstring(z.read("word/_rels/document.xml.rels"))
        for r in rels.iter(_REL_NS + "Relationship"):
            if r.get("Type") == _COMMENTS_REL:
                target = r.get("Target", "").lstrip("/")
                path = target if target.startswith("word/") else f"word/{target}"
                if path in names:
                    return path
    except KeyError:
        pass
    return "word/comments.xml" if "word/comments.xml" in names else None


def read_comments(docx: bytes, blocks: list[Block]) -> list[dict]:
    """Word comments with the block they are anchored to and the anchored text."""
    z = zipfile.ZipFile(io.BytesIO(docx))
    part = _comments_part(z)
    if part is None:
        return []
    croot = etree.fromstring(z.read(part))
    comments = {}
    for c in croot.iter(_W + "comment"):
        cid = c.get(_W + "id")
        text = "\n".join(_para_text(p) for p in c.iter(_W + "p")).strip()
        comments[cid] = {"comment_id": cid, "author": c.get(_W + "author") or "", "text": text}
    body = etree.fromstring(z.read("word/document.xml")).find(_W + "body")
    para_of: dict[str, str] = {}
    anchors: dict[str, list[str]] = {}
    open_ids: set[str] = set()
    for el in body.iter():
        tag = el.tag
        if tag == _W + "commentRangeStart":
            open_ids.add(el.get(_W + "id"))
            para = next((a for a in el.iterancestors(_W + "p")), None)
            if para is not None:
                para_of.setdefault(el.get(_W + "id"), _para_text(para))
        elif tag == _W + "commentRangeEnd":
            open_ids.discard(el.get(_W + "id"))
        elif tag == _W + "commentReference":
            para = next((a for a in el.iterancestors(_W + "p")), None)
            if para is not None:
                para_of.setdefault(el.get(_W + "id"), _para_text(para))
        elif tag == _W + "t" and open_ids:
            if any(a.tag in (_W + "del", _W + "moveFrom") for a in el.iterancestors()):
                continue
            for cid in open_ids:
                anchors.setdefault(cid, []).append(el.text or "")
    out = []
    for cid, c in comments.items():
        ptext = normalize_ws(para_of.get(cid, ""))
        exact = [b.id for b in blocks if ptext and normalize_ws(b.text) == ptext]
        matches = exact or [b.id for b in blocks if ptext and ptext in normalize_ws(b.text)]
        one = matches[0] if len(matches) == 1 else None
        out.append({**c, "block_id": one, "candidate_block_ids": [] if one else matches[:CANDIDATE_LIMIT],
                    "anchor": "".join(anchors.get(cid, [])).strip()})
    return [c for c in out if c["text"]]


def material_text(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in MATERIAL_EXTENSIONS:
        raise ValueError(f"Format bahan tidak didukung ({ext or 'tanpa ekstensi'}). Gunakan: {', '.join(MATERIAL_EXTENSIONS)}")
    try:
        return _material_text(ext, data)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Berkas bahan {filename} rusak atau tidak bisa dibaca: {exc}") from exc


def _material_text(ext: str, data: bytes) -> str:
    if ext in (".txt", ".md"):
        return data.decode("utf-8", errors="replace").strip()
    if ext == ".docx":
        import docx as pydocx

        d = pydocx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        z = zipfile.ZipFile(io.BytesIO(data))
        part = _comments_part(z)
        if part:
            root = etree.fromstring(z.read(part))
            for c in root.iter(_W + "comment"):
                txt = " ".join(_para_text(p) for p in c.iter(_W + "p")).strip()
                if txt:
                    parts.append(f"Komentar ({c.get(_W + 'author') or '?'}): {txt}")
        return "\n".join(parts).strip()
    if ext == ".pdf":
        from pypdf import PdfReader

        return "\n".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(data)).pages).strip()
    raise ValueError(f"Format bahan tidak didukung ({ext or 'tanpa ekstensi'}). Gunakan: {', '.join(MATERIAL_EXTENSIONS)}")


def _chunks(text: str, limit: int = MAX_MATERIAL_CHARS) -> list[str]:
    out, cur = [], ""
    for para in re.split(r"\n\s*\n|\n", text):
        if cur and len(cur) + len(para) + 1 > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{para}" if cur else para
    if cur.strip():
        out.append(cur)
    return out


async def extract_instructions(text: str, llm) -> list[dict]:
    if not text.strip():
        return []
    out = []
    for part in _chunks(text):
        reply = await llm.complete_json(INSTRUCTIONS_SYSTEM, [{"role": "user", "content": f"BAHAN REVISI:\n{part}"}],
                                        INSTRUCTIONS_SCHEMA, purpose="doc_instructions")
        for i in reply.get("instructions") or []:
            if isinstance(i, dict) and str(i.get("text") or "").strip():
                out.append({"text": str(i["text"]).strip(), "quote": str(i.get("quote") or "").strip()})
    return out


def locate(store, version_id: str, instr: dict) -> dict:
    """Blocks an instruction refers to: exact quote match first; otherwise retrieval candidates for the worker."""
    from .retrieval import search

    blocks = store.get_blocks(version_id)
    q = normalize_ws(instr.get("quote") or "")
    if q:
        hits = [b.id for b in blocks if q in normalize_ws(b.text)]
        if len(hits) == 1:
            return {"block_ids": hits, "candidate_block_ids": []}
        if hits:
            return {"block_ids": [], "candidate_block_ids": hits[:CANDIDATE_LIMIT]}
    query = f"{instr.get('text', '')} {instr.get('quote', '')}".strip()
    cand: list[str] = []
    for h in search(store, version_id, query, k=3):
        for bid in h.chunk.block_ids:
            if bid not in cand:
                cand.append(bid)
    return {"block_ids": [], "candidate_block_ids": cand[:CANDIDATE_LIMIT]}
