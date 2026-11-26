# DocPipe Tahap 3 (Revisi otomatis + Revisi dari bahan + Diff) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dari temuan review atau dari bahan revisi user (catatan teks, file, komentar Word), tim agen menulis perbaikan per paragraf; perbaikan yang lolos validator dan supervisor diterapkan ke DOCX lewat adeu sebagai **versi baru** (bersih + tracked changes), dengan tabel perubahan per paragraf, tabel tindak lanjut per instruksi, dan perbandingan dua versi.

**Architecture:** `edits.py` (terapkan edit per blok via adeu `RedlineEngine.process_batch`, validator edit), `service.create_revised_version` (versi baru + warisan uid + tabel `changes`), `revise.py` (job `revision` dari temuan), `materials.py` (bahan → instruksi → lokasi), `instructed.py` (job `instructed_revision`), `compare.py` (diff blok + redline DOCX). Memakai ulang JobRunner, pola worker→validator→supervisor, RAG, OfficeBridge, dan UI tab Dokumen dari tahap 2.

**Tech Stack:** Python 3.12, adeu 4.2 (`ModifyText`, `RedlineEngine`, `accept_all_revisions`, `adeu.compare.build_redline_comparison`), python-docx, SQLite, Claude Code via `LLMClient.complete_json`.

**Spec:** `docs/superpowers/specs/2026-11-26-agentorium-docpipe-design.md` §5 (Revisi otomatis, Bahan revisi, Hasil setiap job, Diff & rollback) + aturan v1 `docs/superpowers/specs/2026-11-26-dcmt-review-revision-design.md`.

## Global Constraints

- Branch `feat/docpipe-phase3` dari `rahmat`; commit tanpa footer AI; subject ≤ 100 karakter.
- Revisi hanya untuk versi **DOCX** berstatus `ready`; versi PDF → 409 "revisi butuh DOCX".
- Satu edit = satu blok: `target_text` = teks blok lengkap saat ini, `new_text` = teks blok baru; adeu `match_mode="strict"`. Teks blok yang muncul lebih dari sekali / tidak ditemukan / gagal di adeu → edit `needs_human`, tidak pernah diterapkan ke tempat lain.
- Revisi otomatis menerapkan kategori `language | consistency | structure`; `substance` hanya jadi saran (`suggestion`), tidak diterapkan.
- Validator edit (deterministik): teks baru tidak kosong; setiap angka dan tanggal di teks lama tetap ada di teks baru **kecuali** angka itu disebut di temuan/instruksi yang menjadi dasarnya; angka/tanggal baru di teks baru wajib ada di temuan/instruksi atau di blok lain dokumen; panjang teks baru 0,3×–3× teks lama.
- Supervisor maks `settings.max_revisions` putaran; tanpa verdict / masih `revise` → `needs_human`; JSON rusak → task gagal (fail-closed).
- Hasil job yang mengubah: versi baru `v<N+1>.docx` (bersih, semua perubahan diterima) + `v<N+1>.tracked.docx` (tracked changes, author "DocPipe"), `source` = `revision` / `instructed_revision`, `parent_version_id` = versi dasar, catatan "Revisi dari vN". Tidak ada edit yang lolos → tidak ada versi baru (job `done`, catatan "tidak ada perubahan").
- Uid blok versi baru diwarisi per posisi dari versi dasar (struktur blok tidak berubah karena edit hanya mengganti teks).
- Tabel tindak lanjut instruksi: status `done | not_applicable | needs_human`, tanggapan singkat, paragraf terkait; dapat diunduh sebagai DOCX.
- Test tidak memanggil Claude; uji nyata ditandai/dijalankan manual.

## Review Focus

1. Teks paragraf yang sama muncul dua kali di dokumen → edit tidak diterapkan, `needs_human`, paragraf lain tidak tersentuh. Test: Task 1 `test_duplicate_block_text_needs_human`.
2. LLM mengubah angka/tanggal yang tidak diminta → edit ditolak validator. Test: Task 1 `test_edit_changing_unrelated_number_rejected`.
3. Format (bold/italic) paragraf yang diedit tetap terjaga di versi baru. Test: Task 1 `test_formatting_preserved`.
4. Instruksi dari bahan revisi yang tidak menunjuk bagian mana pun → muncul di tabel tindak lanjut sebagai `needs_human`/`not_applicable` dengan catatan, tidak hilang. Test: Task 5 `test_unlocated_instruction_reported`.
5. Revisi dari versi lama padahal sudah ada versi lebih baru → versi baru bernomor berikutnya dengan parent = versi dasar; versi lain tidak berubah. Test: Task 2 `test_revision_of_older_version_gets_next_number`.

---

### Task 1: Mesin edit + validator edit

**Files:** Create `agentorium/docpipe/edits.py`; Test `tests/docpipe/test_edits.py`

**Interfaces — Produces:**
- `BlockEdit(block_id: str, old_text: str, new_text: str, reason: str, sources: list[str])` (dataclass)
- `EditResult(block_id, status: "applied"|"needs_human", error: str | None)`
- `def apply_block_edits(docx: bytes, edits: list[BlockEdit], author: str = "DocPipe") -> tuple[bytes | None, bytes | None, list[EditResult]]` → `(tracked, clean, results)`; `None, None` bila tidak ada yang terterapkan. Satu `process_batch(partial=True)`; clean = `accept_all_revisions` atas hasil tracked.
- `def validate_edit(old: str, new: str, basis_texts: list[str], doc_texts: list[str]) -> str | None` → alasan tolak atau `None`. Ekstraksi angka/tanggal: regex angka (dengan `.`/`,`), nama bulan Indonesia + tahun.
- [ ] Test (RED): `test_apply_single_edit_tracked_and_clean`, `test_duplicate_block_text_needs_human`, `test_missing_target_needs_human`, `test_formatting_preserved`, `test_edit_changing_unrelated_number_rejected`, `test_number_change_allowed_when_in_basis`, `test_new_number_needs_source`, `test_empty_or_wild_length_rejected`.
- [ ] Implement → GREEN + suite → commit `feat(docpipe): apply block edits via adeu with an edit validator`

### Task 2: Versi hasil revisi + tabel perubahan

**Files:** Modify `store.py`, `models.py`, `service.py`, `api.py` (download `variant=tracked`); Test `tests/docpipe/test_revised_version.py`

**Interfaces — Produces:**
- Tabel `changes(id, version_id, job_id, block_uid, block_id, old_text, new_text, reason, source_kind, source_ids JSON, status, note, ordinal)` — `status ∈ applied|needs_human|rejected|suggestion`; `Change` dataclass; Store `add_changes`, `get_changes(version_id=None, job_id=None)`.
- Kolom `versions.tracked_path`.
- `def create_revised_version(store, base_version_id, tracked: bytes, clean: bytes, *, source: str, note: str) -> Version` — simpan `v<N+1>.docx` + `.tracked.docx`, parse clean, uid diwarisi per ordinal bila jumlah blok sama (selain itu `assign_uids`), `schedule_index` dipanggil oleh pemanggil async.
- `version_file(..., variant="tracked")`.
- [ ] Test: `test_revised_version_files_and_parent`, `test_uids_inherited_by_position`, `test_revision_of_older_version_gets_next_number`, `test_download_tracked_variant`, `test_changes_roundtrip`.
- [ ] Commit `feat(docpipe): revised versions with tracked copy and change log`

### Task 3: Job revisi otomatis dari temuan

**Files:** Create `agentorium/docpipe/revise.py`; Modify `prompts.py`, `office.py` (hook generik per job type), `server.py` (register handler); Test `tests/docpipe/test_revise.py`

**Interfaces — Produces:**
- `async def start_revision(review_job_id: str, categories: list[str] | None) -> Job` — job `revision` di versi review itu; `params={"review_job_id", "categories"}`; task per section yang punya temuan `valid` kategori terpilih (≠ substance); temuan `substance` valid → `changes` berstatus `suggestion` saat finalisasi.
- Handler `revision_section`: worker diberi blok section + temuan (id, kutipan, masalah, saran) → `{"edits":[{"block_id","new_text","finding_ids":[...],"reason"}]}`; `validate_edit` (basis = teks temuan) → supervisor (`approve|reject|revise`) → edit disetujui disimpan di `task.output["edits"]`.
- Finalizer: kumpulkan edit semua task, `apply_block_edits` atas file versi dasar → `create_revised_version(source="revision")` + `changes` (applied/needs_human/rejected/suggestion) → `job.params["result_version_id"]`.
- [ ] Test (LLM palsu): `test_revision_applies_approved_edits_new_version`, `test_substance_findings_become_suggestions`, `test_rejected_edit_not_applied`, `test_no_edits_no_version`, `test_pdf_version_rejected`, `test_office_events_for_revision`.
- [ ] Commit `feat(docpipe): automatic revision job from review findings`

### Task 4: Bahan revisi → instruksi berlokasi

**Files:** Create `agentorium/docpipe/materials.py`; Modify `parse.py` (baca komentar Word); Test `tests/docpipe/test_materials.py`

**Interfaces — Produces:**
- `def read_comments(docx: bytes, blocks: list[Block]) -> list[dict]` — `{"comment_id","author","text","block_id","anchor"}` dari `word/comments.xml` + `commentRangeStart/commentReference` per paragraf (urutan blok parser).
- `def material_text(filename: str, data: bytes) -> str` — `.txt/.md` (utf-8), `.docx` (teks + komentar), `.pdf`.
- `async def extract_instructions(text: str, llm) -> list[dict]` — supervisor memecah bahan menjadi `{"text","quote"}`; schema JSON.
- `def locate(store, version_id, instr: dict) -> list[str]` — kutipan persis (normalize_ws) di blok → blok itu; selain itu RAG top-3 (hanya kandidat, dipilih worker).
- Tabel `instructions(id, job_id, ordinal, source, author, text, quote, block_ids JSON, candidate_block_ids JSON, status, response, ordinal)`; Store `add_instructions`, `get_instructions`, `update_instruction`.
- [ ] Test: `test_read_word_comments_with_anchor_block`, `test_material_text_formats`, `test_extract_instructions_with_fake_llm`, `test_locate_by_quote_then_retrieval`.
- [ ] Commit `feat(docpipe): revision materials to located instructions`

### Task 5: Job revisi dari bahan + tabel tindak lanjut

**Files:** Create `agentorium/docpipe/instructed.py`; Modify `prompts.py`, `api.py`; Test `tests/docpipe/test_instructed.py`

**Interfaces — Produces:**
- `async def start_instructed_revision(version_id, *, notes: str, files: list[tuple[str, bytes]], use_comments: bool) -> Job` — preparer: ekstrak & lokasikan instruksi, buat task per section (instruksi dikelompokkan menurut section blok target/kandidat pertama); instruksi tanpa kandidat sama sekali → `needs_human` "bagian yang dimaksud tidak ditemukan".
- Handler `instructed_section`: worker menerima blok section + instruksinya → `{"edits":[...instruction_ids...], "responses":[{"instruction_id","status","response"}]}` → `validate_edit` (basis = teks instruksi) → supervisor → tanggapan & edit tersimpan.
- Finalizer: terapkan edit → versi baru `source="instructed_revision"`; instruksi yang editnya gagal diterapkan → `needs_human`.
- `def followup_docx(job_id) -> bytes` — tabel: No, Sumber, Instruksi, Status, Tanggapan, Bagian.
- [ ] Test: `test_instructed_revision_end_to_end`, `test_unlocated_instruction_reported`, `test_comment_instruction_targets_anchor_block`, `test_followup_docx_has_all_instructions`, `test_failed_apply_marks_instruction_needs_human`.
- [ ] Commit `feat(docpipe): revision from user materials with follow-up table`

### Task 6: Bandingkan versi

**Files:** Create `agentorium/docpipe/compare.py`; Modify `api.py`; Test `tests/docpipe/test_compare.py`

**Interfaces — Produces:**
- `def block_diff(store, a_version_id, b_version_id) -> list[dict]` — per uid: `changed|added|removed` dengan teks lama/baru dan heading.
- `def redline_docx(store, a, b) -> bytes` — `build_redline_comparison(a_bytes, b_bytes, author="DocPipe").data` (DOCX keduanya), selain itu 409.
- [ ] Test: `test_block_diff_changed_added_removed`, `test_redline_docx_contains_tracked_changes`.
- [ ] Commit `feat(docpipe): compare two versions (block diff and redline docx)`

### Task 7: API

Endpoints: `POST /jobs/{review_job_id}/revisions` `{"categories"?}` → 202; `POST /versions/{vid}/instructed-revisions` (multipart: `notes`, `files[]`, `use_comments`) → 202; `GET /jobs/{id}/changes`; `GET /versions/{vid}/changes`; `GET /jobs/{id}/instructions`; `GET /jobs/{id}/followup.docx`; `GET /versions/{a}/compare/{b}` (JSON) dan `GET /versions/{a}/compare/{b}/redline` (DOCX). `GET /jobs/{id}` mengikutkan `result_version_id`. Satu job aktif yang mengubah dokumen per versi (409).
- [ ] Test `tests/docpipe/test_api_revision.py`: 202 + selesai, 409 PDF, 409 paralel, changes/instructions/followup/compare endpoints.
- [ ] Commit `feat(docpipe): revision, materials, follow-up and compare API`

### Task 8: UI

Detail dokumen: (a) tombol **Revisi otomatis** di bawah tabel temuan (aktif bila review terakhir selesai); (b) panel **Revisi dari bahan**: textarea catatan, input file (txt/md/docx/pdf, multiple), centang "pakai komentar Word di dokumen", tombol Mulai; (c) progres job memakai komponen yang sama (label jenis job); (d) hasil: tautan versi baru, unduh bersih/tracked, tabel **Perubahan** (bagian, teks lama → baru, alasan, status), tabel **Tindak lanjut** + unduh DOCX; (e) **Bandingkan**: pilih dua versi → daftar perubahan blok + unduh redline. Teks hanya lewat `textContent`; id/en.
- [ ] Test statis + verifikasi Chrome headless dengan Claude nyata (desktop & 390px, tanpa error console).
- [ ] Commit `feat(web): automatic revision, revision from materials, follow-up and compare`

### Task 9: Uji nyata + dokumentasi

- `scripts/eval_revision.py`: dokumen eval tahap 2 → review → revisi otomatis → cek di versi baru: kesalahan ejaan sisipan sudah hilang, semua angka asli yang tidak disebut temuan tetap ada, struktur blok sama; lalu revisi dari bahan (catatan contoh 5 instruksi termasuk 1 yang tidak ada bagiannya) → cek tabel tindak lanjut. Simpan `data/evals/revision-<ts>.json`.
- `docs/DOCPIPE.md`: bagian revisi otomatis, revisi dari bahan, tindak lanjut, bandingkan; status fase 3.
- [ ] Commit `feat(docpipe): revision eval and docs`
