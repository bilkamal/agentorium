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

from .models import Block, Change, Chunk, Document, Fact, Finding, Job, ParsedDoc, Section, Task, Version

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
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  version_id TEXT NOT NULL REFERENCES versions(id) ON DELETE CASCADE, type TEXT NOT NULL, status TEXT NOT NULL,
  params TEXT NOT NULL DEFAULT '{}', total_tasks INTEGER NOT NULL DEFAULT 0, done_tasks INTEGER NOT NULL DEFAULT 0,
  failed_tasks INTEGER NOT NULL DEFAULT 0, error TEXT, created_at REAL NOT NULL, started_at REAL, finished_at REAL,
  resume_at REAL);
CREATE INDEX IF NOT EXISTS jobs_by_version ON jobs(version_id, created_at);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL, section_id TEXT NOT NULL, block_ids TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, error TEXT, output TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS tasks_by_job ON tasks(job_id, ordinal);
CREATE TABLE IF NOT EXISTS findings(id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  task_id TEXT, version_id TEXT NOT NULL, block_id TEXT NOT NULL, other_block_id TEXT, category TEXT NOT NULL,
  severity TEXT NOT NULL, quote TEXT NOT NULL, description TEXT NOT NULL, suggestion TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0.5, status TEXT NOT NULL, validator_note TEXT, ordinal INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS findings_by_job ON findings(job_id, ordinal);
CREATE TABLE IF NOT EXISTS facts(id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  version_id TEXT NOT NULL, block_id TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
  unit TEXT NOT NULL DEFAULT '', quote TEXT NOT NULL, ordinal INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS changes(id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions(id) ON DELETE CASCADE,
  job_id TEXT, block_uid TEXT NOT NULL, block_id TEXT NOT NULL, old_text TEXT NOT NULL, new_text TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '', source_kind TEXT NOT NULL, source_ids TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL,
  note TEXT, ordinal INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS changes_by_version ON changes(version_id, ordinal);
"""

MIGRATIONS = {"versions": [("index_status", "TEXT NOT NULL DEFAULT 'none'"), ("index_error", "TEXT"),
                           ("tracked_path", "TEXT")]}

JOB_FIELDS = ("status", "params", "total_tasks", "error", "started_at", "finished_at", "resume_at")
TASK_FIELDS = ("status", "attempts", "error", "output")
VERSION_FIELDS = ("status", "error", "warnings", "original_path", "file_path", "note", "index_status", "index_error",
                  "tracked_path")


class _Result:
    """Fully fetched result of one statement (so no cursor is shared between threads)."""

    def __init__(self, rows: list, rowcount: int):
        self._rows, self.rowcount = rows, rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class _LockedConnection:
    """One SQLite connection shared by the event loop and worker threads: every statement and every
    `with db:` transaction runs under the same re-entrant lock, so readers never see another thread's
    half-finished work or an interleaved cursor."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock):
        self._conn, self._lock = conn, lock

    def execute(self, sql: str, args=()) -> _Result:
        with self._lock:
            cur = self._conn.execute(sql, args)
            return _Result(cur.fetchall(), cur.rowcount)

    def executemany(self, sql: str, rows) -> _Result:
        with self._lock:
            cur = self._conn.executemany(sql, rows)
            return _Result([], cur.rowcount)

    def executescript(self, sql: str) -> None:
        with self._lock:
            self._conn.executescript(sql)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def __enter__(self):
        self._lock.acquire()
        self._conn.__enter__()
        return self

    def __exit__(self, *exc):
        try:
            return self._conn.__exit__(*exc)
        finally:
            self._lock.release()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        self._db = _LockedConnection(conn, self._lock)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r[1] for r in self._db.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        self._db.commit()

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


    # ----------------------------------------------------------------- jobs
    def _job(self, r: sqlite3.Row) -> Job:
        return Job(**{**dict(r), "params": json.loads(r["params"])})

    def create_job(self, document_id: str, version_id: str, type: str, params: dict | None = None) -> Job:
        jid = _new_id()
        with self._lock, self._db:
            self._db.execute("INSERT INTO jobs(id,document_id,version_id,type,status,params,created_at) "
                             "VALUES(?,?,?,?,'queued',?,?)",
                             (jid, document_id, version_id, type, json.dumps(params or {}), time.time()))
        return self.get_job(jid)

    def get_job(self, job_id: str) -> Job | None:
        r = self._db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._job(r) if r else None

    def list_jobs(self, version_id: str | None = None, statuses: tuple[str, ...] | None = None) -> list[Job]:
        q, args = "SELECT * FROM jobs WHERE 1=1", []
        if version_id:
            q += " AND version_id=?"
            args.append(version_id)
        if statuses:
            q += f" AND status IN ({','.join('?' * len(statuses))})"
            args.extend(statuses)
        return [self._job(r) for r in self._db.execute(q + " ORDER BY created_at DESC", args).fetchall()]

    def update_job(self, job_id: str, **fields) -> Job:
        unknown = set(fields) - set(JOB_FIELDS)
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        if "params" in fields:
            fields["params"] = json.dumps(fields["params"])
        if fields:
            with self._lock, self._db:
                self._db.execute(f"UPDATE jobs SET {','.join(f'{k}=?' for k in fields)} WHERE id=?",
                                 (*fields.values(), job_id))
        return self.get_job(job_id)

    def increment_job(self, job_id: str, field: str, by: int = 1) -> None:
        if field not in ("done_tasks", "failed_tasks", "total_tasks"):
            raise ValueError(field)
        with self._lock, self._db:
            self._db.execute(f"UPDATE jobs SET {field}={field}+? WHERE id=?", (by, job_id))

    def add_tasks(self, job_id: str, items: list[tuple[str, list[str], str]]) -> list[Task]:
        with self._lock, self._db:
            start = self._db.execute("SELECT COUNT(*) FROM tasks WHERE job_id=?", (job_id,)).fetchone()[0]
            self._db.executemany(
                "INSERT INTO tasks(id,job_id,ordinal,section_id,block_ids,kind,status) VALUES(?,?,?,?,?,?,'queued')",
                [(_new_id(), job_id, start + i, sid, json.dumps(bids), kind) for i, (sid, bids, kind) in enumerate(items)])
            self._db.execute("UPDATE jobs SET total_tasks=total_tasks+? WHERE id=?", (len(items), job_id))
        return self.get_tasks(job_id)

    def _task(self, r: sqlite3.Row) -> Task:
        return Task(**{**dict(r), "block_ids": json.loads(r["block_ids"]), "output": json.loads(r["output"])})

    def get_tasks(self, job_id: str) -> list[Task]:
        return [self._task(r) for r in
                self._db.execute("SELECT * FROM tasks WHERE job_id=? ORDER BY ordinal", (job_id,)).fetchall()]

    def get_task(self, task_id: str) -> Task | None:
        r = self._db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._task(r) if r else None

    def update_task(self, task_id: str, **fields) -> None:
        unknown = set(fields) - set(TASK_FIELDS)
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        if "output" in fields:
            fields["output"] = json.dumps(fields["output"])
        with self._lock, self._db:
            self._db.execute(f"UPDATE tasks SET {','.join(f'{k}=?' for k in fields)} WHERE id=?",
                             (*fields.values(), task_id))

    def requeue_running_tasks(self, job_id: str) -> int:
        with self._lock, self._db:
            return self._db.execute("UPDATE tasks SET status='queued' WHERE job_id=? AND status='running'",
                                    (job_id,)).rowcount

    # ------------------------------------------------------- findings/facts
    def _insert_rows(self, table: str, cols: tuple[str, ...], rows: list[dict]) -> list[str]:
        ids = []
        with self._lock, self._db:
            n = self._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for i, row in enumerate(rows):
                rid = _new_id()
                ids.append(rid)
                self._db.execute(f"INSERT INTO {table}(id,{','.join(cols)},ordinal) VALUES(?{',?' * len(cols)},?)",
                                 (rid, *(row.get(c) for c in cols), n + i))
        return ids

    FINDING_COLS = ("job_id", "task_id", "version_id", "block_id", "other_block_id", "category", "severity", "quote",
                    "description", "suggestion", "confidence", "status", "validator_note")
    FACT_COLS = ("job_id", "version_id", "block_id", "kind", "key", "value", "unit", "quote")

    def add_findings(self, rows: list[dict]) -> list[str]:
        return self._insert_rows("findings", self.FINDING_COLS, [{"suggestion": "", **r} for r in rows])

    def get_findings(self, job_id: str, status: str | None = None, category: str | None = None) -> list[Finding]:
        q, args = "SELECT * FROM findings WHERE job_id=?", [job_id]
        if status:
            q += " AND status=?"
            args.append(status)
        if category:
            q += " AND category=?"
            args.append(category)
        rows = self._db.execute(q + " ORDER BY ordinal", args).fetchall()
        return [Finding(**{k: r[k] for k in r.keys() if k != "ordinal"}) for r in rows]

    def delete_job_level_findings(self, job_id: str) -> None:
        """Findings not tied to a section task (the cross-section consistency pass)."""
        with self._db:
            self._db.execute("DELETE FROM findings WHERE job_id=? AND task_id IS NULL", (job_id,))

    def count_findings(self, job_id: str) -> dict[str, int]:
        rows = self._db.execute("SELECT status, COUNT(*) FROM findings WHERE job_id=? GROUP BY status", (job_id,))
        return {r[0]: r[1] for r in rows}

    CHANGE_COLS = ("version_id", "job_id", "block_uid", "block_id", "old_text", "new_text", "reason", "source_kind",
                   "source_ids", "status", "note")

    def add_changes(self, rows: list[dict]) -> list[str]:
        return self._insert_rows("changes", self.CHANGE_COLS,
                                 [{"reason": "", **r, "source_ids": json.dumps(list(r.get("source_ids") or []))}
                                  for r in rows])

    def get_changes(self, version_id: str | None = None, job_id: str | None = None) -> list[Change]:
        q, args = "SELECT * FROM changes WHERE 1=1", []
        if version_id:
            q += " AND version_id=?"
            args.append(version_id)
        if job_id:
            q += " AND job_id=?"
            args.append(job_id)
        rows = self._db.execute(q + " ORDER BY ordinal", args).fetchall()
        return [Change(**{**{k: r[k] for k in r.keys() if k != "ordinal"}, "source_ids": json.loads(r["source_ids"])})
                for r in rows]

    def add_facts(self, rows: list[dict]) -> list[str]:
        return self._insert_rows("facts", self.FACT_COLS, [{"unit": "", **r} for r in rows])

    def get_facts(self, job_id: str) -> list[Fact]:
        rows = self._db.execute("SELECT * FROM facts WHERE job_id=? ORDER BY ordinal", (job_id,)).fetchall()
        return [Fact(**{k: r[k] for k in r.keys() if k != "ordinal"}) for r in rows]


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
