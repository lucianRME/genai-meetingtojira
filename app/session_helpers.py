# session_helpers.py  (drop this EXACT file into whichever module your tests import)
from __future__ import annotations

import os
import json
import sqlite3
import uuid
import datetime
from typing import Dict, Any, List

DB_PATH = os.getenv("REPO_DB_PATH", os.getenv("DB_PATH", "repo.db"))
HEADER = "Recent actions:"

# ---------------- connection & schema ----------------
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    return conn

def _ensure_tables(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("""
      CREATE TABLE IF NOT EXISTS sessions(
        session_id TEXT PRIMARY KEY,
        project_id TEXT,
        rolling_summary TEXT,
        last_actions_json TEXT,
        created_at TEXT,
        updated_at TEXT
      )
    """)
    cols = {r[1] for r in cur.execute("PRAGMA table_info(sessions)").fetchall()}
    if "project_id" not in cols:
        cur.execute("ALTER TABLE sessions ADD COLUMN project_id TEXT")
    if "rolling_summary" not in cols:
        cur.execute("ALTER TABLE sessions ADD COLUMN rolling_summary TEXT")
    if "last_actions_json" not in cols:
        cur.execute("ALTER TABLE sessions ADD COLUMN last_actions_json TEXT")
    if "created_at" not in cols:
        cur.execute("ALTER TABLE sessions ADD COLUMN created_at TEXT")
    if "updated_at" not in cols:
        cur.execute("ALTER TABLE sessions ADD COLUMN updated_at TEXT")
    conn.commit()

# ---------------- API used by tests ----------------
def ensure_session(conn: sqlite3.Connection, project_id: str, session_id: str | None) -> str:
    """Create a session row if missing. We keep rolling_summary empty; snapshot will compute it."""
    sid = session_id or str(uuid.uuid4())
    now = _now_iso()
    cur = conn.cursor()
    cur.execute("SELECT session_id FROM sessions WHERE session_id=?", (sid,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO sessions(session_id, project_id, rolling_summary, last_actions_json, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (sid, project_id, "", "[]", now, now)
        )
    else:
        cur.execute("UPDATE sessions SET updated_at=? WHERE session_id=?", (now, sid))
    conn.commit()
    return sid

def append_action(conn: sqlite3.Connection, session_id: str, action: Dict[str, Any]) -> None:
    """
    Append an action into last_actions_json (keep last 20).
    We do not depend on rolling_summary; snapshot computes it on demand.
    """
    cur = conn.cursor()
    row = cur.execute(
        "SELECT COALESCE(last_actions_json,'[]') AS last_actions_json FROM sessions WHERE session_id=?",
        (session_id,)
    ).fetchone()

    try:
        actions: List[dict] = json.loads(row["last_actions_json"] or "[]") if row else []
    except Exception:
        actions = []

    a = dict(action or {})
    a["ts"] = _now_iso()
    actions.append(a)
    actions = actions[-20:]  # keep last 20

    cur.execute(
        "UPDATE sessions SET last_actions_json=?, updated_at=? WHERE session_id=?",
        (json.dumps(actions), a["ts"], session_id)
    )
    conn.commit()

def get_session_snapshot(conn: sqlite3.Connection, session_id: str) -> Dict[str, Any]:
    """
    Always return a headered, newest-first summary computed solely from last_actions_json.
    This guarantees the header is present even if any stored rolling_summary is header-less.
    """
    cur = conn.cursor()
    row = cur.execute(
        "SELECT COALESCE(last_actions_json,'[]') AS last_actions_json FROM sessions WHERE session_id=?",
        (session_id,)
    ).fetchone()

    try:
        actions = json.loads(row["last_actions_json"] or "[]") if row else []
    except Exception:
        actions = []

    bullets = [HEADER]
    for a in reversed(actions[-10:]):  # newest first
        kind = a.get("action", "")
        if a.get("step"):
            bullets.append(f"• {kind} {a['step']}")
        elif a.get("mode"):
            bullets.append(f"• {kind} {a['mode']}")
        elif a.get("status"):
            bullets.append(f"• {kind} {a['status']}")
        elif a.get("item_id"):
            bullets.append(f"• {kind} {a['item_id']}")
        else:
            bullets.append(f"• {kind}")

    return {
        "session_id": session_id,
        "rolling_summary": "\n".join(bullets),
    }

# ---------------- utils ----------------
def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
