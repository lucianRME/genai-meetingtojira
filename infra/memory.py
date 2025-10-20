# infra/memory.py
from __future__ import annotations
import sqlite3
from typing import Optional

DDL = """
CREATE TABLE IF NOT EXISTS memory_global (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS memory_project (
  project_id TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT,
  PRIMARY KEY(project_id, key)
);
CREATE TABLE IF NOT EXISTS memory_session (
  session_id TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT,
  PRIMARY KEY(session_id, key)
);
"""

def ensure_memory_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()

def load_memory(conn: sqlite3.Connection, project_id: Optional[str], session_id: Optional[str]) -> dict:
    """Return a merged memory view; safe if tables don’t exist yet."""
    ensure_memory_tables(conn)
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row

    g = cur.execute("SELECT key,value FROM memory_global").fetchall()
    p = []
    if project_id:
        p = cur.execute(
            "SELECT key,value FROM memory_project WHERE project_id=?",
            (project_id,)
        ).fetchall()
    s = []
    if session_id:
        s = cur.execute(
            "SELECT key,value FROM memory_session WHERE session_id=?",
            (session_id,)
        ).fetchall()

    def rows_to_dict(rows):
        out = {}
        for r in rows:
            out[r["key"]] = r["value"]
        return out

    return {
        "global": rows_to_dict(g),
        "project": rows_to_dict(p),
        "session": rows_to_dict(s),
    }

def prompt_hydrator(conn: sqlite3.Connection, *, base_system_prompt: str,
                    project_id: Optional[str] = None, session_id: Optional[str] = None,
                    extra_ctx: str = "") -> str:
    """
    Build a SYSTEM prompt using Memory. Always safe to call (creates tables if missing).
    """
    mem = load_memory(conn, project_id, session_id)
    tone = (mem["project"].get("tone") or mem["global"].get("tone") or "British English").strip()
    jira_prefix = (mem["project"].get("jira_story_prefix") or "").strip()

    blocks = [
        base_system_prompt.strip(),
        f"[Tone] Use {tone}.",
    ]
    if jira_prefix:
        blocks.append(f"[Jira] Story prefix: {jira_prefix}")
    if extra_ctx:
        blocks.append(f"[Context]\n{extra_ctx.strip()}")

    return "\n\n".join(blocks).strip()
