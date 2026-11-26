# Desain v2: Review & Revisi Dokumen di atas Agentorium

Tanggal: 2026-11-26 · Status: draft untuk disetujui
**Menggantikan** arsitektur di `2026-11-26-dcmt-review-revision-design.md` (Go + Python + Postgres). Dokumen lama disimpan sebagai cadangan bila nanti butuh sistem multi-user besar. Aturan perilaku di dokumen lama (§8 alur kerja, fail-closed, cakupan revisi otomatis, angka turunan, bahasa/locale) **tetap berlaku** kecuali diubah di sini.

## 1. Maksud
Sama dengan desain v1: review, revisi otomatis, revisi dari bahan revisi user, dan update laporan berkala untuk dokumen jenis apa pun, dengan riwayat versi yang bisa ditelusuri — kini sebagai **aplikasi web pribadi** yang ringan.

Alasan perubahan: membangun aplikasi Go + Python dari nol terlalu banyak. User ingin tampilan web dan memakai yang sudah ada.

## 2. Pendekatan
| Opsi | Trade-off | Keputusan |
|---|---|---|
| Go + Python + Postgres (v1) | Paling kuat, paling banyak kerja | Cadangan |
| Claude Code + perintah terminal | Paling cepat, tanpa web | Ditolak: user butuh web |
| **Fork Agentorium + adeu + RAG lokal** | UI web, orkestrasi agen & klien LLM sudah jadi; edit DOCX dari adeu; inti pipeline dokumen dibuat baru | **Dipilih** |

## 3. Komponen
- **Agentorium (fork, MIT)** — dipakai: `server.py` (FastAPI), `llm.py`, event bus + SSE, UI web & visualiser kantor, registry agen. Mode chat lama dibiarkan tetap jalan. Fork disimpan di repo ini dengan remote `upstream` ke `bilkamal/agentorium`; LICENSE & atribusi dipertahankan.
- **adeu (MIT, `pip install adeu`)** — semua baca/tulis DOCX yang bersifat edit: baca outline/halaman, search, `process_document_batch` (modify teks → tracked changes, komentar, insert/delete baris tabel, accept/reject), `diff_docx_files`.
- **python-docx** — hanya untuk membaca struktur (urutan paragraf & tabel, heading) saat ingest.
- **RAG lokal** — SQLite FTS5 + embedding `BAAI/bge-m3` (CPU) disimpan di SQLite, cosine di numpy, digabung dengan Reciprocal Rank Fusion.
- **Penyimpanan** — satu SQLite `data/app.db` + file versi di `data/docs/<doc_id>/v<N>.docx` (dan `v<N>.tracked.docx`). Tidak memakai git untuk versi.

## 4. Perubahan pada Agentorium
1. **Adapter `claude_code` di `llm.py`** (prioritas pertama; user hanya punya langganan Claude Code):
   `claude -p --output-format json` tanpa tool (mode LLM murni), model dari config, dijalankan via `asyncio.create_subprocess_exec`. Semaphore global `LLM_CONCURRENCY` default 2. Error batas pemakaian dibedakan → `LLMRateLimited(reset_at)`; job di-`pause` dan dilanjutkan otomatis, section yang sudah selesai tidak diulang.
2. **Fail-closed**: review/verdict yang tidak bisa di-parse = gagal, bukan approve (berlaku di pipeline dokumen; mode chat lama boleh tetap).
3. **Tanpa pemotongan 60k karakter** di pipeline dokumen: dokumen diproses per section, tidak pernah dipotong.

## 5. Pipeline dokumen (paket baru `agentorium/docpipe/`)
- **Ingest**: upload → simpan `v1.docx` → python-docx membaca body **berurutan** (paragraf & tabel di posisinya) → `sections` (dari heading) dan `blocks` (teks per paragraf/sel) → chunking per section (~400 token, prefix path heading) → FTS5 + embedding → ekstrak fact sheet (angka, istilah, tanggal per blok). DOCX yang berisi tracked changes: UI memperingatkan; working copy di-accept-all via adeu.
- **Identitas lokasi**: setiap blok punya `block_id` (urutan dalam versi) dan `block_uid` (lintas versi, diwariskan untuk versi yang dibuat aplikasi; versi upload ulang dicocokkan dengan kemiripan teks ≥ 0.8). **Edit diterapkan lewat adeu dengan teks target persis** (diperluas dengan konteks sampai unik); adeu menolak target yang ambigu → perubahan itu `needs_human`.
- **Planner deterministik**: satu task per section (section terlalu besar dipecah per kelompok blok). Tidak ada planner LLM bebas di pipeline dokumen → semua section pasti diproses; job `done` hanya jika semua task `done`, selain itu `partial` dengan daftar section yang gagal.
- **Loop per task** (pola Agentorium): worker → validator deterministik (kutipan ada persis di blok; angka/tanggal tidak berubah kecuali memang targetnya; angka baru harus punya sumber) → supervisor approve/revise (maks 2) → yang tetap ditolak jadi `needs_human`.
- **Review**: per section + pass konsistensi lintas section dari fact sheet + RAG.
- **Revisi otomatis**: kategori language/structure/consistency diterapkan; substance hanya saran.
- **Bahan revisi**: file/teks/komentar Word → instruksi → target blok (komentar = langsung; lainnya via kutipan + RAG) → edit → tabel tindak lanjut (done / not_applicable / needs_human), bisa diunduh.
- **Update berkala**: data baru (XLSX/CSV/DOCX/PDF/teks) → per section ganti angka/tabel/tanggal/narasi; angka turunan lewat placeholder rumus + kalkulator deterministik; baris tabel baru via adeu `insert_row`; angka tanpa sumber ditahan.
- **Hasil setiap job yang mengubah**: versi baru = `v<N+1>.docx` (bersih, perubahan di-accept) + `v<N+1>.tracked.docx` (tracked changes untuk diperiksa di Word) + catatan perubahan per blok dengan alasan/sumber.
- **Diff & rollback**: diff dua versi via adeu `diff_docx_files` (unduh sebagai tracked docx) + diff per blok di UI; rollback = versi baru yang menyalin versi lama.

## 6. Tampilan web (tambahan di UI Agentorium)
Tab baru **Dokumen**: daftar dokumen → halaman dokumen berisi daftar versi, outline section, tombol Review / Revisi otomatis / Revisi dari bahan / Update berkala, progres per section (SSE; agen terlihat bekerja di visualiser kantor), tabel temuan, tabel perubahan per versi, unduh (bersih/tracked), bandingkan versi, rollback. Halaman debug pencarian RAG.

Akses: satu user, server bind `127.0.0.1` (akses lewat SSH tunnel/reverse proxy) + token Agentorium yang sudah ada.

## 7. Fase
1. Fork + adapter `claude_code` + penyimpanan SQLite + ingest + tab Dokumen (upload, versi, outline, unduh, rollback).
2. RAG + review + tabel temuan + eval dengan dokumen uji.
3. Revisi otomatis + revisi dari bahan revisi + diff.
4. Update berkala.

Contoh dokumen dan bahan revisi nyata dari user dipakai untuk menguji ulang fase 1–3 begitu tersedia.

## 8. Diserahkan ke builder
Struktur file di dalam `docpipe/`, skema tabel SQLite (mengikuti entitas desain v1 §7 secara disederhanakan), isi prompt, detail tampilan, ukuran chunk & ambang (mulai dari angka di desain v1).
