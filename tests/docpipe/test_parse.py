import pytest

from agentorium.docpipe.parse import ParseError, accept_tracked_changes, parse_docx, parse_pdf

from .fixtures import (make_basic_docx, make_intro_docx, make_merged_table_docx, make_no_heading_docx,
                       make_outline_level_docx, make_pdf, make_tracked_docx)


def test_sections_from_headings(tmp_path):
    p = parse_docx(make_basic_docx(tmp_path))
    assert [s.heading for s in p.sections] == ["Bab 1 Pendahuluan", "1.1 Latar", "Bab 2 Metode"]
    assert [s.level for s in p.sections] == [1, 2, 1]
    assert p.sections[1].path == "Bab 1 Pendahuluan > 1.1 Latar"
    assert p.sections[1].parent_id == p.sections[0].id
    assert p.sections[2].parent_id is None
    assert [s.id for s in p.sections] == ["s0000", "s0001", "s0002"]


def test_body_order_preserved(tmp_path):
    types = [b.type for b in parse_docx(make_basic_docx(tmp_path)).blocks]
    assert types == ["heading", "paragraph", "heading", "table_cell", "table_cell", "table_cell", "table_cell",
                     "paragraph", "heading", "paragraph", "list_item"]


def test_block_ids_and_uids(tmp_path):
    blocks = parse_docx(make_basic_docx(tmp_path)).blocks
    assert [b.id for b in blocks[:2]] == ["b00000", "b00001"]
    assert [b.ordinal for b in blocks] == list(range(len(blocks)))
    assert all(b.uid == "" for b in blocks)


def test_bold_run_text_is_joined(tmp_path):
    blocks = parse_docx(make_basic_docx(tmp_path)).blocks
    assert blocks[1].text == "Kami telah menganalisa data penjualan bulan September."
    assert blocks[1].section_id == "s0000"


def test_table_cells_have_coordinates(tmp_path):
    cells = [b for b in parse_docx(make_basic_docx(tmp_path)).blocks if b.type == "table_cell"]
    assert [(b.table_index, b.row, b.col, b.text) for b in cells] == [
        (0, 0, 0, "Bulan"), (0, 0, 1, "Nilai"), (0, 1, 0, "Sep"), (0, 1, 1, "1.200")]
    assert all(b.section_id == "s0001" for b in cells)


def test_merged_cells_not_duplicated(tmp_path):
    cells = [b for b in parse_docx(make_merged_table_docx(tmp_path)).blocks if b.type == "table_cell"]
    assert [(b.row, b.col, b.text) for b in cells] == [(0, 0, "Gabung"), (1, 0, "x"), (1, 1, "y")]


def test_text_before_first_heading_goes_to_intro_section(tmp_path):
    p = parse_docx(make_intro_docx(tmp_path))
    assert [(s.heading, s.level) for s in p.sections] == [("Awal dokumen", 1), ("Bab 1", 1)]
    assert p.blocks[0].section_id == p.sections[0].id


def test_outline_level_counts_as_heading(tmp_path):
    p = parse_docx(make_outline_level_docx(tmp_path))
    assert [s.heading for s in p.sections] == ["Judul Manual"]
    assert p.blocks[0].type == "heading"


def test_no_headings_makes_synthetic_sections(tmp_path):
    p = parse_docx(make_no_heading_docx(tmp_path, 95))
    assert [s.heading for s in p.sections] == ["Bagian 1", "Bagian 2", "Bagian 3"]
    assert len(p.blocks) == 95
    assert [b.section_id for b in p.blocks].count("s0002") == 15


def test_empty_paragraphs_skipped(tmp_path):
    assert all(b.text.strip() for b in parse_docx(make_basic_docx(tmp_path)).blocks)


def test_parse_tracked_changes_are_accepted(tmp_path):
    src = make_tracked_docx(tmp_path)
    dst = tmp_path / "w.docx"
    assert accept_tracked_changes(src, dst) is True
    texts = [b.text for b in parse_docx(dst).blocks]
    assert "1.1 Latar Belakang" in texts
    assert "1.1 Latar" not in texts


def test_accept_returns_false_for_clean_doc(tmp_path):
    dst = tmp_path / "w.docx"
    assert accept_tracked_changes(make_basic_docx(tmp_path), dst) is False
    assert not dst.exists()


def test_corrupt_docx_raises_parse_error(tmp_path):
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"bukan docx sama sekali")
    with pytest.raises(ParseError):
        parse_docx(bad)
    with pytest.raises(ParseError):
        accept_tracked_changes(bad, tmp_path / "x.docx")


def test_pdf_sections_per_page(tmp_path):
    p = parse_pdf(make_pdf(tmp_path))
    assert [s.heading for s in p.sections] == ["Halaman 1", "Halaman 2"]
    assert [b.text for b in p.blocks] == ["Halaman pertama berisi pendahuluan.", "Halaman kedua berisi kesimpulan."]
    assert [b.section_id for b in p.blocks] == ["s0000", "s0001"]


def test_corrupt_pdf_raises_parse_error(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-rusak")
    with pytest.raises(ParseError):
        parse_pdf(bad)
