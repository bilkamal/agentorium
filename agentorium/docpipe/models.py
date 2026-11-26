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
    index_status: str = "none"  # none | indexing | ready | fts_only | failed
    index_error: str | None = None
    tracked_path: str | None = None  # revisions: the same version as tracked changes against its parent


@dataclass
class Chunk:
    id: str
    version_id: str
    section_id: str
    block_ids: list[str]
    heading_path: str
    content: str
    ordinal: int


@dataclass
class Job:
    id: str
    document_id: str
    version_id: str
    type: str
    status: str  # queued | running | paused | finalizing | done | partial | failed | cancelled
    params: dict
    total_tasks: int
    done_tasks: int
    failed_tasks: int
    error: str | None
    created_at: float
    started_at: float | None
    finished_at: float | None
    resume_at: float | None


@dataclass
class Task:
    id: str
    job_id: str
    ordinal: int
    section_id: str
    block_ids: list[str]
    kind: str
    status: str  # queued | running | done | failed
    attempts: int
    error: str | None
    output: dict


@dataclass
class Finding:
    id: str
    job_id: str
    task_id: str | None
    version_id: str
    block_id: str
    other_block_id: str | None
    category: str
    severity: str
    quote: str
    description: str
    suggestion: str
    confidence: float
    status: str  # valid | rejected | needs_human
    validator_note: str | None


@dataclass
class Fact:
    id: str
    job_id: str
    version_id: str
    block_id: str
    kind: str
    key: str
    value: str
    unit: str
    quote: str


@dataclass
class Change:
    id: str
    version_id: str
    job_id: str | None
    block_uid: str
    block_id: str
    old_text: str
    new_text: str
    reason: str
    source_kind: str  # finding | instruction
    source_ids: list[str]
    status: str  # applied | needs_human | rejected | suggestion
    note: str | None


@dataclass
class Instruction:
    id: str
    job_id: str
    ordinal: int
    source: str  # comment | notes | file:<name>
    author: str
    text: str
    quote: str
    block_ids: list[str]
    candidate_block_ids: list[str]
    status: str  # pending | done | not_applicable | needs_human
    response: str
