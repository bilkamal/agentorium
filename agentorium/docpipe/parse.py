"""Turn DOCX/PDF files into ordered sections and blocks."""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .models import Block, ParsedDoc, Section

SYNTHETIC_SECTION_SIZE = 40
INTRO_HEADING = "Awal dokumen"
_HEADING_STYLE_RE = re.compile(r"^(?:heading|judul)\s*(\d)", re.I)


class ParseError(Exception):
    """The file could not be read as the expected format; message is safe to show."""


@dataclass
class _Item:
    type: str
    text: str
    level: int = 0
    table_index: int | None = None
    row: int | None = None
    col: int | None = None


# ---------------------------------------------------------------- docx helpers
def _style_chain(style):
    while style is not None:
        yield style
        style = style.base_style


def _ppr_child(ppr, tag: str):
    if ppr is None:
        return None
    from docx.oxml.ns import qn

    return ppr.find(qn(tag))


def _heading_level(paragraph) -> int:
    from docx.oxml.ns import qn

    for style in _style_chain(paragraph.style):
        name = style.name or ""
        m = _HEADING_STYLE_RE.match(name)
        if m:
            return max(1, int(m.group(1)))
        if name.lower() == "title":
            return 1
    candidates = [paragraph._p.pPr] + [s.element.pPr for s in _style_chain(paragraph.style)]
    for ppr in candidates:
        lvl = _ppr_child(ppr, "w:outlineLvl")
        if lvl is not None:
            val = int(lvl.get(qn("w:val"), "9"))
            return val + 1 if val < 9 else 0
    return 0


def _is_list(paragraph) -> bool:
    if _ppr_child(paragraph._p.pPr, "w:numPr") is not None:
        return True
    for style in _style_chain(paragraph.style):
        if _ppr_child(style.element.pPr, "w:numPr") is not None or "list" in (style.name or "").lower():
            return True
    return False


def _docx_items(path: Path) -> list[_Item]:
    import docx
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = docx.Document(str(path))
    except Exception as exc:  # zip errors, bad XML, wrong content type ...
        raise ParseError(f"Dokumen DOCX tidak bisa dibaca: {exc}") from exc

    items: list[_Item] = []
    table_index = 0
    for el in document.element.body.iterchildren():
        if el.tag == qn("w:p"):
            p = Paragraph(el, document)
            text = p.text.strip()
            if not text:
                continue
            level = _heading_level(p)
            kind = "heading" if level else ("list_item" if _is_list(p) else "paragraph")
            items.append(_Item(kind, text, level))
        elif el.tag == qn("w:tbl"):
            table = Table(el, document)
            seen = set()
            for r, row in enumerate(table.rows):
                for c, cell in enumerate(row.cells):
                    if cell._tc in seen:
                        continue
                    seen.add(cell._tc)
                    text = cell.text.strip()
                    if text:
                        items.append(_Item("table_cell", text, 0, table_index, r, c))
            table_index += 1
    return items


def _build(items: list[_Item]) -> ParsedDoc:
    sections: list[Section] = []
    blocks: list[Block] = []

    def new_section(heading: str, level: int, parent_id: str | None, path: str) -> Section:
        s = Section(id=f"s{len(sections):04d}", ordinal=len(sections), level=level, heading=heading,
                    path=path, parent_id=parent_id)
        sections.append(s)
        return s

    def add_block(item: _Item, section: Section) -> None:
        n = len(blocks)
        blocks.append(Block(id=f"b{n:05d}", uid="", ordinal=n, section_id=section.id, type=item.type,
                            text=item.text, table_index=item.table_index, row=item.row, col=item.col))

    if not any(i.type == "heading" for i in items):
        for start in range(0, len(items), SYNTHETIC_SECTION_SIZE):
            label = f"Bagian {start // SYNTHETIC_SECTION_SIZE + 1}"
            s = new_section(label, 1, None, label)
            for item in items[start:start + SYNTHETIC_SECTION_SIZE]:
                add_block(item, s)
        return ParsedDoc(sections=sections, blocks=blocks, warnings=[])

    stack: list[Section] = []
    current: Section | None = None
    for item in items:
        if item.type == "heading":
            while stack and stack[-1].level >= item.level:
                stack.pop()
            parent = stack[-1] if stack else None
            path = f"{parent.path} > {item.text}" if parent else item.text
            current = new_section(item.text, item.level, parent.id if parent else None, path)
            stack.append(current)
        elif current is None:
            current = new_section(INTRO_HEADING, 1, None, INTRO_HEADING)
        add_block(item, current)
    return ParsedDoc(sections=sections, blocks=blocks, warnings=[])


def parse_docx(path: Path) -> ParsedDoc:
    return _build(_docx_items(path))


def _has_revisions(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise ParseError(f"Dokumen DOCX tidak bisa dibaca: {exc}") from exc
    return b"<w:ins " in xml or b"<w:del " in xml or b"<w:ins>" in xml or b"<w:del>" in xml


def accept_tracked_changes(src: Path, dst: Path) -> bool:
    """Write a copy of `src` with every tracked change accepted to `dst`; False (no file) if there were none."""
    if not _has_revisions(src):
        return False
    from adeu import RedlineEngine

    try:
        engine = RedlineEngine(io.BytesIO(src.read_bytes()))
        engine.accept_all_revisions(remove_comments=False)
        dst.write_bytes(engine.save_to_stream().getvalue())
    except Exception as exc:
        raise ParseError(f"Tracked changes tidak bisa diterima: {exc}") from exc
    return True


# ---------------------------------------------------------------------- pdf
def parse_pdf(path: Path) -> ParsedDoc:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise ParseError("PDF dilindungi password")
        pages = [page.extract_text() or "" for page in reader.pages]
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"PDF tidak bisa dibaca: {exc}") from exc

    sections: list[Section] = []
    blocks: list[Block] = []
    for page_no, text in enumerate(pages, start=1):
        paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip()]
        if not paragraphs:
            continue
        heading = f"Halaman {page_no}"
        s = Section(id=f"s{len(sections):04d}", ordinal=len(sections), level=1, heading=heading, path=heading,
                    parent_id=None)
        sections.append(s)
        for para in paragraphs:
            n = len(blocks)
            blocks.append(Block(id=f"b{n:05d}", uid="", ordinal=n, section_id=s.id, type="paragraph", text=para))
    return ParsedDoc(sections=sections, blocks=blocks, warnings=[])
