"""Deterministic calculator for derived numbers in periodic updates: {{calc: EXPR | fmt}} placeholders.

EXPR is plain arithmetic (+ - * / parentheses, numbers with a '.' decimal point, no thousands separators).
No names, calls or powers: the AST is walked, never eval'd. fmt: num:<d> | pct:<d> | '' (integer if whole,
else 1 decimal). Output uses Indonesian number formatting (1.234,5)."""
from __future__ import annotations

import ast
import math
import re
from decimal import ROUND_HALF_UP, Decimal

from .consistency import _parse_number

PLACEHOLDER = re.compile(r"\{\{\s*calc\s*:\s*(.*?)\s*(?:\|\s*([a-z]+(?::\d)?)?\s*)?\}\}", re.S)
_AMBIGUOUS = re.compile(r"(?<![\d.])\d{1,3}(\.\d{3})+(?![\d.])")
_E_NOTATION = re.compile(r"\d[eE][+-]?\d")
MAX_NODES = 200


class CalcError(ValueError):
    pass


def evaluate(expr: str) -> float:
    if not expr.strip():
        raise CalcError("rumus kosong")
    if _AMBIGUOUS.search(expr):
        raise CalcError(f"tulis angka tanpa pemisah ribuan: {expr}")
    if _E_NOTATION.search(expr):
        raise CalcError(f"tulis angka biasa, bukan notasi e: {expr}")
    try:
        tree = ast.parse(expr, mode="eval")
    except (SyntaxError, RecursionError, MemoryError) as exc:
        raise CalcError(f"rumus tidak valid: {expr[:80]}") from exc
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise CalcError("rumus terlalu panjang")

    def walk(n):
        if isinstance(n, ast.Expression):
            return walk(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = walk(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = walk(n.left), walk(n.right)
            if isinstance(n.op, ast.Add):
                return a + b
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            if b == 0:
                raise CalcError("pembagian dengan nol")
            return a / b
        raise CalcError(f"rumus hanya boleh angka dan + - * / ( ): {expr}")

    value = walk(tree)
    if not math.isfinite(value):
        raise CalcError("hasil rumus tidak terhingga")
    return value


def literal_info(expr: str) -> list[tuple[str, bool]]:
    """Every numeric literal (normalized) and whether it only scales: a direct operand of * or /."""
    tree = ast.parse(expr, mode="eval")
    scaling = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Mult, ast.Div)):
            for side in (n.left, n.right):
                if isinstance(side, ast.Constant):
                    scaling.add(id(side))
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            v = _parse_number(str(int(n.value)) if float(n.value).is_integer() else repr(float(n.value)))
            if v is not None:
                out.append((v, id(n) in scaling))
    return out


def literals(expr: str) -> set[str]:
    out = set()
    for n in ast.walk(ast.parse(expr, mode="eval")):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            out.add(_parse_number(repr(float(n.value)) if isinstance(n.value, float) else str(n.value)))
    return {x for x in out if x is not None}


def format_id(value: float, fmt: str) -> str:
    if fmt and not re.fullmatch(r"(num|pct):\d", fmt):
        raise CalcError(f"format tidak dikenal: {fmt}")
    digits = int(fmt.split(":")[1]) if fmt else (0 if float(value).is_integer() else 1)
    rounded = Decimal(repr(abs(float(value)))).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    s = f"{rounded:,.{digits}f}"  # 1,234,567.89 — half-up, as people round
    s = s.replace(",", "_").replace(".", ",").replace("_", ".")
    negative = value < 0 and any(ch not in "0,." for ch in s)
    return ("-" if negative else "") + s


def resolve_placeholders(text: str) -> tuple[str, list[dict], set[str]]:
    """Replace every placeholder by its value. Returns (text, [{expr, fmt, value}], numeric literals used)."""
    formulas, lits = [], set()

    def repl(m):
        expr, fmt = m.group(1), m.group(2) or ""
        value = format_id(evaluate(expr), fmt)
        formulas.append({"expr": expr, "fmt": fmt, "value": value})
        lits.update(literals(expr))
        return value

    return PLACEHOLDER.sub(repl, text), formulas, lits
