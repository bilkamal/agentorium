"""Measure the periodic update: October eval report + November data (XLSX) → November report.

Usage: .venv/bin/python scripts/eval_update.py [--model haiku]
Checks the new version for the key November values, the derived growth computed by the calculator, the period
label, unchanged block structure, and what was held for a person. Writes data/evals/update-<timestamp>.json.
Uses the Claude Code quota.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PERIOD = "November 2026"
EXPECTED = {  # name → text that must appear in the November report
    "penjualan_286": "286 juta",
    "growth_10": ("10 persen", "10,0 persen"),
    "responden_130": "130 responden",
    "puas_85": "85 persen",
    "anggaran_600": "600 juta",
    "rapat_11nov": "11 November 2026",
    "pelatihan_20": "20 karyawan",
}


def data_xlsx() -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ringkasan"
    ws.append(["Indikator", "Oktober 2026", "November 2026"])
    for row in (["Penjualan (Rp)", 260000000, 286000000], ["Responden survei", 125, 130],
                ["Responden puas terhadap kasir (persen)", 82, 85], ["Anggaran kegiatan (Rp)", 540000000, 600000000],
                ["Realisasi belanja (Rp)", 540000000, 590000000], ["Peserta pelatihan kasir (orang)", 18, 20]):
        ws.append(row)
    ev = wb.create_sheet("Kegiatan")
    ev.append(["Kegiatan", "Tanggal"])
    ev.append(["Rapat evaluasi bulanan", "11 November 2026"])
    ev.append(["Pembahasan hasil survei", "11 November 2026"])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def score_update(new_text: str, *, old_text: str) -> dict:
    def found(v):
        return any(x in new_text for x in (v if isinstance(v, tuple) else (v,)))

    return {"expected_found": {k: found(v) for k, v in EXPECTED.items()},
            "period_mentioned": PERIOD in new_text,
            "old_period_left": new_text.count("Oktober 2026")}


async def run_once(model: str, work: Path) -> dict:
    from agentorium.config import settings
    from agentorium.docpipe import review, update
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
    runner.register("update_section", update.update_task)
    docx = work / "eval.docx"
    make_eval_docx(docx)
    store = get_store()
    _, v1 = ingest(store, filename="eval.docx", data=docx.read_bytes())
    t0 = time.time()
    job = await update.start_update(v1.id, period_label=PERIOD, files=[("data-november.xlsx", data_xlsx())])
    await runner.wait(job.id)
    job = store.get_job(job.id)
    changes = store.get_changes(job_id=job.id)
    out = {"model": model, "status": job.status, "duration_s": round(time.time() - t0, 1),
           "changes": {s: sum(c.status == s for c in changes) for s in ("applied", "needs_human", "rejected")},
           "held": [{"old": c.old_text, "note": c.note} for c in changes if c.status == "needs_human"],
           "formulas": [c.reason.split("rumus: ", 1)[1] for c in changes if "rumus: " in c.reason]}
    if job.params.get("result_version_id"):
        v2 = store.get_version(job.params["result_version_id"])
        old = [b for b in store.get_blocks(v1.id)]
        new = [b for b in store.get_blocks(v2.id)]
        out.update(version_no=v2.version_no, same_structure=[b.type for b in old] == [b.type for b in new],
                   **score_update("\n".join(b.text for b in new), old_text="\n".join(b.text for b in old)),
                   new_text=[b.text for b in new])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="haiku")
    args = ap.parse_args()
    out_dir = ROOT / "data" / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        r = asyncio.run(run_once(args.model, Path(tmp)))
    print(json.dumps(r, ensure_ascii=False, indent=1))
    path = out_dir / f"update-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(r, ensure_ascii=False, indent=1))
    print("saved", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
