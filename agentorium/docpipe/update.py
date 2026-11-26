"""Periodic update: last period's report + new data → new version with updated numbers, tables, dates, narrative."""
from __future__ import annotations

import asyncio
import csv
import io
import re
from pathlib import Path

from . import materials, prompts, review
from .calc import CalcError, resolve_placeholders
from .edits import MONTHS, _tokens
from .editloop import edit_loop
from .jobs import runner
from .models import Job, Task
from .review import _hooks, pick_agents, plan_tasks
from .revise import _change, apply_job_edits, ensure_idle, require_docx
from .store import get_store

DATA_EXTENSIONS = (".xlsx", ".csv", ".docx", ".pdf", ".txt", ".md")
MAX_DATA_CHARS = 12000
_AMBIG = re.compile(r"\d{1,3}(\.\d{3})+")


def _cell(v) -> str:
    if isinstance(v, bool) or v is None:
        return "" if v is None else str(v)
    if isinstance(v, (int, float)):
        if float(v).is_integer():
            return str(int(v))
        s = repr(float(v))
        return s + "0" if _AMBIG.fullmatch(s) else s  # "1.234" would read as a thousands separator
    if hasattr(v, "strftime"):
        return v.strftime("%d-%m-%Y")
    return str(v).strip()


def _rows_text(label: str, rows: list[list]) -> str:
    rows = [[_cell(c) for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""
    header = rows[0]
    has_header = all(h and _parse_free(h) for h in header if h)
    out = []
    for i, r in enumerate(rows[1:] if has_header else rows, start=2 if has_header else 1):
        cells = [(f"{header[j]}=" if has_header and j < len(header) and header[j] else "") + c
                 for j, c in enumerate(r) if c]
        out.append(f"{label}baris {i}: " + " | ".join(cells))
    return "\n".join(out)


def _parse_free(h: str) -> bool:
    """A header cell is text, not a number."""
    return not re.fullmatch(r"-?[\d.,]+", h)


def extract_data(filename: str, data: bytes) -> list[dict]:
    ext = Path(filename).suffix.lower()
    if ext not in DATA_EXTENSIONS:
        raise ValueError(f"Format data tidak didukung ({ext or 'tanpa ekstensi'}). Gunakan: {', '.join(DATA_EXTENSIONS)}")
    try:
        if ext == ".xlsx":
            import openpyxl

            wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
            text = "\n".join(t for t in (_rows_text(f"{ws.title} ", [list(r) for r in ws.iter_rows(values_only=True)])
                                         for ws in wb.worksheets) if t)
        elif ext == ".csv":
            raw = data.decode("utf-8-sig", errors="replace")
            dialect = csv.Sniffer().sniff(raw[:2048], delimiters=",;\t") if raw.strip() else csv.excel
            text = _rows_text("", list(csv.reader(io.StringIO(raw), dialect)))
        else:
            text = materials.material_text(filename, data)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Berkas data {filename} rusak atau tidak bisa dibaca: {exc}") from exc
    return [{"name": filename, "text": text, "numbers": sorted(_tokens(text))}]


def data_for_section(sources: list[dict], section_text: str, limit: int = MAX_DATA_CHARS) -> str:
    """All data when it fits; otherwise the lines sharing the most words with the section."""
    full = "\n\n".join(f"[{s['name']}]\n{s['text']}" for s in sources)
    if len(full) <= limit:
        return full
    words = {w for w in re.findall(r"\w{3,}", section_text.lower())}
    lines = [(s["name"], ln) for s in sources for ln in s["text"].splitlines() if ln.strip()]
    ranked = sorted(lines, key=lambda x: -len(words & set(re.findall(r"\w{3,}", x[1].lower()))))
    out, size = [], 0
    for name, ln in ranked:
        if size + len(ln) > limit:
            break
        out.append(f"[{name}] {ln}")
        size += len(ln) + 1
    return "\n".join(out)


_SCALE = {"ribu": 1e3, "juta": 1e6, "miliar": 1e9, "milyar": 1e9, "triliun": 1e12}
_SCALED = re.compile(r"(\d[\d.,]*)\s*(ribu|juta|miliar|milyar|triliun)\b", re.I)


def _scaled_ok(text: str, known: set[str]) -> set[str]:
    """Numbers written with a unit word ("286 juta") that equal a data value (286000000): a unit, not a derivation."""
    from .consistency import _parse_number

    ok = set()
    for m in _SCALED.finditer(text):
        n = _parse_number(m.group(1))
        if n is None:
            continue
        full = _parse_number(str(round(float(n) * _SCALE[m.group(2).lower()])))
        if full in known:
            ok.add(n)
    return ok


def _constant(x: str) -> bool:
    """Scale factors and small counts a formula may use without a source: 10, 100, 1000000 …, and 1–12."""
    return bool(re.fullmatch(r"10*", x)) or (x.isdigit() and 1 <= int(x) <= 12)


def check_update_edit(old: str, e: dict, known: set[str], *, doc_tokens: set[str]) -> str | None:
    """Resolve {{calc}} placeholders in e["new_text"], then require a source for every number in the result."""
    try:
        text, formulas, lits = resolve_placeholders(e["new_text"])
    except CalcError as exc:
        return f"rumus tidak valid: {exc}"
    unknown_lits = sorted(x for x in lits - known - doc_tokens - _tokens(old) if not _constant(x))
    if unknown_lits:
        return f"angka dalam rumus tanpa sumber: {', '.join(unknown_lits)}"
    e["new_text"] = text
    if formulas:
        e["formulas"] = formulas
    if "{{" in text or "}}" in text:
        return "placeholder rumus tidak lengkap"
    ratio = len(text.strip()) / max(1, len(old.strip()))
    if not text.strip() or not 0.3 <= ratio <= 3.0:
        return "teks baru kosong atau panjangnya berubah terlalu jauh"
    values = {v for f in formulas for v in _tokens(f["value"])}
    added = sorted(_tokens(text) - _tokens(old) - known - values - _scaled_ok(text, known))
    if added:
        return f"angka/tanggal tanpa sumber di data: {', '.join(added)}"
    return None


async def start_update(version_id: str, *, period_label: str, files: list[tuple[str, bytes]], notes: str = "") -> Job:
    store = get_store()
    version = store.get_version(version_id)
    require_docx(version)
    pick_agents()
    if not period_label.strip():
        raise ValueError("Isi label periode baru, mis. 'November 2026'")
    ensure_idle(store, version.id)
    sources = []
    for name, data in files:
        sources += await asyncio.to_thread(extract_data, name, data)
    sources = [s for s in sources if s["text"].strip()]
    if not sources:
        raise ValueError("Data periode baru kosong: unggah minimal satu berkas data (xlsx, csv, docx, pdf, txt)")
    ensure_idle(store, version.id)  # again: the awaits above let another request in
    job = store.create_job(version.document_id, version.id, "update",
                           {"period_label": period_label.strip(), "notes": notes.strip()[:5000], "sources": sources})
    store.add_tasks(job.id, [(sid, ids, "update_section") for sid, ids in
                             plan_tasks(store.get_sections(version.id), store.get_blocks(version.id))])
    await runner.start(job.id)
    return store.get_job(job.id)


async def update_task(job: Job, task: Task) -> dict:
    store = get_store()
    supervisor, workers = pick_agents()
    worker = workers[task.ordinal % len(workers)]
    section = next(s for s in store.get_sections(job.version_id) if s.id == task.section_id)
    ids = set(task.block_ids)
    blocks = [b for b in store.get_blocks(job.version_id, section_id=task.section_id) if b.id in ids]
    blocks_by_id = {b.id: b for b in blocks}
    period = job.params["period_label"]
    sources = job.params["sources"]
    data_text = data_for_section(sources, section.path + " " + " ".join(b.text for b in blocks))
    all_text = [b.text for b in store.get_blocks(job.version_id)]
    doc_tokens = set().union(*(_tokens(t) for t in all_text)) if all_text else set()
    known = {n for s in sources for n in s["numbers"]} | _tokens(period) | {t for t in doc_tokens if t in MONTHS}
    hooks = _hooks()
    if hooks:
        hooks.task_started(job, task, worker, supervisor, section)
    out = await edit_loop(
        wllm=review.make_llm(worker), sllm=review.make_llm(supervisor),
        worker_system=prompts.update_worker_system(worker.name),
        user_for=lambda fb: prompts.update_worker_user(section.path, period, blocks, data_text,
                                                       job.params.get("notes", ""), fb),
        section_path=section.path, blocks_by_id=blocks_by_id, basis={"data": f"data periode {period}"},
        doc_texts=all_text, purpose="doc_update", hooks=hooks, job=job, task=task, worker=worker,
        supervisor=supervisor, schema=prompts.UPDATE_SCHEMA, default_source="data",
        check=lambda block, e: check_update_edit(block.text, e, known, doc_tokens=doc_tokens),
        supervisor_context=prompts.update_supervisor_context(period, data_text))
    for e in out.approved:
        if e.get("formulas"):
            e["reason"] = (e["reason"] + " | rumus: "
                           + "; ".join(f"{f['expr']} = {f['value']}" for f in e["formulas"])).strip(" |")
    # numbers without a source are held for a person, not silently dropped
    held = [e for e in out.rejected if str(e.get("note", "")).startswith("validator:")]
    rejected = [e for e in out.rejected if e not in held]
    skipped = [{"block_id": s["source_id"], "reason": s["reason"]} for s in out.skipped if s["source_id"] in blocks_by_id]
    if hooks:
        hooks.task_finished(job, task, worker, supervisor, len(out.approved))
    return {"edits": out.approved, "rejected": rejected, "needs_human": out.needs_human + held,
            "held": skipped, "worker": worker.id}


async def _finalize_update(job: Job) -> None:
    store = get_store()
    base = store.get_version(job.version_id)
    blocks = {b.id: b for b in store.get_blocks(base.id)}
    extra = []
    for t in store.get_tasks(job.id):
        for h in (t.output or {}).get("held", []):
            if h["block_id"] in blocks:
                extra.append(_change(job, blocks[h["block_id"]], {"source_ids": ["data"], "reason": ""},
                                     "needs_human", base.id, f"ditahan: {h['reason']}", kind="data"))
    await apply_job_edits(job, source="update", kind="data", extra_changes=extra,
                          note=f"Update {job.params['period_label']} dari v{base.version_no}")


runner.register("update_section", update_task)
runner.register_finalizer("update", _finalize_update)
