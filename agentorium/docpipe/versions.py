"""Carry block identity (`uid`) from one version of a document to the next."""
from __future__ import annotations

import uuid
from difflib import SequenceMatcher

from .models import Block


WINDOW = 25  # candidates considered on each side of the expected position


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
            span_prev, span_new = i2 - i1, j2 - j1
            taken: set[int] = set()
            for j in range(j1, j2):
                # Only look near the proportional position: keeps large replaced regions linear.
                centre = i1 + ((j - j1) * span_prev) // max(span_new, 1)
                lo, hi = max(i1, centre - WINDOW), min(i2, centre + WINDOW + 1)
                best, best_ratio = None, threshold
                # The block at the expected position wins if it is similar enough (in-place edit);
                # otherwise search the window, nearest first, a farther one winning only with a better ratio.
                order = sorted(range(lo, hi), key=lambda k: abs(k - centre))
                if order and order[0] == centre and centre not in taken and prev_blocks[centre].uid not in used \
                        and SequenceMatcher(None, prev_texts[centre], new_texts[j], autojunk=False).ratio() >= threshold:
                    order = [centre]
                for i in order:
                    if i in taken or prev_blocks[i].uid in used:
                        continue
                    sm = SequenceMatcher(None, prev_texts[i], new_texts[j], autojunk=False)
                    floor = best_ratio if best is None else best_ratio + 1e-9
                    if sm.real_quick_ratio() < floor or sm.quick_ratio() < floor:
                        continue
                    ratio = sm.ratio()
                    if ratio >= floor:
                        best, best_ratio = i, ratio
                if best is None:
                    new_blocks[j].uid = _fresh()
                else:
                    new_blocks[j].uid = prev_blocks[best].uid
                    taken.add(best)
                used.add(new_blocks[j].uid)
        elif tag == "insert":
            for j in range(j1, j2):
                new_blocks[j].uid = _fresh()
                used.add(new_blocks[j].uid)
