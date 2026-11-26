import pytest

from agentorium.docpipe.calc import CalcError, evaluate, format_id, resolve_placeholders


def test_evaluate_arithmetic_only():
    assert evaluate("(260 - 200) / 200 * 100") == pytest.approx(30.0)
    assert evaluate("-(5 + 2.5) * 2") == -15.0
    for bad in ("__import__('os')", "2 ** 10", "x + 1", "max(1, 2)", "1 / 0", "", "1.500 + 1"):
        with pytest.raises(CalcError):
            evaluate(bad)


def test_format_indonesian():
    assert format_id(1234567.891, "num:2") == "1.234.567,89"
    assert format_id(30.0, "pct:1") == "30,0"
    assert format_id(1200, "num:0") == "1.200"
    assert format_id(12.345, "") == "12,3"
    assert format_id(260, "") == "260"
    assert format_id(-0.04, "num:1") == "0,0"


def test_resolve_placeholders_returns_text_values_and_literals():
    text = "Penjualan naik {{calc: (260 - 200) / 200 * 100 | pct:1}} persen menjadi Rp {{calc: 260000000 / 1000000}} juta."
    out, formulas, literals = resolve_placeholders(text)
    assert out == "Penjualan naik 30,0 persen menjadi Rp 260 juta."
    assert [f["value"] for f in formulas] == ["30,0", "260"]
    assert literals == {"260", "200", "100", "260000000", "1000000"}


def test_resolve_placeholder_error():
    with pytest.raises(CalcError):
        resolve_placeholders("naik {{calc: 1 / 0 | pct:1}} persen")
    with pytest.raises(CalcError):
        resolve_placeholders("naik {{calc: 5 | bogus}}")
