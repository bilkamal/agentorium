"""Cross-section consistency: facts with the same key but different values are checked by the supervisor."""
from __future__ import annotations

import re
from collections import defaultdict
from itertools import combinations

from .models import Fact, Job
from .store import get_store

MAX_PAIRS = 50
BATCH = 10

CONSISTENCY_SCHEMA = {
    "type": "object",
    "properties": {"conflicts": {"type": "array", "items": {
        "type": "object",
        "properties": {"pair": {"type": "integer"}, "is_conflict": {"type": "boolean"},
                       "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                       "description": {"type": "string"}},
        "required": ["pair", "is_conflict", "severity", "description"]}}},
    "required": ["conflicts"],
}

CONSISTENCY_SYSTEM = """Anda supervisor review dokumen. Anda menerima pasangan fakta dari dua bagian berbeda dokumen
yang tampak membicarakan hal yang sama tetapi nilainya berbeda. Untuk tiap pasangan putuskan apakah ini benar-benar
inkonsistensi (is_conflict=true) atau wajar (misalnya periode, cakupan, atau satuan berbeda → false).
Tulis description singkat dalam bahasa dokumen yang menyebut kedua nilai."""


def normalize_key(key: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", key.lower())).strip()


def _parse_number(value: str) -> str | None:
    m = re.search(r"-?\d[\d.,]*", value.replace(" ", ""))
    if not m:
        return None
    s = m.group(0).rstrip(".,")
    if "," in s and "." in s and s.rfind(".") > s.rfind(","):  # English: 1,500.25
        s = s.replace(",", "")
    elif "," in s:  # Indonesian: '.' thousands, ',' decimal
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        n = float(s)
    except ValueError:
        return None
    return str(int(n)) if n == int(n) else repr(n)


def normalize_value(value: str, kind: str) -> str:
    if kind == "number":
        n = _parse_number(value)
        if n is not None:
            return n
    return re.sub(r"\s+", " ", value.lower()).strip()


def candidate_conflicts(facts: list[Fact]) -> list[tuple[Fact, Fact]]:
    groups: dict[tuple[str, str], list[Fact]] = defaultdict(list)
    for f in facts:
        groups[(f.kind, normalize_key(f.key))].append(f)
    pairs: list[tuple[Fact, Fact]] = []
    for group in groups.values():
        for a, b in combinations(group, 2):
            if a.block_id == b.block_id:
                continue
            ua, ub = normalize_key(a.unit or ""), normalize_key(b.unit or "")
            if ua and ub and ua != ub:
                continue
            if normalize_value(a.value, a.kind) != normalize_value(b.value, b.kind):
                pairs.append((a, b))
                if len(pairs) >= MAX_PAIRS:
                    return pairs
    return pairs


async def check_consistency(job: Job) -> int:
    from . import review

    store = get_store()
    store.delete_job_level_findings(job.id)  # rerun after a pause/restart starts clean
    # a block the workers already flagged for consistency is not reported again
    flagged = {f.block_id for f in store.get_findings(job.id, status="valid", category="consistency")}
    pairs = [(a, b) for a, b in candidate_conflicts(store.get_facts(job.id))
             if a.block_id not in flagged and b.block_id not in flagged]
    if not pairs:
        return 0
    supervisor, _ = review.pick_agents()
    llm = review.make_llm(supervisor)
    texts = {b.id: b for b in store.get_blocks(job.version_id)}
    created = 0
    for start in range(0, len(pairs), BATCH):
        batch = pairs[start:start + BATCH]
        lines = []
        for i, (a, b) in enumerate(batch):
            lines += [f"[{i}] {a.key}",
                      f"    A ({a.block_id}): {a.value} {a.unit} — \"{a.quote}\" — blok: {texts[a.block_id].text if a.block_id in texts else ''}",
                      f"    B ({b.block_id}): {b.value} {b.unit} — \"{b.quote}\" — blok: {texts[b.block_id].text if b.block_id in texts else ''}"]
        reply = await llm.complete_json(CONSISTENCY_SYSTEM, [{"role": "user", "content": "\n".join(lines)}],
                                        CONSISTENCY_SCHEMA, purpose="doc_consistency")
        rows = []
        for c in reply.get("conflicts") or []:
            if not isinstance(c, dict) or c.get("is_conflict") is not True or not isinstance(c.get("pair"), int):
                continue
            if not 0 <= c["pair"] < len(batch):
                continue
            a, b = batch[c["pair"]]
            sev = c.get("severity") if c.get("severity") in ("low", "medium", "high") else "medium"
            rows.append({"job_id": job.id, "task_id": None, "version_id": job.version_id, "block_id": a.block_id,
                         "other_block_id": b.block_id, "category": "consistency", "severity": sev, "quote": a.quote,
                         "description": str(c.get("description") or f"{a.key}: {a.value} vs {b.value}"),
                         "suggestion": "", "confidence": 0.8, "status": "valid", "validator_note": None})
        if rows:
            store.add_findings(rows)
            created += len(rows)
    return created
