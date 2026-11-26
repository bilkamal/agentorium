def test_app_index_and_config(tmp_settings):
    from fastapi.testclient import TestClient

    from agentorium.server import app

    c = TestClient(app)
    assert c.get("/").status_code == 200
    assert "providers" in c.get("/api/config").json()
