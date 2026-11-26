import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("eval_update", Path(__file__).parents[2] / "scripts" / "eval_update.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def test_data_xlsx_readable():
    from agentorium.docpipe.update import extract_data

    (src,) = extract_data("data-november.xlsx", ev.data_xlsx())
    assert "286000000" in src["numbers"] and "november" in src["numbers"]


def test_score_update():
    text = "Laporan bulan November 2026. Penjualan naik dari Rp 260 juta menjadi Rp 286 juta, atau naik 10 persen."
    r = ev.score_update(text, old_text="Laporan bulan Oktober 2026.")
    assert r["expected_found"]["penjualan_286"] and r["expected_found"]["growth_10"]
    assert r["period_mentioned"] and not r["expected_found"]["responden_130"]
