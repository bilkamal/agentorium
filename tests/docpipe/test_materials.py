import asyncio
import io

import docx
import pytest

from agentorium.docpipe.index import index_version
from agentorium.docpipe.materials import extract_instructions, locate, material_text, read_comments
from agentorium.docpipe.parse import parse_docx
from agentorium.docpipe.service import ingest
from agentorium.docpipe.store import get_store

from .fixtures import make_basic_docx, make_pdf


def commented_docx(tmp_path) -> bytes:
    from adeu import ModifyText, RedlineEngine

    src = make_basic_docx(tmp_path).read_bytes()
    e = RedlineEngine(io.BytesIO(src), author="Atasan")
    e.process_batch([ModifyText(target_text="sistem kasir", new_text="sistem kasir",
                                comment="Sebutkan nama aplikasinya")], partial=True)
    return e.save_to_stream().getvalue()


def test_read_word_comments_with_anchor_block(tmp_path):
    data = commented_docx(tmp_path)
    p = tmp_path / "c.docx"
    p.write_bytes(data)
    blocks = parse_docx(p).blocks
    comments = read_comments(data, blocks)
    assert len(comments) == 1
    c = comments[0]
    assert c["author"] == "Atasan" and c["text"] == "Sebutkan nama aplikasinya" and c["anchor"] == "sistem kasir"
    assert next(b for b in blocks if b.id == c["block_id"]).text == "Data dikumpulkan dari sistem kasir."


def test_read_comments_none(tmp_path):
    p = make_basic_docx(tmp_path)
    assert read_comments(p.read_bytes(), parse_docx(p).blocks) == []


def test_material_text_formats(tmp_path):
    assert material_text("catatan.txt", "Perbaiki judul.\nTambah sumber.".encode()) == "Perbaiki judul.\nTambah sumber."
    assert "Perbaiki" in material_text("c.md", "# Catatan\n- Perbaiki".encode())
    d = docx.Document()
    d.add_paragraph("Ganti istilah omzet menjadi pendapatan.")
    b = io.BytesIO()
    d.save(b)
    assert "omzet" in material_text("bahan.docx", b.getvalue())
    assert "Halaman pertama" in material_text("bahan.pdf", make_pdf(tmp_path).read_bytes())
    assert "Sebutkan nama aplikasinya" in material_text("dok.docx", commented_docx(tmp_path))
    with pytest.raises(ValueError):
        material_text("x.exe", b"MZ")


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    async def complete_json(self, system, messages, schema, *, purpose=""):
        self.calls.append(messages[-1]["content"])
        return self.reply


def test_extract_instructions_with_fake_llm():
    llm = FakeLLM({"instructions": [{"text": "Ganti 'menganalisa' jadi baku", "quote": "menganalisa"},
                                    {"text": " ", "quote": ""}, {"text": "Tambah kesimpulan", "quote": ""}]})
    out = asyncio.run(extract_instructions("1. ganti menganalisa\n2. tambah kesimpulan", llm))
    assert out == [{"text": "Ganti 'menganalisa' jadi baku", "quote": "menganalisa"},
                   {"text": "Tambah kesimpulan", "quote": ""}]
    assert "ganti menganalisa" in llm.calls[0]
    assert asyncio.run(extract_instructions("   ", llm)) == []


def test_locate_by_quote_then_retrieval(tmp_settings, tmp_path, fake_embedder):
    store = get_store()
    _, v = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())
    index_version(store, v.id)
    blocks = {b.text: b for b in store.get_blocks(v.id)}
    exact = locate(store, v.id, {"text": "baku-kan", "quote": "telah  menganalisa"})
    assert exact == {"block_ids": [blocks["Kami telah menganalisa data penjualan bulan September."].id],
                     "candidate_block_ids": []}
    guess = locate(store, v.id, {"text": "jelaskan metode pengumpulan data kasir", "quote": "tidak ada kutipan ini"})
    assert guess["block_ids"] == [] and blocks["Data dikumpulkan dari sistem kasir."].id in guess["candidate_block_ids"]
    assert len(guess["candidate_block_ids"]) <= 12
    none = locate(store, v.id, {"text": "zzzz qqqq", "quote": ""})
    assert none == {"block_ids": [], "candidate_block_ids": []} or none["block_ids"] == []


def test_instructions_store_roundtrip(tmp_settings):
    store = get_store()
    d = store.create_document("x")
    v = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=None)
    job = store.create_job(d.id, v.id, "instructed_revision", {})
    store.add_instructions([dict(job_id=job.id, source="comment", author="Atasan", text="t", quote="q",
                                 block_ids=["b1"], candidate_block_ids=[], status="pending", response="")])
    i = store.get_instructions(job.id)[0]
    assert i.block_ids == ["b1"] and i.ordinal == 0
    store.update_instruction(i.id, status="done", response="sudah")
    assert store.get_instructions(job.id)[0].status == "done"
