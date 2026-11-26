# DocPipe Tahap 2 (RAG + Review) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Index setiap versi dokumen untuk pencarian hybrid (FTS5 + embedding lokal), lalu jalankan **review per bab** oleh worker dan supervisor Agentorium (Claude Code) dengan validator deterministik, pemeriksaan konsistensi lintas bab, progres yang terlihat di kantor, dan tabel temuan di tab Dokumen.

**Architecture:** Paket `agentorium/docpipe/` ditambah `index.py` (chunk + FTS5 + vektor), `retrieval.py` (hybrid RRF), `jobs.py` (antrean job in-process yang persisten di SQLite, bisa resume dan pause saat kuota habis), `review.py` (loop worker → validator → supervisor), `validate.py`, `consistency.py`, `office.py` (event ke visualiser). Agen diambil dari registry Agentorium (peran `worker` dan `supervisor`), sehingga worker yang diatur di ⚙ dipakai juga untuk dokumen.

**Tech Stack:** Python 3.12, SQLite FTS5, fastembed 0.9 (ONNX, tanpa torch) dengan `intfloat/multilingual-e5-large`, numpy, Claude Code via `LLMClient.complete_json`.

**Spec:** `docs/superpowers/specs/2026-11-26-agentorium-docpipe-design.md` (+ aturan perilaku `docs/superpowers/specs/2026-11-26-dcmt-review-revision-design.md` §8.2, §9, §10, §12).

## Global Constraints

- Branch kerja `feat/docpipe-phase2` (dari `rahmat`); commit tanpa footer AI; subject ≤ 100 karakter.
- Dependency baru: `fastembed>=0.9,<1`, `numpy>=1.26`. Tidak ada torch.
- Embedding default `intfloat/multilingual-e5-large` (1024 dim) — **menggantikan bge-m3 di spec** karena bge-m3 tidak tersedia di fastembed dan butuh torch; diukur di server ini: 5 chunk/detik, hasil uji cari benar (MiniLM salah). Prefix e5: `passage: ` untuk chunk, `query: ` untuk pertanyaan. Setting `OFFICE_EMBED_MODEL` (nilai `none` = hanya FTS), cache model di `data/models/`.
- Chunk: per section, gabungan blok berurutan sampai ±1600 karakter (≈400 token), tidak melewati batas section, konten diawali `heading path`.
- Hybrid: top-30 vektor + top-30 FTS5 → Reciprocal Rank Fusion (k=60) → top-8; scope satu versi; section yang sedang direview dikecualikan.
- Kategori temuan: `language | consistency | structure | substance`; severity `low | medium | high`; status `valid | rejected | needs_human`.
- Supervisor maksimal 2 putaran revisi (`settings.max_revisions`); output yang tidak bisa diparse = gagal (fail-closed), tidak pernah approve.
- Section > 24.000 karakter dipecah per kelompok blok berurutan (task terpisah) — tidak pernah dipotong.
- Job `done` hanya jika semua task `done`; ada task `failed` → `partial`. Kuota habis (`LLMRateLimited`) → job `paused`, lanjut otomatis setelah `OFFICE_RATE_LIMIT_WAIT` (default 900 detik) tanpa mengulang task yang selesai.
- Test tidak memanggil Claude maupun model embedding sungguhan (embedder & LLM palsu); uji nyata ditandai `live`.

## Review Focus

1. Section sangat panjang (> 24.000 karakter) → dipecah jadi beberapa task, semua blok tercakup. Test: Task 6 `test_long_section_split_covers_all_blocks`.
2. Worker mengarang `block_id` atau kutipan yang tidak ada → temuan `rejected`, tidak pernah `valid`. Test: Task 5.
3. Server restart di tengah review → job dilanjutkan dari task yang belum selesai. Test: Task 4 `test_resume_requeues_unfinished_tasks`.
4. Kuota Claude Code habis di tengah job → job `paused`, task tidak dihitung gagal, lalu lanjut. Test: Task 4 `test_rate_limit_pauses_then_resumes`.
5. Model embedding belum terunduh / gagal dimuat → index tetap jalan dengan FTS saja, peringatan tercatat, review tetap bisa. Test: Task 2 `test_index_falls_back_to_fts_when_embedder_fails`.

---

### Task 1: Dependency, setting, embedder

**Files:**
- Modify: `requirements.txt`, `agentorium/config.py`, `.env.example`
- Create: `agentorium/docpipe/embed.py`
- Test: `tests/docpipe/test_embed.py`, `tests/docpipe/conftest.py`

**Interfaces:**
- Produces:
  - `Settings.embed_model: str` (`OFFICE_EMBED_MODEL`, default `intfloat/multilingual-e5-large`), `Settings.rate_limit_wait: int` (`OFFICE_RATE_LIMIT_WAIT`, 900)
  - `class Embedder(Protocol)`: `name: str`, `dim: int`, `def embed_passages(texts: list[str]) -> np.ndarray`, `def embed_query(text: str) -> np.ndarray` (vektor ter-normalisasi L2, float32)
  - `class FastEmbedder(Embedder)` (lazy load, `cache_dir=settings.data_dir/"models"`, prefix e5 bila nama model mengandung `e5`)
  - `class HashEmbedder(Embedder)` — deterministik untuk test (bag-of-words hashing ke 256 dim)
  - `def get_embedder() -> Embedder | None` (singleton; `None` bila `embed_model == "none"`); `def set_embedder(e: Embedder | None) -> None` (untuk test)
  - fixture `fake_embedder` di `tests/docpipe/conftest.py` memasang `HashEmbedder`

- [ ] **Step 1: Test gagal** — `test_hash_embedder_similarity` (kalimat yang berbagi kata lebih mirip daripada yang tidak), `test_vectors_normalized`, `test_get_embedder_none_when_disabled`, `test_e5_prefixes` (FastEmbedder dengan model palsu: monkeypatch `fastembed.TextEmbedding` → objek yang merekam input; `embed_passages(["x"])` mengirim `"passage: x"`, `embed_query("y")` mengirim `"query: y"`).
- [ ] **Step 2: Jalankan → FAIL** (`.venv/bin/pytest -q tests/docpipe/test_embed.py`)
- [ ] **Step 3: Implementasi** + `requirements.txt` tambah `fastembed>=0.9,<1` dan `numpy>=1.26`; install di `.venv`.
- [ ] **Step 4: Jalankan → PASS**, lalu suite penuh.
- [ ] **Step 5: Commit** `feat(docpipe): local embedder (fastembed e5) with test double`

---

### Task 2: Chunking & index per versi

**Files:**
- Create: `agentorium/docpipe/index.py`
- Modify: `agentorium/docpipe/store.py` (tabel baru), `agentorium/docpipe/models.py`, `agentorium/docpipe/service.py` (picu index setelah ingest/restore), `agentorium/docpipe/api.py` (`index_status` di JSON versi)
- Test: `tests/docpipe/test_index.py`

**Interfaces:**
- Produces:
  - `Chunk(id: str, version_id: str, section_id: str, block_ids: list[str], heading_path: str, content: str, ordinal: int)`
  - Store: tabel `chunks(id, version_id, section_id, block_ids JSON, heading_path, content, ordinal)`, virtual `chunks_fts USING fts5(content, chunk_id UNINDEXED, version_id UNINDEXED, tokenize='unicode61 remove_diacritics 2')`, tabel `chunk_vectors(chunk_id PRIMARY KEY, version_id, dim, vec BLOB)`; kolom `versions.index_status TEXT DEFAULT 'none'` (`none|indexing|ready|fts_only|failed`) dan `versions.index_error`.
  - Store methods: `save_chunks(version_id, chunks)`, `get_chunks(version_id) -> list[Chunk]`, `save_vectors(version_id, ids, matrix)`, `load_vectors(version_id) -> tuple[list[str], np.ndarray]`, `fts_search(version_id, query, limit) -> list[str]` (chunk id, urut bm25).
  - `def build_chunks(version_id: str, sections: list[Section], blocks: list[Block], max_chars: int = 1600) -> list[Chunk]`
  - `def index_version(store: Store, version_id: str) -> str` — sinkron; mengembalikan status akhir; embed dalam batch 32; exception embedder → status `fts_only` + `index_error`.
  - `async def schedule_index(version_id: str) -> None` — `asyncio.to_thread(index_version, ...)` dijalankan sebagai background task; ingest & restore memanggilnya.
  - Migrasi kolom baru: `ALTER TABLE` bila kolom belum ada (DB lama dari tahap 1).

- [ ] **Step 1: Test gagal**:
  `test_chunks_respect_section_boundary`, `test_chunk_size_limit_and_block_ids_cover_all_blocks`, `test_chunk_content_prefixed_with_heading_path`, `test_index_version_ready_with_vectors(fake_embedder)`, `test_fts_search_finds_word_and_ignores_case`, `test_index_falls_back_to_fts_when_embedder_fails` (embedder yang raise → status `fts_only`, `fts_search` tetap jalan), `test_old_database_gets_new_columns` (buat DB dengan skema tahap 1 lalu buka dengan Store baru).
- [ ] **Step 2: Jalankan → FAIL**
- [ ] **Step 3: Implementasi**
- [ ] **Step 4: PASS** + suite penuh
- [ ] **Step 5: Commit** `feat(docpipe): section-aware chunks, FTS5 and vector index per version`

---

### Task 3: Retrieval hybrid + endpoint cari

**Files:**
- Create: `agentorium/docpipe/retrieval.py`
- Modify: `agentorium/docpipe/api.py`
- Test: `tests/docpipe/test_retrieval.py`

**Interfaces:**
- Produces:
  - `Hit(chunk: Chunk, score: float, fts_rank: int | None, vec_rank: int | None)`
  - `def search(store: Store, version_id: str, query: str, *, k: int = 8, exclude_section_id: str | None = None, pool: int = 30) -> list[Hit]` — RRF `1/(60+rank)`; vektor hanya bila `index_status == "ready"`; query FTS dibersihkan jadi token alfanumerik di-OR-kan (cegah error sintaks FTS5).
  - `GET /api/docs/versions/{vid}/search?q=&k=` → `[{chunk..., score, fts_rank, vec_rank}]`

- [ ] **Step 1: Test gagal**: `test_hybrid_ranks_relevant_chunk_first`, `test_exclude_section`, `test_fts_only_when_no_vectors`, `test_query_with_fts_syntax_chars_does_not_crash` (`'"naik" AND (12%'`), `test_search_endpoint`.
- [ ] **Step 2: FAIL → Step 3: implementasi → Step 4: PASS + suite**
- [ ] **Step 5: Commit** `feat(docpipe): hybrid retrieval (FTS5 + vectors, RRF) and search API`

---

### Task 4: Job, task, temuan, fakta — penyimpanan & runner

**Files:**
- Create: `agentorium/docpipe/jobs.py`
- Modify: `agentorium/docpipe/store.py`, `agentorium/docpipe/models.py`, `agentorium/server.py` (startup hook memanggil `resume_jobs()`)
- Test: `tests/docpipe/test_jobs.py`

**Interfaces:**
- Produces:
  - `Job(id, document_id, version_id, type, status, params: dict, total_tasks, done_tasks, failed_tasks, error, created_at, started_at, finished_at, resume_at)` — `status ∈ queued|running|paused|finalizing|done|partial|failed|cancelled`
  - `Task(id, job_id, section_id, block_ids: list[str], kind, status, attempts, error, output: dict)` — `status ∈ queued|running|done|failed`
  - `Finding(id, job_id, task_id, version_id, block_id, other_block_id: str | None, category, severity, quote, description, suggestion, confidence: float, status, validator_note)`
  - `Fact(id, job_id, version_id, block_id, kind, key, value, unit, quote)`
  - Store: CRUD minimal untuk keempatnya (`create_job`, `update_job`, `get_job`, `list_jobs(version_id)`, `add_tasks`, `update_task`, `get_tasks(job_id)`, `add_findings`, `get_findings(job_id, status=None)`, `add_facts`, `get_facts(job_id)`), plus `increment_job(job_id, field)` atomik.
  - `class JobRunner`: `register(kind: str, handler)` dengan handler `async (job, task) -> dict`; `register_finalizer(type, async fn(job))`; `async start(job_id)`; `async cancel(job_id)`; menjalankan task paralel dibatasi `settings.llm_concurrency`; `LLMRateLimited` → task kembali `queued`, job `paused`, `resume_at = now + rate_limit_wait`, timer melanjutkan; exception lain → `attempts += 1`, ulang bila `attempts < 2`, selain itu task `failed`; setelah semua task selesai → finalizer → status `done`/`partial`.
  - `runner = JobRunner()` (singleton modul) dan `async def resume_jobs()` (job `running|paused|finalizing` → task `running` dikembalikan ke `queued`, job dilanjutkan).
  - Callback progres: `runner.on_event(fn(job, event: str, payload: dict))` dipakai Task 8.

- [ ] **Step 1: Test gagal** (handler palsu, tanpa LLM):
  `test_job_runs_all_tasks_and_finishes_done`, `test_failed_task_retried_once_then_partial`, `test_concurrency_respected`, `test_rate_limit_pauses_then_resumes` (handler raise `LLMRateLimited` sekali; `rate_limit_wait=0.2`; akhirnya `done`, `attempts` tidak bertambah karena kuota), `test_resume_requeues_unfinished_tasks` (task `running` + job `running` di DB → `resume_jobs()` → selesai), `test_cancel_stops_remaining_tasks`, `test_finalizer_called_once`.
- [ ] **Step 2: FAIL → Step 3: implementasi → Step 4: PASS + suite**
- [ ] **Step 5: Commit** `feat(docpipe): persistent job runner with retry, pause on usage limit and resume`

---

### Task 5: Validator deterministik

**Files:**
- Create: `agentorium/docpipe/validate.py`
- Test: `tests/docpipe/test_validate.py`

**Interfaces:**
- Produces:
  - `CATEGORIES = ("language", "consistency", "structure", "substance")`, `SEVERITIES = ("low", "medium", "high")`
  - `def normalize_ws(s: str) -> str` (spasi/tab/newline/nbsp → satu spasi, strip, tanda kutip lengkung → lurus)
  - `def validate_finding(raw: dict, blocks_by_id: dict[str, Block]) -> tuple[dict | None, str | None]` → `(finding_bersih, None)` atau `(None, alasan)`. Aturan: `block_id` ada di `blocks_by_id`; `quote` tidak kosong dan `normalize_ws(quote)` substring dari `normalize_ws(block.text)` (case-sensitive); kategori & severity valid; `description` tidak kosong; `confidence` di-clamp 0..1 (default 0.5).
  - `def validate_fact(raw: dict, blocks_by_id) -> dict | None` (aturan kutipan sama; `kind ∈ number|date|term|name|claim`).

- [ ] **Step 1: Test gagal**: `test_valid_finding_passes`, `test_unknown_block_rejected`, `test_quote_not_in_block_rejected`, `test_quote_matches_across_whitespace_and_curly_quotes`, `test_bad_category_rejected`, `test_empty_description_rejected`, `test_confidence_clamped`, `test_fact_validation`.
- [ ] **Step 2: FAIL → Step 3 → Step 4 PASS + suite**
- [ ] **Step 5: Commit** `feat(docpipe): deterministic finding and fact validator`

---

### Task 6: Review per section (worker → validator → supervisor)

**Files:**
- Create: `agentorium/docpipe/review.py`, `agentorium/docpipe/prompts.py`
- Test: `tests/docpipe/test_review.py`

**Interfaces:**
- Consumes: Task 3 `search`, Task 4 runner/store, Task 5 validator, `registry` Agentorium, `LLMClient`.
- Produces:
  - `MAX_TASK_CHARS = 24000`
  - `def plan_tasks(sections, blocks) -> list[tuple[str, list[str]]]` — `(section_id, block_ids)`; section > `MAX_TASK_CHARS` dipecah berurutan; section tanpa blok dilewati.
  - `def pick_agents() -> tuple[AgentDef, list[AgentDef]]` — supervisor pertama dan semua worker dari `registry`; tidak ada → `RuntimeError` dengan pesan jelas.
  - `WORKER_SCHEMA` (JSON Schema): `{"findings": [{"block_id","category","severity","quote","description","suggestion","confidence"}], "facts": [{"block_id","kind","key","value","unit","quote"}]}`; `SUPERVISOR_SCHEMA`: `{"verdicts": [{"index": int, "verdict": "approve"|"reject"|"revise", "feedback": str}]}`
  - Prompt (Bahasa Indonesia; PUEBI/KBBI; istilah Inggris wajar; locale angka Indonesia): system worker = peran + aturan kategori + "kutip persis dari teks blok"; user worker = outline dokumen (path section, maks 200 baris) + konteks pendukung (hit retrieval: `heading_path` + isi, ditandai "BUKAN bagian yang direview") + blok section dengan format `[b00012] teks…` + (putaran revisi) feedback supervisor.
  - `async def review_task(job: Job, task: Task) -> dict` — handler `kind="section_review"`: worker dipilih round-robin berdasar urutan task; worker → validator (yang gagal disimpan `rejected` dengan `validator_note`) → supervisor menilai temuan yang lolos → `approve` = `valid`, `reject` = `rejected`, `revise` → worker diulang dengan feedback untuk temuan itu (maks `settings.max_revisions` putaran) → sisa `revise` = `needs_human`. Fakta tervalidasi disimpan. Return `{"findings": n_valid, "rejected": n, "needs_human": n}`.
  - Query retrieval per task: `heading_path + " " + 300 karakter pertama teks task`, `k=6`, `exclude_section_id`.

- [ ] **Step 1: Test gagal** (LLM palsu: `ScriptedLLM` mengembalikan JSON berurutan per panggilan dan merekam prompt; dipasang dengan monkeypatch `review.make_llm`):
  `test_plan_tasks_one_per_section`, `test_long_section_split_covers_all_blocks`, `test_review_task_valid_and_rejected_findings` (satu temuan benar, satu kutipan karangan → 1 valid, 1 rejected), `test_supervisor_reject_marks_rejected`, `test_supervisor_revise_then_approve`, `test_revise_exhausted_becomes_needs_human`, `test_supervisor_invalid_json_fails_task` (LLMError → exception keluar dari handler), `test_worker_prompt_contains_blocks_outline_and_context` (blok berformat `[b0000x]`, konteks dari section lain, tidak memuat section sendiri sebagai konteks), `test_facts_saved`, `test_pick_agents_requires_supervisor_and_worker`.
- [ ] **Step 2: FAIL → Step 3 → Step 4 PASS + suite**
- [ ] **Step 5: Commit** `feat(docpipe): section review loop with worker, validator and supervisor`

---

### Task 7: Konsistensi lintas bab & finalisasi job review

**Files:**
- Create: `agentorium/docpipe/consistency.py`
- Modify: `agentorium/docpipe/review.py` (`start_review`, finalizer)
- Test: `tests/docpipe/test_consistency.py`

**Interfaces:**
- Produces:
  - `def candidate_conflicts(facts: list[Fact]) -> list[tuple[Fact, Fact]]` — kunci dinormalisasi (`lower`, spasi tunggal, tanpa tanda baca) sama, `kind` sama, `value` (dinormalisasi; angka diparse locale Indonesia `1.234,5`) berbeda, beda blok; maksimal 50 pasangan.
  - `async def check_consistency(job: Job) -> int` — kirim pasangan ke supervisor (batch 10) dengan schema `{"conflicts":[{"pair": int, "is_conflict": bool, "description": str, "severity": ...}]}`; konflik → `Finding(category="consistency", block_id=a.block_id, other_block_id=b.block_id, quote=a.quote, status="valid")`.
  - `async def start_review(version_id: str, categories: list[str] | None) -> Job` — versi harus `ready`; bila `index_status` bukan `ready|fts_only` jalankan `index_version` dulu; buat job `review` + task; finalizer = `check_consistency`.

- [ ] **Step 1: Test gagal**: `test_candidate_conflicts_same_key_different_value`, `test_number_normalization_treats_equal_values_as_same` (`1.200` vs `1200` bukan konflik), `test_check_consistency_creates_paired_finding` (LLM palsu), `test_start_review_end_to_end_with_fake_llm` (dokumen basic → job `done`, semua section punya task `done`, temuan tersimpan).
- [ ] **Step 2: FAIL → Step 3 → Step 4 PASS + suite**
- [ ] **Step 5: Commit** `feat(docpipe): cross-section consistency check and review job finalizer`

---

### Task 8: Agen terlihat bekerja di Kantor

**Files:**
- Create: `agentorium/docpipe/office.py`
- Modify: `agentorium/docpipe/review.py` (panggil hook), `agentorium/server.py` (berikan `orchestrator` ke office)
- Test: `tests/docpipe/test_office.py`

**Interfaces:**
- Produces: `class OfficeBridge(orchestrator)` dengan `session_for(job) -> str` (sesi `external` bernama `Review: <judul> v<N>`, agen di-register dengan id/nama/warna dari registry), `status(job, agent_id, text)`, `flow(job, src, dst, label)`, `step(job, task, status)`, `note(job, text)`; semua best-effort (exception ditelan & di-log). `Job.params["session_id"]` menyimpan sesi.
- Pemetaan event: worker mulai section → `agent_status "membaca <heading>"` + `flow supervisor→worker "tugas"`; temuan dikirim → `flow worker→supervisor "temuan"`; supervisor menilai → `agent_status "meninjau"`; selesai → `idle`; job selesai → `note` ringkasan + `stage done`.

- [ ] **Step 1: Test gagal**: `test_session_created_with_registry_agents`, `test_events_emitted_during_review` (job dengan LLM palsu → sesi berisi event `agent_status` dan `flow`), `test_bridge_errors_do_not_break_review`.
- [ ] **Step 2: FAIL → Step 3 → Step 4 PASS + suite**
- [ ] **Step 5: Commit** `feat(docpipe): show review agents working in the office view`

---

### Task 9: API review & temuan

**Files:**
- Modify: `agentorium/docpipe/api.py`
- Test: `tests/docpipe/test_api_review.py`

**Interfaces:**
- Produces:
  - `POST /api/docs/versions/{vid}/reviews` body `{"categories": [...]?}` → 202 `{"job": {...}}`; 409 bila sudah ada job review `queued|running|paused` untuk versi itu; 400 bila agen belum lengkap (pesan dari `pick_agents`).
  - `GET /api/docs/versions/{vid}/jobs` → daftar job versi itu.
  - `GET /api/docs/jobs/{job_id}` → `{"job", "tasks": [{section_id, heading, status, attempts, error}], "counts": {"valid", "rejected", "needs_human"}}`
  - `GET /api/docs/jobs/{job_id}/findings?status=&category=` → temuan + `heading_path` dan `block_text` bloknya (dan blok pasangan untuk konsistensi).
  - `POST /api/docs/jobs/{job_id}/cancel`

- [ ] **Step 1: Test gagal** (LLM palsu): `test_start_review_returns_202_and_job_completes`, `test_second_review_while_running_409`, `test_missing_agents_400`, `test_findings_filter_and_block_text`, `test_cancel`.
- [ ] **Step 2: FAIL → Step 3 → Step 4 PASS + suite**
- [ ] **Step 5: Commit** `feat(docpipe): review, job and findings API`

---

### Task 10: UI — tombol Review, progres, tabel temuan, uji cari

**Files:**
- Modify: `web/index.html`, `web/docs.js`, `web/style.css`
- Test: `tests/docpipe/test_ui_static.py` (tambah)

**Interfaces:** Consumes Task 3 & 9.

Tata letak di detail dokumen (di bawah tabel versi):
```
[Review versi ini]   status: berjalan 7/12 bab · ⏸ menunggu kuota (lanjut 14.05)   [Batalkan] [Lihat di Kantor]
Temuan (valid 14 · perlu dicek 3 · ditolak 5)   filter: [kategori ▾] [status ▾]
┌ kategori ┬ tingkat ┬ lokasi (bab › blok) ┬ kutipan ┬ masalah ┬ saran ┐
Cari di dokumen (uji RAG): [______________] [Cari] → daftar chunk + skor + sumber (FTS/vektor)
```
- Polling `GET /api/docs/jobs/{id}` tiap 2 detik selama job aktif; berhenti saat selesai.
- Klik baris temuan → buka section di outline & sorot blok (`.blk.hl`).
- "Lihat di Kantor" → pindah ke tab Kantor dan buka sesi `job.params.session_id` (`openSession` sudah ada di app.js — ekspos `window.Office = { openSession }`).
- Semua teks lewat kamus id/en; hanya `textContent`.

- [ ] **Step 1: Test statis gagal**: `test_review_controls_present` (`id="doc-review"`, `id="findings-table"`, `id="doc-search"`).
- [ ] **Step 2: FAIL → Step 3 → Step 4 PASS**
- [ ] **Step 5: Verifikasi di Chrome headless (skrip CDP)**: unggah DOCX, review dengan provider `mock`… **tidak cukup** (mock mengembalikan `{}`) → jalankan dengan `claude_code` model `haiku` pada dokumen basic; periksa progres berjalan, tabel temuan terisi, klik temuan menyorot blok, cari "penjualan" mengembalikan chunk; tidak ada error console; lebar 390px tanpa scroll horizontal.
- [ ] **Step 6: Commit** `feat(web): review button, progress, findings table and RAG search`

---

### Task 11: Eval harness & dokumentasi

**Files:**
- Create: `scripts/eval_review.py`, `tests/docpipe/eval_doc.py` (pembuat dokumen uji dengan kesalahan sisipan)
- Modify: `docs/DOCPIPE.md`
- Test: `tests/docpipe/test_eval_doc.py`

**Interfaces:**
- Produces: `def make_eval_docx(path) -> list[SeededError]` dengan `SeededError(section_heading, block_text_contains, category)`; minimal 20 kesalahan: ejaan (`menganalisa`, `praktek`, `resiko`…), angka bertentangan antarbab (jumlah responden 120 vs 125), istilah tidak konsisten, kalimat tidak logis, struktur (bab tanpa isi). `scripts/eval_review.py [--model haiku] [--runs 1]`: ingest → review → cocokkan (temuan `valid` pada blok yang mengandung teks sisipan dan kategori sama; konsistensi cocok bila salah satu blok pasangan) → cetak dan simpan `data/evals/<timestamp>.json` berisi recall, precision, coverage, durasi, jumlah panggilan LLM.

- [ ] **Step 1: Test gagal**: `test_eval_doc_contains_all_seeded_errors` (setiap `block_text_contains` muncul tepat di satu blok hasil parse).
- [ ] **Step 2: FAIL → Step 3 → Step 4 PASS**
- [ ] **Step 5: Jalankan eval nyata sekali** — `.venv/bin/python scripts/eval_review.py --model haiku`; catat angka di ledger dan di `docs/DOCPIPE.md` (bagian "Hasil evaluasi").
- [ ] **Step 6: Dokumentasi** — `docs/DOCPIPE.md`: cara review, setting embedding (unduhan model 2,2 GB pertama kali), kuota & pause, uji cari, eval. Status tabel fase 2 → selesai.
- [ ] **Step 7: Commit** `feat(docpipe): review eval harness and docs`
