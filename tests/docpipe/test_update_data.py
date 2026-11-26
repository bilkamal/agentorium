import io

import openpyxl
import pytest

from .test_review import team  # noqa: F401

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


KNOWN = {"286000000", "260000000"}


def test_formula_needs_a_sourced_literal_and_constants_only_scale():
    for bad in ("Rp {{calc: 3*100+7*10+4}} juta.", "Rp {{calc: 286000000 / 1000000 + 7}} juta.",
                "naik {{calc: 1e2}} persen."):
        e = {"new_text": bad}
        assert check_update_edit("Rp 260 juta.", e, KNOWN, doc_tokens=set()), bad
    e = {"new_text": "Rp {{calc: 286000000 / 1000000}} juta, naik {{calc: (286000000 - 260000000) / 260000000 * 100 | pct:1}} persen."}
    assert check_update_edit("Rp 260 juta, naik 5 persen.", e, KNOWN, doc_tokens=set()) is None


def test_unit_word_rule_only_covers_its_own_span():
    e = {"new_text": "Rp 286 juta, naik 286 persen."}
    assert "286" in check_update_edit("Rp 260 juta, naik 5 persen.", e, KNOWN, doc_tokens=set())


def test_row_numbers_and_sheet_names_are_not_sources():
    rows = "Kode;Nilai\n" + "".join(f"Toko{chr(65 + i % 26)};{1000 + i}\n" for i in range(60))
    (src,) = extract_data("d.csv", rows.encode())
    assert "37" not in src["numbers"] and "1037" in src["numbers"]


def test_single_column_csv_and_english_numbers():
    (one,) = extract_data("one.csv", b"Penjualan\n1500\n1700\n")
    assert "1700" in one["numbers"]
    (en,) = extract_data("en.csv", b"Item,Sales,Share\nA,\"1,500.25\",0.15\nB,\"2,000\",0.85\n")
    assert {"1500.25", "2000", "0.15"} <= set(en["numbers"])


def test_large_data_keeps_new_period_rows_and_says_it_was_cut():
    from agentorium.docpipe.update import data_for_section

    lines = "\n".join(f"Penjualan baris {i}: Bulan=Oktober | Toko=T{i} | Nilai={i * 10}" for i in range(2, 590))
    lines += "\n" + "\n".join(f"Penjualan baris {i}: Bulan=November | Toko=T{i} | Nilai={i * 10}" for i in range(590, 600))
    src = [{"name": "big.xlsx", "text": lines, "numbers": []}]
    out = data_for_section(src, "Penjualan bulan Oktober naik", period="November 2026", limit=3000)
    assert len(out) <= 3200 and out.count("Bulan=November") == 10 and "DATA DIPOTONG" in out


def test_sources_not_stored_in_job_params(tmp_settings, tmp_path, fake_embedder, team, monkeypatch):  # noqa: F811
    import asyncio

    from agentorium.docpipe import update
    from agentorium.docpipe.service import ingest
    from agentorium.docpipe.store import get_store

    from .fixtures import make_basic_docx

    monkeypatch.setattr(update.runner, "start", lambda job_id: asyncio.sleep(0))
    store = get_store()
    _, v = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())
    job = asyncio.run(update.start_update(v.id, period_label="Oktober 2026", files=[("d.csv", b"Bulan;Nilai\nOkt;1500\n")]))
    assert "sources" not in job.params and job.params["source_names"] == ["d.csv"]
    assert update.load_sources(job)[0]["text"].endswith("Bulan=Okt | Nilai=1500")
