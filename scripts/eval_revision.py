"""Measure automatic revision and revision-from-materials on the review eval document.

Usage: .venv/bin/python scripts/eval_revision.py [--model haiku]
Review → automatic revision → checks on the new version (planted misspellings gone, numbers kept, untouched
paragraphs unchanged, same block structure) → revision from notes (5 instructions, one about a section that does not
exist) → follow-up table. Writes data/evals/revision-<timestamp>.json. Uses the Claude Code quota.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentorium.docpipe.edits import _tokens  # noqa: E402

BAD_WORDS = ["menganalisa", "praktek", "Aktifitas", "memperbaharui", "sistim", "Kwalitas", "Frekwensi", "nasehat",
             "Resiko", "merubah", "Ijin", "jadual"]
NOTES = """Catatan revisi dari Ketua Pengurus:
1. Anggaran kegiatan yang benar Rp 540 juta, sesuaikan kalimat anggaran di bagian Pelaksanaan Kegiatan.
2. Jumlah responden survei yang benar 125 orang; sesuaikan di Pendahuluan.
3. Rapat evaluasi bulanan dilaksanakan tanggal 21 Oktober 2026, bukan tanggal 12.
4. Ganti "Unit Pelayanan Anggota" menjadi "Unit Layanan Anggota" di Pendahuluan.
5. Perbarui daftar foto di bagian Lampiran Dokumentasi Kegiatan."""
UNLOCATABLE = "Lampiran"


def score_revision(old_blocks, new_blocks, *, bad_words: list[str], touched_uids: set[str]) -> dict:
    old_text = "\n".join(b.text for b in old_blocks)
    new_text = "\n".join(b.text for b in new_blocks)
    present = [w for w in bad_words if w in old_text]
    still = [w for w in present if w in new_text]
    new_by_uid = {b.uid: b for b in new_blocks}
    untouched_changed = [b.uid for b in old_blocks if b.uid not in touched_uids
                         and b.uid in new_by_uid and new_by_uid[b.uid].text != b.text]
    lost = []
    for b in old_blocks:
        nb = new_by_uid.get(b.uid)
        if nb is not None:
            lost += sorted(_tokens(b.text) - _tokens(nb.text))
    return {"bad_words": len(present), "fixed_words": len(present) - len(still), "still_wrong": still,
            "untouched_changed": untouched_changed, "numbers_lost": lost,
            "same_structure": [b.type for b in old_blocks] == [b.type for b in new_blocks]}


def score_followup(instructions, *, unlocatable: str) -> dict:
    n = {s: sum(i.status == s for i in instructions) for s in ("done", "not_applicable", "needs_human")}
    bad = [i for i in instructions if unlocatable.lower() in i.text.lower()]
    return {"instructions": len(instructions), **n, "unlocatable_not_done": bool(bad) and all(i.status != "done" for i in bad)}


async def run_once(model: str, work: Path) -> dict:
    from agentorium.config import settings
    from agentorium.docpipe import instructed, review, revise
    from agentorium.docpipe.jobs import runner
    from agentorium.docpipe.service import ingest
    from agentorium.docpipe.store import get_store
    from agentorium.registry import AgentDef, registry
    from tests.docpipe.eval_doc import make_eval_docx

    data = work / "data"
    data.mkdir(parents=True)
    models = ROOT / "data" / "models"
    if models.exists():
        (data / "models").symlink_to(models)
    object.__setattr__(settings, "data_dir", data)
    registry.agents = [AgentDef(id="sup", name="Supervisor", role="supervisor", provider="claude_code", model=model),
                       AgentDef(id="w1", name="Worker 1", role="worker", provider="claude_code", model=model),
                       AgentDef(id="w2", name="Worker 2", role="worker", provider="claude_code", model=model)]
    for kind, fn in (("section_review", review.review_task), ("revision_section", revise.revision_task),
                     ("instructed_section", instructed.instructed_task)):
        runner.register(kind, fn)
    docx = work / "eval.docx"
    make_eval_docx(docx)
    store = get_store()
    _, v1 = ingest(store, filename="eval.docx", data=docx.read_bytes())
    out: dict = {"model": model}

    t0 = time.time()
    rjob = await review.start_review(v1.id, None)
    await runner.wait(rjob.id)
    out["review"] = {"status": store.get_job(rjob.id).status, "counts": store.count_findings(rjob.id),
                     "duration_s": round(time.time() - t0, 1)}

    t0 = time.time()
    job = await revise.start_revision(rjob.id, None)
    await runner.wait(job.id)
    job = store.get_job(job.id)
    changes = store.get_changes(job_id=job.id)
    out["revision"] = {"status": job.status, "duration_s": round(time.time() - t0, 1),
                       "changes": {s: sum(c.status == s for c in changes)
                                   for s in ("applied", "needs_human", "rejected", "suggestion")}}
    if job.params.get("result_version_id"):
        v2 = store.get_version(job.params["result_version_id"])
        touched = {c.block_uid for c in changes if c.status == "applied"}
        out["revision"].update(version_no=v2.version_no, **score_revision(
            store.get_blocks(v1.id), store.get_blocks(v2.id), bad_words=BAD_WORDS, touched_uids=touched))

    t0 = time.time()
    ijob = await instructed.start_instructed_revision(v1.id, notes=NOTES, files=[], use_comments=False)
    await runner.wait(ijob.id)
    ijob = store.get_job(ijob.id)
    ins = store.get_instructions(ijob.id)
    out["instructed"] = {"status": ijob.status, "duration_s": round(time.time() - t0, 1),
                         **score_followup(ins, unlocatable=UNLOCATABLE),
                         "rows": [{"text": i.text, "status": i.status, "response": i.response} for i in ins]}
    if ijob.params.get("result_version_id"):
        v3 = store.get_version(ijob.params["result_version_id"])
        text = "\n".join(b.text for b in store.get_blocks(v3.id))
        out["instructed"].update(version_no=v3.version_no, parent_is_v1=v3.parent_version_id == v1.id,
                                 has_540="Rp 540 juta" in text and "Rp 450 juta" not in text,
                                 has_125="125 responden" in text and "120 responden" not in text)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="haiku")
    args = ap.parse_args()
    out_dir = ROOT / "data" / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        r = asyncio.run(run_once(args.model, Path(tmp)))
    print(json.dumps({k: v for k, v in r.items()}, ensure_ascii=False, indent=1))
    path = out_dir / f"revision-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(r, ensure_ascii=False, indent=1))
    print("saved", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
