"""Measure review quality on a document with planted errors.

Usage: .venv/bin/python scripts/eval_review.py [--model haiku] [--runs 1]
Runs a real review with Claude Code agents (supervisor + 2 workers) in a throwaway data folder and writes
recall / precision / coverage / duration / LLM calls to data/evals/<timestamp>.json.
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

from agentorium.docpipe.models import Block, Finding  # noqa: E402


def score(seeded, findings: list[Finding], blocks: list[Block]) -> dict:
    """A seeded error is found when a valid finding of its category sits on its block (or, for a conflict,
    on either side). Precision counts valid findings that hit any seeded error — a lower bound, since the
    reviewer may also report real problems nobody planted."""
    def block_ids(needle):
        return {b.id for b in blocks if needle and needle in b.text}

    targets = []
    for e in seeded:
        ids = block_ids(e.block_text_contains) | block_ids(e.alt_contains)
        targets.append((e, ids))
    valid = [f for f in findings if f.status == "valid"]
    found, missed, by_cat = 0, [], {}
    matched: set[int] = set()
    for e, ids in targets:
        hits = [i for i, f in enumerate(valid)
                if f.category == e.category and ({f.block_id, f.other_block_id} & ids)]
        matched.update(hits)
        c = by_cat.setdefault(e.category, {"seeded": 0, "found": 0})
        c["seeded"] += 1
        if hits:
            found += 1
            c["found"] += 1
        else:
            missed.append(e.block_text_contains)
    return {"seeded": len(seeded), "found": found, "recall": found / len(seeded) if seeded else 0.0,
            "valid_findings": len(valid), "matched_findings": len(matched),
            "precision": len(matched) / len(valid) if valid else 0.0, "missed": missed, "by_category": by_cat}


async def run_once(model: str, work: Path) -> dict:
    from agentorium.config import settings
    from agentorium.docpipe import review
    from agentorium.docpipe.jobs import runner
    from agentorium.docpipe.service import ingest
    from agentorium.docpipe.store import get_store
    from agentorium.registry import AgentDef, registry
    from tests.docpipe.eval_doc import make_eval_docx

    data = work / "data"
    data.mkdir(parents=True)
    models = ROOT / "data" / "models"  # reuse the downloaded embedding model
    if models.exists():
        (data / "models").symlink_to(models)
    object.__setattr__(settings, "data_dir", data)
    registry.agents = [AgentDef(id="sup", name="Supervisor", role="supervisor", provider="claude_code", model=model),
                       AgentDef(id="w1", name="Worker 1", role="worker", provider="claude_code", model=model),
                       AgentDef(id="w2", name="Worker 2", role="worker", provider="claude_code", model=model)]
    calls = {"n": 0}
    make = review.make_llm

    def counting(agent):
        client = make(agent)
        inner = client.complete_json

        async def complete_json(*a, **k):
            calls["n"] += 1
            return await inner(*a, **k)

        client.complete_json = complete_json
        return client

    review.make_llm = counting
    docx = work / "eval.docx"
    seeded = make_eval_docx(docx)
    store = get_store()
    _, version = ingest(store, filename="eval.docx", data=docx.read_bytes())
    t0 = time.time()
    job = await review.start_review(version.id, None)
    await runner.wait(job.id)
    job = store.get_job(job.id)
    result = score(seeded, store.get_findings(job.id), store.get_blocks(version.id))
    tasks = store.get_tasks(job.id)
    result.update({"model": model, "job_status": job.status, "duration_s": round(time.time() - t0, 1),
                   "llm_calls": calls["n"], "index_status": store.get_version(version.id).index_status,
                   "coverage": sum(t.status == "done" for t in tasks) / len(tasks) if tasks else 0.0,
                   "counts": store.count_findings(job.id)})
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args()
    out_dir = ROOT / "data" / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for i in range(args.runs):
        with tempfile.TemporaryDirectory() as tmp:
            r = asyncio.run(run_once(args.model, Path(tmp)))
        results.append(r)
        print(f"run {i + 1}: recall {r['recall']:.0%} ({r['found']}/{r['seeded']}), precision≥{r['precision']:.0%}, "
              f"coverage {r['coverage']:.0%}, {r['duration_s']}s, {r['llm_calls']} LLM calls, job {r['job_status']}")
        for cat, c in sorted(r["by_category"].items()):
            print(f"   {cat:12s} {c['found']}/{c['seeded']}")
        if r["missed"]:
            print("   missed:", "; ".join(r["missed"]))
    path = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print("saved", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
