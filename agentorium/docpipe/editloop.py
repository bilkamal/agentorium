"""Shared worker → validator → supervisor loop that produces approved block edits."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import settings
from . import prompts
from .edits import validate_edit


@dataclass
class EditOutcome:
    approved: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)      # edit + "note"
    needs_human: list[dict] = field(default_factory=list)   # edit + "note"
    skipped: list[dict] = field(default_factory=list)       # {"source_id", "reason"}
    extra: dict = field(default_factory=dict)               # other keys of the last worker reply


def _clean(raw, blocks_by_id, basis: dict[str, str], doc_texts: list[str], rejected: list) -> list[dict]:
    out: dict[str, dict] = {}
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        bid = str(r.get("block_id") or "")
        block = blocks_by_id.get(bid)
        if block is None:
            continue
        new = str(r.get("new_text") or "").strip()
        sids = [str(s) for s in (r.get("source_ids") or r.get("finding_ids") or r.get("instruction_ids") or [])
                if str(s) in basis]
        e = {"block_id": bid, "new_text": new, "source_ids": sids, "reason": str(r.get("reason") or "")}
        if new == block.text:
            continue
        if not sids:
            rejected.append({**e, "note": "validator: edit tanpa dasar yang dikenal"})
            continue
        why = validate_edit(block.text, new, [basis[s] for s in sids], doc_texts)
        if why:
            rejected.append({**e, "note": f"validator: {why}"})
            continue
        out[bid] = e  # one edit per block: the last one wins
    return list(out.values())


async def edit_loop(*, wllm, sllm, worker_system: str, user_for, section_path: str, blocks_by_id: dict,
                    basis: dict[str, str], doc_texts: list[str], purpose: str, hooks=None, job=None, task=None,
                    worker=None, supervisor=None, schema=None) -> EditOutcome:
    """`user_for(feedback)` builds the worker prompt; `basis` maps source id → text the edits may rely on."""
    res = EditOutcome()
    schema = schema or prompts.EDIT_SCHEMA
    reply = await wllm.complete_json(worker_system, [{"role": "user", "content": user_for(None)}],
                                     schema, purpose=purpose)
    res.extra = {k: v for k, v in reply.items() if k not in ("edits", "skipped")}
    res.skipped = [{"source_id": str(s.get("source_id") or s.get("finding_id") or s.get("instruction_id") or ""),
                    "reason": str(s.get("reason") or "")} for s in reply.get("skipped") or [] if isinstance(s, dict)]
    candidates = _clean(reply.get("edits"), blocks_by_id, basis, doc_texts, res.rejected)
    rounds = 0
    while candidates:
        if hooks:
            hooks.submitted(job, task, worker, supervisor, len(candidates))
        verdict_reply = await sllm.complete_json(
            prompts.EDIT_SUPERVISOR_SYSTEM,
            [{"role": "user", "content": prompts.edit_supervisor_user(section_path, blocks_by_id, candidates, basis)}],
            prompts.SUPERVISOR_SCHEMA, purpose=f"{purpose}_verdict")
        verdicts = {}
        for v in verdict_reply.get("verdicts") or []:
            if isinstance(v, dict) and isinstance(v.get("index"), int):
                verdicts.setdefault(v["index"], v)
        to_revise = []
        for i, e in enumerate(candidates):
            v = verdicts.get(i)
            kind, fb = (v.get("verdict"), str(v.get("feedback") or "")) if v else (None, "")
            if kind == "approve":
                res.approved.append(e)
            elif kind == "reject":
                res.rejected.append({**e, "note": f"supervisor: {fb}".strip()})
            elif kind == "revise":
                to_revise.append((e, fb))
            else:
                res.needs_human.append({**e, "note": "supervisor tidak memberi keputusan"})
        if not to_revise:
            break
        if rounds >= settings.max_revisions:
            res.needs_human += [{**e, "note": f"supervisor: {fb}".strip()} for e, fb in to_revise]
            break
        rounds += 1
        reply = await wllm.complete_json(worker_system, [{"role": "user", "content": user_for(to_revise)}],
                                         schema, purpose=f"{purpose}_revise")
        candidates = _clean(reply.get("edits"), blocks_by_id, basis, doc_texts, res.rejected)
    return res
