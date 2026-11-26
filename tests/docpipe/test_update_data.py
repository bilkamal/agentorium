import io

import openpyxl
import pytest

from agentorium.docpipe.update import check_update_edit, extract_data


def xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Penjualan"
    ws.append(["Bulan", "Penjualan (Rp)", "Responden"])
    ws.append(["Oktober", 260000000, 125])
    ws.append(["November", 286000000, 131.5])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_extract_xlsx_rows_with_headers():
    (src,) = extract_data("data.xlsx", xlsx())
    assert src["name"] == "data.xlsx"
    assert "Penjualan baris 3: Bulan=November | Penjualan (Rp)=286000000 | Responden=131.5" in src["text"]
    assert {"286000000", "131.5", "november"} <= set(src["numbers"])


def test_extract_csv_and_text():
    (c,) = extract_data("d.csv", "Bulan;Anggaran\nNovember;610000000\n".encode())
    assert "baris 2: Bulan=November | Anggaran=610000000" in c["text"] and "610000000" in c["numbers"]
    (t,) = extract_data("catatan.txt", "Rapat evaluasi 18 November 2026.".encode())
    assert {"18", "2026", "november"} <= set(t["numbers"])
    with pytest.raises(ValueError, match="Format"):
        extract_data("x.exe", b"MZ")
    with pytest.raises(ValueError, match="rusak"):
        extract_data("x.xlsx", b"PK\x03\x04 broken")


def test_check_update_edit_resolves_calc_and_requires_sources():
    known = {"286000000", "260000000", "november", "131.5"}
    e = {"new_text": "Penjualan November naik {{calc: (286000000 - 260000000) / 260000000 * 100 | pct:1}} persen."}
    assert check_update_edit("Penjualan Oktober naik 20 persen.", e, known, doc_tokens=set()) is None
    assert e["new_text"] == "Penjualan November naik 10,0 persen."
    assert e["formulas"][0]["value"] == "10,0"

    e = {"new_text": "Penjualan November naik 11 persen."}
    why = check_update_edit("Penjualan Oktober naik 20 persen.", e, known, doc_tokens=set())
    assert why and "11" in why

    e = {"new_text": "Naik {{calc: (999 - 260000000) | num:0}}."}
    assert "999" in check_update_edit("Naik 5.", e, known, doc_tokens=set())

    e = {"new_text": "Naik {{calc: 1 / 0}}."}
    assert "nol" in check_update_edit("Naik 5.", e, known, doc_tokens=set())

    # numbers kept from the old text are fine; numbers from elsewhere in the old document only inside a formula
    e = {"new_text": "Survei 2026 diikuti 131.5 responden."}
    assert check_update_edit("Survei 2026 diikuti 125 responden.", e, known, doc_tokens=set()) is None


def test_number_with_unit_word_matches_scaled_data():
    known = {"286000000", "1200000000", "260000000"}
    e = {"new_text": "Penjualan naik menjadi Rp 286 juta; aset Rp 1,2 miliar."}
    assert check_update_edit("Penjualan Rp 260 juta; aset Rp 1 miliar.", e, known, doc_tokens=set()) is None
    e = {"new_text": "Penjualan naik menjadi Rp 287 juta."}
    assert "287" in check_update_edit("Penjualan Rp 260 juta.", e, known, doc_tokens=set())
