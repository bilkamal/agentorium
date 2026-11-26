"""Builders for test documents (no binary fixtures in the repo)."""
from __future__ import annotations

import io
from pathlib import Path

import docx
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def _save(d, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(path))
    return path


def make_basic_docx(tmp: Path, name: str = "basic.docx") -> Path:
    d = docx.Document()
    d.add_heading("Bab 1 Pendahuluan", 1)
    p = d.add_paragraph("Kami telah menganalisa data ")
    p.add_run("penjualan").bold = True
    p.add_run(" bulan September.")
    d.add_heading("1.1 Latar", 2)
    t = d.add_table(rows=2, cols=2)
    for (r, c), text in {(0, 0): "Bulan", (0, 1): "Nilai", (1, 0): "Sep", (1, 1): "1.200"}.items():
        t.cell(r, c).text = text
    d.add_paragraph("Penjualan naik 12% dibanding Agustus.")
    d.add_heading("Bab 2 Metode", 1)
    d.add_paragraph("")  # empty paragraph must be skipped
    d.add_paragraph("Data dikumpulkan dari sistem kasir.")
    d.add_paragraph("Wawancara pemilik toko", style="List Bullet")
    return _save(d, tmp / name)


def make_intro_docx(tmp: Path) -> Path:
    d = docx.Document()
    d.add_paragraph("Ringkasan eksekutif sebelum bab pertama.")
    d.add_heading("Bab 1", 1)
    d.add_paragraph("Isi bab satu.")
    return _save(d, tmp / "intro.docx")


def make_merged_table_docx(tmp: Path) -> Path:
    d = docx.Document()
    d.add_heading("Tabel", 1)
    t = d.add_table(rows=2, cols=2)
    a = t.cell(0, 0).merge(t.cell(0, 1))
    a.text = "Gabung"
    t.cell(1, 0).text = "x"
    t.cell(1, 1).text = "y"
    return _save(d, tmp / "merged.docx")


def make_no_heading_docx(tmp: Path, n_paragraphs: int = 95) -> Path:
    d = docx.Document()
    for i in range(n_paragraphs):
        d.add_paragraph(f"Paragraf nomor {i + 1}.")
    return _save(d, tmp / "noheading.docx")


def make_outline_level_docx(tmp: Path) -> Path:
    d = docx.Document()
    p = d.add_paragraph("Judul Manual")
    ppr = p._p.get_or_add_pPr()
    lvl = OxmlElement("w:outlineLvl")
    lvl.set(qn("w:val"), "0")
    ppr.append(lvl)
    d.add_paragraph("Isi setelah judul manual.")
    return _save(d, tmp / "outline.docx")


def make_tracked_docx(tmp: Path) -> Path:
    from adeu import ModifyText, RedlineEngine

    src = make_basic_docx(tmp, "tracked_src.docx")
    eng = RedlineEngine(io.BytesIO(src.read_bytes()), author="Reviewer")
    eng.process_batch([ModifyText(target_text="1.1 Latar", new_text="1.1 Latar Belakang")])
    out = tmp / "tracked.docx"
    out.write_bytes(eng.save_to_stream().getvalue())
    return out


def make_pdf(tmp: Path, pages: tuple[str, ...] = ("Halaman pertama berisi pendahuluan.", "Halaman kedua berisi kesimpulan.")) -> Path:
    """Minimal valid PDF with one line of Helvetica text per page."""
    objs: list[bytes] = []
    n = len(pages)
    page_ids = [3 + 2 * i for i in range(n)]
    font_id = 3 + 2 * n
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{p} 0 R" for p in page_ids).encode()
    objs.append(b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(n).encode() + b" >>")
    for i, text in enumerate(pages):
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font_id} 0 R >> >> "
                    f"/Contents {page_ids[i] + 1} 0 R >>".encode())
        objs.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path = tmp / "doc.pdf"
    path.write_bytes(bytes(out))
    return path
