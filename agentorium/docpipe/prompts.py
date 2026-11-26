"""Prompts and JSON schemas for the document review loop."""
from __future__ import annotations

CATEGORY_RULES = {
    "language": "language: ejaan (PUEBI/KBBI), tata bahasa, kata tidak baku, tanda baca, kalimat ambigu atau "
                "berbelit. Istilah asing yang lazim di bidangnya bukan kesalahan.",
    "consistency": "consistency: istilah, singkatan, angka, tanggal, nama, atau satuan yang berbeda dengan bagian "
                   "lain (lihat KONTEKS) atau di dalam bagian ini sendiri.",
    "structure": "structure: urutan logis, paragraf yang tidak nyambung, judul tidak sesuai isi, penomoran, "
                 "bagian yang tampak terpotong atau kosong.",
    "substance": "substance: klaim tanpa dasar, kesimpulan yang tidak didukung data, perhitungan yang salah, "
                 "kontradiksi logis. Hanya saran — jangan mengarang fakta.",
}

FINDING_ITEM = {
    "type": "object",
    "properties": {
        "block_id": {"type": "string"},
        "category": {"type": "string", "enum": list(CATEGORY_RULES)},
        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
        "quote": {"type": "string"},
        "description": {"type": "string"},
        "suggestion": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["block_id", "category", "severity", "quote", "description", "suggestion", "confidence"],
}

FACT_ITEM = {
    "type": "object",
    "properties": {
        "block_id": {"type": "string"},
        "kind": {"type": "string", "enum": ["number", "date", "term", "name", "claim"]},
        "key": {"type": "string"},
        "value": {"type": "string"},
        "unit": {"type": "string"},
        "quote": {"type": "string"},
    },
    "required": ["block_id", "kind", "key", "value", "unit", "quote"],
}

WORKER_SCHEMA = {
    "type": "object",
    "properties": {"findings": {"type": "array", "items": FINDING_ITEM},
                   "facts": {"type": "array", "items": FACT_ITEM}},
    "required": ["findings", "facts"],
}

SUPERVISOR_SCHEMA = {
    "type": "object",
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object",
        "properties": {"index": {"type": "integer"},
                       "verdict": {"type": "string", "enum": ["approve", "reject", "revise"]},
                       "feedback": {"type": "string"}},
        "required": ["index", "verdict", "feedback"]}}},
    "required": ["verdicts"],
}


def worker_system(name: str, categories: list[str]) -> str:
    rules = "\n".join(f"- {CATEGORY_RULES[c]}" for c in categories)
    return f"""Anda {name}, reviewer dokumen yang teliti. Tugas Anda memeriksa SATU bagian dokumen dan melaporkan temuan.

Kategori yang diperiksa (hanya ini):
{rules}

Aturan:
- Setiap temuan menunjuk satu blok lewat block_id persis seperti tertulis di [bXXXXX].
- quote WAJIB disalin persis (huruf demi huruf) dari teks blok itu, sependek mungkin tetapi unik. Jangan mengutip dari KONTEKS.
- description menjelaskan masalahnya; suggestion berisi teks pengganti atau langkah perbaikan.
- severity: high = salah makna/angka/fatal, medium = mengganggu pembaca, low = kosmetik.
- confidence 0..1: seberapa yakin ini benar-benar masalah.
- Angka ditulis dengan format Indonesia (1.234,5). Jangan menandai format Indonesia sebagai kesalahan.
- Tulis description dan suggestion dalam bahasa dokumen, ditujukan kepada penulis dokumen.
- Jika bagian ini bertentangan dengan KONTEKS, laporkan pada blok di bagian ini dan sebut nilai serta judul bagian
  lainnya. Jangan membahas aturan tugas ini, format prompt, atau ID blok di luar bagian ini dalam description.
- Jangan melaporkan hal yang bukan masalah. Tidak ada temuan = daftar kosong.
- facts: catat angka, tanggal, istilah kunci, dan nama penting di bagian ini (key = nama besaran yang ringkas,
  misalnya "jumlah responden"), dengan quote persis dari bloknya. Dipakai untuk cek konsistensi antarbab."""


SUPERVISOR_SYSTEM = """Anda supervisor review dokumen. Anda menilai temuan yang diajukan worker untuk satu bagian dokumen.

Untuk setiap temuan beri verdict:
- approve: masalahnya nyata, lokasinya tepat, saran masuk akal.
- reject: bukan masalah, salah paham konteks, atau terlalu remeh/subjektif.
- revise: masalahnya nyata tetapi deskripsi, tingkat, kategori, atau sarannya perlu diperbaiki — tulis di feedback apa yang harus diubah.

Nilai setiap temuan sesuai index-nya. Bersikap tegas: lebih baik menolak temuan meragukan daripada meloloskannya."""


def _block_line(b) -> str:
    where = ""
    if b.type == "table_cell" and b.table_index is not None:
        where = f"(tabel {b.table_index + 1}, baris {(b.row or 0) + 1}, kolom {(b.col or 0) + 1}) "
    return f"[{b.id}] {where}{b.text}"


def worker_user(doc_title: str, outline: list[str], section_path: str, blocks, context_hits,
                feedback: list[tuple[dict, str]] | None = None) -> str:
    parts = [f"DOKUMEN: {doc_title}", "", "OUTLINE DOKUMEN:", *outline[:200]]
    if len(outline) > 200:
        parts.append(f"… ({len(outline) - 200} bagian lagi)")
    parts += ["", "KONTEKS (potongan bagian lain dokumen — BUKAN bagian yang direview, hanya untuk cek konsistensi):"]
    if context_hits:
        for h in context_hits:
            parts += [f"--- {h.chunk.heading_path}", h.chunk.content.split("\n", 1)[-1]]
    else:
        parts.append("(tidak ada)")
    parts += ["", f"BAGIAN YANG DIREVIEW: {section_path}", *(_block_line(b) for b in blocks)]
    if feedback:
        parts += ["", "REVISI DIMINTA SUPERVISOR. Perbaiki temuan berikut sesuai feedback dan kirim ulang hanya "
                      "temuan yang masih Anda anggap benar (facts boleh kosong):"]
        for f, fb in feedback:
            parts.append(f"- temuan: {f}\n  feedback: {fb}")
    return "\n".join(parts)


def supervisor_user(section_path: str, blocks_by_id: dict, findings: list[dict]) -> str:
    parts = [f"BAGIAN: {section_path}", "", "TEMUAN:"]
    for i, f in enumerate(findings):
        b = blocks_by_id[f["block_id"]]
        parts += [f"[{i}] {f['category']}/{f['severity']} pada {f['block_id']}",
                  f"    teks blok: {b.text}", f"    kutipan: {f['quote']}",
                  f"    masalah: {f['description']}", f"    saran: {f['suggestion']}"]
    return "\n".join(parts)


# --------------------------------------------------------------------- editing (phase 3)
EDIT_ITEM = {
    "type": "object",
    "properties": {"block_id": {"type": "string"}, "new_text": {"type": "string"},
                   "source_ids": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}},
    "required": ["block_id", "new_text", "source_ids", "reason"],
}

EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "edits": {"type": "array", "items": EDIT_ITEM},
        "skipped": {"type": "array", "items": {"type": "object", "properties": {
            "source_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["source_id", "reason"]}},
    },
    "required": ["edits", "skipped"],
}

EDIT_RULES = """Aturan menyunting:
- Satu edit = satu blok. new_text adalah teks LENGKAP blok itu setelah diperbaiki (bukan potongan, tanpa [bXXXXX]).
- Ubah seminimal mungkin; pertahankan gaya, istilah, dan susunan kalimat yang tidak bermasalah.
- Jangan mengubah, menambah, atau menghapus angka, tanggal, nama, atau satuan kecuali dasar perubahannya memintanya
  secara tegas dan nilai yang benar jelas.
- Jangan pakai tanda markdown (*, **, _, #). Tulis teks biasa.
- source_ids berisi id dasar perubahan (temuan/instruksi) yang ditangani edit itu.
- Bila sebuah dasar tidak bisa/tidak perlu dikerjakan (nilai yang benar tidak jelas, butuh data dari luar, sudah benar),
  masukkan ke skipped dengan alasannya. Jangan memaksakan edit."""


def revision_worker_system(name: str) -> str:
    return f"""Anda {name}, penyunting dokumen. Anda menerima satu bagian dokumen dan daftar temuan review yang sudah
disetujui. Tugas Anda menulis perbaikan untuk blok-blok yang disebut temuan.

{EDIT_RULES}
- Untuk temuan konsistensi, ubah hanya bila nilai yang benar dapat dipastikan dari dokumen; jika tidak, skipped."""


def revision_worker_user(section_path: str, blocks, findings: list[dict],
                         feedback: list[tuple[dict, str]] | None = None) -> str:
    parts = [f"BAGIAN: {section_path}", "", "BLOK:", *(_block_line(b) for b in blocks), "", "TEMUAN:"]
    for f in findings:
        parts.append(f"- id={f['id']} [{f['block_id']}] {f['category']}: {f['description']}"
                     f" | kutipan: \"{f['quote']}\"" + (f" | saran: {f['suggestion']}" if f.get("suggestion") else ""))
    if feedback:
        parts += ["", "REVISI DIMINTA SUPERVISOR. Perbaiki edit berikut sesuai feedback dan kirim ulang:"]
        for e, fb in feedback:
            parts.append(f"- edit [{e['block_id']}]: {e['new_text']}\n  feedback: {fb}")
    return "\n".join(parts)


EDIT_SUPERVISOR_SYSTEM = """Anda supervisor penyuntingan dokumen. Nilai setiap usulan edit:
- approve: edit benar-benar menyelesaikan dasarnya, tidak mengubah makna, tidak mengubah fakta yang tidak diminta.
- reject: edit salah, mengubah makna, atau tidak perlu.
- revise: arahnya benar tetapi teksnya perlu diperbaiki — tulis di feedback apa yang harus diubah.
Bersikap tegas: dokumen akan berubah sesuai edit yang Anda setujui."""


def edit_supervisor_user(section_path: str, blocks_by_id: dict, edits: list[dict], basis: dict[str, str]) -> str:
    parts = [f"BAGIAN: {section_path}", "", "USULAN EDIT:"]
    for i, e in enumerate(edits):
        parts += [f"[{i}] blok {e['block_id']}",
                  f"    lama: {blocks_by_id[e['block_id']].text}", f"    baru: {e['new_text']}",
                  f"    alasan: {e['reason']}"]
        for sid in e["source_ids"]:
            if sid in basis:
                parts.append(f"    dasar {sid}: {basis[sid]}")
    return "\n".join(parts)


INSTRUCTED_SCHEMA = {
    "type": "object",
    "properties": {
        **EDIT_SCHEMA["properties"],
        "responses": {"type": "array", "items": {"type": "object", "properties": {
            "instruction_id": {"type": "string"}, "status": {"type": "string", "enum": ["done", "not_applicable"]},
            "response": {"type": "string"}}, "required": ["instruction_id", "status", "response"]}},
    },
    "required": ["edits", "skipped", "responses"],
}


def instructed_worker_system(name: str) -> str:
    return f"""Anda {name}, penyunting dokumen. Anda menerima satu bagian dokumen dan instruksi revisi dari atasan/klien.
Kerjakan instruksi yang menyangkut blok di bagian ini.

{EDIT_RULES}
- Bila instruksi meminta nilai baru (angka, nama, tanggal), pakai persis nilai dari instruksi.
- Untuk SETIAP instruksi yang Anda tangani, isi responses: status "done" bila Anda membuat edit untuknya,
  "not_applicable" bila tidak perlu/tidak bisa dikerjakan di bagian ini; response = tanggapan singkat untuk atasan
  (apa yang diubah, atau mengapa tidak).
- Instruksi bertanda "kandidat lokasi" belum pasti tempatnya: kerjakan hanya bila jelas menyangkut blok di bagian ini."""


def instructed_worker_user(section_path: str, blocks, instructions: list[dict],
                           feedback: list[tuple[dict, str]] | None = None) -> str:
    parts = [f"BAGIAN: {section_path}", "", "BLOK:", *(_block_line(b) for b in blocks), "", "INSTRUKSI:"]
    for i in instructions:
        where = (f"blok {', '.join('[' + b + ']' for b in i['block_ids'])}" if i["block_ids"]
                 else f"kandidat lokasi {', '.join('[' + b + ']' for b in i['candidate_block_ids'])}")
        who = f" dari {i['author']}" if i.get("author") else ""
        quote = f" | merujuk: \"{i['quote']}\"" if i.get("quote") else ""
        parts.append(f"- id={i['id']} ({i['source']}{who}; {where}): {i['text']}{quote}")
    if feedback:
        parts += ["", "REVISI DIMINTA SUPERVISOR. Perbaiki edit berikut sesuai feedback dan kirim ulang:"]
        for e, fb in feedback:
            parts.append(f"- edit [{e['block_id']}]: {e['new_text']}\n  feedback: {fb}")
    return "\n".join(parts)


UPDATE_SCHEMA = EDIT_SCHEMA


def update_worker_system(name: str) -> str:
    return f"""Anda {name}, penyusun laporan berkala. Anda menerima satu bagian laporan periode lalu dan DATA periode baru.
Perbarui blok yang bergantung periode (tanggal, bulan, angka, sel tabel, kalimat analisis/kesimpulan yang menyebut
angka atau tren) supaya sesuai data baru. Blok yang tidak bergantung periode (metodologi, profil, definisi) jangan diubah.

Aturan:
- Satu edit = satu blok; new_text = teks LENGKAP blok setelah diperbarui (tanpa [bXXXXX], tanpa markdown).
- Setiap angka di new_text harus diambil persis dari DATA, atau sudah ada di blok itu dan memang tidak berubah.
- Angka TURUNAN (persen kenaikan, selisih, total, porsi, rata-rata, konversi satuan seperti rupiah → juta) JANGAN
  dihitung sendiri. Tulis rumus: {{{{calc: (286000000 - 260000000) / 260000000 * 100 | pct:1}}}}. Di dalam rumus hanya
  angka dari DATA atau dari laporan lama, ditulis tanpa pemisah ribuan dengan titik sebagai desimal; operator + - * / ( ).
  Format: pct:1 (persen 1 desimal, tulis kata "persen"/"%" di luar rumus), num:0 (bilangan bulat), num:2, dst.
  Contoh konversi: "Rp {{{{calc: 286000000 / 1000000 | num:0}}}} juta".
- Sesuaikan kata arah (naik/turun, meningkat/menurun) dengan data.
- Bila data untuk sebuah angka TIDAK ADA, jangan menebak: masukkan blok itu ke skipped (source_id = id blok,
  reason = data apa yang kurang). Blok itu akan ditahan untuk diperiksa manusia.
- source_ids boleh ["data"]."""


def update_worker_user(section_path: str, period: str, blocks, data_text: str, notes: str,
                       feedback: list[tuple[dict, str]] | None = None) -> str:
    parts = [f"PERIODE BARU: {period}"]
    if notes.strip():
        parts += ["", f"CATATAN DARI PENGGUNA: {notes.strip()}"]
    parts += ["", "DATA PERIODE BARU:", data_text, "", f"BAGIAN: {section_path}", "",
              "BLOK (laporan periode lalu):", *(_block_line(b) for b in blocks)]
    if feedback:
        parts += ["", "REVISI DIMINTA SUPERVISOR. Perbaiki edit berikut sesuai feedback dan kirim ulang:"]
        for e, fb in feedback:
            parts.append(f"- edit [{e['block_id']}]: {e['new_text']}\n  feedback: {fb}")
    return "\n".join(parts)


def update_supervisor_context(period: str, data_text: str) -> str:
    return (f"Ini UPDATE LAPORAN BERKALA ke periode {period}. Setujui edit hanya bila angka/tanggal/arah tren sesuai DATA "
            f"berikut (angka hasil rumus sudah dihitung mesin dan benar secara aritmetika; nilai apakah rumusnya tepat).\n"
            f"DATA PERIODE BARU:\n{data_text}\n\n")
