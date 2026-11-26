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
    # where this block sits among the document's blocks with exactly the same text (counted in order):
    # lets a heading that also appears in the table of contents, or a cell reading "Status", be edited in place
    occurrence: int | None = None
    total: int | None = None


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
    # "the right value is 540" replaces 450 without naming it; a replacement whose old value the basis names
    # already explains itself, so its credit is not available for another, unrequested change
    grounded = len((new_t - old_t) & basis) - len((old_t - new_t) & basis)
    if len(dropped) > max(0, grounded):
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


_CRITIC_COMMENT = re.compile(r"\{>>.*?<<\}", re.S)


def _strip_critic(md: str) -> str:
    """Drop comment markup ({==anchor==}{>>comment<<}) adeu adds to the projection; targets are matched without it."""
    return _CRITIC_COMMENT.sub("", md).replace("{==", "").replace("==}", "")


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
                out[plain] = _strip_critic(build_paragraph_text(par, {}))
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


_GAP = re.compile(r"(?:[ \t]*\{#cell:[0-9A-Fa-f]+\})?\s*")  # between two paragraphs of one block in adeu's text


_LEAD = re.compile(r"[ \t]*(?:#{1,6} |[-*+] |\d+[.)] )?[ \t]*")
_TRAIL = re.compile(r"[ \t]*(?:\{#cell:[0-9A-Fa-f]+\})?[ \t]*")


def _whole(full: str, start: int, end: int) -> bool:
    """full[start:end] is a whole paragraph or cell, not part of a longer sentence."""
    a = max(full.rfind("\n", 0, start), full.rfind("|", 0, start)) + 1
    b = min([i for i in (full.find("\n", end), full.find("|", end)) if i >= 0] or [len(full)])
    return bool(_LEAD.fullmatch(full, a, start)) and bool(_TRAIL.fullmatch(full, end, b))


def _chains(full: str, targets: list[str]) -> list[list[tuple[int, int]]]:
    """Every place where the block's paragraphs appear, whole, one after another; one span per paragraph."""
    out = []
    for m in re.finditer(re.escape(targets[0]), full):
        if not _whole(full, *m.span()):
            continue
        spans, pos = [m.span()], m.end()
        for t in targets[1:]:
            g = _GAP.match(full, pos)
            start = g.end() if g and "\n" in full[pos:g.end()] else -1
            if start < 0 or not full.startswith(t, start) or not _whole(full, start, start + len(t)):
                break
            spans.append((start, start + len(t)))
            pos = start + len(t)
        if len(spans) == len(targets):
            out.append(spans)
    return out


def _anchored(full: str, target: str, start: int, end: int) -> str | None:
    """A regex matching `target` only at full[start:end], by the text around it. The `\\A…\\Z` branch lets
    adeu's own re.sub on the matched span alone rewrite it."""
    lit = re.escape(target)
    for n in (8, 16, 32, 64, 128, 256):
        before, after = full[max(0, start - n):start], full[end:end + n]
        pat = f"(?<={re.escape(before)}){lit}(?={re.escape(after)})" if before else f"\\A{lit}(?={re.escape(after)})"
        hits = [m.start() for m in re.finditer(pat, full)]
        if hits == [start]:
            return f"{pat}|\\A{lit}\\Z"
    return None


def _changed_span(old: str, new: str) -> tuple[int, str, str]:
    """(offset in old, old part, new part) covering only the words that differ, so the tracked change marks those
    words rather than the whole paragraph. The old part is never empty (adeu needs text to anchor on)."""
    a, b = re.split(r"(\s+)", old), re.split(r"(\s+)", new)
    p = 0
    while p < min(len(a), len(b)) and a[p] == b[p]:
        p += 1
    s = 0
    while s < min(len(a), len(b)) - p and a[len(a) - 1 - s] == b[len(b) - 1 - s]:
        s += 1
    while not "".join(a[p:len(a) - s]).strip():  # pure insertion (or whitespace only): take a neighbouring word
        if p > 0:
            p -= 1
        elif s > 0:
            s -= 1
        else:
            break
    off = len("".join(a[:p]))
    return off, "".join(a[p:len(a) - s]), "".join(b[p:len(b) - s])


def _parts(e: BlockEdit, proj: dict[str, str], full: str):
    """(ModifyText kwargs per changed paragraph, error). A block spanning several paragraphs (a table cell) is
    edited paragraph by paragraph, at this block's own place in the document."""
    olds = e.old_text.split("\n")
    if len(olds) == 1:  # one paragraph stays one paragraph: a split would show as the whole paragraph rewritten
        news = [re.sub(r"\s*\n\s*", " ", e.new_text).strip()]
    else:
        news = e.new_text.split("\n")
    if len(news) != len(olds):
        return None, "jumlah paragraf dalam blok berubah; edit per paragraf tidak bisa dipetakan"
    mds = [proj.get(o) or o for o in olds]
    chains = _chains(full, mds)
    if len(chains) == 1:
        chain = chains[0]
    elif e.occurrence is not None and len(chains) == e.total and e.occurrence < len(chains):
        chain = chains[e.occurrence]
    elif not chains and len(olds) == 1 and (e.occurrence is None or e.total == 1):
        chain = None  # not found verbatim: let adeu's own matching (quotes, clean view) try
    else:
        return None, (f"teks blok muncul {len(chains)} kali di dokumen dan letaknya tidak bisa dipastikan"
                      if chains else "teks blok tidak ditemukan di dokumen")
    parts = []
    for i, (old, new, md) in enumerate(zip(olds, news, mds)):
        if old == new:
            continue
        new_md = carry_markup(md, old, new) if md != old else None
        repl = new_md if new_md is not None else _escape(new)
        if chain is None or len(re.findall(re.escape(md), full)) == 1:
            parts.append({"target_text": md, "new_text": repl})
            continue
        start, _ = chain[i]
        off, old_mid, new_mid = _changed_span(md, repl)
        pat = _anchored(full, old_mid, start + off, start + off + len(old_mid))
        if pat is None:
            return None, "teks blok berulang dan konteks sekitarnya tidak unik"
        parts.append({"target_text": pat, "new_text": new_mid.replace("\\", "\\\\"), "regex": True})
    return parts, None


def apply_block_edits(docx: bytes, edits: list[BlockEdit], author: str = "DocPipe"
                      ) -> tuple[bytes | None, bytes | None, list[EditResult]]:
    """Apply all edits in one batch. Each edit replaces one block's text at that block's own place (a text that
    repeats elsewhere is located by `occurrence`); a multi-paragraph block is edited paragraph by paragraph."""
    from adeu import ModifyText, RedlineEngine

    if not edits:
        return None, None, []
    proj = _projections(docx)
    engine = RedlineEngine(io.BytesIO(docx), author=author)
    full = engine.mapper.full_text
    changes, owner, errors = [], [], {}
    for k, e in enumerate(edits):
        parts, why = _parts(e, proj, full)
        if why:
            errors[k] = why
            continue
        for p in parts:
            changes.append(ModifyText(match_mode="strict", **p))
            owner.append(k)
    report = {"edits": []}
    if changes:
        try:
            report = engine.process_batch(changes, partial=True)
        except Exception as exc:  # adeu refused the whole batch
            log.warning("adeu batch failed: %s", exc)
            return None, None, [EditResult(e.block_id, "needs_human", f"adeu: {exc}"[:500]) for e in edits]
    by_index = {r.get("index", i): r for i, r in enumerate(report.get("edits") or [])}
    status: dict[int, list] = {}
    for i, k in enumerate(owner):
        status.setdefault(k, []).append(by_index.get(i) or {})
    results = []
    for k, e in enumerate(edits):
        if k in errors:
            results.append(EditResult(e.block_id, "needs_human", errors[k]))
            continue
        rs = status.get(k, [])
        failed = [r for r in rs if r.get("status") != "applied"]
        if rs and not failed:
            results.append(EditResult(e.block_id, "applied"))
        else:
            err = str((failed[0] if failed else {}).get("error") or "tidak diterapkan")
            if failed and len(failed) < len(rs):
                err = "sebagian paragraf sudah diubah, sisanya gagal — periksa blok ini: " + err
            results.append(EditResult(e.block_id, "needs_human", err[:500]))
    if not any(r.status == "applied" for r in results):
        return None, None, results
    tracked = _inherit_replaced_formatting(engine.save_to_stream().getvalue())
    clean_engine = RedlineEngine(io.BytesIO(tracked), author=author)
    clean_engine.accept_all_revisions()
    return tracked, clean_engine.save_to_stream().getvalue(), results
