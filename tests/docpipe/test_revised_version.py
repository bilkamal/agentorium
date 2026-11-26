import pytest
from fastapi.testclient import TestClient

from agentorium.docpipe.edits import BlockEdit, apply_block_edits
from agentorium.docpipe.service import create_revised_version, ingest, restore, version_file
from agentorium.docpipe.store import get_store

from .fixtures import make_basic_docx

OLD = "Kami telah menganalisa data penjualan bulan September."
NEW = "Kami telah menganalisis data penjualan bulan September."


@pytest.fixture
def base(tmp_settings, tmp_path):
    store = get_store()
    doc, v = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())
    return store, doc, v


def revise(store, v):
    tracked, clean, _ = apply_block_edits(version_file(store, v).read_bytes(),
                                          [BlockEdit("b", OLD, NEW, "ejaan")])
    return create_revised_version(store, v.id, tracked, clean, source="revision", note=f"Revisi dari v{v.version_no}")


def test_revised_version_files_and_parent(base):
    store, doc, v = base
    nv = revise(store, v)
    assert nv.version_no == 2 and nv.parent_version_id == v.id and nv.source == "revision" and nv.status == "ready"
    assert nv.note == "Revisi dari v1" and nv.tracked_path
    assert NEW in [b.text for b in store.get_blocks(nv.id)]
    assert b"w:ins" in __import__("zipfile").ZipFile(version_file(store, nv, "tracked")).read("word/document.xml")
    assert store.get_document(doc.id).latest_version_id == nv.id


def test_uids_inherited_by_position(base):
    store, _, v = base
    nv = revise(store, v)
    assert [b.uid for b in store.get_blocks(nv.id)] == [b.uid for b in store.get_blocks(v.id)]


def test_revision_of_older_version_gets_next_number(base):
    store, _, v1 = base
    v2 = restore(store, v1.id)
    v3 = revise(store, v1)
    assert v3.version_no == 3 and v3.parent_version_id == v1.id
    assert [b.text for b in store.get_blocks(v2.id)] == [b.text for b in store.get_blocks(v1.id)]


def test_download_tracked_variant(base):
    from agentorium.server import app

    store, _, v = base
    nv = revise(store, v)
    c = TestClient(app)
    r = c.get(f"/api/docs/versions/{nv.id}/download", params={"variant": "tracked"})
    assert r.status_code == 200 and "tracked" in r.headers["content-disposition"].lower()
    assert c.get(f"/api/docs/versions/{v.id}/download", params={"variant": "tracked"}).status_code == 404


def test_changes_roundtrip(base):
    store, _, v = base
    nv = revise(store, v)
    b = store.get_blocks(nv.id)[1]
    store.add_changes([dict(version_id=nv.id, job_id=None, block_uid=b.uid, block_id=b.id, old_text=OLD, new_text=NEW,
                            reason="ejaan", source_kind="finding", source_ids=["f1"], status="applied", note=None)])
    ch = store.get_changes(version_id=nv.id)
    assert len(ch) == 1 and ch[0].source_ids == ["f1"] and ch[0].status == "applied"
    assert store.get_changes(job_id="none") == []
