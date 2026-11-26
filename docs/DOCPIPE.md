# DocPipe — review & revisi dokumen di Agentorium

Fork ini menambahkan tab **Dokumen** ke Agentorium: unggah DOCX/PDF, simpan setiap versi, lihat outline dan isi per bagian, unduh, kembalikan ke versi lama, cari isi dokumen (RAG), **review per bab oleh tim agen** (worker + supervisor dari ⚙ Pengaturan tim), **revisi otomatis** dari temuan, **revisi dari bahan revisi** (catatan, berkas, komentar Word) dengan tabel tindak lanjut, **update berkala** dari data periode baru, dan **bandingkan versi** (lihat `docs/superpowers/specs/2026-11-26-agentorium-docpipe-design.md`).

## Status

| Fase | Isi | Status |
|---|---|---|
| 1 | Adapter Claude Code, penyimpanan, ingest DOCX/PDF, versi, rollback, tab Dokumen | selesai |
| 2 | RAG lokal + review per bab | selesai |
| 3 | Revisi otomatis + revisi dari bahan revisi + bandingkan versi | selesai |
| 4 | Update laporan berkala | selesai |

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

## Revisi otomatis

Setelah review selesai, tombol **Revisi otomatis dari temuan** membuat versi baru:

- Hanya untuk **DOCX** (PDF ditolak). Temuan *bahasa, konsistensi, struktur* yang valid dikerjakan per bab; temuan **substansi tidak pernah diubah otomatis** — muncul sebagai *saran* di tabel Perubahan.
- Worker menulis teks baru per paragraf → **validator kode** menolak edit yang mengubah/menambah angka atau tanggal tanpa dasar, teks kosong, atau perubahan panjang yang ekstrem → supervisor menyetujui / menolak / minta perbaiki (maks. `OFFICE_MAX_REVISIONS`).
- Edit diterapkan dengan [adeu](https://github.com/dealfluence/adeu) sebagai **tracked changes**; format (tebal, miring, gaya) dipertahankan. Paragraf yang teksnya muncul lebih dari sekali tidak diubah (→ *perlu dicek*).
- Hasil: versi baru `v<N+1>` (sumber *revisi AI*, induk = versi yang direvisi) berisi semua perubahan diterima, plus **Unduh tracked changes** (`v<N+1>.tracked.docx`) untuk dicek di Word. Revisi atas versi lama tetap mendapat nomor berikutnya; versi lama tidak pernah ditimpa.
- Tabel **Perubahan**: lokasi, teks lama → baru, alasan, status (*diterapkan, perlu dicek, ditolak, saran*). Temuan yang dilewati worker tidak hilang — tercatat *perlu dicek* beserta alasannya.

## Revisi dari bahan revisi

Panel **Revisi dari bahan revisi**: tempel catatan, unggah berkas (txt, md, docx, pdf — boleh beberapa), dan/atau centang **Pakai komentar Word di dokumen ini**.

1. Catatan dan berkas dipecah supervisor menjadi instruksi (satu instruksi = satu perubahan). Komentar Word langsung menjadi instruksi di paragraf tempat komentar itu menempel.
2. Setiap instruksi dicari lokasinya: kutipan persis dulu, lalu pencarian RAG (kandidat). Instruksi yang **tidak ditemukan lokasinya tetap dilaporkan** (*perlu dicek manual*).
3. Worker mengerjakan instruksi per bab lewat loop yang sama (validator + supervisor); nilai baru (angka, nama) hanya boleh dari instruksinya.
4. **Tabel tindak lanjut** — No, Sumber, Instruksi, Status (*selesai / tidak dikerjakan / perlu dicek manual*), Tanggapan, Lokasi — tampil di web dan bisa diunduh sebagai DOCX. Instruksi hanya *selesai* bila perubahannya benar-benar masuk ke DOCX.

Satu versi hanya boleh punya satu job revisi yang berjalan (permintaan kedua → 409).

## Update berkala

Panel **Update berkala**: isi periode baru (mis. *November 2026*), unggah data (xlsx, csv, docx, pdf, txt — boleh beberapa), catatan opsional → **Mulai update**. Hasilnya versi baru bersumber *update berkala* dari versi yang sedang dibuka.

- **Data**: XLSX/CSV dibaca per baris dengan nama kolom (`Ringkasan baris 3: Indikator=Penjualan (Rp) | November 2026=286000000`); DOCX/PDF/teks sebagai teks. Bila data panjang, tiap bab hanya menerima baris yang paling relevan (±12.000 karakter).
- **Per bab** worker memperbarui yang bergantung periode — tanggal, bulan, angka, sel tabel, kalimat analisis — dan tidak menyentuh bagian tetap (metodologi, profil).
- **Angka turunan dihitung mesin, bukan LLM**: worker menulis `{{calc: (286000000 - 260000000) / 260000000 * 100 | pct:1}}`, kalkulator (hanya + − × ÷ dan kurung, tanpa `eval`) menghitung dan memformat gaya Indonesia (`10,0`). Rumusnya tercatat di kolom alasan tabel Perubahan. Angka di dalam rumus harus berasal dari data atau laporan lama (kecuali pengali 10/100/1.000.000… dan bilangan kecil 1–12).
- **Setiap angka baru wajib punya sumber**: dari data, dari hasil rumus, atau sudah ada di blok itu. Yang tidak → blok **ditahan** (tetap teks lama, status *perlu dicek* dengan alasannya). Blok yang datanya tidak ada juga ditahan dengan alasan "ditahan: …".
- Supervisor memeriksa setiap edit terhadap data yang sama sebelum diterapkan.
- Angka bersatuan yang sama dengan data dikenali langsung: "Rp 286 juta" = 286.000.000 (ribu, juta, miliar, triliun).
- Menambah/menghapus baris tabel belum didukung (hanya isi sel yang diubah).

### Hasil uji update berkala

Laporan uji Oktober 2026 + `data-november.xlsx` (indikator Oktober vs November, jadwal kegiatan) → periode *November 2026*, Claude Code `haiku`, lewat UI (26 Nov 2026):

| Ukuran | Hasil |
|---|---|
| Nilai kunci November di versi baru | **7/7** (Rp 286 juta, naik 10,0 persen, 130 responden, 85 persen puas, Rp 600 juta, 11 November 2026, 20 karyawan) |
| Angka turunan dari kalkulator | naik 10,0 persen · realisasi 98,3 persen dari anggaran · naik 3 poin |
| Perubahan | 12 diterapkan · 2 ditahan (data kunjungan dan durasi pelatihan tidak ada di data) |
| Struktur blok | sama dengan versi asal |
| Durasi | ±1,5 menit untuk 7 bab |

## Bandingkan versi

Kotak **Bandingkan versi**: pilih dua versi → daftar paragraf *diubah / ditambah / dihapus* (dicocokkan lewat `uid` paragraf) dan **Unduh redline (DOCX)** — versi kedua sebagai tracked changes terhadap versi pertama (keduanya harus DOCX).

## Pencarian (RAG)

Setiap versi diindeks otomatis setelah diunggah: dipotong per bab (±1.600 karakter, tidak melewati batas bab), dicari dengan **kata kunci (SQLite FTS5)** dan **makna (embedding e5, dihitung di CPU, vektor disimpan di SQLite)**, lalu digabung (Reciprocal Rank Fusion). Kotak **Cari di dokumen** di tab Dokumen menampilkan hasilnya beserta sumber peringkat (FTS / vektor).

Model embedding (±2,2 GB) diunduh sekali ke `data/models/` saat pertama dipakai. Bila gagal dimuat, indeks tetap jalan dengan kata kunci saja (`indeks kata kunci saja`).

## Data

Semua data ada di `data/` (atau `OFFICE_DATA_DIR`):

- `data/docpipe.db` — SQLite: dokumen, versi, bagian (section), blok (paragraf/sel tabel), potongan indeks + vektor, job review, temuan, fakta.
- `data/models/` — cache model embedding.
- `data/evals/` — hasil evaluasi review.
- `data/docs/<doc_id>/v<N>.docx` — file setiap versi. Bila DOCX berisi tracked changes, versi kerja = semua perubahan diterima, dan file asli disimpan sebagai `v<N>.original.docx`. Versi hasil revisi juga punya `v<N>.tracked.docx`.
- Tabel `changes` (log perubahan per paragraf) dan `instructions` (instruksi dari bahan revisi + tindak lanjutnya).

Setiap blok punya `uid` yang dibawa lintas versi (paragraf yang sama atau mirip ≥ 80% tetap ber-uid sama), sehingga riwayat per paragraf bisa ditelusuri.

## API

`/api/docs` — `POST` unggah · `GET` daftar · `GET/DELETE /{id}` · `POST /{id}/versions` versi baru ·
`GET /versions/{vid}/outline` · `GET /versions/{vid}/blocks?section_id=` · `GET /versions/{vid}/download?variant=current|original` · `POST /versions/{vid}/restore` ·
`GET /versions/{vid}/search?q=&k=` · `POST /versions/{vid}/reviews` `{"categories": [...]}` · `GET /versions/{vid}/jobs` ·
`GET /jobs/{job_id}` · `GET /jobs/{job_id}/findings?status=&category=` · `POST /jobs/{job_id}/cancel` ·
`POST /jobs/{review_job_id}/revisions` `{"categories"?: [...]}` · `POST /versions/{vid}/instructed-revisions` (multipart `notes`, `files`, `use_comments`) ·
`GET /jobs/{job_id}/changes` · `GET /versions/{vid}/changes` · `GET /jobs/{job_id}/instructions` · `GET /jobs/{job_id}/followup.docx` ·
`POST /versions/{vid}/updates` (multipart `period_label`, `files`, `notes`) · `GET /versions/{vid}/download?variant=tracked` · `GET /versions/{a}/compare/{b}` · `GET /versions/{a}/compare/{b}/redline`.

## Test

```bash
.venv/bin/pytest            # semua test (tanpa memanggil Claude)
.venv/bin/pytest -m live    # uji nyata ke Claude Code (butuh login)
.venv/bin/python scripts/eval_review.py --model haiku     # ukur kualitas review (memakai kuota Claude)
.venv/bin/python scripts/eval_revision.py --model haiku   # ukur revisi otomatis + revisi dari bahan (memakai kuota)
.venv/bin/python scripts/eval_update.py --model haiku     # ukur update berkala Oktober → November (memakai kuota)
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

### Hasil evaluasi revisi

`scripts/eval_revision.py` memakai laporan uji yang sama: review → revisi otomatis → revisi dari catatan berisi 5 instruksi (salah satunya tentang bagian yang tidak ada).

Hasil 26 Nov 2026 (Claude Code `haiku`):

| Ukuran | Hasil |
|---|---|
| Ejaan sisipan diperbaiki di versi baru | **12/12** |
| Paragraf tanpa temuan yang ikut berubah | **0** |
| Angka/tanggal yang hilang | **0** |
| Struktur blok | sama dengan versi asal |
| Perubahan | 12 diterapkan · 13 perlu dicek (mis. konsistensi yang nilai benarnya tak bisa dipastikan) · 1 ditolak · 7 saran substansi |
| Revisi dari catatan | 4 selesai (Rp 540 juta, 125 responden, 21 Oktober, Unit Layanan Anggota) · instruksi "Lampiran" → *tidak dikerjakan* dengan alasan |
| Durasi | review ±3 menit · revisi otomatis ±1 menit · revisi dari bahan ±1 menit |

## Git

`main` mengikuti upstream Agentorium. Basis kerja fork ini adalah branch `rahmat`; setiap pekerjaan dibuat di branch sendiri dari `rahmat`.
