# tests/test_app_routes.py
import os, importlib, types

def test_home_sets_cookie_and_shows_session(db, monkeypatch):
    # import after env/DB are ready
    app_module = importlib.import_module("app.app")
    app = app_module.app
    client = app.test_client()

    res = client.get("/")
    assert res.status_code == 200
    # cookie set
    assert "session_id=" in res.headers.get("Set-Cookie","")
    # session panel content present
    html = res.get_data(as_text=True)
    assert "Session" in html
    assert "Requirements Review" in html

def test_api_session_json(db):
    app_module = importlib.import_module("app.app")
    app = app_module.app
    client = app.test_client()

    res = client.get("/api/session")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert "session_id" in data
    assert "session" in data

def test_resume_button_calls_pipeline(db, monkeypatch):
    # mock subprocess.run so we don't actually run the pipeline
    called = {"ok": False, "cmd": None}
    def fake_run(cmd, check):
        called["ok"] = True
        called["cmd"] = cmd
        class R: pass
        return R()
    monkeypatch.setattr("subprocess.run", fake_run)

    # ensure TRANSCRIPT_FILE optional
    monkeypatch.delenv("TRANSCRIPT_FILE", raising=False)

    app_module = importlib.import_module("app.app")
    app = app_module.app
    client = app.test_client()

    # visit home to get a cookie sid
    client.get("/")
    res = client.post("/run", follow_redirects=False)
    assert res.status_code in (302, 303)
    assert called["ok"] is True
    # command should include run_pipeline.py and --session
    cmd = " ".join(called["cmd"])
    assert "run_pipeline.py" in cmd and "--session" in cmd