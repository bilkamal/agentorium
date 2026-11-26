# DocPipe — review & revisi dokumen di Agentorium

Fork ini menambahkan tab **Dokumen** ke Agentorium: unggah DOCX/PDF, simpan setiap versi, lihat outline dan isi per bagian, unduh, dan kembalikan ke versi lama. Review dan revisi berbasis AI menyusul di fase 2–4 (lihat `docs/superpowers/specs/2026-11-26-agentorium-docpipe-design.md`).

## Status

| Fase | Isi | Status |
|---|---|---|
| 1 | Adapter Claude Code, penyimpanan, ingest DOCX/PDF, versi, rollback, tab Dokumen | selesai |
| 2 | RAG lokal + review per bab | berikutnya |
| 3 | Revisi otomatis + revisi dari bahan revisi | belum |
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

Claude Code dijalankan tanpa tool, tanpa setting/plugin/MCP, dan tanpa menyimpan sesi — murni sebagai LLM. Bila kuota langganan habis, error-nya dibedakan (`LLMRateLimited`) supaya job bisa dilanjutkan nanti.

## Menjalankan

```bash
./run.sh            # http://127.0.0.1:8000
```

Server hanya mendengar di `127.0.0.1`. Dari laptop: `ssh -L 8000:127.0.0.1:8000 <server>` lalu buka `http://127.0.0.1:8000` dan pilih tab **Dokumen**.

## Data

Semua data ada di `data/` (atau `OFFICE_DATA_DIR`):

- `data/docpipe.db` — SQLite: dokumen, versi, bagian (section), blok (paragraf/sel tabel).
- `data/docs/<doc_id>/v<N>.docx` — file setiap versi. Bila DOCX berisi tracked changes, versi kerja = semua perubahan diterima, dan file asli disimpan sebagai `v<N>.original.docx`.

Setiap blok punya `uid` yang dibawa lintas versi (paragraf yang sama atau mirip ≥ 80% tetap ber-uid sama), sehingga riwayat per paragraf bisa ditelusuri.

## API

`/api/docs` — `POST` unggah · `GET` daftar · `GET/DELETE /{id}` · `POST /{id}/versions` versi baru ·
`GET /versions/{vid}/outline` · `GET /versions/{vid}/blocks?section_id=` · `GET /versions/{vid}/download?variant=current|original` · `POST /versions/{vid}/restore`.

## Test

```bash
.venv/bin/pytest            # semua test (tanpa memanggil Claude)
.venv/bin/pytest -m live    # uji nyata ke Claude Code (butuh login)
```

## Git

`main` mengikuti upstream Agentorium. Basis kerja fork ini adalah branch `rahmat`; setiap pekerjaan dibuat di branch sendiri dari `rahmat`.
