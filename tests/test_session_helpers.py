# tests/test_session_helpers.py
import sqlite3, json, os
from run_pipeline import (
    get_conn, ensure_session, append_action, session_set, session_get,
    get_session_snapshot, get_compact_context
)

def test_session_creation_and_actions(db):
    conn = get_conn()
    sid = ensure_session(conn, "primark", None)
    # append a few actions
    append_action(conn, sid, {"actor":"pipeline","action":"start","mode":"agentic"})
    append_action(conn, sid, {"actor":"pipeline","action":"step","step":"requirements"})
    snap = get_session_snapshot(conn, sid)
    assert snap["session_id"] == sid
    assert "Recent actions:" in snap["rolling_summary"]
    assert len(snap["last_actions"]) == 2

def test_session_kv_and_compact_context(db):
    conn = get_conn()
    sid = ensure_session(conn, "primark", None)
    session_set(conn, sid, "last_transcript_summary", "Users reset password via OTP; expiry 10m.")
    # compact context should include rolling summary (may be empty) + transcript
    ctx = get_compact_context(conn, sid, max_chars=2000)
    assert "Users reset password" in ctx
    # KV get
    val = session_get(conn, sid, "last_transcript_summary", "")
    assert "expiry 10m" in val