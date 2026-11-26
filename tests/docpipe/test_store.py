import pytest

from agentorium.docpipe.models import Block, ParsedDoc, Section
from agentorium.docpipe.store import Store, get_store


@pytest.fixture
def store(tmp_settings):
    return get_store()


def parsed(texts_by_section):
    sections, blocks, n = [], [], 0
    for i, (heading, texts) in enumerate(texts_by_section.items()):
        sid = f"s{i:04d}"
        sections.append(Section(id=sid, ordinal=i, level=1, heading=heading, path=heading, parent_id=None))
        for t in texts:
            blocks.append(Block(id=f"b{n:05d}", uid=f"u{n}", ordinal=n, section_id=sid, type="paragraph", text=t))
            n += 1
    return ParsedDoc(sections=sections, blocks=blocks, warnings=[])


def test_versions_numbered_sequentially(store):
    d = store.create_document("Laporan", "bulanan")
    v1 = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=None)
    v2 = store.add_version(d.id, source="upload", filename="b.docx", parent_version_id=v1.id)
    assert (v1.version_no, v2.version_no) == (1, 2)
    assert v2.file_path == f"docs/{d.id}/v2.docx"
    assert v1.status == "processing"
    assert [v.version_no for v in store.list_versions(d.id)] == [1, 2]


def test_save_structure_roundtrip_and_order(store):
    d = store.create_document("L")
    v = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=None)
    store.save_structure(v.id, parsed({"Bab 1": ["a", "b"], "Bab 2": ["c"]}))
    assert [s.heading for s in store.get_sections(v.id)] == ["Bab 1", "Bab 2"]
    assert [b.text for b in store.get_blocks(v.id)] == ["a", "b", "c"]
    assert [b.text for b in store.get_blocks(v.id, section_id="s0001")] == ["c"]
    assert store.get_blocks(v.id)[0].uid == "u0"


def test_save_structure_replaces_previous(store):
    d = store.create_document("L")
    v = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=None)
    store.save_structure(v.id, parsed({"A": ["x", "y"]}))
    store.save_structure(v.id, parsed({"B": ["z"]}))
    assert [b.text for b in store.get_blocks(v.id)] == ["z"]
    assert [s.heading for s in store.get_sections(v.id)] == ["B"]


def test_latest_ready_version_skips_failed(store):
    d = store.create_document("L")
    v1 = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=None)
    store.update_version(v1.id, status="ready")
    v2 = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=v1.id)
    store.update_version(v2.id, status="failed", error="rusak")
    assert store.latest_ready_version(d.id).id == v1.id
    assert store.get_document(d.id).latest_version_id == v1.id


def test_delete_document_cascades(store):
    d = store.create_document("L")
    v = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=None)
    store.save_structure(v.id, parsed({"A": ["x"]}))
    assert store.delete_document(d.id) is True
    assert store.get_document(d.id) is None
    assert store.get_version(v.id) is None
    assert store.get_blocks(v.id) == []
    assert store.delete_document(d.id) is False


def test_warnings_roundtrip_as_list(store):
    d = store.create_document("L")
    v = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=None)
    v = store.update_version(v.id, warnings=["tracked_changes_accepted"])
    assert store.get_version(v.id).warnings == ["tracked_changes_accepted"]


def test_delete_version(store):
    d = store.create_document("L")
    v = store.add_version(d.id, source="upload", filename="a.docx", parent_version_id=None)
    store.delete_version(v.id)
    assert store.list_versions(d.id) == []


def test_get_store_follows_data_dir(tmp_settings, tmp_path):
    a = get_store()
    assert a is get_store()
    object.__setattr__(tmp_settings, "data_dir", tmp_path / "other")
    assert get_store() is not a
    assert isinstance(get_store(), Store)


def test_reads_are_consistent_while_another_thread_writes(tmp_settings, tmp_path):
    import threading

    from agentorium.docpipe.index import index_version
    from agentorium.docpipe.service import ingest
    from agentorium.docpipe.store import get_store

    from .fixtures import make_basic_docx

    store = get_store()
    _, v = ingest(store, filename="a.docx", data=make_basic_docx(tmp_path).read_bytes())
    stop = threading.Event()

    def writer():
        while not stop.is_set():
            index_version(store, v.id)

    t = threading.Thread(target=writer)
    t.start()
    bad = []
    try:
        for _ in range(3000):
            try:
                got = store.get_version(v.id)
                if got is None or got.status != "ready":
                    bad.append(got)
            except Exception as exc:  # interleaved cursors can also raise
                bad.append(exc)
    finally:
        stop.set()
        t.join()
    assert bad == []
