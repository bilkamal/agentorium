"""Plain data objects shared by the document pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Section:
    id: str
    ordinal: int
    level: int
    heading: str
    path: str
    parent_id: str | None


@dataclass
class Block:
    id: str
    uid: str
    ordinal: int
    section_id: str
    type: str  # heading | paragraph | list_item | table_cell
    text: str
    table_index: int | None = None
    row: int | None = None
    col: int | None = None


@dataclass
class ParsedDoc:
    sections: list[Section]
    blocks: list[Block]
    warnings: list[str] = field(default_factory=list)


@dataclass
class Document:
    id: str
    title: str
    label: str
    created_at: float
    latest_version_id: str | None = None


@dataclass
class Version:
    id: str
    document_id: str
    version_no: int
    source: str  # upload | rollback | revision | instructed_revision | update
    parent_version_id: str | None
    filename: str
    file_path: str  # relative to settings.data_dir
    original_path: str | None
    status: str  # processing | ready | failed
    error: str | None
    warnings: list[str]
    note: str
    created_at: float
