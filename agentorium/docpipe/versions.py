"""Carry block identity (`uid`) from one version of a document to the next."""
from __future__ import annotations

import uuid
from difflib import SequenceMatcher

from .models import Block


def _fresh() -> str:
    return uuid.uuid4().hex


def assign_uids(new_blocks: list[Block], prev_blocks: list[Block] | None, *, threshold: float = 0.8) -> None:
    """Fill `uid` on `new_blocks` in place: unchanged or similar blocks inherit the previous uid."""
    if not prev_blocks:
        for b in new_blocks:
            b.uid = _fresh()
        return

    used: set[str] = set()
    prev_texts = [b.text for b in prev_blocks]
    new_texts = [b.text for b in new_blocks]
    opcodes = SequenceMatcher(None, prev_texts, new_texts, autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            for k in range(i2 - i1):
                uid = prev_blocks[i1 + k].uid
                new_blocks[j1 + k].uid = uid if uid not in used else _fresh()
                used.add(new_blocks[j1 + k].uid)
        elif tag == "replace":
            candidates = list(range(i1, i2))
            for j in range(j1, j2):
                best, best_ratio = None, threshold
                for i in candidates:
                    if prev_blocks[i].uid in used:
                        continue
                    ratio = SequenceMatcher(None, prev_texts[i], new_texts[j], autojunk=False).ratio()
                    if ratio >= best_ratio:
                        best, best_ratio = i, ratio
                if best is None:
                    new_blocks[j].uid = _fresh()
                else:
                    new_blocks[j].uid = prev_blocks[best].uid
                    candidates.remove(best)
                used.add(new_blocks[j].uid)
        elif tag == "insert":
            for j in range(j1, j2):
                new_blocks[j].uid = _fresh()
                used.add(new_blocks[j].uid)
