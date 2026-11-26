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
_REVISION_RE = re.compile(rb"<(?:\w+:)?(?:ins|del|moveFrom|moveTo|pPrChange|rPrChange)[\s>/]")
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


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
_SKIP_TEXT_IN = {_W + "del", _W + "moveFrom", _MC_FALLBACK}


def _para_text(p_el) -> str:
    """Visible text of a w:p: runs, hyperlinks, fields and content controls; no deleted/moved-away text."""
    parts: list[str] = []

    def walk(el):
        for child in el:
            tag = child.tag
            if tag in _SKIP_TEXT_IN:
                continue
            if tag == _W + "t":
                parts.append(child.text or "")
            elif tag == _W + "tab":
                parts.append("\t")
            elif tag in (_W + "br", _W + "cr"):
                parts.append("\n")
            else:
                walk(child)

    walk(p_el)
    return "".join(parts)


def _docx_items(path: Path) -> list[_Item]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = docx.Document(str(path))
    except Exception as exc:  # zip errors, bad XML, wrong content type ...
        raise ParseError(f"Dokumen DOCX tidak bisa dibaca: {exc}") from exc

    items: list[_Item] = []
    table_counter = [0]

    def cell_children(tc):
        """Direct paragraphs/tables of a cell, unwrapping content controls."""
        for child in tc:
            if child.tag == _W + "sdt":
                content = child.find(_W + "sdtContent")
                if content is not None:
                    yield from cell_children(content)
            elif child.tag in (_W + "p", _W + "tbl"):
                yield child

    def do_table(tbl_el, parent) -> None:
        table = Table(tbl_el, parent)
        index = table_counter[0]
        table_counter[0] += 1
        seen = set()
        nested: list = []
        for r, row in enumerate(table.rows):
            for c, cell in enumerate(row.cells):
                if cell._tc in seen:
                    continue
                seen.add(cell._tc)
                texts = []
                for child in cell_children(cell._tc):
                    if child.tag == _W + "p":
                        texts.append(_para_text(child))
                    else:
                        nested.append((child, cell))
                text = "\n".join(t for t in texts if t.strip()).strip()
                if text:
                    items.append(_Item("table_cell", text, 0, index, r, c))
                for tbl, owner in nested:
                    do_table(tbl, owner)
                nested.clear()

    def walk(container) -> None:
        for el in container.iterchildren():
            if el.tag == _W + "p":
                text = _para_text(el).strip()
                if not text:
                    continue
                p = Paragraph(el, document)
                level = _heading_level(p)
                kind = "heading" if level else ("list_item" if _is_list(p) else "paragraph")
                items.append(_Item(kind, text, level))
            elif el.tag == _W + "tbl":
                do_table(el, document)
            elif el.tag == _W + "sdt":
                content = el.find(_W + "sdtContent")
                if content is not None:
                    walk(content)

    walk(document.element.body)
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
    try:
        return _build(_docx_items(path))
    except ParseError:
        raise
    except Exception as exc:  # malformed values inside otherwise valid XML
        raise ParseError(f"Struktur DOCX tidak bisa diproses: {exc}") from exc


def _has_revisions(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise ParseError(f"Dokumen DOCX tidak bisa dibaca: {exc}") from exc
    return _REVISION_RE.search(xml) is not None


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
