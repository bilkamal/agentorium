"""A realistic monthly report with known, deliberately planted errors — used to measure review quality."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from docx import Document


@dataclass
class SeededError:
    section_heading: str
    block_text_contains: str
    category: str
    alt_contains: str | None = None  # consistency errors: the other side of the conflict


S1, S2, S3, S4, S5, S6 = ("1. Pendahuluan", "2. Pelaksanaan Kegiatan", "3. Capaian Penjualan", "4. Kepuasan Pelanggan",
                          "5. Analisis Risiko", "6. Kesimpulan dan Rekomendasi")

SECTIONS: list[tuple[str, list[str]]] = [
    (S1, [
        "Laporan ini menganalisa kinerja Koperasi Sejahtera Mandiri selama bulan Oktober 2026.",
        "Data diperoleh dari catatan kasir, laporan gudang, dan survei terhadap 120 responden anggota.",
        "Penyusunan laporan mengikuti praktek pelaporan yang berlaku di koperasi sejak tahun 2020.",
        "Seluruh kegiatan dikoordinasikan oleh Unit Pelayanan Anggota.",
    ]),
    (S2, [
        "Rapat evaluasi bulanan dilaksanakan pada tanggal 12 Oktober 2026 di aula koperasi.",
        "Aktifitas pelatihan kasir diikuti oleh 18 karyawan dan berlangsung selama dua hari.",
        "Petugas gudang memperbaharui sistim pencatatan stok menggunakan aplikasi baru.",
        "Anggaran kegiatan bulan ini sebesar Rp 450 juta telah disetujui pengurus.",
        "Demikian laporan ini kami sampaikan, atas perhatiannya kami ucapkan terima kasih.",
        "Koordinasi harian kini dipegang oleh Unit Layanan Anggota yang baru dibentuk.",
    ]),
    (S3, [
        "Penjualan bulan Oktober naik dari Rp 200 juta menjadi Rp 260 juta, atau naik 20 persen.",
        "Kwalitas barang yang dijual tetap terjaga karena pemasok tidak berganti.",
        "Kenaikan penjualan membuktikan bahwa seluruh anggota pasti puas dengan semua layanan koperasi.",
        "Realisasi belanja kegiatan mencapai Rp 540 juta sesuai anggaran yang disetujui.",
        "Frekwensi kunjungan anggota meningkat terutama pada akhir pekan.",
    ]),
    (S4, [
        "Survei kepuasan diikuti oleh 125 responden anggota yang dipilih secara acak.",
        "Sebanyak 82 persen responden menyatakan puas terhadap pelayanan kasir.",
        "Beberapa anggota memberi nasehat agar jam buka diperpanjang hingga pukul 21.00.",
        "Hasil survei dibahas dalam rapat evaluasi tanggal 21 Oktober 2026.",
        "Sebagian responden mengeluhkan jadual pengiriman barang yang sering terlambat.",
    ]),
    (S5, []),
    (S6, [
        "Resiko utama bulan depan adalah kenaikan harga beras dari pemasok.",
        "Pengurus perlu merubah jadwal piket kasir agar antrean akhir pekan berkurang.",
        "Ijin perpanjangan jam operasional perlu diajukan ke dinas terkait.",
        "Karena survei menunjukkan 82 persen puas, maka koperasi tidak perlu melakukan perbaikan apa pun.",
    ]),
]

SEEDED: list[SeededError] = [
    SeededError(S1, "menganalisa kinerja", "language"),
    SeededError(S1, "praktek pelaporan", "language"),
    SeededError(S2, "Aktifitas pelatihan", "language"),
    SeededError(S2, "memperbaharui sistim", "language"),
    SeededError(S3, "Kwalitas barang", "language"),
    SeededError(S3, "Frekwensi kunjungan", "language"),
    SeededError(S4, "memberi nasehat", "language"),
    SeededError(S6, "Resiko utama", "language"),
    SeededError(S6, "perlu merubah", "language"),
    SeededError(S6, "Ijin perpanjangan", "language"),
    SeededError(S1, "survei terhadap 120 responden", "consistency", alt_contains="diikuti oleh 125 responden"),
    SeededError(S2, "sebesar Rp 450 juta", "consistency", alt_contains="mencapai Rp 540 juta"),
    SeededError(S1, "Unit Pelayanan Anggota", "consistency", alt_contains="Unit Layanan Anggota"),
    SeededError(S2, "tanggal 12 Oktober 2026", "consistency", alt_contains="tanggal 21 Oktober 2026"),
    SeededError(S2, "Demikian laporan ini kami sampaikan", "structure"),
    SeededError(S5, S5, "structure"),
    SeededError(S3, "atau naik 20 persen", "substance"),
    SeededError(S3, "seluruh anggota pasti puas", "substance"),
    SeededError(S6, "tidak perlu melakukan perbaikan", "substance"),
    SeededError(S4, "jadual pengiriman", "language"),
]


def make_eval_docx(path: Path) -> list[SeededError]:
    d = Document()
    d.add_heading("Laporan Bulanan Koperasi Sejahtera Mandiri — Oktober 2026", 0)
    for heading, paragraphs in SECTIONS:
        d.add_heading(heading, 1)
        for p in paragraphs:
            d.add_paragraph(p)
    d.save(str(path))
    return list(SEEDED)
