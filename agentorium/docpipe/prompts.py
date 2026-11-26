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
