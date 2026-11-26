# Desain: Aplikasi Review & Revisi Dokumen (dcmt-review-and-revision)

Tanggal: 2026-11-26 · Status: **digantikan** oleh 2026-11-26-agentorium-docpipe-design.md (cadangan untuk sistem multi-user besar)

## 1. Maksud & alasan

Aplikasi untuk mengelola dokumen/laporan **jenis apa pun** (bukan khusus domain tertentu) dengan bantuan AI:

1. **Review** — memeriksa semua aspek: bahasa & ejaan, konsistensi isi antarbagian, struktur/format, dan substansi (logika, kecukupan argumen/data).
2. **Revisi otomatis** — AI langsung menghasilkan versi dokumen yang sudah diperbaiki.
3. **Update laporan berkala** — misalnya laporan bulanan: laporan periode lalu + data periode baru → laporan periode baru (angka, tabel, tanggal, *dan* narasi ikut diperbarui).

Semua perubahan **tercatat sebagai versi** dan bisa ditelusuri: apa yang berubah, kenapa (temuan/data yang mendasari), oleh job apa, dan bisa dibandingkan atau dikembalikan.

Dokumen bisa panjang (~200 halaman), jadi sistem harus menjamin **seluruh bagian benar-benar diproses** dan **setiap perubahan bisa dibuktikan** — bukan sekadar "AI bilang sudah".

## 2. Goals, non-goals, anti-goals

**Goals**
- Upload DOCX (utama) dan PDF (baca saja); struktur dokumen tersimpan dengan ID paragraf yang stabil.
- Versioning penuh: setiap versi immutable, ada diff antarversi, rollback.
- Review per section + pemeriksaan konsistensi lintas bagian, dengan temuan yang berbukti.
- Revisi otomatis yang ditulis kembali ke DOCX **tanpa merusak format** bagian yang tidak diubah.
- Update laporan berkala dari data baru dalam format bebas (XLSX, CSV, DOCX, PDF, teks).
- Penyedia LLM bisa ditukar: Claude API, OpenAI API (dan endpoint kompatibel OpenAI), Claude Code headless.

**Non-goals (MVP)**
- Mengedit PDF (hasil revisi dari PDF selalu berupa DOCX baru).
- Mengedit gambar, chart, text box, header/footer, footnote, komentar Word — semuanya **dipertahankan apa adanya**, tidak diubah.
- Menambah/menghapus baris tabel saat update berkala (fase 4 hanya mengubah isi sel; tambah baris = pekerjaan lanjutan).
- Pencarian lintas banyak dokumen; kolaborasi real-time multi-user.

**Anti-goals (gagal walau "jalan")**
- Ada section yang diam-diam tidak diproses atau terpotong (seperti pemotongan 60k karakter di Agentorium).
- Revisi yang mengarang angka/fakta yang tidak ada di sumber.
- Revisi yang merusak format dokumen (style, bold, numbering, tabel).
- Hasil yang tidak bisa ditelusuri ke paragraf asal.
- Gagal-terbuka: output AI yang tidak bisa diparse dianggap "lolos".

## 3. Batasan

- Server: RAM 14 GB (±6 GB bebas saat ini). Tidak memakai stack berat (RAGFlow/Elasticsearch, Docling+torch di MVP).
- Backend wajib memakai starter kit milik user: `github.com/RahmatRafiq/golang_strarter_kit_2025` (Go 1.24, Gin, GORM, Asynq+Redis, MinIO, JWT/RBAC, migration SQL, zerolog, Prometheus/OTel).
- Pengolahan dokumen & AI di Python (ekosistem python-docx/lxml/openpyxl & SDK LLM paling matang).
- Dokumen dikirim ke penyedia LLM eksternal (kecuali bila nanti memakai model lokal via endpoint kompatibel OpenAI).

## 4. Pendekatan yang dipertimbangkan

| Pendekatan | Trade-off | Keputusan |
|---|---|---|
| A. Laravel + Python + RAGFlow + Docling (usulan diskusi awal) | Fitur RAG lengkap siap pakai, tapi berat (RAGFlow ≥16 GB RAM), parsing ganda (Docling vs RAGFlow) membuat pemetaan chunk→paragraf sulit, Laravel bukan stack user | Ditolak |
| B. Agentorium sebagai fondasi | Kecil & mudah dibaca, tapi early-release, tanpa RAG, memotong dokumen 60k karakter, fail-open, state di file JSON | Ditolak sebagai dependency; **polanya diadopsi** |
| C. **Go (starter kit) + Python AI service + Postgres/pgvector**, mengadopsi teknik RAGFlow & pola Agentorium | Ringan, satu database, `block_id` native di semua tabel, kontrol penuh; RAG dirakit sendiri (beberapa ratus baris) | **Dipilih user** |

Yang diadopsi:
- **Dari RAGFlow:** chunking mengikuti struktur dokumen, hybrid search (full-text + vektor), reranking (opsional, berdasar pengukuran), citation yang grounded, retrieval testing terpisah.
- **Dari Agentorium:** loop worker → supervisor dengan verdict JSON (`approve`/`revise`, skor, feedback) dan batas revisi; eksekusi paralel bergelombang (waves); event progres. **Diubah menjadi fail-closed.**

## 5. Arsitektur

```
Frontend (React + TS + Vite)
        │ REST + SSE
        ▼
Go API + Go Worker (starter kit)
  auth · dokumen · versi · diff · rollback
  job orchestration (Asynq) · progres (Redis pub/sub → SSE)
        │ HTTP internal (JSON, header X-Internal-Token)
        ▼
Python AI Service (FastAPI)
  parsing DOCX/PDF/XLSX · normalisasi paraId
  chunking · embedding (bge-m3) · hybrid retrieval
  agent loop (worker → validator → supervisor) · LLM provider layer
  penulisan kembali DOCX (run-aware) · export tracked changes
        │
        ▼
PostgreSQL 16 + pgvector · Redis · MinIO
```

**Pembagian tanggung jawab**
- **Go** pemilik skema (semua migration di Go) dan pemilik data bisnis: dokumen, versi, section, blok, job, task, temuan, perubahan. Go yang memecah job menjadi task, mengatur concurrency/retry via Asynq, menghitung progres, dan memutuskan kapan job selesai.
- **Python** stateless terhadap alur bisnis: menerima satu pekerjaan (parse satu file, review satu section, revisi satu section, dst.), mengembalikan hasil JSON. Pengecualian: Python **menulis langsung** tabel `chunks` (embedding) dan membaca `blocks`/`chunks`/`doc_facts` untuk retrieval. Python membaca/menulis file di MinIO langsung (kredensial dari env).
- **Frontend** terpisah, memanggil API Go saja.

**Deploy:** `docker-compose.yml` berisi `postgres` (image `pgvector/pgvector:pg16`), `redis`, `minio`, `api` (Go), `worker` (Go Asynq), `ai` (Python), `web` (frontend). MySQL dari kit dihapus dari compose.

## 6. Identitas dokumen: blok, section, versi

Ini fondasi semua fitur; harus benar sejak fase 1.

**Blok** = unit terkecil yang bisa direview/diubah:
- `heading`, `paragraph`, `list_item` → satu `<w:p>` di DOCX.
- `table` → blok induk; setiap paragraf di dalam sel menjadi blok `table_cell` dengan `table_block_id`, `row`, `col`.
- Elemen yang tidak didukung (gambar, chart, text box) dicatat sebagai blok `opaque` (tidak diedit, tetap ada di urutan supaya struktur utuh).

**Dua jenis ID**
- `blocks.id` (UUID) — baris unik per versi.
- `blocks.block_uid` (UUID) — **identitas logis lintas versi**. Paragraf yang sama di versi 1 dan versi 2 punya `block_uid` sama walau teksnya berubah. Diff dan riwayat per paragraf memakai ini.

**Jangkar di file DOCX: `w14:paraId`**
- Saat ingest, Python **menormalisasi** DOCX: setiap `<w:p>` dipastikan punya `w14:paraId` unik (ditambahkan bila tidak ada / duplikat). File hasil normalisasi disimpan sebagai `normalized_file_key` dan menjadi basis semua edit. File asli tetap disimpan.
- Pemetaan blok ↔ `<w:p>` lewat `para_id`. Semua penulisan balik menemukan paragraf lewat `para_id`, bukan nomor halaman atau urutan.

**Pencocokan `block_uid` antarversi**
- Versi yang dibuat aplikasi (revisi/update/rollback): `block_uid` diwariskan langsung.
- Versi yang di-upload ulang user (diedit di Word): cocokkan per `para_id`; sisanya dengan kemiripan teks (≥ 0.8, urutan dijaga); yang tidak cocok = blok baru. (Word bisa mengganti paraId saat paragraf diedit, karena itu ada fallback kemiripan.)

**Section** diturunkan dari heading (style Heading N / `outlineLvl`). Dokumen tanpa heading → section sintetis per ~40 blok berurutan. Setiap section menyimpan `path` (mis. `Bab 3 > 3.2 Metode`).

**Isi khusus di DOCX input** *(keputusan builder-check)*
- *Tracked changes yang masih pending* (`w:ins`/`w:del`): di salinan normalisasi semua perubahan **diterima** (accept all), file asli tetap utuh, dan UI memberi peringatan "dokumen berisi tracked changes yang diterima otomatis".
- *Field* (TOC, nomor halaman, cross-reference), *hyperlink*, *gambar inline*: teks hasil field/hyperlink ikut dalam `blocks.text` supaya bisa direview, tetapi rentangnya ditandai `protected_ranges`. Penulis DOCX menolak perubahan yang menyentuh rentang terlindung → perubahan itu menjadi `needs_human`, sisa paragraf tetap boleh diubah. Paragraf TOC = `opaque`.

**PDF:** diparse dengan PyMuPDF (teks + ukuran font untuk deteksi heading, nomor halaman). `para_id` kosong; blok tetap dapat `block_uid`. Revisi dari PDF menghasilkan DOCX baru yang dibangun dari blok (format sederhana). Docling dipertimbangkan nanti bila kualitas parsing PDF kurang.

**Versi** immutable. Setiap versi = satu file DOCX di MinIO + snapshot `sections`/`blocks` miliknya. Rollback = membuat versi baru yang menyalin isi versi lama (riwayat tidak pernah ditimpa).

## 7. Model data (PostgreSQL)

```
documents        id, owner_id, title, label (bebas, mis. "laporan bulanan"), current_version_id, created_at
document_versions id, document_id, version_no, parent_version_id, source (upload|revision|instructed_revision|update|rollback),
                 job_id, file_key, normalized_file_key, format (docx|pdf), number_locale, status (processing|ready|failed),
                 change_summary, created_by, created_at
sections         id, version_id, parent_id, ordinal, level, heading_text, heading_block_id, path
blocks           id, version_id, section_id, block_uid, para_id, ordinal, type, table_block_id, row, col,
                 style_name, text, text_hash, page, protected_ranges jsonb
chunks           id, version_id, section_id, block_ids uuid[], heading_path, content,
                 embedding vector(1024), tsv tsvector
doc_facts        id, version_id, block_id, kind (term|number|date|name|claim), key, value, unit, quote
jobs             id, document_id, version_id, type (ingest|review|revise|instructed_revise|update), status
                 (queued|running|paused|finalizing|done|failed|partial), params jsonb, total_tasks, done_tasks,
                 failed_tasks, result_version_id, error, tokens_in, tokens_out, cost_usd, started_at, finished_at
job_tasks        id, job_id, section_id, kind, status (queued|running|done|failed), attempts, error, output jsonb
findings         id, job_id, task_id, version_id, block_id, category (language|consistency|structure|substance),
                 severity (low|medium|high), description, quote, suggestion, confidence,
                 status (valid|rejected|needs_human), validator_notes
block_changes    id, job_id, from_version_id, to_version_id, block_uid, old_text, new_text, reason,
                 finding_ids uuid[], source_refs jsonb
revision_instructions id, job_id, source (file|text|docx_comment), raw_text, target_block_ids uuid[],
                 status (pending|done|not_applicable|needs_human), resolution_note, change_ids uuid[]
update_inputs    id, job_id, file_key, kind (xlsx|csv|docx|pdf|text), extracted jsonb
llm_calls        id, job_id, task_id, provider, model, purpose, tokens_in, tokens_out, cache_read_tokens,
                 latency_ms, status, created_at
```

Index penting: `blocks(version_id, ordinal)`, `blocks(block_uid)`, `chunks USING hnsw (embedding vector_cosine_ops)`, `chunks USING gin(tsv)`, `findings(job_id)`, `block_changes(block_uid)`.

Model contoh di kit (`products`, `categories`, `stores`, `test_postgres`) dihapus. Users/roles/permissions/auth dari kit dipakai apa adanya (MVP: satu user/admin, tetapi `owner_id` sudah ada).

## 8. Alur kerja

### 8.1 Ingest (upload)
1. `POST /api/v1/documents` (multipart) → Go simpan file ke MinIO, buat `documents` + `document_versions(status=processing)`, enqueue job `ingest`.
2. Worker Go → `POST ai/internal/parse` → Python: normalisasi paraId, parse jadi sections + blocks, simpan `normalized_file_key`, kembalikan JSON struktur.
3. Go simpan sections/blocks (satu transaksi).
4. Worker Go → `POST ai/internal/index` → Python: bangun chunks + embedding + tsv, ekstrak `doc_facts` per section (LLM, kecil).
5. Versi `ready`. Progres dikirim via SSE.

### 8.2 Review
1. `POST /api/v1/documents/{id}/versions/{vid}/reviews` (opsional: kategori yang dicek) → job `review`, satu `job_task` per section (task `section_review`).
2. Asynq menjalankan task paralel (concurrency dari config, default 4). Tiap task → `POST ai/internal/review/section`.
3. Di Python, per section:
   - **Konteks utama:** seluruh teks section (semua blok, dengan `block_id`). Tidak pernah dipotong; section yang terlalu besar untuk konteks model dipecah per sub-section/kelompok blok berurutan oleh Go saat membuat task.
   - **Konteks bersama (prefix yang di-cache):** outline dokumen + fact sheet (`doc_facts`). Prompt caching dipakai bila provider mendukung.
   - **Konteks pendukung:** hybrid retrieval ke bagian lain dokumen (lihat §9).
   - **Worker** → temuan JSON (`block_id`, `category`, `severity`, `quote`, `description`, `suggestion`, `confidence`).
   - **Validator deterministik** (kode, bukan LLM): `block_id` milik section/versi ini; `quote` ada persis di teks blok (setelah normalisasi spasi); kategori valid. Gagal → temuan `rejected`.
   - **Supervisor (LLM):** menilai temuan yang lolos → `approve` / `revise` dengan feedback; worker mengulang maksimal 2 kali. Supervisor menilai **semua temuan satu section dalam satu panggilan** (hemat panggilan). Output supervisor yang tidak bisa diparse → task dianggap gagal untuk putaran itu (**fail-closed**), bukan approve. Jika setelah 2 putaran supervisor masih `revise`, temuan yang belum di-approve disimpan sebagai `needs_human` (tidak dipakai revisi otomatis), task tetap `done` — section dihitung terproses. *(keputusan builder-check)*
4. Setelah semua task section selesai (counter atomik di `jobs`; task terakhir meng-enqueue `finalize`), jalankan task `cross_section`: membandingkan `doc_facts` untuk konflik (angka/istilah/tanggal yang sama kunci tapi beda nilai) + LLM untuk mengonfirmasi; temuan `consistency` dengan dua `block_id`.
5. Job `done` hanya jika semua task `done`; jika ada yang gagal setelah retry → `partial` dan UI menunjukkan section mana yang tidak terproses. **Tidak boleh ada status "selesai" yang menyembunyikan section terlewat.**

### 8.3 Revisi otomatis
1. `POST .../reviews/{job}/revise` (atau opsi "review + revisi sekaligus") → job `revise`, task per section yang punya temuan `valid`.
   **Cakupan revisi otomatis** *(keputusan builder-check)*: kategori `language`, `structure`, dan `consistency` diterapkan otomatis. Kategori `substance` (mis. "argumen kurang data") **tidak** diterapkan otomatis karena memperbaikinya berarti menambah isi yang bisa dikarang AI; temuan ini tampil sebagai saran saja. Untuk `consistency`, nilai yang dipakai adalah nilai yang muncul di blok lain dokumen (bukan nilai baru), dan validator memastikannya.
2. Python per section: worker menulis teks pengganti per blok berdasar temuan; validator deterministik:
   - hanya blok yang punya temuan yang boleh berubah;
   - **angka/tanggal tidak boleh berubah** kecuali temuannya memang tentang angka/tanggal itu (ekstraksi angka sebelum/sesudah dibandingkan);
   - teks baru tidak kosong, panjang wajar.
   Supervisor approve/revise seperti review.
3. Finalize: Python `POST ai/internal/docx/apply` — menerapkan semua perubahan ke `normalized_file_key` versi sumber dengan **edit run-aware**: hitung diff karakter lama→baru, petakan offset ke run, sisipkan/hapus teks di run yang bersangkutan sehingga format bagian yang tidak berubah tetap utuh. Hasil: DOCX baru + (opsional) DOCX tracked-changes (`w:ins`/`w:del`) untuk dibuka di Word.
4. Go buat `document_versions(source=revision)` baru, salin blocks (warisi `block_uid`, teks baru untuk yang berubah), catat `block_changes` (dengan `finding_ids`), re-index.
5. Diterapkan otomatis tanpa persetujuan per perubahan; user melihat diff dan bisa rollback.

### 8.3b Revisi dari bahan revisi user
Selain temuan AI, revisi bisa digerakkan oleh **bahan revisi** yang diberikan user (catatan/permintaan perbaikan dari reviewer, atasan, klien, dsb.). User akan memberikan contoh bahan revisi nyata; format di bawah adalah asumsi awal dan disesuaikan setelah contoh tersedia.
- **Input yang diterima:** file (DOCX/PDF/teks) berisi daftar catatan, teks bebas yang diketik, dan **komentar Word** (`w:comment`) yang ada di DOCX itu sendiri.
- **Alur:**
  1. `POST .../versions/{vid}/revisions` (multipart: file dan/atau teks, opsi `use_docx_comments`) → job `instructed_revise`.
  2. Python memecah bahan menjadi **instruksi** satu per satu (`revision_instructions`).
  3. Setiap instruksi dipetakan ke blok target: komentar Word sudah menunjuk paragrafnya; catatan bebas dipetakan lewat kutipan/rujukan bab/halaman + hybrid retrieval. Instruksi yang targetnya tidak bisa dipastikan → `needs_human` (UI meminta user memilih paragraf).
  4. Task per section mengerjakan instruksi yang targetnya di section itu; worker → validator deterministik (hanya blok target yang boleh berubah; angka baru harus berasal dari instruksi atau data yang dilampirkan) → supervisor.
  5. Finalize sama seperti §8.3 → versi baru `source=instructed_revision`.
- **Laporan pertanggungjawaban:** setiap instruksi berakhir dengan status `done` (dengan tautan ke `block_changes`), `not_applicable` (dengan alasan), atau `needs_human`. Ini bisa diekspor sebagai tabel "catatan revisi → tindak lanjut" (berguna untuk dikirim balik ke pemberi catatan).
- Berbeda dengan §8.3: instruksi dari manusia boleh menyentuh kategori substansi (menambah/mengubah isi), karena isinya berasal dari pemberi instruksi, bukan dikarang AI.

### 8.4 Update laporan berkala
Input: versi sumber (laporan periode lalu), file data baru (bebas: XLSX/CSV/DOCX/PDF/teks), label periode baru (mis. "Oktober 2026").
1. `POST .../versions/{vid}/updates` (multipart) → job `update`; file data ke MinIO, `update_inputs`.
2. Python `extract`: XLSX/CSV → tabel terstruktur (openpyxl/pandas); DOCX/PDF/teks → teks + tabel. Hasil di `update_inputs.extracted`, dan di-index sebagai sumber data job ini.
3. Python `plan`: per section, tandai blok yang bergantung periode (tanggal, angka, tabel, narasi analisis/kesimpulan) vs yang tetap (metodologi, profil, dsb.).
4. Task per section: worker menulis ulang blok yang ditandai memakai data baru (retrieval ke data baru + konteks section).
5. **Angka turunan** (pertumbuhan %, selisih, total, porsi, pembulatan seperti "naik 12,3%", "1,2 juta") *(keputusan builder-check)*: worker tidak menulis angka turunan secara bebas. Ia menulis placeholder dengan rumus atas rujukan data, mis. `{{calc: (data.okt.penjualan - prev.sep.penjualan) / prev.sep.penjualan * 100 | pct:1}}`. Modul kalkulasi deterministik (evaluator ekspresi aman, tanpa `eval`) menghitung nilainya, memformat sesuai locale dokumen, dan menyimpan rumus + rujukan di `source_refs`. Rujukan `prev.*` boleh menunjuk angka di versi sumber.
6. Validator deterministik: **setiap angka di teks baru harus berasal dari data baru, dari hasil kalkulasi, atau sudah ada di teks lama yang tidak berubah**; angka tanpa sumber → blok ditandai `needs_human` dan tidak diterapkan (blok lama dipertahankan). `block_changes.source_refs` menyimpan rujukan ke sel/baris data sumber atau rumusnya.
7. Finalize sama seperti revisi → versi baru `source=update` + changelog (apa yang diperbarui, apa yang ditahan untuk diperiksa manusia).

### 8.5 Diff & rollback
- Diff dihitung di Go per `block_uid`: added / removed / modified / unchanged; untuk modified ada diff kata (lib `sergi/go-diff`). Setiap perubahan menautkan `block_changes.reason`/`finding_ids`/`source_refs` → bisa ditelusuri "kenapa berubah".
- Rollback: `POST .../versions/{vid}/restore` → versi baru `source=rollback` yang menyalin file & blok versi lama.

## 9. RAG

- **Chunking:** per section; blok berurutan digabung sampai ~400 token, tidak pernah melewati batas section. Setiap chunk diberi prefix `heading_path` sebelum di-embed. `block_ids` disimpan sehingga setiap hasil retrieval langsung menunjuk paragraf.
- **Embedding:** `BAAI/bge-m3` (dense 1024 dim) lokal di CPU di service Python; adapter bisa diganti ke OpenAI embedding lewat config.
- **Full-text:** `tsvector` Postgres (konfigurasi `simple`; builder boleh mengganti ke stemmer Indonesia bila tersedia dan terbukti lebih baik).
- **Hybrid:** top-30 vektor + top-30 full-text, digabung dengan Reciprocal Rank Fusion; ambil top-8. Scope: versi yang sedang diproses (dan data update untuk job update), section yang sedang diproses dikecualikan.
- **Rerank:** `bge-reranker-v2-m3` sebagai opsi config, **mati default**; dinyalakan hanya jika eval (§12) menunjukkan peningkatan.
- **Retrieval testing:** endpoint `POST ai/internal/retrieve` + halaman debug sederhana di frontend untuk melihat chunk yang kembali untuk sebuah query.

## 10. Lapisan penyedia LLM

Interface Python `LLMProvider.complete(messages, schema, model, cache_prefix) → (json, usage)`.

**Akses yang dimiliki user saat ini: hanya langganan Claude Code.** Karena itu `claude_code` adalah adapter **pertama yang dibangun, diuji, dan dipakai default**. Dua adapter lain dibangun di fase 2 dengan unit test (mock HTTP), siap dipakai begitu ada API key.

- **`claude_code`** (default) — Claude Code headless: `claude -p --output-format json` dengan semua tool dimatikan (mode LLM murni, satu turn), atau Claude Agent SDK Python dengan opsi setara. Detail:
  - Output terstruktur tidak dijamin oleh API → skema JSON dimasukkan ke prompt, output divalidasi Pydantic, 1 retry dengan pesan error, lalu fail-closed.
  - Prompt caching tidak bisa dikontrol dari aplikasi → prefix bersama (outline + fact sheet) dibuat ringkas.
  - Autentikasi: CLI `claude` terpasang di container `ai`, kredensial login user di-mount dari host (read-only bila memungkinkan).
  - Concurrency default **2** (config `LLM_CONCURRENCY`, dijaga dengan semaphore global di Python, bukan hanya Asynq).
  - **Batas pemakaian langganan:** error rate limit/usage limit dideteksi dan dibedakan dari error biasa. Task tidak dihitung gagal; Python mengembalikan status `rate_limited` dengan perkiraan waktu reset, Go menjadwal ulang task (Asynq `ProcessIn`) dan job berstatus `paused` sampai bisa lanjut. UI menampilkan "menunggu kuota".
  - Ditujukan untuk pemakaian pribadi; untuk multi-user/produksi gunakan API key.
- **`anthropic`** — Claude API (SDK resmi), structured output & prompt caching untuk prefix bersama; default model `claude-sonnet-5-5`.
- **`openai`** — OpenAI API dan endpoint kompatibel OpenAI (termasuk model lokal seperti Ollama/vLLM bila nanti dibutuhkan).

**Konsekuensi waktu:** dengan concurrency 2, review dokumen ~200 halaman diperkirakan memakan waktu dalam hitungan jam. Ini diterima: job berjalan di background, progres per section terlihat, dan job bisa dilanjutkan setelah jeda kuota tanpa mengulang section yang sudah selesai.

Konfigurasi per peran: `LLM_WORKER`, `LLM_SUPERVISOR`, `LLM_FACTS` masing-masing `provider:model`. Semua output terstruktur divalidasi dengan skema (Pydantic); gagal parse → 1 retry dengan pesan error → lalu gagal (fail-closed). Setiap panggilan dicatat di `llm_calls` (token, biaya, latensi). Ada batas token per job (config) untuk menghindari biaya liar.

## 11. API

**Publik (Go, JWT)** — prefix `/api/v1`
```
POST   /documents                                   upload (versi 1)
GET    /documents  · GET /documents/{id}
POST   /documents/{id}/versions                     upload versi baru (hasil edit di Word)
GET    /documents/{id}/versions
GET    /versions/{vid}                              metadata + outline section
GET    /versions/{vid}/blocks?section_id=
GET    /versions/{vid}/download?variant=clean|tracked
GET    /versions/{vid}/diff?against={vid2}
POST   /versions/{vid}/restore
POST   /versions/{vid}/reviews                      {categories?, auto_revise?}
POST   /reviews/{job_id}/revise
POST   /versions/{vid}/revisions                    multipart: bahan revisi (file/teks) + use_docx_comments
POST   /versions/{vid}/updates                      multipart: data files + period_label
GET    /jobs/{job_id}  · GET /jobs/{job_id}/findings  · GET /jobs/{job_id}/changes
GET    /jobs/{job_id}/events                        SSE progres
```

**Internal (Python, header `X-Internal-Token`, hanya jaringan docker)**
```
POST /internal/parse              {file_key, format} → {normalized_file_key, sections[], blocks[]}
POST /internal/index              {version_id} → {chunks, facts}
POST /internal/retrieve           {version_id, query, exclude_section_id?, k} → chunks[]
POST /internal/review/section     {version_id, section_id, categories} → {findings[], usage}
POST /internal/revise/section     {version_id, section_id, finding_ids} → {changes[], usage}
POST /internal/update/extract     {job_id, inputs[]} → {extracted}
POST /internal/update/section     {job_id, version_id, section_id, period_label} → {changes[], held[], usage}
POST /internal/docx/apply         {source_file_key, changes[{para_id, old_text, new_text}], tracked} → {file_key, tracked_file_key}
```
Panggilan internal yang lama (review/revise section) tetap sinkron per task; timeout per task 30 menit (config); Asynq retry maks 2 untuk error biasa; status `rate_limited` dijadwal ulang tanpa memakan jatah retry. Status job ditambah `paused`.

## 12. Evaluasi (bagian dari fase 2, wajib sebelum fase 3)

**Bahasa & angka** *(keputusan builder-check)*: dokumen diasumsikan berbahasa Indonesia (PUEBI/KBBI untuk cek bahasa), dengan istilah/kutipan bahasa Inggris diperbolehkan dan tidak dianggap salah. Format angka default locale Indonesia (`1.234,5`); parser angka mendeteksi locale per dokumen dari sampel angka dan menyimpannya di `document_versions.number_locale`. Kedua validator memakai parser yang sama.

**Contoh dokumen nyata akan diberikan user** (laporan panjang dan pasangan laporan bulanan). Sampai itu tersedia, fase 1–2 diuji dengan dokumen sintetis; parser dan eval wajib diuji ulang dengan dokumen nyata begitu ada, sebelum fase 3 dianggap selesai.

- Siapkan dokumen uji (≥1 DOCX panjang) yang disisipi 30–50 kesalahan yang diketahui (ejaan, angka bertentangan antarbab, istilah tidak konsisten, kalimat tidak logis) dengan posisi tercatat.
- Ukur: **recall** (kesalahan yang ditemukan), **precision** (temuan yang benar), **coverage** (100% section diproses), **stabilitas** (overlap temuan antara dua run).
- Skrip eval di repo, hasil disimpan per run supaya perubahan prompt/model/retrieval bisa dibandingkan.
- Untuk revisi: cek otomatis bahwa paragraf yang tidak punya temuan tidak berubah, dan format (style per run) tetap sama di bagian yang tidak diubah.

## 13. Fase

1. **Fondasi** — salin & bersihkan starter kit; compose (pgvector, redis, minio, ai); migration skema §7; Python parse DOCX (normalisasi paraId) + PDF; upload, daftar dokumen & versi, viewer per section, upload versi baru (pencocokan `block_uid`), diff, rollback, download; frontend dasar.
2. **Review** — chunking/embedding/hybrid retrieval, fact sheet, lapisan LLM (3 adapter), review per section + validator + supervisor, cross-section, SSE progres, UI temuan, eval harness.
3. **Revisi otomatis** — revise per section, edit DOCX run-aware, export tracked changes, versi baru + `block_changes`, UI diff dengan alasan; **revisi dari bahan revisi user** (§8.3b) beserta laporan tindak lanjut per instruksi.
4. **Update berkala** — ekstraksi data, plan per section, rewrite + validator angka, changelog & blok yang ditahan.

## 14. Diserahkan ke builder

- Struktur folder dalam repo (mis. `backend/`, `ai-service/`, `web/`), library frontend (router, state, UI kit), dan tampilan detail.
- Isi prompt, selama memenuhi skema output & aturan validator di atas.
- Detail pemetaan style heading untuk dokumen non-standar, ambang kemiripan, ukuran chunk — mulai dari angka di dokumen ini, sesuaikan dengan eval.
- Penamaan endpoint/field selama semantiknya sama.
