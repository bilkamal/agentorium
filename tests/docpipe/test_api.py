import pytest
from fastapi.testclient import TestClient

from agentorium.config import settings

from .fixtures import make_basic_docx, make_tracked_docx

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def client(tmp_settings):
    from agentorium.server import app

    return TestClient(app)


def upload(client, path, **form):
    return client.post("/api/docs", files={"file": (path.name, path.read_bytes(), DOCX)}, data=form)


def test_upload_list_get_outline_blocks_download(client, tmp_path):
    r = upload(client, make_basic_docx(tmp_path), title="Laporan Sep", label="bulanan")
    assert r.status_code == 201, r.text
    doc, ver = r.json()["document"], r.json()["version"]
    assert doc["title"] == "Laporan Sep" and ver["version_no"] == 1 and ver["block_count"] == 11

    listing = client.get("/api/docs").json()
    assert [(d["id"], d["version_count"], d["latest_version"]["id"]) for d in listing] == [(doc["id"], 1, ver["id"])]

    detail = client.get(f"/api/docs/{doc['id']}").json()
    assert [v["version_no"] for v in detail["versions"]] == [1]

    outline = client.get(f"/api/docs/versions/{ver['id']}/outline").json()
    assert [(s["heading"], s["block_count"]) for s in outline] == [("Bab 1 Pendahuluan", 2), ("1.1 Latar", 6), ("Bab 2 Metode", 3)]

    blocks = client.get(f"/api/docs/versions/{ver['id']}/blocks", params={"section_id": outline[1]["id"]}).json()
    assert blocks[0]["text"] == "1.1 Latar" and blocks[1]["type"] == "table_cell"

    dl = client.get(f"/api/docs/versions/{ver['id']}/download")
    assert dl.status_code == 200 and dl.content[:2] == b"PK"
    from urllib.parse import unquote

    assert "Laporan Sep v1.docx" in unquote(dl.headers["content-disposition"])


def test_upload_new_version_and_restore(client, tmp_path):
    doc = upload(client, make_basic_docx(tmp_path)).json()["document"]
    v1 = client.get(f"/api/docs/{doc['id']}").json()["versions"][0]
    p = make_tracked_docx(tmp_path)
    r = client.post(f"/api/docs/{doc['id']}/versions", files={"file": (p.name, p.read_bytes(), DOCX)}, data={"note": "dari Word"})
    assert r.status_code == 201 and r.json()["version"]["note"] == "dari Word"
    r = client.post(f"/api/docs/versions/{v1['id']}/restore")
    assert r.status_code == 201 and r.json()["version"]["source"] == "rollback"
    versions = client.get(f"/api/docs/{doc['id']}").json()["versions"]
    assert [v["version_no"] for v in versions] == [1, 2, 3]


def test_corrupt_docx_returns_422_and_leaves_nothing(client):
    r = client.post("/api/docs", files={"file": ("x.docx", b"bukan docx", "application/octet-stream")})
    assert r.status_code == 422
    assert r.json()["detail"]
    assert client.get("/api/docs").json() == []


def test_unknown_extension_422(client):
    r = client.post("/api/docs", files={"file": ("x.txt", b"halo", "text/plain")})
    assert r.status_code == 422


def test_too_large_413(client, monkeypatch):
    import agentorium.docpipe.api as api

    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 10)
    r = client.post("/api/docs", files={"file": ("x.docx", b"0" * 11, DOCX)})
    assert r.status_code == 413


def test_download_original_variant(client, tmp_path):
    ver = upload(client, make_tracked_docx(tmp_path)).json()["version"]
    assert "tracked_changes_accepted" in ver["warnings"]
    cur = client.get(f"/api/docs/versions/{ver['id']}/download").content
    orig = client.get(f"/api/docs/versions/{ver['id']}/download", params={"variant": "original"}).content
    assert cur != orig


def test_delete_document_removes_files(client, tmp_path):
    doc = upload(client, make_basic_docx(tmp_path)).json()["document"]
    assert client.delete(f"/api/docs/{doc['id']}").json() == {"ok": True}
    assert not (settings.data_dir / "docs" / doc["id"]).exists()
    assert client.get(f"/api/docs/{doc['id']}").status_code == 404


def test_404s(client):
    assert client.get("/api/docs/nope").status_code == 404
    assert client.delete("/api/docs/nope").status_code == 404
    assert client.get("/api/docs/versions/nope/outline").status_code == 404
    assert client.get("/api/docs/versions/nope/blocks").status_code == 404
    assert client.get("/api/docs/versions/nope/download").status_code == 404
    assert client.post("/api/docs/versions/nope/restore").status_code == 404
    assert client.post("/api/docs/nope/versions", files={"file": ("a.docx", b"x", DOCX)}).status_code == 404


def test_old_agentorium_endpoints_still_work(client):
    assert client.post("/api/sessions", json={}).status_code == 201
