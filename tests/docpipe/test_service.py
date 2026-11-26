import pytest

from agentorium.config import settings
from agentorium.docpipe.service import MAX_UPLOAD_BYTES, IngestError, ingest, restore, version_file
from agentorium.docpipe.store import get_store

from .fixtures import make_basic_docx, make_pdf, make_tracked_docx


@pytest.fixture
def store(tmp_settings):
    return get_store()


def test_ingest_new_document_creates_v1_ready(store, tmp_path):
    d, v = ingest(store, filename="Laporan Sep.docx", data=make_basic_docx(tmp_path).read_bytes(), label="bulanan")
    assert d.title == "Laporan Sep" and d.label == "bulanan"
    assert (v.version_no, v.status, v.source) == (1, "ready", "upload")
    assert version_file(store, v, "current").exists()
    assert [s.heading for s in store.get_sections(v.id)] == ["Bab 1 Pendahuluan", "1.1 Latar", "Bab 2 Metode"]
    assert all(b.uid for b in store.get_blocks(v.id))
    assert store.get_document(d.id).latest_version_id == v.id


def test_ingest_pdf(store, tmp_path):
    d, v = ingest(store, filename="doc.pdf", data=make_pdf(tmp_path).read_bytes())
    assert v.status == "ready" and v.file_path.endswith("v1.pdf")
    assert len(store.get_sections(v.id)) == 2


def test_ingest_second_upload_inherits_uids(store, tmp_path):
    data = make_basic_docx(tmp_path).read_bytes()
    d, v1 = ingest(store, filename="a.docx", data=data)
    _, v2 = ingest(store, filename="a.docx", data=data, document_id=d.id, note="edit Word")
    assert v2.version_no == 2 and v2.parent_version_id == v1.id and v2.note == "edit Word"
    assert [b.uid for b in store.get_blocks(v2.id)] == [b.uid for b in store.get_blocks(v1.id)]


def test_ingest_keeps_original_when_tracked(store, tmp_path):
    d, v = ingest(store, filename="t.docx", data=make_tracked_docx(tmp_path).read_bytes())
    assert "tracked_changes_accepted" in v.warnings
    original, current = version_file(store, v, "original"), version_file(store, v, "current")
    assert original != current and original.exists()
    assert "1.1 Latar Belakang" in [b.text for b in store.get_blocks(v.id)]


def test_original_variant_falls_back_to_current(store, tmp_path):
    _, v = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())
    assert version_file(store, v, "original") == version_file(store, v, "current")


def test_ingest_rejects_unknown_extension(store):
    with pytest.raises(IngestError):
        ingest(store, filename="a.txt", data=b"halo")
    assert store.list_documents() == []


def test_ingest_corrupt_file_leaves_no_version_or_file(store):
    with pytest.raises(IngestError):
        ingest(store, filename="x.docx", data=b"bukan docx")
    assert store.list_documents() == []
    docs_dir = settings.data_dir / "docs"
    assert not docs_dir.exists() or not any(docs_dir.rglob("*.*"))


def test_corrupt_new_version_keeps_existing_document(store, tmp_path):
    d, v1 = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())
    with pytest.raises(IngestError):
        ingest(store, filename="a.docx", data=b"rusak", document_id=d.id)
    assert [v.version_no for v in store.list_versions(d.id)] == [1]
    assert version_file(store, v1, "current").exists()


def test_ingest_too_large(store):
    with pytest.raises(IngestError):
        ingest(store, filename="a.docx", data=b"0" * (MAX_UPLOAD_BYTES + 1))


def test_ingest_unknown_document_id(store, tmp_path):
    with pytest.raises(IngestError):
        ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes(), document_id="nope")


def test_restore_creates_new_version_with_same_uids(store, tmp_path):
    d, v1 = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())
    _, v2 = ingest(store, filename="b.docx", data=make_tracked_docx(tmp_path).read_bytes(), document_id=d.id)
    v3 = restore(store, v1.id)
    assert (v3.version_no, v3.source, v3.parent_version_id, v3.status) == (3, "rollback", v1.id, "ready")
    assert v3.note == "Rollback ke v1"
    b1, b3 = store.get_blocks(v1.id), store.get_blocks(v3.id)
    assert [(b.text, b.uid) for b in b3] == [(b.text, b.uid) for b in b1]
    assert version_file(store, v3, "current").read_bytes() == version_file(store, v1, "current").read_bytes()
    assert store.get_document(d.id).latest_version_id == v3.id
