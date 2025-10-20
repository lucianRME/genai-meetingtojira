# tests/test_app.py
import app.app as web

def test_session_start_endpoint(monkeypatch):
    app = web.app
    client = app.test_client()
    r = client.post("/api/session/start")
    assert r.status_code == 200
    assert r.json["ok"] is True
    assert "session_id" in r.json
    # cookie set
    assert "session_id=" in r.headers.get("Set-Cookie","")

def test_home_renders(monkeypatch):
    app = web.app
    client = app.test_client()
    r = client.get("/")
    assert r.status_code == 200
    assert b"Requirements Review" in r.data

def test_heartbeat_logs_action(monkeypatch):
    app = web.app
    client = app.test_client()
    client.post("/api/session/start")
    r = client.post("/api/session/heartbeat", json={"page":"/"})
    assert r.status_code == 200