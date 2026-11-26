"""Apply per-block text edits to a DOCX as tracked changes (adeu), and check edits before applying them."""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field

from .consistency import _parse_number

log = logging.getLogger(__name__)

MONTHS = ("januari", "februari", "maret", "april", "mei", "juni", "juli", "agustus", "september", "oktober",
          "november", "desember", "january", "february", "march", "may", "june", "july", "august", "october",
          "december")
_NUM_RE = re.compile(r"\d[\d.,]*\d|\d")
_WORD_RE = re.compile(r"[A-Za-z]+")


@dataclass
class BlockEdit:
    block_id: str
    old_text: str
    new_text: str
    reason: str
    sources: list[str] = field(default_factory=list)


@dataclass
class EditResult:
    block_id: str
    status: str  # applied | needs_human
    error: str | None = None


def _tokens(text: str) -> set[str]:
    """Numbers (normalised, Indonesian locale) and month names — the facts an edit must not silently change."""
    out = set()
    for m in _NUM_RE.findall(text):
        n = _parse_number(m)
        out.add(n if n is not None else m)
    out |= {w.lower() for w in _WORD_RE.findall(text) if w.lower() in MONTHS}
    return out


def validate_edit(old: str, new: str, basis_texts: list[str], doc_texts: list[str]) -> str | None:
    """Return why an edit must not be applied, or None when it is acceptable."""
    if not new.strip():
        return "teks baru kosong"
    ratio = len(new.strip()) / max(1, len(old.strip()))
    if not 0.3 <= ratio <= 3.0:
        return f"panjang teks berubah terlalu jauh ({ratio:.1f}×)"
    basis = set().union(*(_tokens(t) for t in basis_texts)) if basis_texts else set()
    old_t, new_t = _tokens(old), _tokens(new)
    dropped = sorted(old_t - new_t - basis)
    if dropped:
        return f"angka/tanggal berubah tanpa dasar: {', '.join(dropped)}"
    known = basis | (set().union(*(_tokens(t) for t in doc_texts)) if doc_texts else set())
    added = sorted(new_t - old_t - known)
    if added:
        return f"angka/tanggal baru tanpa sumber: {', '.join(added)}"
    return None


_PRE_RE = re.compile(r"^[*_]+")
_SUF_RE = re.compile(r"[*_]+$")


def _plain(tok: str) -> str:
    return _SUF_RE.sub("", _PRE_RE.sub("", tok))


def _escape(text: str) -> str:
    return re.sub(r"([*_])", r"\\\1", text)


def carry_markup(old_md: str, old_plain: str, new_plain: str) -> str | None:
    """Rewrite `new_plain` in adeu's markdown projection, keeping the bold/italic markers of unchanged words.
    None when the markers cannot be carried safely (caller then sends plain text)."""
    from difflib import SequenceMatcher

    md = old_md.split()
    old = old_plain.split()
    if [_plain(t) for t in md] != old:
        return None
    new = new_plain.split()
    out: list[str] = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if op == "equal":
            out += md[i1:i2]
            continue
        if op == "delete":
            if any(_PRE_RE.search(t) or _SUF_RE.search(t) for t in md[i1:i2]):
                return None
            continue
        words = [_escape(w) for w in new[j1:j2]]
        if op == "replace":
            pre = _PRE_RE.search(md[i1])
            suf = _SUF_RE.search(md[i2 - 1])
            inner = [t for t in md[i1:i2]]
            if any(_PRE_RE.search(t) for t in inner[1:]) or any(_SUF_RE.search(t) for t in inner[:-1]):
                return None
            if pre:
                words[0] = pre.group(0) + words[0]
            if suf:
                words[-1] = words[-1] + suf.group(0)
        out += words
    result = " ".join(out)
    markers = [m for t in out for m in ((_PRE_RE.search(t) or [""])[0], (_SUF_RE.search(t) or [""])[0]) if m]
    if result.count("**") % 2 or sum(m.replace("**", "").count("_") for m in markers) % 2:
        return None
    return result


def _projections(docx: bytes) -> dict[str, str]:
    """Plain paragraph text → adeu's markdown projection of that paragraph (bold/italic markers)."""
    import docx as pydocx
    from adeu.ingest import build_paragraph_text
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph

    out: dict[str, str] = {}
    try:
        doc = pydocx.Document(io.BytesIO(docx))
        for el in doc.element.body.iter(qn("w:p")):
            par = Paragraph(el, doc)
            plain = par.text
            if plain and plain not in out:
                out[plain] = build_paragraph_text(par, {})
    except Exception as exc:  # projection is an optimisation; plain targets still work
        log.warning("paragraph projection failed: %s", exc)
    return out


def _inherit_replaced_formatting(docx: bytes) -> bytes:
    """A tracked insertion that directly follows a tracked deletion (a replacement) gets the run formatting of
    the deleted text when it has none — adeu re-applies bold but not italic/underline/colour."""
    import copy
    import zipfile

    from lxml import etree

    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    q = lambda tag: f"{{{w}}}{tag}"  # noqa: E731
    src = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(src.read("word/document.xml"))
    changed = False
    for ins in root.iter(q("ins")):
        prev = ins.getprevious()
        if prev is None or prev.tag != q("del"):
            continue
        del_runs = [r for r in prev if r.tag == q("r")]
        rpr = del_runs[0].find(q("rPr")) if del_runs else None
        if rpr is None:
            continue
        for run in ins.iter(q("r")):
            mine = run.find(q("rPr"))
            if mine is None:
                run.insert(0, copy.deepcopy(rpr))
                changed = True
            else:
                have = {c.tag for c in mine}
                for c in rpr:
                    if c.tag not in have:
                        mine.append(copy.deepcopy(c))
                        changed = True
    if not changed:
        return docx
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True) \
                if item.filename == "word/document.xml" else src.read(item.filename)
            dst.writestr(item, data)
    return out.getvalue()


def apply_block_edits(docx: bytes, edits: list[BlockEdit], author: str = "DocPipe"
                      ) -> tuple[bytes | None, bytes | None, list[EditResult]]:
    """Apply all edits in one batch. Each edit replaces one whole block text, matched strictly (must be unique)."""
    from adeu import ModifyText, RedlineEngine

    if not edits:
        return None, None, []
    proj = _projections(docx)
    changes = []
    for e in edits:
        md = proj.get(e.old_text)
        new_md = carry_markup(md, e.old_text, e.new_text) if md and md != e.old_text else None
        if new_md is not None:
            changes.append(ModifyText(target_text=md, new_text=new_md, match_mode="strict"))
        else:
            changes.append(ModifyText(target_text=md or e.old_text, new_text=_escape(e.new_text), match_mode="strict"))
    engine = RedlineEngine(io.BytesIO(docx), author=author)
    try:
        report = engine.process_batch(changes, partial=True)
    except Exception as exc:  # adeu refused the whole batch
        log.warning("adeu batch failed: %s", exc)
        return None, None, [EditResult(e.block_id, "needs_human", f"adeu: {exc}"[:500]) for e in edits]
    by_index = {r.get("index", i): r for i, r in enumerate(report.get("edits") or [])}
    results = []
    for i, e in enumerate(edits):
        r = by_index.get(i) or {}
        if r.get("status") == "applied":
            results.append(EditResult(e.block_id, "applied"))
        else:
            results.append(EditResult(e.block_id, "needs_human", str(r.get("error") or "tidak diterapkan")[:500]))
    if not any(r.status == "applied" for r in results):
        return None, None, results
    tracked = _inherit_replaced_formatting(engine.save_to_stream().getvalue())
    clean_engine = RedlineEngine(io.BytesIO(tracked), author=author)
    clean_engine.accept_all_revisions()
    return tracked, clean_engine.save_to_stream().getvalue(), results
