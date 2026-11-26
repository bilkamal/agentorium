import io

import docx

from agentorium.docpipe.edits import BlockEdit, apply_block_edits, validate_edit
from agentorium.docpipe.parse import parse_docx

from .fixtures import make_basic_docx


def blocks_of(data: bytes, tmp_path, name="x.docx"):
    p = tmp_path / name
    p.write_bytes(data)
    return parse_docx(p).blocks


def edit(old, new):
    return BlockEdit(block_id="b", old_text=old, new_text=new, reason="r", sources=[])


def test_apply_single_edit_tracked_and_clean(tmp_path):
    src = make_basic_docx(tmp_path).read_bytes()
    old = "Kami telah menganalisa data penjualan bulan September."
    tracked, clean, results = apply_block_edits(src, [edit(old, old.replace("menganalisa", "menganalisis"))])
    assert [r.status for r in results] == ["applied"]
    texts = [b.text for b in blocks_of(clean, tmp_path)]
    assert "Kami telah menganalisis data penjualan bulan September." in texts
    assert b"w:ins" in _document_xml(tracked) and b"w:del" in _document_xml(tracked)
    assert b"w:ins" not in _document_xml(clean)
    assert "Data dikumpulkan dari sistem kasir." in texts  # untouched


def _document_xml(data: bytes) -> bytes:
    import zipfile

    return zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml")


def test_duplicate_block_text_needs_human(tmp_path):
    d = docx.Document()
    d.add_paragraph("Sama persis.")
    d.add_paragraph("Sama persis.")
    d.add_paragraph("Lain.")
    p = tmp_path / "dup.docx"
    d.save(p)
    tracked, clean, results = apply_block_edits(p.read_bytes(), [edit("Sama persis.", "Diubah.")])
    assert results[0].status == "needs_human" and results[0].error
    assert tracked is None and clean is None


def test_missing_target_needs_human(tmp_path):
    src = make_basic_docx(tmp_path).read_bytes()
    _, clean, results = apply_block_edits(src, [edit("Tidak ada di dokumen.", "x"),
                                                edit("Data dikumpulkan dari sistem kasir.", "Data diambil dari sistem kasir.")])
    assert [r.status for r in results] == ["needs_human", "applied"]
    assert "Data diambil dari sistem kasir." in [b.text for b in blocks_of(clean, tmp_path)]


def test_formatting_preserved(tmp_path):
    src = make_basic_docx(tmp_path).read_bytes()
    old = "Kami telah menganalisa data penjualan bulan September."
    _, clean, _ = apply_block_edits(src, [edit(old, old.replace("menganalisa", "menganalisis"))])
    p = tmp_path / "clean.docx"
    p.write_bytes(clean)
    para = next(x for x in docx.Document(p).paragraphs if "menganalisis" in x.text)
    assert any(r.bold and "penjualan" in r.text for r in para.runs)


def test_edit_changing_unrelated_number_rejected():
    reason = validate_edit("Penjualan naik 12% menjadi Rp 1.200 juta.", "Penjualan meningkat 15% menjadi Rp 1.200 juta.",
                           basis_texts=["kata 'naik' kurang baku"], doc_texts=[])
    assert reason and "12" in reason
    assert validate_edit("Rapat tanggal 12 Oktober 2026.", "Rapat diadakan tanggal 21 Oktober 2026.",
                         basis_texts=["perbaiki kalimat"], doc_texts=[])


def test_number_change_allowed_when_in_basis():
    assert validate_edit("Survei terhadap 120 responden.", "Survei terhadap 125 responden.",
                         basis_texts=["Jumlah responden 120 berbeda dengan 125 di bab 4"], doc_texts=[]) is None


def test_new_number_needs_source():
    assert validate_edit("Penjualan naik.", "Penjualan naik 30 persen.", basis_texts=["kurang rinci"], doc_texts=[])
    assert validate_edit("Penjualan naik.", "Penjualan naik 30 persen.", basis_texts=["kurang rinci"],
                         doc_texts=["Tabel: kenaikan 30 persen"]) is None


def test_empty_or_wild_length_rejected():
    assert validate_edit("Kalimat yang cukup panjang di sini.", "  ", basis_texts=[], doc_texts=[])
    assert validate_edit("Kalimat yang cukup panjang di sini.", "Ya.", basis_texts=[], doc_texts=[])
    assert validate_edit("Pendek.", "Pendek. " * 10, basis_texts=[], doc_texts=[])
    assert validate_edit("Kami menganalisa data.", "Kami menganalisis data.", basis_texts=[], doc_texts=[]) is None


def test_number_formats_equivalent():
    assert validate_edit("Nilai 1.200 juta.", "Nilainya 1.200 juta.", basis_texts=[], doc_texts=[]) is None


def test_mixed_formatting_preserved_and_changed_word_keeps_style(tmp_path):
    d = docx.Document()
    p = d.add_paragraph("Hasil ")
    r = p.add_run("analisa")
    r.italic = True
    p.add_run(" menunjukkan ")
    r = p.add_run("kenaikan tajam")
    r.bold = True
    p.add_run(" bulan ini.")
    path = tmp_path / "fmt.docx"
    d.save(path)
    old = "Hasil analisa menunjukkan kenaikan tajam bulan ini."
    _, clean, results = apply_block_edits(path.read_bytes(), [edit(old, "Hasil analisis menunjukkan kenaikan tajam bulan ini.")])
    assert results[0].status == "applied", results[0].error
    out = tmp_path / "out.docx"
    out.write_bytes(clean)
    para = docx.Document(out).paragraphs[0]
    assert para.text == "Hasil analisis menunjukkan kenaikan tajam bulan ini."
    assert any(r.italic and "analisis" in r.text for r in para.runs)
    assert any(r.bold and "kenaikan tajam" in r.text for r in para.runs)


def test_carry_markup():
    from agentorium.docpipe.edits import carry_markup

    assert carry_markup("Kami **data penjualan** bulan", "Kami data penjualan bulan", "Kami data penjualan tahun") == \
        "Kami **data penjualan** tahun"
    assert carry_markup("A _miring_ b", "A miring b", "A condong b") == "A _condong_ b"
    assert carry_markup("**satu dua**", "satu dua", "dua") is None  # opener deleted → caller falls back


def test_grounded_replacement_may_drop_the_old_value():
    from agentorium.docpipe.edits import validate_edit

    basis = ["Anggaran kegiatan yang benar Rp 540 juta"]
    assert validate_edit("Anggaran Rp 450 juta.", "Anggaran Rp 540 juta.", basis, []) is None
    assert validate_edit("Rapat 12 Oktober.", "Rapat 21 Oktober.", ["tanggal rapat 21 Oktober"], []) is None
    why = validate_edit("Anggaran Rp 450 juta pada 12 Oktober.", "Anggaran Rp 540 juta pada Oktober.", basis, [])
    assert why and "12" in why


def test_edit_paragraph_with_word_comment(tmp_path):
    from agentorium.docpipe.edits import BlockEdit, apply_block_edits

    from .test_materials import commented_docx

    old = "Data dikumpulkan dari sistem kasir."
    new = "Data dikumpulkan dari aplikasi kasir."
    tracked, clean, results = apply_block_edits(commented_docx(tmp_path), [BlockEdit("b1", old, new, "x")])
    assert results[0].status == "applied", results[0].error
    import io

    import docx
    assert new in [p.text for p in docx.Document(io.BytesIO(clean)).paragraphs]


def test_grounded_credit_not_reused_for_an_unrelated_number():
    from agentorium.docpipe.edits import validate_edit

    why = validate_edit("Peserta 450 orang dalam 12 kelas.", "Peserta 540 orang dalam 13 kelas.",
                        ["Ganti jumlah peserta 450 menjadi 540"], ["Ada 13 ruang."])
    assert why and "12" in why


def _three(tmp_path, texts, name="t.docx"):
    d = docx.Document()
    for t in texts:
        d.add_paragraph(t)
    p = tmp_path / name
    d.save(p)
    return p.read_bytes()


def test_repeated_text_is_edited_at_its_own_occurrence(tmp_path):
    # a heading that also appears in the table of contents; the edit targets the second occurrence only
    src = _three(tmp_path, ["2.2.2 Interpretasi Periode", "Isi pertama.", "2.2.2 Interpretasi Periode", "Isi kedua."])
    e = BlockEdit("b3", "2.2.2 Interpretasi Periode", "2.2.2 Hasil Kepatuhan", "r", [], occurrence=1, total=2)
    _, clean, results = apply_block_edits(src, [e])
    assert [r.status for r in results] == ["applied"], results
    assert [b.text for b in blocks_of(clean, tmp_path)] == [
        "2.2.2 Interpretasi Periode", "Isi pertama.", "2.2.2 Hasil Kepatuhan", "Isi kedua."]


def test_repeated_text_first_occurrence_and_short_cell_text(tmp_path):
    src = _three(tmp_path, ["Status", "Status", "Status", "Lain."])
    e = BlockEdit("b1", "Status", "Status/Jenis", "r", [], occurrence=0, total=3)
    _, clean, results = apply_block_edits(src, [e])
    assert [r.status for r in results] == ["applied"]
    assert [b.text for b in blocks_of(clean, tmp_path)] == ["Status/Jenis", "Status", "Status", "Lain."]


def test_occurrence_count_mismatch_stays_needs_human(tmp_path):
    src = _three(tmp_path, ["Sama.", "Sama.", "Lain."])
    e = BlockEdit("b1", "Sama.", "Beda.", "r", [], occurrence=0, total=3)  # caller's view disagrees with the file
    tracked, _, results = apply_block_edits(src, [e])
    assert results[0].status == "needs_human" and tracked is None


def test_multi_paragraph_block_edited_per_paragraph(tmp_path):
    d = docx.Document()
    t = d.add_table(rows=1, cols=1)
    cell = t.cell(0, 0)
    cell.paragraphs[0].text = "COLLECTOR HEALTH"
    cell.add_paragraph("2 Normal")
    cell.add_paragraph("Snapshot terakhir 12:12.")
    d.add_paragraph("Ringkasan: 2 Normal")
    p = tmp_path / "cell.docx"
    d.save(p)
    old = "COLLECTOR HEALTH\n2 Normal\nSnapshot terakhir 12:12."
    new = "COLLECTOR HEALTH\n2 terdaftar, Health tidak terbaca\nSnapshot terakhir 12:12."
    _, clean, results = apply_block_edits(p.read_bytes(), [BlockEdit("b0", old, new, "r", [], occurrence=0, total=1)])
    assert [r.status for r in results] == ["applied"], results
    out = docx.Document(io.BytesIO(clean))
    assert [q.text for q in out.tables[0].cell(0, 0).paragraphs] == [
        "COLLECTOR HEALTH", "2 terdaftar, Health tidak terbaca", "Snapshot terakhir 12:12."]
    assert out.paragraphs[-1].text == "Ringkasan: 2 Normal"


def test_duplicate_neighbour_line_may_be_deleted(tmp_path):
    from agentorium.docpipe.editloop import _clean
    from agentorium.docpipe.models import Block

    def blk(i, text):
        return Block(id=f"b{i}", uid=f"u{i}", ordinal=i, section_id="s", type="paragraph", text=text)

    bs = {b.id: b for b in [blk(1, "x AS period_end,"), blk(2, "x AS period_end,"), blk(3, "FROM t")]}
    rej = []
    out = _clean([{"block_id": "b2", "new_text": "", "finding_ids": ["f"]}], bs, {"f": "duplikat"}, [], rej)
    assert [e["block_id"] for e in out] == ["b2"] and not rej
    out = _clean([{"block_id": "b3", "new_text": "", "finding_ids": ["f"]}], bs, {"f": "hapus"}, [], rej)
    assert out == [] and "kosong" in rej[-1]["note"]


def test_repeat_counted_as_whole_paragraphs_not_substrings(tmp_path):
    # "Status" as a cell of its own three times, and inside other sentences many times
    src = _three(tmp_path, ["Status perangkat aktif.", "Status", "Status", "Status", "Status kolektor."])
    e = BlockEdit("b2", "Status", "Status/Jenis", "r", [], occurrence=1, total=3)
    _, clean, results = apply_block_edits(src, [e])
    assert [r.status for r in results] == ["applied"], results
    assert [b.text for b in blocks_of(clean, tmp_path)] == [
        "Status perangkat aktif.", "Status", "Status/Jenis", "Status", "Status kolektor."]


def test_location_label_from_prompt_is_not_written_into_the_document():
    from agentorium.docpipe.editloop import _clean
    from agentorium.docpipe.models import Block

    b = Block(id="b1", uid="u", ordinal=1, section_id="s", type="table_cell", text="2 Normal", table_index=4, row=3,
              col=1)
    out = _clean([{"block_id": "b1", "new_text": "(tabel 5, baris 4, kolom 2) 2 terdaftar", "finding_ids": ["f"]}],
                 {"b1": b}, {"f": "x"}, [], [])
    assert out[0]["new_text"] == "2 terdaftar"


def _deleted_inserted(tracked: bytes) -> tuple[str, str]:
    from lxml import etree

    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    x = etree.fromstring(_document_xml(tracked))
    dels = "".join(t.text or "" for t in x.iter(w + "delText"))
    ins = "".join(t.text or "" for i in x.iter(w + "ins") for t in i.iter(w + "t"))
    return dels, ins


def test_located_edit_marks_only_the_changed_words(tmp_path):
    src = _three(tmp_path, ["2.2.2 Interpretasi Periode Pelaporan", "Isi.", "2.2.2 Interpretasi Periode Pelaporan"])
    e = BlockEdit("b3", "2.2.2 Interpretasi Periode Pelaporan", "2.2.2 Hasil Periode Pelaporan", "r", [], 1, 2)
    tracked, clean, results = apply_block_edits(src, [e])
    assert [r.status for r in results] == ["applied"]
    dels, ins = _deleted_inserted(tracked)
    assert dels.strip() == "Interpretasi" and ins.strip() == "Hasil", (dels, ins)
    assert [b.text for b in blocks_of(clean, tmp_path)][2] == "2.2.2 Hasil Periode Pelaporan"


def test_located_insertion_marks_only_the_inserted_words(tmp_path):
    src = _three(tmp_path, ["Status", "Status"])
    tracked, clean, results = apply_block_edits(src, [BlockEdit("b2", "Status", "Status/Jenis", "r", [], 1, 2)])
    assert [r.status for r in results] == ["applied"]
    dels, ins = _deleted_inserted(tracked)
    assert len(dels) + len(ins) < len("Status") + len("Status/Jenis")
    assert [b.text for b in blocks_of(clean, tmp_path)] == ["Status", "Status/Jenis"]


def test_paragraph_is_not_split_by_an_edit(tmp_path):
    old = "Kalimat pertama di sini. Kalimat kedua yang panjang tentang data penjualan bulan ini."
    src = _three(tmp_path, [old, "Lain."])
    new = "Kalimat pertama di sini.\n\nKalimat kedua yang panjang tentang data penjualan bulan ini."
    tracked, clean, results = apply_block_edits(src, [BlockEdit("b1", old, new.replace("panjang", "lebih panjang"),
                                                                "r", [], 0, 1)])
    assert [r.status for r in results] == ["applied"]
    assert [b.text for b in blocks_of(clean, tmp_path)][0] == old.replace("panjang", "lebih panjang")
    dels, ins = _deleted_inserted(tracked)
    assert "Kalimat pertama" not in dels and len(ins) < 20, (dels, ins)
