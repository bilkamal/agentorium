"""Revision from user materials (notes, files, Word comments) with a follow-up table per instruction."""
from __future__ import annotations

import asyncio
import io

from . import materials, prompts, review
from .editloop import edit_loop
from .jobs import runner
from .models import Job, Task
from .review import _hooks, pick_agents, plan_tasks
from .revise import apply_job_edits, ensure_idle, require_docx
from .service import version_file
from .store import get_store

STATUS_LABEL = {"pending": "belum diproses", "done": "selesai", "not_applicable": "tidak dikerjakan",
                "needs_human": "perlu dicek manual"}


async def start_instructed_revision(version_id: str, *, notes: str, files: list[tuple[str, bytes]],
                                    use_comments: bool) -> Job:
    store = get_store()
    version = store.get_version(version_id)
    require_docx(version)
    pick_agents()
    ensure_idle(store, version.id)
    texts = []
    if notes.strip():
        texts.append({"source": "notes", "text": notes.strip()[:200000]})
    for name, data in files:
        t = await asyncio.to_thread(materials.material_text, name, data)
        if t.strip():
            texts.append({"source": f"file:{name}", "text": t[:200000]})
    comments = []
    if use_comments:
        data = version_file(store, version).read_bytes()
        blocks = store.get_blocks(version.id)
        # comments live in the uploaded original (the working copy has changes accepted but keeps comments)
        if version.original_path:
            data = version_file(store, version, "original").read_bytes()
        comments = await asyncio.to_thread(materials.read_comments, data, blocks)
    if not texts and not comments:
        raise ValueError("Bahan revisi kosong: isi catatan, unggah berkas, atau pakai komentar Word di dokumen")
    ensure_idle(store, version.id)  # again: the awaits above let another request in
    job = store.create_job(version.document_id, version.id, "instructed_revision",
                           {"materials": texts, "comments": comments})
    await runner.start(job.id)
    return store.get_job(job.id)


async def _prepare(job: Job) -> None:
    from .index import ensure_index

    store = get_store()
    if store.get_instructions(job.id):  # resumed job: instructions already extracted
        return
    try:
        await ensure_index(job.version_id)
    except Exception:  # retrieval is optional; exact quotes still locate
        pass
    rows = [dict(job_id=job.id, source="comment", author=c["author"], text=c["text"], quote=c.get("anchor") or "",
                 block_ids=[c["block_id"]] if c.get("block_id") else [],
                 candidate_block_ids=c.get("candidate_block_ids") or [], status="pending")
            for c in job.params.get("comments") or []]
    supervisor, _ = pick_agents()
    llm = review.make_llm(supervisor)
    for m in job.params.get("materials") or []:
        for ins in await materials.extract_instructions(m["text"], llm):
            where = await asyncio.to_thread(materials.locate, store, job.version_id, ins)
            rows.append(dict(job_id=job.id, source=m["source"], author="", text=ins["text"], quote=ins["quote"],
                             **where, status="pending"))
    for r in rows:
        if not r["block_ids"] and not r["candidate_block_ids"]:
            r["status"], r["response"] = "needs_human", "lokasi tidak ditemukan di dokumen"
    if not rows:
        return
    store.add_instructions(rows)
    wanted = {b for r in rows if r["status"] == "pending" for b in r["block_ids"] + r["candidate_block_ids"]}
    plan = [(sid, ids) for sid, ids in plan_tasks(store.get_sections(job.version_id), store.get_blocks(job.version_id))
            if wanted & set(ids)]
    store.add_tasks(job.id, [(sid, ids, "instructed_section") for sid, ids in plan])


async def instructed_task(job: Job, task: Task) -> dict:
    store = get_store()
    supervisor, workers = pick_agents()
    worker = workers[task.ordinal % len(workers)]
    section = next(s for s in store.get_sections(job.version_id) if s.id == task.section_id)
    ids = set(task.block_ids)
    blocks = [b for b in store.get_blocks(job.version_id, section_id=task.section_id) if b.id in ids]
    blocks_by_id = {b.id: b for b in blocks}
    ins = [i for i in store.get_instructions(job.id)
           if i.status == "pending" and ids & set(i.block_ids + i.candidate_block_ids)]
    idicts = [{"id": i.id, "source": "komentar Word" if i.source == "comment" else "catatan", "author": i.author,
               "text": i.text, "quote": i.quote, "block_ids": [b for b in i.block_ids if b in ids],
               "candidate_block_ids": [b for b in i.candidate_block_ids if b in ids]} for i in ins]
    basis = {i.id: i.text + (f" | merujuk: {i.quote}" if i.quote else "") for i in ins}
    doc_texts = [b.text for b in store.get_blocks(job.version_id)]
    hooks = _hooks()
    if hooks:
        hooks.task_started(job, task, worker, supervisor, section)
    out = await edit_loop(
        wllm=review.make_llm(worker), sllm=review.make_llm(supervisor),
        worker_system=prompts.instructed_worker_system(worker.name),
        user_for=lambda fb: prompts.instructed_worker_user(section.path, blocks, idicts, fb),
        section_path=section.path, blocks_by_id=blocks_by_id, basis=basis, doc_texts=doc_texts,
        purpose="doc_instructed", hooks=hooks, job=job, task=task, worker=worker, supervisor=supervisor,
        schema=prompts.INSTRUCTED_SCHEMA)
    responses = [{"instruction_id": str(r.get("instruction_id") or ""), "status": str(r.get("status") or ""),
                  "response": str(r.get("response") or "")}
                 for r in out.extra.get("responses") or [] if isinstance(r, dict)]
    if hooks:
        hooks.task_finished(job, task, worker, supervisor, len(out.approved))
    return {"edits": out.approved, "rejected": out.rejected, "needs_human": out.needs_human,
            "skipped": [s for s in out.skipped if s["source_id"] in basis],
            "responses": [r for r in responses if r["instruction_id"] in basis], "worker": worker.id}


async def _finalize(job: Job) -> None:
    store = get_store()
    await apply_job_edits(job, source="instructed_revision", kind="instruction",
                          note=f"Revisi dari bahan (v{store.get_version(job.version_id).version_no})")
    changes = store.get_changes(job_id=job.id)
    applied = {s for c in changes if c.status == "applied" for s in c.source_ids}
    failed = {s: c.note or c.status for c in changes if c.status in ("needs_human", "rejected") for s in c.source_ids}
    responses, skipped = {}, {}
    for t in store.get_tasks(job.id):
        o = t.output or {}
        for r in o.get("responses", []):
            responses.setdefault(r["instruction_id"], r)
        for s in o.get("skipped", []):
            skipped.setdefault(s["source_id"], s["reason"])
    for i in store.get_instructions(job.id):
        if i.status != "pending":
            continue
        r = responses.get(i.id)
        if i.id in applied:
            status = "done"
            reply = (r or {}).get("response") or next((c.reason for c in changes if i.id in c.source_ids
                                                        and c.status == "applied"), "diubah")
        elif i.id in failed:
            status, reply = "needs_human", f"perubahan tidak diterapkan: {failed[i.id]}"
        elif r and r["status"] == "not_applicable":
            status, reply = "not_applicable", r["response"]
        elif i.id in skipped:
            status, reply = "needs_human", f"tidak diubah otomatis: {skipped[i.id]}"
        elif r and r["status"] == "done":
            status, reply = "needs_human", f"dilaporkan selesai tetapi tidak ada perubahan: {r['response']}"
        else:
            status, reply = "needs_human", "tidak ditangani"
        where = {}
        if status == "done":  # point at the paragraphs actually changed, not the retrieval guesses
            where = {"block_ids": [c.block_id for c in changes if c.status == "applied" and i.id in c.source_ids]}
        store.update_instruction(i.id, status=status, response=reply, **where)


def _source_label(i) -> str:
    if i.source == "comment":
        return f"Komentar Word ({i.author})" if i.author else "Komentar Word"
    if i.source.startswith("file:"):
        return f"Berkas {i.source[5:]}"
    return "Catatan"


def followup_docx(job_id: str) -> bytes:
    """Follow-up table: every instruction with its status, response and location."""
    import docx as pydocx

    store = get_store()
    job = store.get_job(job_id)
    doc = store.get_document(job.document_id)
    version = store.get_version(job.version_id)
    sections = {s.id: s for s in store.get_sections(job.version_id)}
    block_section = {b.id: b.section_id for b in store.get_blocks(job.version_id)}
    d = pydocx.Document()
    d.add_heading(f"Tindak lanjut revisi — {doc.title if doc else ''} v{version.version_no if version else ''}", 1)
    result = store.get_version(job.params["result_version_id"]) if job.params.get("result_version_id") else None
    d.add_paragraph(f"Hasil: versi v{result.version_no}" if result else "Hasil: tidak ada versi baru")
    cols = ["No", "Sumber", "Instruksi", "Status", "Tanggapan", "Lokasi"]
    t = d.add_table(rows=1, cols=len(cols))
    t.style = "Table Grid"
    for c, name in zip(t.rows[0].cells, cols):
        c.text = name
    for n, i in enumerate(store.get_instructions(job_id), 1):
        secs = []
        for b in i.block_ids or i.candidate_block_ids:
            s = sections.get(block_section.get(b))
            if s and s.path not in secs:
                secs.append(s.path)
        loc = "; ".join(secs) if secs else "-"
        if not i.block_ids and secs:
            loc = f"kemungkinan: {loc}"
        row = t.add_row().cells
        for c, val in zip(row, [str(n), _source_label(i), i.text, STATUS_LABEL.get(i.status, i.status),
                                i.response, loc]):
            c.text = val
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


runner.register("instructed_section", instructed_task)
runner.register_preparer("instructed_revision", _prepare, required=True)
runner.register_finalizer("instructed_revision", _finalize)
