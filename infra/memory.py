# infra/memory.py
import sqlite3, json
from typing import Optional, Dict

def _kv(rows) -> Dict[str, str]:
    return {r["key"]: r["value"] for r in rows}

def load_memory(conn: sqlite3.Connection, project_id: Optional[str], session_id: Optional[str]) -> dict:
    cur = conn.cursor(); cur.row_factory = sqlite3.Row
    g = cur.execute("SELECT key,value FROM memory_global").fetchall()
    p = cur.execute("SELECT key,value FROM memory_project WHERE project_id=?", (project_id,)).fetchall() if project_id else []
    s = cur.execute("SELECT key,value FROM memory_session WHERE session_id=?", (session_id,)).fetchall() if session_id else []
    return {**_kv(g), **_kv(p), **_kv(s)}

def prompt_hydrator(conn: sqlite3.Connection,
                    base_system_prompt: str,
                    project_id: Optional[str] = None,
                    session_id: Optional[str] = None,
                    extra_ctx: str = "") -> str:
    mem = load_memory(conn, project_id, session_id)
    tone = mem.get("tone", "Concise, British English")
    jira_story_prefix = mem.get("jira.story_prefix", "PK")
    format_rules = mem.get("format.rules", "Use bullet points; BDD Given/When/Then")

    session_bits = ""
    if session_id:
        cur = conn.cursor(); cur.row_factory = sqlite3.Row
        row = cur.execute("SELECT rolling_summary FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row and row["rolling_summary"]:
            session_bits = f"\n[SessionSummary]\n{row['rolling_summary']}\n"

    memory_block = f"""[Memory]
tone: {tone}
jira_story_prefix: {jira_story_prefix}
format_rules: {format_rules}
"""
    context_block = f"\n[Context]\n{extra_ctx}\n" if extra_ctx else ""
    return memory_block + session_bits + context_block + "\n" + base_system_prompt