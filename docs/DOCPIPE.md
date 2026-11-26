# DocPipe — review & revisi dokumen di Agentorium

Fork ini menambahkan tab **Dokumen** ke Agentorium: unggah DOCX/PDF, simpan setiap versi, lihat outline dan isi per bagian, unduh, kembalikan ke versi lama, cari isi dokumen (RAG), dan **review per bab oleh tim agen** (worker + supervisor dari ⚙ Pengaturan tim). Revisi otomatis dan update berkala menyusul di fase 3–4 (lihat `docs/superpowers/specs/2026-11-26-agentorium-docpipe-design.md`).

## Status

| Fase | Isi | Status |
|---|---|---|
| 1 | Adapter Claude Code, penyimpanan, ingest DOCX/PDF, versi, rollback, tab Dokumen | selesai |
| 2 | RAG lokal + review per bab | selesai |
| 3 | Revisi otomatis + revisi dari bahan revisi | berikutnya |
| 4 | Update laporan berkala | belum |

## Instalasi

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
```

### Memakai langganan Claude Code (tanpa API key)

1. Pastikan `claude` terpasang dan sudah login di server: `claude login`.
2. Buka ⚙ **Pengaturan tim** di web dan pilih provider `claude_code` untuk agent, model mis. `claude-sonnet-5-5` atau `haiku`.
   (Atau isi `OFFICE_DEFAULT_PROVIDER=claude_code` di `.env` sebelum start pertama.)

Setting tambahan di `.env`:

| Variabel | Default | Arti |
|---|---|---|
| `OFFICE_LLM_CONCURRENCY` | `2` | maksimum panggilan Claude Code bersamaan |
| `OFFICE_CLAUDE_BIN` | `claude` | lokasi CLI |
| `OFFICE_CLAUDE_TIMEOUT` | `1800` | detik sebelum panggilan dihentikan |
| `OFFICE_EMBED_MODEL` | `intfloat/multilingual-e5-large` | model embedding untuk pencarian; `none` = kata kunci saja |
| `OFFICE_RATE_LIMIT_WAIT` | `900` | detik jeda sebelum review dilanjutkan setelah kuota habis |
| `OFFICE_MAX_REVISIONS` | `2` | berapa kali supervisor boleh meminta worker memperbaiki temuan |

Claude Code dijalankan tanpa tool, tanpa setting/plugin/MCP, dan tanpa menyimpan sesi — murni sebagai LLM. Bila kuota langganan habis, error-nya dibedakan (`LLMRateLimited`) supaya job bisa dilanjutkan nanti.

## Menjalankan

```bash
./run.sh            # http://127.0.0.1:8000
```

Server hanya mendengar di `127.0.0.1`. Dari laptop: `ssh -L 8000:127.0.0.1:8000 <server>` lalu buka `http://127.0.0.1:8000` dan pilih tab **Dokumen**.

## Review dokumen

1. Pastikan tim punya **satu supervisor dan minimal satu worker** (⚙ Pengaturan tim), provider `claude_code`.
2. Tab **Dokumen** → pilih dokumen → centang kategori (Bahasa, Konsistensi, Struktur, Substansi) → **Review versi ini**.
3. Progres tampil per bab. **Lihat di Kantor** membuka sesi review di tab Kantor: agen terlihat bekerja, satu langkah per bab.
4. Tabel temuan: filter kategori/status; klik baris untuk membuka bab dan menyorot paragrafnya.

Cara kerjanya:

- **Per bab** (bab > 24.000 karakter dipecah, tidak pernah dipotong). Worker membaca isi bab + outline + potongan bab lain hasil pencarian RAG, lalu melapor temuan dan fakta (angka, tanggal, istilah).
- **Validator kode**: temuan wajib menunjuk paragraf yang ada dan mengutipnya persis; yang tidak → *ditolak* otomatis.
- **Supervisor** menilai: terima / tolak / minta perbaiki (maks. `OFFICE_MAX_REVISIONS` kali). Tanpa keputusan atau masih diminta perbaiki → *perlu dicek* (tidak pernah diloloskan diam-diam).
- **Cek konsistensi antarbab** di akhir: fakta dengan nama sama tetapi nilai berbeda (format angka Indonesia dikenali, `1.200` = `1200`) dinilai supervisor.
- **Kuota habis** → review *dijeda* dan lanjut sendiri setelah `OFFICE_RATE_LIMIT_WAIT` detik; bab yang sudah selesai tidak diulang. **Server restart** → review dilanjutkan otomatis.

## Pencarian (RAG)

Setiap versi diindeks otomatis setelah diunggah: dipotong per bab (±1.600 karakter, tidak melewati batas bab), dicari dengan **kata kunci (SQLite FTS5)** dan **makna (embedding e5, dihitung di CPU, vektor disimpan di SQLite)**, lalu digabung (Reciprocal Rank Fusion). Kotak **Cari di dokumen** di tab Dokumen menampilkan hasilnya beserta sumber peringkat (FTS / vektor).

Model embedding (±2,2 GB) diunduh sekali ke `data/models/` saat pertama dipakai. Bila gagal dimuat, indeks tetap jalan dengan kata kunci saja (`indeks kata kunci saja`).

## Data

Semua data ada di `data/` (atau `OFFICE_DATA_DIR`):

- `data/docpipe.db` — SQLite: dokumen, versi, bagian (section), blok (paragraf/sel tabel), potongan indeks + vektor, job review, temuan, fakta.
- `data/models/` — cache model embedding.
- `data/evals/` — hasil evaluasi review.
- `data/docs/<doc_id>/v<N>.docx` — file setiap versi. Bila DOCX berisi tracked changes, versi kerja = semua perubahan diterima, dan file asli disimpan sebagai `v<N>.original.docx`.

Setiap blok punya `uid` yang dibawa lintas versi (paragraf yang sama atau mirip ≥ 80% tetap ber-uid sama), sehingga riwayat per paragraf bisa ditelusuri.

## API

`/api/docs` — `POST` unggah · `GET` daftar · `GET/DELETE /{id}` · `POST /{id}/versions` versi baru ·
`GET /versions/{vid}/outline` · `GET /versions/{vid}/blocks?section_id=` · `GET /versions/{vid}/download?variant=current|original` · `POST /versions/{vid}/restore` ·
`GET /versions/{vid}/search?q=&k=` · `POST /versions/{vid}/reviews` `{"categories": [...]}` · `GET /versions/{vid}/jobs` ·
`GET /jobs/{job_id}` · `GET /jobs/{job_id}/findings?status=&category=` · `POST /jobs/{job_id}/cancel`.

## Test

```bash
.venv/bin/pytest            # semua test (tanpa memanggil Claude)
.venv/bin/pytest -m live    # uji nyata ke Claude Code (butuh login)
.venv/bin/python scripts/eval_review.py --model haiku   # ukur kualitas review (memakai kuota Claude)
```

### Hasil evaluasi

`scripts/eval_review.py` mereview laporan uji berisi 20 kesalahan sisipan (11 ejaan, 4 konsistensi antarbab, 2 struktur, 3 substansi) dan mengukur *recall* (kesalahan sisipan yang ditemukan), *precision* (batas bawah: temuan valid yang mengenai kesalahan sisipan — temuan lain bisa saja masalah nyata), cakupan bab, durasi, dan jumlah panggilan Claude.

Hasil 26 Nov 2026 (Claude Code `haiku` untuk supervisor dan 2 worker, indeks e5 siap):

| Ukuran | Hasil |
|---|---|
| Recall | **95%** (19/20) — ejaan 11/11, konsistensi 4/4, substansi 3/3, struktur 1/2 |
| Precision (batas bawah) | **≥ 91%** (39 dari 43 temuan valid mengenai kesalahan sisipan) |
| Cakupan bab | 100%, job `done`, 1 temuan *perlu dicek* |
| Panggilan Claude | 23 |
| Durasi review | ±3 menit (run pertama total 23 menit karena model embedding diunduh dulu) |

Terlewat: kalimat penutup surat ("Demikian laporan ini kami sampaikan…") di tengah bab Pelaksanaan Kegiatan.

## Git

`main` mengikuti upstream Agentorium. Basis kerja fork ini adalah branch `rahmat`; setiap pekerjaan dibuat di branch sendiri dari `rahmat`.
