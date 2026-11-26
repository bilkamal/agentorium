"""SQLite persistence for documents, versions, sections and blocks."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from ..config import settings
import numpy as np

from .models import Block, Chunk, Document, ParsedDoc, Section, Version

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, title TEXT NOT NULL, label TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  version_no INTEGER NOT NULL, source TEXT NOT NULL, parent_version_id TEXT, filename TEXT NOT NULL, file_path TEXT NOT NULL,
  original_path TEXT, status TEXT NOT NULL, error TEXT, warnings TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL, UNIQUE(document_id, version_no));
CREATE TABLE IF NOT EXISTS sections(id TEXT NOT NULL, version_id TEXT NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL, level INTEGER NOT NULL, heading TEXT NOT NULL, path TEXT NOT NULL, parent_id TEXT,
  PRIMARY KEY(version_id, id));
CREATE TABLE IF NOT EXISTS blocks(id TEXT NOT NULL, version_id TEXT NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
  uid TEXT NOT NULL, ordinal INTEGER NOT NULL, section_id TEXT NOT NULL, type TEXT NOT NULL, text TEXT NOT NULL,
  table_index INTEGER, row INTEGER, col INTEGER, PRIMARY KEY(version_id, id));
CREATE INDEX IF NOT EXISTS blocks_by_section ON blocks(version_id, section_id, ordinal);
CREATE INDEX IF NOT EXISTS blocks_by_uid ON blocks(uid);
-- phase2
CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
  section_id TEXT NOT NULL, block_ids TEXT NOT NULL, heading_path TEXT NOT NULL, content TEXT NOT NULL, ordinal INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS chunks_by_version ON chunks(version_id, ordinal);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(content, chunk_id UNINDEXED, version_id UNINDEXED,
  tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS chunk_vectors(chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
  version_id TEXT NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL);
"""

MIGRATIONS = {"versions": [("index_status", "TEXT NOT NULL DEFAULT 'none'"), ("index_error", "TEXT")]}

VERSION_FIELDS = ("status", "error", "warnings", "original_path", "file_path", "note", "index_status", "index_error")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r[1] for r in self._db.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        self._db.commit()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ documents
    def create_document(self, title: str, label: str = "") -> Document:
        doc = Document(id=_new_id(), title=title, label=label, created_at=time.time())
        with self._lock, self._db:
            self._db.execute("INSERT INTO documents(id,title,label,created_at) VALUES(?,?,?,?)",
                             (doc.id, doc.title, doc.label, doc.created_at))
        return doc

    def _document(self, row: sqlite3.Row) -> Document:
        latest = self.latest_ready_version(row["id"])
        return Document(id=row["id"], title=row["title"], label=row["label"], created_at=row["created_at"],
                        latest_version_id=latest.id if latest else None)

    def get_document(self, document_id: str) -> Document | None:
        row = self._db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        return self._document(row) if row else None

    def list_documents(self) -> list[Document]:
        rows = self._db.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
        return [self._document(r) for r in rows]

    def delete_document(self, document_id: str) -> bool:
        with self._lock, self._db:
            self._db.execute("DELETE FROM chunks_fts WHERE version_id IN (SELECT id FROM versions WHERE document_id=?)",
                             (document_id,))
            cur = self._db.execute("DELETE FROM documents WHERE id=?", (document_id,))
        return cur.rowcount > 0

    # ------------------------------------------------------------- versions
    @staticmethod
    def _version(row: sqlite3.Row) -> Version:
        data = dict(row)
        data["warnings"] = json.loads(data["warnings"] or "[]")
        return Version(**data)

    def add_version(self, document_id: str, *, source: str, filename: str, parent_version_id: str | None,
                    note: str = "") -> Version:
        ext = Path(filename).suffix.lower()
        with self._lock, self._db:
            n = self._db.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM versions WHERE document_id=?",
                                 (document_id,)).fetchone()[0]
            v = Version(id=_new_id(), document_id=document_id, version_no=n, source=source,
                        parent_version_id=parent_version_id, filename=filename,
                        file_path=f"docs/{document_id}/v{n}{ext}", original_path=None, status="processing",
                        error=None, warnings=[], note=note, created_at=time.time())
            row = asdict(v)
            row["warnings"] = json.dumps(v.warnings)
            cols = ",".join(row)
            self._db.execute(f"INSERT INTO versions({cols}) VALUES({','.join('?' * len(row))})", tuple(row.values()))
        return v

    def update_version(self, version_id: str, **fields) -> Version:
        unknown = set(fields) - set(VERSION_FIELDS)
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        if "warnings" in fields:
            fields["warnings"] = json.dumps(list(fields["warnings"]))
        if fields:
            sets = ",".join(f"{k}=?" for k in fields)
            with self._lock, self._db:
                self._db.execute(f"UPDATE versions SET {sets} WHERE id=?", (*fields.values(), version_id))
        return self.get_version(version_id)

    def get_version(self, version_id: str) -> Version | None:
        row = self._db.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone()
        return self._version(row) if row else None

    def list_versions(self, document_id: str) -> list[Version]:
        rows = self._db.execute("SELECT * FROM versions WHERE document_id=? ORDER BY version_no", (document_id,)).fetchall()
        return [self._version(r) for r in rows]

    def latest_ready_version(self, document_id: str) -> Version | None:
        row = self._db.execute("SELECT * FROM versions WHERE document_id=? AND status='ready' "
                               "ORDER BY version_no DESC LIMIT 1", (document_id,)).fetchone()
        return self._version(row) if row else None

    def delete_version(self, version_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM chunks_fts WHERE version_id=?", (version_id,))
            self._db.execute("DELETE FROM versions WHERE id=?", (version_id,))

    # ------------------------------------------------------------ structure
    def save_structure(self, version_id: str, parsed: ParsedDoc) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM blocks WHERE version_id=?", (version_id,))
            self._db.execute("DELETE FROM sections WHERE version_id=?", (version_id,))
            self._db.executemany(
                "INSERT INTO sections(id,version_id,ordinal,level,heading,path,parent_id) VALUES(?,?,?,?,?,?,?)",
                [(s.id, version_id, s.ordinal, s.level, s.heading, s.path, s.parent_id) for s in parsed.sections])
            self._db.executemany(
                "INSERT INTO blocks(id,version_id,uid,ordinal,section_id,type,text,table_index,row,col) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                [(b.id, version_id, b.uid, b.ordinal, b.section_id, b.type, b.text, b.table_index, b.row, b.col)
                 for b in parsed.blocks])

    def get_sections(self, version_id: str) -> list[Section]:
        rows = self._db.execute("SELECT id,ordinal,level,heading,path,parent_id FROM sections WHERE version_id=? "
                                "ORDER BY ordinal", (version_id,)).fetchall()
        return [Section(**dict(r)) for r in rows]

    def get_blocks(self, version_id: str, section_id: str | None = None) -> list[Block]:
        sql = "SELECT id,uid,ordinal,section_id,type,text,table_index,row,col FROM blocks WHERE version_id=?"
        args: tuple = (version_id,)
        if section_id is not None:
            sql += " AND section_id=?"
            args += (section_id,)
        rows = self._db.execute(sql + " ORDER BY ordinal", args).fetchall()
        return [Block(**dict(r)) for r in rows]


    # --------------------------------------------------------------- chunks
    def save_chunks(self, version_id: str, chunks: list[Chunk]) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM chunks_fts WHERE version_id=?", (version_id,))
            self._db.execute("DELETE FROM chunks WHERE version_id=?", (version_id,))
            self._db.executemany(
                "INSERT INTO chunks(id,version_id,section_id,block_ids,heading_path,content,ordinal) VALUES(?,?,?,?,?,?,?)",
                [(c.id, version_id, c.section_id, json.dumps(c.block_ids), c.heading_path, c.content, c.ordinal)
                 for c in chunks])
            self._db.executemany("INSERT INTO chunks_fts(content,chunk_id,version_id) VALUES(?,?,?)",
                                 [(c.content, c.id, version_id) for c in chunks])

    def get_chunks(self, version_id: str) -> list[Chunk]:
        rows = self._db.execute("SELECT * FROM chunks WHERE version_id=? ORDER BY ordinal", (version_id,)).fetchall()
        return [Chunk(**{**dict(r), "block_ids": json.loads(r["block_ids"])}) for r in rows]

    def save_vectors(self, version_id: str, chunk_ids: list[str], matrix: np.ndarray) -> None:
        m = np.asarray(matrix, dtype=np.float32)
        with self._lock, self._db:
            self._db.execute("DELETE FROM chunk_vectors WHERE version_id=?", (version_id,))
            self._db.executemany("INSERT INTO chunk_vectors(chunk_id,version_id,dim,vec) VALUES(?,?,?,?)",
                                 [(cid, version_id, m.shape[1], m[i].tobytes()) for i, cid in enumerate(chunk_ids)])

    def load_vectors(self, version_id: str) -> tuple[list[str], np.ndarray]:
        rows = self._db.execute("SELECT v.chunk_id, v.dim, v.vec FROM chunk_vectors v JOIN chunks c ON c.id=v.chunk_id "
                                "WHERE v.version_id=? ORDER BY c.ordinal", (version_id,)).fetchall()
        if not rows:
            return [], np.zeros((0, 0), dtype=np.float32)
        return [r[0] for r in rows], np.vstack([np.frombuffer(r[2], dtype=np.float32) for r in rows])

    def fts_search(self, version_id: str, query: str, limit: int) -> list[str]:
        tokens = re.findall(r"\w+", query.lower())
        if not tokens:
            return []
        match = " OR ".join(f'"{t}"' for t in dict.fromkeys(tokens))
        rows = self._db.execute("SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ? AND version_id=? "
                                "ORDER BY bm25(chunks_fts) LIMIT ?", (match, version_id, limit)).fetchall()
        return [r[0] for r in rows]


_store: Store | None = None
_store_lock = threading.Lock()


def get_store() -> Store:
    """Process-wide store at settings.data_dir/docpipe.db (rebuilt when data_dir changes, e.g. in tests)."""
    global _store
    path = settings.data_dir / "docpipe.db"
    with _store_lock:
        if _store is None or _store.path != path:
            _store = Store(path)
        return _store
