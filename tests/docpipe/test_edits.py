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
