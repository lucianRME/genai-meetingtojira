# tests/test_run_pipeline.py
import run_pipeline as rp

def test_run_agentic(sample_vtt, db_conn, stub_chat, monkeypatch):
    # force agentic
    monkeypatch.setenv("PIPELINE_MODE","agentic")
    sid = rp.ensure_session(db_conn, "myproject", "unit-sid")
    res = rp.run_agentic(sample_vtt, "myproject", sid, db_conn)
    assert res.get("output_json") or res.get("requirements") is not None
    snap = rp.get_session_snapshot(db_conn, sid)
    assert "rolling_summary" in snap

def test_run_classic(sample_vtt, db_conn, stub_chat):
    sid = rp.ensure_session(db_conn, "myproject", "unit-sid-2")
    res = rp.run_classic(sample_vtt, "myproject", sid, db_conn)
    assert res["db_path"].endswith(".db") or "repo.db" in res["db_path"]
