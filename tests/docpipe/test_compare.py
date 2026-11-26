import io
import zipfile

import docx
import pytest

from agentorium.docpipe.compare import block_diff, redline_docx
from agentorium.docpipe.service import ingest
from agentorium.docpipe.store import get_store

from .fixtures import make_basic_docx, make_pdf


def edited_copy(tmp_path) -> bytes:
    d = docx.Document(str(make_basic_docx(tmp_path)))
    for p in d.paragraphs:
        if p.text.startswith("Kami telah"):
            for r in p.runs:
                r.text = r.text.replace("menganalisa", "menganalisis")
        if p.text == "Wawancara pemilik toko":
            p._element.getparent().remove(p._element)
    d.add_paragraph("Data dianalisis dengan statistik deskriptif.")
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


@pytest.fixture
def two(tmp_settings, tmp_path):
    store = get_store()
    doc, v1 = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())
    _, v2 = ingest(store, filename="a.docx", data=edited_copy(tmp_path), document_id=doc.id)
    return store, v1, v2


def test_block_diff_changed_added_removed(two):
    store, v1, v2 = two
    diff = block_diff(store, v1.id, v2.id)
    by = {d["status"]: [] for d in diff}
    for d in diff:
        by[d["status"]].append(d)
    assert set(by) == {"changed", "added", "removed"}
    (ch,) = by["changed"]
    assert ch["old_text"] == "Kami telah menganalisa data penjualan bulan September."
    assert ch["new_text"] == "Kami telah menganalisis data penjualan bulan September."
    assert ch["section"].startswith("Bab 1")
    assert [d["new_text"] for d in by["added"]] == ["Data dianalisis dengan statistik deskriptif."]
    assert [d["old_text"] for d in by["removed"]] == ["Wawancara pemilik toko"]
    assert block_diff(store, v1.id, v1.id) == []


def test_block_diff_other_document_rejected(two, tmp_path):
    store, v1, _ = two
    _, other = ingest(store, filename="b.docx", data=make_basic_docx(tmp_path, "b.docx").read_bytes())
    with pytest.raises(ValueError, match="dokumen"):
        block_diff(store, v1.id, other.id)


def test_redline_docx_contains_tracked_changes(two):
    store, v1, v2 = two
    xml = zipfile.ZipFile(io.BytesIO(redline_docx(store, v1.id, v2.id))).read("word/document.xml").decode()
    assert "<w:ins" in xml and "<w:del" in xml and "menganalisis" in xml


def test_redline_needs_docx(two, tmp_path):
    store, v1, _ = two
    _, pv = ingest(store, filename="a.pdf", data=make_pdf(tmp_path).read_bytes(), document_id=v1.document_id)
    with pytest.raises(ValueError, match="DOCX"):
        redline_docx(store, v1.id, pv.id)


def test_compare_api(two):
    from fastapi.testclient import TestClient

    import agentorium.server as server

    store, v1, v2 = two
    with TestClient(server.app) as c:
        r = c.get(f"/api/docs/versions/{v2.id}/compare?base={v1.id}")
        assert r.status_code == 200 and {d["status"] for d in r.json()["blocks"]} == {"changed", "added", "removed"}
        r = c.get(f"/api/docs/versions/{v2.id}/compare.docx?base={v1.id}")
        assert r.status_code == 200 and r.content[:2] == b"PK" and "banding" in r.headers["content-disposition"]
        assert c.get(f"/api/docs/versions/{v2.id}/compare?base=nope").status_code == 404
