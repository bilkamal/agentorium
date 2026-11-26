# DocPipe Tahap 1 (Fondasi) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fork Agentorium ke repo ini dan tambahkan fondasi pipeline dokumen: adapter LLM Claude Code, penyimpanan SQLite, ingest DOCX/PDF menjadi section & blok, versioning (upload versi baru, rollback, unduh), dan tab **Dokumen** di UI web.

**Architecture:** Agentorium (FastAPI + UI statis) tetap utuh; kode baru berada di paket `agentorium/docpipe/` dan dipasang sebagai `APIRouter` di `/api/docs`. Data di satu SQLite `data/docpipe.db` + file versi di `data/docs/<doc_id>/`. LLM lewat `claude -p` headless (langganan Claude Code user).

**Tech Stack:** Python 3.12, FastAPI, sqlite3 (stdlib), python-docx, pypdf, adeu 4.2 (accept tracked changes), pytest + httpx TestClient, JS vanilla (tanpa build step, mengikuti Agentorium).

**Spec:** `docs/superpowers/specs/2026-11-26-agentorium-docpipe-design.md` (aturan perilaku dari `docs/superpowers/specs/2026-11-26-dcmt-review-revision-design.md` tetap berlaku).

## Global Constraints

- Repo ini = fork `https://github.com/bilkamal/agentorium` (remote `upstream`); file `LICENSE` (MIT) dan atribusi tidak diubah.
- Python 3.12; dependency baru: `adeu>=4.2,<5`, `pytest>=8`. Tidak ada dependency berat (torch/embedding) di tahap 1.
- Mode chat/kantor Agentorium yang lama harus tetap jalan (tidak ada endpoint lama yang berubah perilakunya).
- Server default bind `127.0.0.1`; tidak ada auth tambahan di tahap 1 (sama dengan Agentorium).
- Semua data di bawah `settings.data_dir` (`OFFICE_DATA_DIR`, default `data/`), yang sudah di-`.gitignore`.
- Format masuk pipeline dokumen: `.docx` dan `.pdf`; batas upload `100 MB`.
- Perintah Claude Code persis: `claude -p --output-format json --tools "" --no-session-persistence --strict-mcp-config --setting-sources "" --model <model> --system-prompt <system> [--json-schema <json>] [--effort <level>]`, prompt lewat stdin, cwd = `data/claude_cwd/` (folder kosong).
- `OFFICE_LLM_CONCURRENCY` default `2`; `OFFICE_CLAUDE_TIMEOUT` default `1800` detik; `OFFICE_CLAUDE_BIN` default `claude`.
- Teks UI baru berbahasa Indonesia dengan padanan Inggris (mengikuti `lang` Agentorium).

## Review Focus

1. DOCX berisi tracked changes/komentar yang belum diterima → versi kerja = semua perubahan di-accept, `warnings` berisi `tracked_changes_accepted`, file asli tetap bisa diunduh (`variant=original`). Test: Task 4 `test_parse_tracked_changes_are_accepted`, Task 6 `test_ingest_keeps_original_when_tracked`.
2. Tabel dengan sel gabungan (merged cells) → teks sel tidak terduplikasi, urutan body (paragraf–tabel–paragraf) terjaga. Test: Task 4 `test_merged_cells_not_duplicated`, `test_body_order_preserved`.
3. Dokumen tanpa style heading → section sintetis, tidak ada blok yang hilang. Test: Task 4 `test_no_headings_makes_synthetic_sections`.
4. Claude Code tidak terpasang / belum login / kena batas pemakaian → error yang berbeda dan jelas (`LLMError("... claude CLI not found")`, `LLMError` berisi pesan login, `LLMRateLimited`). Test: Task 2.
5. File rusak atau ekstensi palsu → HTTP 422 dengan pesan, tanpa baris versi atau file yatim. Test: Task 7 `test_corrupt_docx_returns_422_and_leaves_nothing`.

---

### Task 1: Fork Agentorium & siapkan test harness

**Files:**
- Create (via git): seluruh isi upstream di root repo ini
- Modify: `requirements.txt`, `.gitignore`
- Create: `requirements-dev.txt`, `tests/conftest.py`, `tests/test_smoke.py`, `pytest.ini`

**Interfaces:**
- Produces: fixture pytest `tmp_settings(monkeypatch, tmp_path)` yang mengarahkan `settings.data_dir` ke `tmp_path/data` (dipakai semua task berikutnya); app FastAPI di `agentorium.server:app`.

- [ ] **Step 1: Inisialisasi repo dari upstream tanpa menimpa `docs/superpowers/`**

```bash
cd /home/rahmat/mvp/dcmt-review-and-revision
git init -b main
git remote add upstream https://github.com/bilkamal/agentorium.git
git fetch upstream
git checkout -B main upstream/main   # file kita di docs/superpowers/ tidak bentrok dengan upstream
git branch --unset-upstream || true
ls agentorium web docs/superpowers
```
Expected: folder `agentorium/`, `web/`, dan `docs/superpowers/specs` + `plans` ada.

- [ ] **Step 2: Tambah dependency & venv**

`requirements.txt` tambah baris `adeu>=4.2,<5`. Buat `requirements-dev.txt` berisi `-r requirements.txt` dan `pytest>=8`. Tambah ke `.gitignore`: `.venv/`, `.pytest_cache/` (jika belum ada; `data/` sudah ada di upstream — pastikan).
Run: `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`
Expected: selesai tanpa error.

- [ ] **Step 3: Tulis smoke test**

`pytest.ini`: `[pytest]\ntestpaths = tests\nmarkers =\n    live: memanggil Claude Code sungguhan (jalankan manual: pytest -m live)\naddopts = -m "not live"`.

`tests/conftest.py`: fixture `tmp_settings` — `monkeypatch.setattr` pada objek frozen tidak bisa, jadi gunakan `object.__setattr__(settings, "data_dir", tmp_path / "data")` dan kembalikan nilai lama saat teardown.

`tests/test_smoke.py`:
```python
def test_app_index_and_config(tmp_settings):
    from fastapi.testclient import TestClient
    from agentorium.server import app
    c = TestClient(app)
    assert c.get("/").status_code == 200
    assert "providers" in c.get("/api/config").json()
```

- [ ] **Step 4: Jalankan**

Run: `.venv/bin/pytest -q`
Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: fork Agentorium, add pytest harness and design docs"
```

---

### Task 2: Adapter LLM `claude_code`

**Files:**
- Modify: `agentorium/config.py` (PROVIDERS, settings baru), `agentorium/llm.py`, `agentorium/registry.py:41` (`configured`), `agentorium/server.py` (`/api/config` providers), `web/index.html` (opsi provider), `.env.example`
- Test: `tests/test_llm_claude_code.py`, `tests/fake_claude.py`

**Interfaces:**
- Produces:
  - `config.PROVIDERS = ("anthropic", "openai", "claude_code", "mock")`
  - `Settings.llm_concurrency: int`, `Settings.claude_bin: str`, `Settings.claude_timeout: int`
  - `class LLMRateLimited(LLMError)` dengan atribut `reset_at: float | None`
  - `def build_claude_cmd(model: str, system: str, schema: dict | None, effort: str | None) -> list[str]` (fungsi modul di `llm.py`)
  - `async def LLMClient.complete_json(self, system: str, messages: list[dict], schema: dict, *, purpose: str = "") -> dict`
  - `LLMClient.last_usage: dict | None` (dari field `usage` output CLI; `None` untuk provider lain)
  - `LLMClient.complete(...)` mendukung `provider == "claude_code"` (tanpa schema → mengembalikan field `result`)

- [ ] **Step 1: Tulis fake CLI**

`tests/fake_claude.py`: skrip Python yang membaca env `FAKE_CLAUDE_MODE` (`ok` | `ratelimit` | `error` | `sleep` | `login`), menulis argv ke file `FAKE_CLAUDE_ARGS` (JSON) dan stdin ke `FAKE_CLAUDE_STDIN`, lalu mencetak JSON hasil:
- `ok`: `{"type":"result","is_error":false,"result":"{\"a\":1}","structured_output":{"a":1},"usage":{"input_tokens":5,"output_tokens":3}}`
- `ratelimit`: `{"type":"result","is_error":true,"api_error_status":429,"result":"Claude usage limit reached. Your limit will reset at 5pm"}`
- `error`: `{"type":"result","is_error":true,"api_error_status":500,"result":"boom"}`
- `login`: `{"type":"result","is_error":true,"api_error_status":401,"result":"Invalid API key · Please run /login"}`
- `sleep`: `time.sleep(10)`.
Test men-set `OFFICE_CLAUDE_BIN` ke file wrapper shell `#!/bin/sh\nexec <sys.executable> tests/fake_claude.py "$@"` yang dibuat di `tmp_path` (chmod +x).

- [ ] **Step 2: Tulis test yang gagal**

```python
async def test_complete_json_returns_structured_output(fake_claude):   # mode ok
    c = make_client("claude_code")   # helper test: LLMClient(AgentDef(id="t", name="T", role="worker", provider="claude_code", model="haiku"))
    out = await c.complete_json("sys", [{"role":"user","content":"halo"}], {"type":"object"})
    assert out == {"a": 1}
    assert c.last_usage["input_tokens"] == 5
    args = fake_claude.args()
    assert args[:3] == ["-p", "--output-format", "json"]
    assert args[args.index("--tools") + 1] == ""
    assert "--no-session-persistence" in args and "--strict-mcp-config" in args
    assert json.loads(args[args.index("--json-schema") + 1]) == {"type": "object"}
    assert fake_claude.stdin() == "halo"

async def test_multi_turn_messages_flattened(fake_claude):
    # messages user/assistant/user → stdin berisi "User: ...\n\nAssistant: ...\n\nUser: ..."

async def test_rate_limit_raises_llm_rate_limited(fake_claude):        # mode ratelimit
    with pytest.raises(LLMRateLimited): ...

async def test_login_error_message_mentions_login(fake_claude):        # mode login
    with pytest.raises(LLMError, match="claude login"): ...

async def test_other_error_raises_llm_error_not_rate_limited(fake_claude):  # mode error
    with pytest.raises(LLMError) as e: ...
    assert not isinstance(e.value, LLMRateLimited)

async def test_timeout_kills_process(fake_claude, monkeypatch):        # mode sleep, timeout 1s
    with pytest.raises(LLMError, match="timeout"): ...

async def test_missing_binary(monkeypatch):                            # OFFICE_CLAUDE_BIN=/nonexistent
    with pytest.raises(LLMError, match="claude CLI not found"): ...

async def test_concurrency_limited_to_setting(fake_claude):
    # llm_concurrency=2, mode sleep pendek (0.3s): 4 panggilan paralel butuh >= 0.6s

@pytest.mark.live
async def test_live_claude_code_structured():
    # model "haiku", schema {"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"]}
    # prompt "Balas ok=true" → out["ok"] is True
```
Tambah `pytest-asyncio`? **Tidak** — gunakan `asyncio.run(...)` di dalam test sinkron supaya tidak menambah dependency.

- [ ] **Step 3: Jalankan, pastikan gagal**

Run: `.venv/bin/pytest tests/test_llm_claude_code.py -q`
Expected: FAIL (`ImportError: LLMRateLimited` / provider tidak dikenal).

- [ ] **Step 4: Implementasi**

- `config.py`: tambah `"claude_code"` ke `PROVIDERS`; field `llm_concurrency` (`OFFICE_LLM_CONCURRENCY`, 2), `claude_bin` (`OFFICE_CLAUDE_BIN`, `"claude"`), `claude_timeout` (`OFFICE_CLAUDE_TIMEOUT`, 1800). Settings dibaca ulang per panggilan untuk `claude_bin`/`claude_timeout` lewat atribut `settings` (test mengubahnya dengan `object.__setattr__`).
- `llm.py`:
  - semaphore modul dibuat malas (`_cc_semaphore()` → `asyncio.Semaphore(settings.llm_concurrency)` per event loop; simpan per-loop dalam `weakref.WeakKeyDictionary` agar aman dengan `asyncio.run` berulang di test).
  - `_run_claude(system, messages, schema) -> dict`: `asyncio.create_subprocess_exec(settings.claude_bin, *build_claude_cmd(...)[1:], stdin=PIPE, stdout=PIPE, stderr=PIPE, cwd=settings.data_dir / "claude_cwd")` (buat folder bila belum ada); `FileNotFoundError` → `LLMError(f"{name}: claude CLI not found ({bin})")`; `asyncio.wait_for(proc.communicate(prompt), settings.claude_timeout)` → pada timeout `proc.kill()` lalu `LLMError("... timeout ...")`; parse stdout JSON (gagal parse → `LLMError` dengan 300 karakter stderr).
  - Klasifikasi error saat `is_error`: `api_error_status == 429` atau regex `(usage|rate)\s*limit` pada `result` → `LLMRateLimited(reset_at=None)`; status 401/403 atau teks `login|api key` → `LLMError(f"{name}: Claude Code belum login — jalankan `claude login` di server")`; selain itu `LLMError(result[:300])`.
  - `build_claude_cmd` tanpa `--effort` bila `effort` kosong; `--model` memakai `cfg.model`.
  - Prompt stdin: satu pesan user → kontennya apa adanya; lebih dari satu → `"User: …\n\nAssistant: …"` digabung `\n\n`.
  - `complete_json`: `claude_code` → `structured_output` (bila `None`, coba `json.loads(result)`; gagal → `LLMError("invalid JSON")`, **fail-closed**); `anthropic`/`openai` → `complete()` lalu `agents.extract_json`, `None` → `LLMError`; `mock` → `{}`.
- `registry.py:41`: `configured = provider in ("mock", "claude_code") or bool(api_key) or bool(base_url)`.
- `server.py` `/api/config`: `"providers": list(PROVIDERS)`.
- `web/index.html`: tambah `<option value="claude_code">claude_code (langganan Claude Code)</option>`.
- `.env.example`: dokumentasikan `claude_code` di blok PROVIDER options dan tiga setting baru.

- [ ] **Step 5: Jalankan test**

Run: `.venv/bin/pytest -q && .venv/bin/pytest -m live -q tests/test_llm_claude_code.py`
Expected: semua lulus; test live `1 passed` (butuh `claude` login di server).

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(llm): add claude_code provider with structured output, rate-limit detection and concurrency cap"
```

---

### Task 3: Penyimpanan SQLite

**Files:**
- Create: `agentorium/docpipe/__init__.py`, `agentorium/docpipe/models.py`, `agentorium/docpipe/store.py`
- Test: `tests/docpipe/test_store.py`

**Interfaces:**
- Produces (`models.py`, dataclass):
  - `Section(id: str, ordinal: int, level: int, heading: str, path: str, parent_id: str | None)`
  - `Block(id: str, uid: str, ordinal: int, section_id: str, type: str, text: str, table_index: int | None = None, row: int | None = None, col: int | None = None)` — `type ∈ {"heading","paragraph","list_item","table_cell"}`
  - `ParsedDoc(sections: list[Section], blocks: list[Block], warnings: list[str])`
  - `Document(id, title, label, created_at, latest_version_id)`, `Version(id, document_id, version_no, source, parent_version_id, filename, file_path, original_path, status, error, warnings: list[str], note, created_at)` — `source ∈ {"upload","rollback","revision","instructed_revision","update"}`, `status ∈ {"processing","ready","failed"}`
- Produces (`store.py`): `class Store(path: Path)` dengan:
  - `create_document(title: str, label: str = "") -> Document`
  - `add_version(document_id: str, *, source: str, filename: str, parent_version_id: str | None, note: str = "") -> Version` (nomor versi = max+1, `file_path` = `docs/<doc_id>/v<N><ext>` relatif terhadap data_dir, status `processing`)
  - `update_version(version_id: str, **fields) -> Version` (status, error, warnings, original_path, file_path)
  - `save_structure(version_id: str, parsed: ParsedDoc) -> None` (ganti isi sections/blocks versi itu dalam satu transaksi)
  - `list_documents() -> list[Document]`, `get_document(id) -> Document | None`, `list_versions(document_id) -> list[Version]` (urut `version_no` naik), `get_version(id) -> Version | None`, `latest_ready_version(document_id) -> Version | None`
  - `get_sections(version_id) -> list[Section]`, `get_blocks(version_id, section_id: str | None = None) -> list[Block]` (urut `ordinal`)
  - `delete_document(id) -> bool` (cascade), `delete_version(id) -> None` (untuk rollback ingest yang gagal)
  - `get_store() -> Store` (singleton di `settings.data_dir / "docpipe.db"`, dibuat ulang bila `data_dir` berubah — penting untuk test)

- [ ] **Step 1: Tulis test yang gagal**

```python
def test_versions_numbered_sequentially(store):
    d = store.create_document("Laporan", "bulanan")
    v1 = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=None)
    v2 = store.add_version(d.id, source="upload", filename="b.docx", parent_version_id=v1.id)
    assert (v1.version_no, v2.version_no) == (1, 2)
    assert v2.file_path == f"docs/{d.id}/v2.docx"

def test_save_structure_roundtrip_and_order(store): ...      # blok kembali urut ordinal, filter section_id
def test_save_structure_replaces_previous(store): ...        # dipanggil 2x → hanya isi kedua
def test_latest_ready_version_skips_failed(store): ...
def test_delete_document_cascades(store): ...                # sections/blocks/versions hilang
def test_warnings_roundtrip_as_list(store): ...
```

- [ ] **Step 2: Jalankan → FAIL** (`ModuleNotFoundError: agentorium.docpipe`)

Run: `.venv/bin/pytest tests/docpipe/test_store.py -q`

- [ ] **Step 3: Implementasi**

`sqlite3.connect(path, check_same_thread=False)`, `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, satu `threading.Lock` di sekitar tulis. Skema:
```sql
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, title TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);
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
```
ID dokumen/versi: `uuid4().hex[:12]`. `Document.latest_version_id` dihitung dari versi `ready` terbaru.

- [ ] **Step 4: Jalankan → PASS**

- [ ] **Step 5: Commit** — `git commit -m "feat(docpipe): sqlite store for documents, versions, sections and blocks"`

---

### Task 4: Parser DOCX & PDF

**Files:**
- Create: `agentorium/docpipe/parse.py`
- Test: `tests/docpipe/test_parse.py`, `tests/docpipe/fixtures.py` (pembuat DOCX uji dengan python-docx; tidak ada file biner di repo)

**Interfaces:**
- Consumes: `Section`, `Block`, `ParsedDoc` (Task 3)
- Produces:
  - `class ParseError(Exception)`
  - `def parse_docx(path: Path) -> ParsedDoc`
  - `def parse_pdf(path: Path) -> ParsedDoc`
  - `def accept_tracked_changes(src: Path, dst: Path) -> bool` — `True` bila ada revisi yang diterima (dst ditulis hanya jika True)
  - `SYNTHETIC_SECTION_SIZE = 40`
  - ID: `Section.id = f"s{ordinal:04d}"`, `Block.id = f"b{ordinal:05d}"`; `Block.uid` diisi `""` (diisi Task 5)

- [ ] **Step 1: Fixtures**

`fixtures.py` berisi fungsi pembuat file (menulis ke `tmp_path`): `make_basic_docx` (Heading 1 "Bab 1 Pendahuluan", paragraf dengan run bold, Heading 2 "1.1 Latar", tabel 2×2, paragraf, Heading 1 "Bab 2 Metode", paragraf, list bullet), `make_merged_table_docx` (sel (0,0)-(0,1) di-merge), `make_no_heading_docx(n_paragraphs=95)`, `make_outline_level_docx` (heading via `w:outlineLvl` pada pPr paragraf berstyle Normal), `make_tracked_docx` (buat basic lalu terapkan `adeu.RedlineEngine(...).process_batch([ModifyText(target_text="Latar", new_text="Latar Belakang")])`), `make_pdf` (dua halaman teks — tulis PDF minimal dengan `pypdf`? pypdf tidak bisa membuat teks; gunakan `docx` → tidak bisa. **Keputusan:** fixture PDF dibuat dengan `reportlab` hanya di dev-deps? Hindari; buat PDF teks minimal secara manual (string PDF 1.4 dengan font Helvetica, dua halaman) di `fixtures.py`).

- [ ] **Step 2: Tulis test yang gagal**

```python
def test_sections_from_headings(tmp_path):
    p = parse_docx(make_basic_docx(tmp_path))
    assert [s.heading for s in p.sections] == ["Bab 1 Pendahuluan", "1.1 Latar", "Bab 2 Metode"]
    assert p.sections[1].path == "Bab 1 Pendahuluan > 1.1 Latar"
    assert p.sections[1].parent_id == p.sections[0].id

def test_body_order_preserved(tmp_path):
    types = [b.type for b in parse_docx(make_basic_docx(tmp_path)).blocks]
    # heading, paragraph, heading, table_cell x4, paragraph, heading, paragraph, list_item
    assert types == ["heading","paragraph","heading","table_cell","table_cell","table_cell","table_cell","paragraph","heading","paragraph","list_item"]

def test_bold_run_text_is_joined(tmp_path): ...            # paragraf bold → teks lengkap satu blok
def test_table_cells_have_coordinates(tmp_path): ...       # (table_index,row,col) = (0,0,0)..(0,1,1)
def test_merged_cells_not_duplicated(tmp_path): ...        # baris 0 hanya satu blok
def test_text_before_first_heading_goes_to_intro_section(tmp_path): ...   # heading "Awal dokumen", level 1
def test_outline_level_counts_as_heading(tmp_path): ...
def test_no_headings_makes_synthetic_sections(tmp_path):
    p = parse_docx(make_no_heading_docx(tmp_path, 95))
    assert [s.heading for s in p.sections] == ["Bagian 1", "Bagian 2", "Bagian 3"]
    assert len(p.blocks) == 95
def test_empty_paragraphs_skipped(tmp_path): ...
def test_parse_tracked_changes_are_accepted(tmp_path):
    src = make_tracked_docx(tmp_path); dst = tmp_path / "w.docx"
    assert accept_tracked_changes(src, dst) is True
    assert any(b.text == "1.1 Latar Belakang" for b in parse_docx(dst).blocks)
def test_accept_returns_false_for_clean_doc(tmp_path): ...
def test_corrupt_docx_raises_parse_error(tmp_path): ...    # bytes acak → ParseError
def test_pdf_sections_per_page(tmp_path):
    p = parse_pdf(make_pdf(tmp_path))
    assert [s.heading for s in p.sections] == ["Halaman 1", "Halaman 2"]
```

- [ ] **Step 3: Jalankan → FAIL**

- [ ] **Step 4: Implementasi**

- Iterasi `document.element.body.iterchildren()`; `w:p` → `docx.text.paragraph.Paragraph(el, doc)`, `w:tbl` → `docx.table.Table(el, doc)`; abaikan `w:sectPr`.
- Heading: style name (atau base style berantai) cocok regex `^(Heading|Judul)\s*(\d)` → level; atau `w:outlineLvl` di pPr paragraf / pPr style → level = val+1 (val 0–8). Style `Title` = level 1.
- `list_item`: paragraf dengan `w:numPr` (langsung atau dari style) atau style mengandung `List`.
- Sel tabel: iterasi `tbl.rows`/`row.cells`, lewati sel yang `cell._tc` sudah terlihat (merged), teks = `cell.text.strip()`, kosong dilewati. Tabel bersarang: teksnya ikut dalam `cell.text` (tidak di-parse terpisah).
- Section path: stack heading berdasar level.
- Tanpa heading sama sekali → section sintetis per `SYNTHETIC_SECTION_SIZE` blok; ada heading tapi ada blok sebelum heading pertama → section "Awal dokumen".
- `accept_tracked_changes`: `RedlineEngine(BytesIO(src.read_bytes()))`; jika `get_pending_revision_authors()` kosong **dan** tidak ada `w:ins`/`w:del` di XML → `False`; selain itu `accept_all_revisions(remove_comments=False)` lalu `save_to_stream()` ke dst → `True`. Matikan log debug adeu: `structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))` sekali di `docpipe/__init__.py`.
- Semua exception python-docx/zipfile → `ParseError("Dokumen DOCX tidak bisa dibaca: …")`.
- `parse_pdf`: `pypdf.PdfReader`; terenkripsi → `ParseError`; per halaman, teks dipecah per baris kosong menjadi blok `paragraph`; section "Halaman N".

- [ ] **Step 5: Jalankan → PASS**

- [ ] **Step 6: Commit** — `git commit -m "feat(docpipe): ordered DOCX/PDF parser with sections, tables and tracked-change acceptance"`

---

### Task 5: Pencocokan `uid` blok antarversi

**Files:**
- Create: `agentorium/docpipe/versions.py`
- Test: `tests/docpipe/test_versions.py`

**Interfaces:**
- Consumes: `Block` (Task 3)
- Produces: `def assign_uids(new_blocks: list[Block], prev_blocks: list[Block] | None, *, threshold: float = 0.8) -> None` — mengisi `Block.uid` in-place.

- [ ] **Step 1: Test yang gagal**

```python
def test_first_version_gets_fresh_unique_uids(): ...
def test_unchanged_blocks_keep_uid(): ...
def test_edited_block_keeps_uid_when_similar():   # "Penjualan naik 12%." → "Penjualan naik 13%."
def test_rewritten_block_gets_new_uid():          # kemiripan < 0.8
def test_inserted_block_gets_new_uid_and_neighbours_keep_theirs(): ...
def test_reordered_duplicate_texts_do_not_share_uid(): ...   # tiap uid lama dipakai paling banyak sekali
```

- [ ] **Step 2: Jalankan → FAIL**

- [ ] **Step 3: Implementasi**

`difflib.SequenceMatcher(None, prev_texts, new_texts, autojunk=False).get_opcodes()`: `equal` → warisi uid; `replace` → pasangkan berurutan di dalam rentang bila `SequenceMatcher(None, a, b).ratio() >= threshold` (greedy terbaik per blok baru, tiap uid lama sekali); sisanya `uuid4().hex`.

- [ ] **Step 4: Jalankan → PASS**

- [ ] **Step 5: Commit** — `git commit -m "feat(docpipe): carry block uids across versions"`

---

### Task 6: Layanan ingest, versi baru, rollback

**Files:**
- Create: `agentorium/docpipe/service.py`
- Test: `tests/docpipe/test_service.py`

**Interfaces:**
- Consumes: `Store`/`get_store` (Task 3), `parse_docx`, `parse_pdf`, `accept_tracked_changes`, `ParseError` (Task 4), `assign_uids` (Task 5)
- Produces:
  - `ALLOWED_EXTENSIONS = {".docx", ".pdf"}`, `MAX_UPLOAD_BYTES = 100 * 1024 * 1024`
  - `class IngestError(Exception)` (pesan aman ditampilkan; dibuat dari `ParseError`, ekstensi salah, ukuran)
  - `def ingest(store: Store, *, filename: str, data: bytes, document_id: str | None = None, title: str | None = None, label: str = "", source: str = "upload", note: str = "") -> tuple[Document, Version]` — sinkron (dipanggil via `asyncio.to_thread`)
  - `def restore(store: Store, version_id: str) -> Version` — versi baru `source="rollback"`, `note=f"Rollback ke v{n}"`, file disalin, struktur disalin dengan uid sama
  - `def version_file(store: Store, version: Version, variant: str) -> Path` — `variant ∈ {"current","original"}`; `original` jatuh ke `current` bila `original_path` kosong

- [ ] **Step 1: Test yang gagal**

```python
def test_ingest_new_document_creates_v1_ready(store, tmp_path): ...     # title default = nama file tanpa ekstensi
def test_ingest_second_upload_inherits_uids(store, tmp_path): ...       # blok sama → uid sama dengan v1
def test_ingest_keeps_original_when_tracked(store, tmp_path):
    d, v = ingest(store, filename="t.docx", data=make_tracked_docx(tmp_path).read_bytes())
    assert "tracked_changes_accepted" in v.warnings
    assert version_file(store, v, "original") != version_file(store, v, "current")
def test_ingest_rejects_unknown_extension(store): ...                  # IngestError, tidak ada dokumen dibuat
def test_ingest_corrupt_file_leaves_no_version_or_file(store, tmp_path): # IngestError; versions kosong; folder file kosong; dokumen baru juga dihapus
def test_ingest_too_large(store): ...
def test_restore_creates_new_version_with_same_uids(store, tmp_path): ...  # v3 rollback ke v1: teks & uid = v1, parent = v1
```

- [ ] **Step 2: Jalankan → FAIL**

- [ ] **Step 3: Implementasi**

Urutan `ingest`: validasi ekstensi & ukuran → buat dokumen (bila `document_id` None) → `add_version` → tulis file ke `data_dir/file_path` → DOCX: `accept_tracked_changes(file, tmp)`; bila True pindahkan file asli ke `v<N>.original.docx` (`original_path`), tmp jadi file versi, warnings += `tracked_changes_accepted` → parse → `assign_uids(blocks, store.get_blocks(prev.id) if prev else None)` dengan `prev = latest_ready_version` sebelum versi ini → `save_structure` → status `ready`. Pada exception apa pun: hapus file yang ditulis, `delete_version`, hapus dokumen bila baru dibuat di panggilan ini, lalu raise `IngestError`.

- [ ] **Step 4: Jalankan → PASS**

- [ ] **Step 5: Commit** — `git commit -m "feat(docpipe): ingest, new versions and rollback service"`

---

### Task 7: API `/api/docs`

**Files:**
- Create: `agentorium/docpipe/api.py`
- Modify: `agentorium/server.py` (`app.include_router(docpipe_router)` sebelum route lain tidak perlu — cukup setelah definisi `app`)
- Test: `tests/docpipe/test_api.py`

**Interfaces:**
- Consumes: Task 3 & 6
- Produces: `router = APIRouter(prefix="/api/docs")` dengan endpoint (JSON memakai field dataclass apa adanya; `Version` ditambah `block_count`):
  - `POST /api/docs` multipart `file`, form `title?`, `label?` → 201 `{"document": {...}, "version": {...}}`
  - `GET /api/docs` → `[{...document, "latest_version": {...} | null, "version_count": n}]`
  - `GET /api/docs/{doc_id}` → `{"document": {...}, "versions": [...]}`; 404 bila tidak ada
  - `DELETE /api/docs/{doc_id}` → `{"ok": true}` (hapus juga folder `data/docs/<doc_id>`)
  - `POST /api/docs/{doc_id}/versions` multipart `file`, form `note?` → 201 `{"version": {...}}`
  - `GET /api/docs/versions/{vid}/outline` → `[{...section, "block_count": n}]`
  - `GET /api/docs/versions/{vid}/blocks?section_id=` → `[block...]`
  - `GET /api/docs/versions/{vid}/download?variant=current|original` → `FileResponse` dengan nama `"<title> v<N>.<ext>"`
  - `POST /api/docs/versions/{vid}/restore` → 201 `{"version": {...}}`
  - Error: `IngestError` → 422 `{"detail": pesan}`; ukuran → 413.

- [ ] **Step 1: Test yang gagal** (TestClient + `tmp_settings`)

```python
def test_upload_list_get_outline_blocks_download(client, tmp_path): ...   # alur lengkap, outline berisi 3 section basic
def test_upload_new_version_and_restore(client, tmp_path): ...           # versions = [1,2,3], v3.source == "rollback"
def test_corrupt_docx_returns_422_and_leaves_nothing(client):
    r = client.post("/api/docs", files={"file": ("x.docx", b"bukan docx", "application/octet-stream")})
    assert r.status_code == 422
    assert client.get("/api/docs").json() == []
def test_unknown_extension_422(client): ...
def test_download_original_variant(client, tmp_path): ...                 # tracked docx → original berbeda byte dari current
def test_404s(client): ...
def test_old_agentorium_endpoints_still_work(client): ...                 # POST /api/sessions → 201
```

- [ ] **Step 2: Jalankan → FAIL**

- [ ] **Step 3: Implementasi** — `await file.read()` lalu `await asyncio.to_thread(ingest, get_store(), ...)`.

- [ ] **Step 4: Jalankan → PASS** — `.venv/bin/pytest -q`

- [ ] **Step 5: Commit** — `git commit -m "feat(docpipe): REST API for documents and versions"`

---

### Task 8: Tab **Dokumen** di UI web

**Files:**
- Modify: `web/index.html` (tombol tab di `.topbar`, `<main id="docs-view" hidden>`), `web/style.css`
- Create: `web/docs.js`
- Test: `tests/docpipe/test_ui_static.py`

**Interfaces:**
- Consumes: endpoint Task 7.
- Produces: `window.DocsView = { show(), hide() }`; tab aktif disimpan di `localStorage['office.view']` (`"office"` | `"docs"`, dibungkus try/catch).

- [ ] **Step 1: Test statis yang gagal**

```python
def test_index_has_docs_tab_and_script(client):
    html = client.get("/").text
    assert 'id="tab-docs"' in html and 'id="docs-view"' in html and "/static/docs.js" in html
def test_docs_js_served(client):
    assert client.get("/static/docs.js").status_code == 200
```

- [ ] **Step 2: Jalankan → FAIL**

- [ ] **Step 3: Implementasi UI**

Tata letak `docs-view` (dua kolom, satu kolom di layar < 900px):
```
┌ Dokumen ───────────────┐ ┌ Laporan Bulanan Sep (bulanan) ─────────────────┐
│ [Unggah DOCX/PDF]      │ │ Versi: v1 upload · v2 upload · v3 rollback     │
│ ▸ Laporan Bulanan Sep  │ │  tiap baris: no · sumber · waktu · status ·    │
│ ▸ Proposal X           │ │  [Unduh] [Unduh asli*] [Kembalikan ke versi ini]│
│                        │ │ ⚠ peringatan (mis. tracked changes diterima)    │
│                        │ │ [Unggah versi baru]  [Hapus dokumen]           │
│                        │ │ Outline (klik section → daftar blok di bawah)  │
└────────────────────────┘ └────────────────────────────────────────────────┘
```
- Tombol tab "Kantor | Dokumen" di topbar; "Kantor" = `main.layout` yang lama.
- Unggah memakai `FormData`; tampilkan error 422/413 dari `detail`.
- Teks tombol/label lewat kamus kecil `{id: {...}, en: {...}}` di `docs.js`, bahasa dari `document.documentElement.lang`.
- Konfirmasi `confirm()` sebelum hapus dokumen dan sebelum rollback.
- Gaya memakai variabel/kelas yang sudah ada di `style.css` (`.panel`, `.btn`, `.tag`) + kelas baru seperlunya.

- [ ] **Step 4: Jalankan test → PASS**

- [ ] **Step 5: Verifikasi manual di browser**

Run: `./run.sh` lalu buka `http://127.0.0.1:8000` (dari laptop: `ssh -L 8000:127.0.0.1:8000 <server>`). Unggah DOCX basic dari fixture (`.venv/bin/python -c "from tests.docpipe.fixtures import make_basic_docx; from pathlib import Path; print(make_basic_docx(Path('/tmp/claude-1000/-home-rahmat-mvp-dcmt-review-and-revision/9f3b7dda-837e-42d0-8fcf-ce4ff75cf9b0/scratchpad')))"`), cek outline 3 section, unggah versi kedua, rollback, unduh. Mode Kantor masih bisa membuat sesi & chat (provider mock).
Expected: semua aksi berhasil tanpa error di console.

- [ ] **Step 6: Commit** — `git commit -m "feat(web): Dokumen tab for uploads, versions, outline and rollback"`

---

### Task 9: Dokumentasi singkat

**Files:**
- Create: `docs/DOCPIPE.md` (bahasa Indonesia)
- Modify: `README.md` (satu paragraf + tautan ke `docs/DOCPIPE.md` di bagian atas; isi README upstream lainnya tetap)

- [ ] **Step 1: Tulis** — cara instal (venv, `requirements-dev.txt`), `claude login` di server, setting `OFFICE_*` baru, menjalankan `./run.sh`, akses via SSH tunnel, menjalankan test (`pytest`, `pytest -m live`), status fase (1 selesai; 2–4 berikutnya).
- [ ] **Step 2: Verifikasi** — `.venv/bin/pytest -q` hijau.
- [ ] **Step 3: Commit** — `git commit -m "docs: DocPipe setup and usage"`
