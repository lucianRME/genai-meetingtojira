# app/session_manager.py
from __future__ import annotations

import os
import time
import json
import sqlite3
import uuid
from typing import Any

# DB path (align with the rest of the app)
DB = os.getenv("REPO_DB_PATH", os.getenv("DB_PATH", "repo.db"))

SUMMARY_CHAR_LIMIT = int(os.getenv("SESSION_SUMMARY_LIMIT", "1800"))
RECENT_ACTIONS_LIMIT = int(os.getenv("RECENT_ACTIONS_LIMIT", "10"))

# --------------------------- DB helpers & schema ---------------------------

def _now() -> int:
    return int(time.time())

def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB, check_same_thread=False)
    con.row_factory = sqlite3.Row
    _ensure_tables(con)
    return con

def _have_col(con: sqlite3.Connection, table: str, col: str) -> bool:
    cur = con.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return any(r[1] == col for r in cur.fetchall())

def _ensure_tables(con: sqlite3.Connection) -> None:
    cur = con.cursor()
    # sessions snapshot (used by UI and pipeline)
    cur.execute("""
      CREATE TABLE IF NOT EXISTS sessions(
        session_id TEXT PRIMARY KEY,
        project_id TEXT,
        rolling_summary TEXT,
        last_actions_json TEXT,
        created_at INTEGER,
        updated_at INTEGER
      )
    """)
    # ensure columns exist on older DBs
    if not _have_col(con, "sessions", "rolling_summary"):
        cur.execute("ALTER TABLE sessions ADD COLUMN rolling_summary TEXT")
    if not _have_col(con, "sessions", "last_actions_json"):
        cur.execute("ALTER TABLE sessions ADD COLUMN last_actions_json TEXT")
    if not _have_col(con, "sessions", "created_at"):
        cur.execute("ALTER TABLE sessions ADD COLUMN created_at INTEGER")
    if not _have_col(con, "sessions", "updated_at"):
        cur.execute("ALTER TABLE sessions ADD COLUMN updated_at INTEGER")
    if not _have_col(con, "sessions", "project_id"):
        cur.execute("ALTER TABLE sessions ADD COLUMN project_id TEXT")

    # rolling action log for UI
    cur.execute("""
      CREATE TABLE IF NOT EXISTS session_actions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        ts INTEGER NOT NULL,
        action_type TEXT NOT NULL,
        payload TEXT
      )
    """)

    # small key/value store per session (UI state, etc.)
    cur.execute("""
      CREATE TABLE IF NOT EXISTS session_state(
        session_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT,
        PRIMARY KEY(session_id, key)
      )
    """)

    con.commit()

# --------------------------- Public API -----------------------------------

def get_or_create_session(response=None) -> tuple[str, Any]:
    """
    Flask-friendly helper (kept for back-compat in case you use it in views).
    """
    from flask import request  # imported lazily to avoid hard dependency
    sid = request.cookies.get("synapse_sid")
    if not sid:
        sid = str(uuid.uuid4())
        _ensure_session_row(sid)
        if response is None:
            # caller must set cookie later
            return sid, None
        response.set_cookie("synapse_sid", sid, httponly=True, samesite="Lax")
        return sid, response
    _ensure_session_row(sid)
    return sid, response

def _ensure_session_row(session_id: str, project_id: str | None = None) -> None:
    con = _db(); cur = con.cursor()
    cur.execute("SELECT session_id FROM sessions WHERE session_id=?", (session_id,))
    row = cur.fetchone()
    now = _now()
    if not row:
        cur.execute(
            "INSERT INTO sessions(session_id, project_id, rolling_summary, last_actions_json, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (session_id, project_id, "", "[]", now, now)
        )
    else:
        cur.execute("UPDATE sessions SET updated_at=? WHERE session_id=?", (now, session_id))
    con.commit(); con.close()

def log_action(session_id: str, action_type: str, payload: dict | None = None) -> None:
    """
    Append a single action row and bump sessions.updated_at.
    """
    con = _db(); cur = con.cursor()
    cur.execute(
        "INSERT INTO session_actions(session_id, ts, action_type, payload) VALUES(?,?,?,?)",
        (session_id, _now(), action_type, json.dumps(payload or {}))
    )
    cur.execute("UPDATE sessions SET updated_at=? WHERE session_id=?", (_now(), session_id))
    con.commit(); con.close()

def set_state(session_id: str, key: str, value: Any) -> None:
    con = _db(); cur = con.cursor()
    cur.execute(
        "INSERT INTO session_state(session_id, key, value) VALUES(?,?,?) "
        "ON CONFLICT(session_id, key) DO UPDATE SET value=excluded.value",
        (session_id, key, json.dumps(value) if not isinstance(value, str) else value)
    )
    con.commit(); con.close()

def get_state(session_id: str, key: str, default=None):
    con = _db(); cur = con.cursor()
    cur.execute("SELECT value FROM session_state WHERE session_id=? AND key=?", (session_id, key))
    row = cur.fetchone()
    con.close()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return row["value"]

def get_recent_actions(session_id: str, limit: int = RECENT_ACTIONS_LIMIT) -> list[dict]:
    con = _db(); cur = con.cursor()
    cur.execute(
        "SELECT ts, action_type, payload FROM session_actions "
        "WHERE session_id=? ORDER BY ts DESC LIMIT ?",
        (session_id, limit)
    )
    rows = cur.fetchall(); con.close()
    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload"] or "{}")
        except Exception:
            payload = {}
        out.append({"ts": r["ts"], "action_type": r["action_type"], "payload": payload})
    return out

def get_summary(session_id: str) -> str:
    """
    Return the compact rolling summary stored on `sessions.rolling_summary`.
    (Older code looked for a `summary` column; we standardize on `rolling_summary`.)
    """
    if not session_id:
        return ""
    con = _db(); cur = con.cursor()
    cur.execute("SELECT rolling_summary FROM sessions WHERE session_id=?", (session_id,))
    row = cur.fetchone(); con.close()
    return (row["rolling_summary"] or "") if row else ""

def update_summary(session_id: str, new_bullet: str) -> None:
    """
    Keeps a compact rolling summary under SUMMARY_CHAR_LIMIT.
    Strategy:
      - Prepend most-recent bullet.
      - Truncate older tail if needed.
    """
    new_bullet = (new_bullet or "").strip()
    if not new_bullet:
        return
    current = get_summary(session_id)
    merged = f"• {new_bullet}\n" + (current or "")
    compact = merged[:SUMMARY_CHAR_LIMIT]
    con = _db(); cur = con.cursor()
    cur.execute(
        "UPDATE sessions SET rolling_summary=?, updated_at=? WHERE session_id=?",
        (compact, _now(), session_id)
    )
    con.commit(); con.close()