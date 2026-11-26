"""Deterministic checks on what a worker returned: every finding/fact must point at a real block
and quote it exactly (whitespace and quote style aside). Nothing the model invents gets through."""
from __future__ import annotations

import re

from .models import Block

CATEGORIES = ("language", "consistency", "structure", "substance")
SEVERITIES = ("low", "medium", "high")
FACT_KINDS = ("number", "date", "term", "name", "claim")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"',
                         "«": '"', "»": '"'})


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).replace(" ", " ").translate(_QUOTES)).strip()


def _quote_in_block(quote, block: Block) -> bool:
    q = normalize_ws(quote or "")
    return bool(q) and q in normalize_ws(block.text)


def _text(v) -> str:
    return str(v).strip() if v is not None else ""


def validate_finding(raw, blocks_by_id: dict[str, Block]) -> tuple[dict | None, str | None]:
    if not isinstance(raw, dict):
        return None, "not an object"
    block = blocks_by_id.get(_text(raw.get("block_id")))
    if block is None:
        return None, f"unknown block {raw.get('block_id')!r}"
    if not _quote_in_block(raw.get("quote"), block):
        return None, "quote not found in block"
    category, severity = _text(raw.get("category")).lower(), _text(raw.get("severity")).lower()
    if category not in CATEGORIES:
        return None, f"invalid category {category!r}"
    if severity not in SEVERITIES:
        return None, f"invalid severity {severity!r}"
    description = _text(raw.get("description"))
    if not description:
        return None, "empty description"
    try:
        confidence = min(1.0, max(0.0, float(raw.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    return {"block_id": block.id, "category": category, "severity": severity, "quote": _text(raw.get("quote")),
            "description": description, "suggestion": _text(raw.get("suggestion")), "confidence": confidence}, None


def validate_fact(raw, blocks_by_id: dict[str, Block]) -> dict | None:
    if not isinstance(raw, dict):
        return None
    block = blocks_by_id.get(_text(raw.get("block_id")))
    kind, key, value = _text(raw.get("kind")).lower(), _text(raw.get("key")), _text(raw.get("value"))
    if block is None or kind not in FACT_KINDS or not key or not value or not _quote_in_block(raw.get("quote"), block):
        return None
    return {"block_id": block.id, "kind": kind, "key": key, "value": value, "unit": _text(raw.get("unit")),
            "quote": _text(raw.get("quote"))}
