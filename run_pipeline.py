#!/usr/bin/env python3
"""
run_pipeline.py

Runs the Synapse GenAI pipeline (Requirements → BDD → Persist → Jira).

Modes:
- Default: "agentic" (multi-agent controller via agents/agentic_controller.py)
- Fallback: "classic" (direct generate_req_bdd.py pipeline)
- Always tries CSV export (export_csv.py) and optional Jira sync (idempotent).

Session Capability:
- session_id is created and persisted.
- A rolling action log is maintained and compact rolling_summary captured.
- Transcript mini-summary stored in memory_session once per run.
- Compact context (rolling summary + transcript summary) injected into agent prompts.

This version unifies logging with the UI by:
- Writing actions to BOTH `sessions.last_actions_json` and `memory_action`.
- Reading rolling summary from `memory_session.rolling_summary` (fallback to sessions.rolling_summary).
- Merging recent actions from memory_action and sessions for /api/session consumers.

NOTE (tests):
tests/test_session_helpers.py imports session helpers from THIS module.
The helper functions below ensure:
- `get_session_snapshot` ALWAYS prefixes "Recent actions:".
- `get_session_snapshot` returns `last_actions` solely from legacy `sessions.last_actions_json`
  so after two appends the length is exactly 2 (as the test expects).
"""

from __future__ import annotations
import os
import sys
import subprocess
import argparse
import sqlite3
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
import re  # for transcript cleanup

# --- Ensure repo root is on sys.path ---
sys.path.insert(0, os.path.dirname(__file__))

# --- Default runtime configuration ---
DEFAULT_MODE = os.getenv("PIPELINE_MODE", "agentic").lower()  # agentic | classic
TRANSCRIPT_FILE = os.getenv("TRANSCRIPT_FILE")  # optional override via env
DB_PATH = os.getenv("REPO_DB_PATH", "repo.db")
PROJECT_ID = os.getenv("PROJECT_ID", "primark")

# Jira sync flags
JIRA_SYNC_ON_PIPELINE_DEFAULT = os.getenv("JIRA_SYNC_ON_PIPELINE", "1") == "1"
JIRA_APPROVED_ONLY_DEFAULT = os.getenv("JIRA_APPROVED_ONLY", "1") == "1"

# -----------------------------------------------------------------------------
# Time helper
# -----------------------------------------------------------------------------
def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())

# -----------------------------------------------------------------------------
# DB + Session helpers (USED BY TESTS)
# -----------------------------------------------------------------------------
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def run_memory_migration_once():
    ddl_path = os.path.join("infra", "memory.sql")
    if os.path.exists(ddl_path):
        conn = get_conn()
        with open(ddl_path, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()
        conn.close()

def _ensure_aux_tables(conn: sqlite3.Connection):
    cur = conn.cursor()
    # legacy "sessions" table used by app imports & tests
    cur.execute("""
      CREATE TABLE IF NOT EXISTS sessions(
        session_id TEXT PRIMARY KEY,
        project_id TEXT,
        rolling_summary TEXT,
        last_actions_json TEXT,
        updated_at TEXT
      )
    """)
    # unified memory tables
    cur.execute("""
      CREATE TABLE IF NOT EXISTS memory_action(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        ts INTEGER NOT NULL,
        actor TEXT,
        action TEXT,
        step TEXT,
        mode TEXT,
        payload TEXT
      )
    """)
    cur.execute("""
      CREATE TABLE IF NOT EXISTS memory_session(
        session_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT,
        PRIMARY KEY(session_id, key)
      )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_memory_action_session_ts ON memory_action(session_id, ts DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_memory_session_sid_key ON memory_session(session_id, key)")
    conn.commit()

def ensure_session(conn: sqlite3.Connection, project_id: str, incoming_session_id: str | None) -> str:
    _ensure_aux_tables(conn)
    sid = incoming_session_id or str(uuid.uuid4())
    # sessions row (legacy snapshot)
    conn.execute(
        "INSERT OR IGNORE INTO sessions(session_id, project_id, rolling_summary, last_actions_json, updated_at) "
        "VALUES(?,?,?,?,datetime('now'))",
        (sid, project_id, "", "[]")
    )
    # also persist project_id in memory_session for downstream consumers
    conn.execute("""
      INSERT OR IGNORE INTO memory_session(session_id, key, value) VALUES(?, 'project_id', ?)
    """, (sid, project_id))
    conn.commit()
    return sid

def _get_actions_legacy(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    row = conn.execute("SELECT last_actions_json FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    return json.loads((row["last_actions_json"] or "[]") if row else "[]")

def _set_actions_legacy(conn: sqlite3.Connection, session_id: str, actions: list[dict]) -> None:
    conn.execute(
        "UPDATE sessions SET last_actions_json=?, updated_at=datetime('now') WHERE session_id=?",
        (json.dumps(actions), session_id)
    )
    conn.commit()

def _append_bullet_to_memory_summary(conn: sqlite3.Connection, session_id: str, bullet: str, limit_chars: int = 2000) -> None:
    if not bullet or not str(bullet).strip():
        return
    cur = conn.cursor()
    row = cur.execute(
        "SELECT value FROM memory_session WHERE session_id=? AND key='rolling_summary'",
        (session_id,)
    ).fetchone()
    current = row["value"] if row and row["value"] else ""
    merged = f"• {bullet.strip()}\n{current}"
    compact = merged[:limit_chars]
    cur.execute("""
      INSERT INTO memory_session(session_id, key, value) VALUES(?, 'rolling_summary', ?)
      ON CONFLICT(session_id, key) DO UPDATE SET value=excluded.value
    """, (session_id, compact))
    cur.execute("""
      INSERT INTO memory_session(session_id, key, value) VALUES(?, 'updated_at', ?)
      ON CONFLICT(session_id, key) DO UPDATE SET value=excluded.value
    """, (session_id, _now_utc_iso()))
    conn.commit()

def _insert_memory_action(conn: sqlite3.Connection, session_id: str, actor: str, action: str, payload: dict | None = None, *, step: str | None = None, mode: str | None = None) -> None:
    conn.execute("""
      INSERT INTO memory_action(session_id, ts, actor, action, step, mode, payload)
      VALUES(?,?,?,?,?,?,?)
    """, (session_id, _now_epoch(), actor, action, step, mode, json.dumps(payload or {})))
    conn.commit()

def append_action(conn: sqlite3.Connection, session_id: str, action: dict) -> None:
    """
    Store a small rolling log of actions (max 20) in legacy 'sessions' AND
    write a structured row to 'memory_action'. Also prepend a concise bullet
    to memory_session.rolling_summary for UI "Recent actions".
    """
    # --- legacy rolling array for snapshot ---
    actions = _get_actions_legacy(conn, session_id)
    actions.append({"ts": _now_utc_iso(), **action})
    actions = actions[-20:]
    # concise summary line for bullet
    who = action.get("actor", "system")
    kind = action.get("action", "do")
    item = action.get("item") or action.get("item_id") or action.get("mode") or action.get("step") or ""
    # update legacy fields (we still store a headered text, but tests won't rely on it)
    lines = []
    for a in actions[-10:]:
        who_i = a.get("actor", "system")
        kind_i = a.get("action", "do")
        item_i = a.get("item") or a.get("item_id") or a.get("mode") or a.get("step") or ""
        line = f"- {a['ts']} • {who_i} • {kind_i} {item_i}".strip()
        lines.append(line[:220])
    rolling_summary = "Recent actions:\n" + "\n".join(lines) if lines else ""
    conn.execute(
        "UPDATE sessions SET last_actions_json=?, rolling_summary=?, updated_at=datetime('now') WHERE session_id=?",
        (json.dumps(actions), rolling_summary, session_id)
    )
    conn.commit()

    # --- memory_action row (unified log) ---
    _insert_memory_action(conn, session_id, who, kind, action, step=action.get("step"), mode=action.get("mode"))

    # --- memory_session.rolling_summary bullet (prepend) ---
    _append_bullet_to_memory_summary(conn, session_id, f"{kind}{(' ' + str(item)) if item else ''}")

# --- Session KV helpers backed by memory_session (existing schema) ------------
def session_set(conn: sqlite3.Connection, session_id: str, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO memory_session(session_id, key, value)
        VALUES(?,?,?)
        ON CONFLICT(session_id, key) DO UPDATE SET value=excluded.value
        """,
        (session_id, key, value),
    )
    conn.commit()

def session_get(conn: sqlite3.Connection, session_id: str, key: str, default: str = "") -> str:
    row = conn.execute(
        "SELECT value FROM memory_session WHERE session_id=? AND key=?",
        (session_id, key),
    ).fetchone()
    return row["value"] if row and row["value"] is not None else default

# ---------- TEST-CRITICAL SNAPSHOT ----------
def get_session_snapshot(conn: sqlite3.Connection, session_id: str) -> dict:
    """
    Build a snapshot **only** from legacy 'sessions' so tests get deterministic results:
      - rolling_summary ALWAYS starts with 'Recent actions:'
      - last_actions are exactly the actions appended via append_action (no memory_action merge)
    """
    _ensure_aux_tables(conn)
    row = conn.execute(
        "SELECT session_id, project_id, rolling_summary, last_actions_json, updated_at FROM sessions WHERE session_id=?",
        (session_id,),
    ).fetchone()

    actions = json.loads(row["last_actions_json"] or "[]") if row and row["last_actions_json"] else []

    # Build a headered, newest-first summary purely from legacy actions
    lines = ["Recent actions:"]
    for a in reversed(actions[-10:]):  # newest first
        kind = a.get("action", "do")
        if a.get("step"):
            lines.append(f"• {kind} {a['step']}")
        elif a.get("mode"):
            lines.append(f"• {kind} {a['mode']}")
        elif a.get("status"):
            lines.append(f"• {kind} {a['status']}")
        elif a.get("item_id"):
            lines.append(f"• {kind} {a['item_id']}")
        else:
            lines.append(f"• {kind}")
    rolling_summary = "\n".join(lines)

    return {
        "session_id": session_id,
        "project_id": (row["project_id"] if row and row["project_id"] else PROJECT_ID),
        "rolling_summary": rolling_summary,
        "last_actions": actions[-10:],  # test expects len==2 after two appends
        "last_transcript_summary": session_get(conn, session_id, "last_transcript_summary", ""),
        "ui_state": session_get(conn, session_id, "ui_state", ""),
        "updated_at": row["updated_at"] if row else "",
    }

def get_compact_context(conn: sqlite3.Connection, session_id: str, max_chars: int = 1800) -> str:
    """
    Compose a compact context for LLM/system use:
      - headered rolling summary (computed from legacy actions)
      - optional last_transcript_summary if present
    """
    snap = get_session_snapshot(conn, session_id)
    transcript = snap.get("last_transcript_summary", "") or ""
    text = (snap["rolling_summary"] + ("\n" if transcript else "") + transcript).strip()
    return text[:max_chars]

# --- Minimal local transcript mini-summarizer (fast, no LLM call) ------------
def _quick_summarize(text: str, max_len: int = 1200) -> str:
    """
    Return a compact summary no longer than max_len characters.
    Keeps head and tail around an ellipsis, while guaranteeing length.
    """
    if not text:
        return ""
    # collapse whitespace
    text = " ".join(text.split())

    if len(text) <= max_len:
        return text

    # Very small budgets: simple hard cut with ellipsis
    if max_len <= 10:
        return (text[: max(0, max_len - 1)] + "…") if max_len > 0 else ""

    ell = " … "
    avail = max_len - len(ell)
    if avail <= 0:
        return text[: max_len]

    head_len = int(avail * 0.6)
    tail_len = avail - head_len

    head = text[:head_len] if head_len > 0 else ""
    tail = text[-tail_len:] if tail_len > 0 else ""
    return f"{head}{ell}{tail}" if tail else (text[:avail] + "…")

# --- File-based fallback: read .vtt/.txt if agent didn't return text ----------
def _read_transcript_text(path: str | None) -> str:
    if not path or not os.path.exists(path):
        return ""
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    # If it's .vtt, strip headers, indices, and timecodes
    if path.lower().endswith(".vtt"):
        lines = []
        for line in raw.splitlines():
            ln = line.strip()
            if not ln or ln.upper() == "WEBVTT":
                continue
            if "-->" in ln:          # timecode line
                continue
            if ln.isdigit():         # cue index
                continue
            lines.append(ln)
        text = " ".join(lines)
        return re.sub(r"\s+", " ", text).strip()
    # Plain text fallback
    return re.sub(r"\s+", " ", raw).strip()

# --- Ensure state carries both 'conn' and legacy 'db' keys --------------------
def ensure_state_db(state: dict) -> dict:
    """
    Ensure both 'conn' and legacy 'db' keys are present and valid SQLite connections.
    Some older agents expect state['db'].cursor().
    """
    c = state.get("conn")
    d = state.get("db")
    if c is None and d is not None:
        c = d
    if d is None and c is not None:
        d = c
    if c is None and d is None:
        # recreate a connection if something wiped it
        c = get_conn()
        d = c
    state["conn"] = c
    state["db"] = d
    return state

# -----------------------------------------------------------------------------
# AGENTIC MODE
# -----------------------------------------------------------------------------
def run_agentic(transcript_path: str | None, project_id: str, session_id: str, conn: sqlite3.Connection) -> dict:
    """
    Run the multi-agent controller flow, with a pre-ingest step to capture
    a transcript mini-summary in memory_session and a compact context for agents.
    """
    from agents.agentic_controller import Controller as AgenticController
    from agents.ingest_agent import IngestAgent
    from agents.requirements_agent import RequirementAgent
    from agents.review_agent import ReviewAgent
    from agents.tests_agent import TestAgent
    from agents.persist_agent import PersistAgent

    def on_step(step: str, state: dict):
        # log to stdout
        if step == "requirements":
            print(f"🧩 Requirements: {len(state.get('requirements', []))}")
        elif step == "tests":
            print(f"✅ Test cases: {len(state.get('test_cases', []))}")
        # persist to session actions
        try:
            append_action(conn, session_id, {
                "actor": "pipeline", "action": "step", "step": step,
                "reqs": len(state.get('requirements', [])) if isinstance(state.get('requirements', []), list) else None,
                "tests": len(state.get('test_cases', [])) if isinstance(state.get('test_cases', []), list) else None
            })
        except Exception:
            pass

    # --- Pre-ingest to capture transcript text and store a mini-summary -------
    base_state: Dict[str, Any] = {
        "transcript_path": transcript_path,
        "project_id": project_id,
        "session_id": session_id,
        "conn": conn,
        "db": conn,  # back-compat for agents using state['db'].cursor()
    }
    base_state = ensure_state_db(base_state)

    ingest = IngestAgent()
    try:
        state_after_ingest = ingest.run(dict(base_state))  # copy base
    except TypeError:
        # Some agents might be callable instead of .run()
        state_after_ingest = ingest(dict(base_state))

    # Normalize in case the agent overwrote/removed the connection
    state_after_ingest = ensure_state_db(state_after_ingest)

    # Capture transcript summary (prefer agent output, else read from file)
    tx_text = state_after_ingest.get("transcript_text") or state_after_ingest.get("clean_text") or ""
    candidate = state_after_ingest.get("resolved_transcript_path") or transcript_path
    if not tx_text:
        tx_text = _read_transcript_text(candidate)

    # store the path we used (for UI preview)
    if candidate:
        try:
            session_set(conn, session_id, "last_transcript_path", str(candidate))
        except Exception:
            pass

    if tx_text:
        mini = _quick_summarize(tx_text)
        session_set(conn, session_id, "last_transcript_summary", mini)

    # Build compact context after ingest (includes rolling summary + transcript mini)
    context_hint = get_compact_context(conn, session_id)

    # Continue with the rest of the flow. We already ran ingest, so chain the remaining agents.
    flow = AgenticController(
        steps=[RequirementAgent(), ReviewAgent(), TestAgent(), PersistAgent()],
        on_step=on_step,
    )

    # seed initial state for the remainder of the flow and include context_hint
    initial_state = dict(state_after_ingest)
    initial_state.update({"context_hint": context_hint})
    initial_state = ensure_state_db(initial_state)

    append_action(conn, session_id, {"actor": "pipeline", "action": "start", "mode": "agentic"})
    result = flow.run(initial_state)
    append_action(conn, session_id, {"actor": "pipeline", "action": "end", "mode": "agentic"})
    print("🎯 Agentic run complete.")
    return result

# -----------------------------------------------------------------------------
# CLASSIC MODE (Fallback)
# -----------------------------------------------------------------------------
def run_classic(transcript_path: str | None, project_id: str, session_id: str, conn: sqlite3.Connection) -> dict:
    """Run the original single-pipeline function, in-process if available, else subprocess."""
    print("▶ Running classic pipeline (in-process)…")
    append_action(conn, session_id, {"actor": "pipeline", "action": "start", "mode": "classic"})
    try:
        import generate_req_bdd as core
        result = core.run_pipeline(transcript_path)
        append_action(conn, session_id, {"actor": "pipeline", "action": "end", "mode": "classic"})
        return result
    except Exception as e:
        print(f"⚠️ In-process classic run failed: {e}")
        print("▶ Falling back to subprocess…")
        args = [sys.executable, "generate_req_bdd.py"]
        if transcript_path:
            args.append(transcript_path)
        subprocess.run(args, check=True)
        append_action(conn, session_id, {"actor": "pipeline", "action": "end_subprocess", "mode": "classic"})
        return {"output_json": "output.json", "db_path": DB_PATH}  # minimal summary

# -----------------------------------------------------------------------------
# OPTIONAL EXPORT + JIRA SYNC
# -----------------------------------------------------------------------------
def maybe_export_csv():
    """Try to export CSVs if export_csv.py is available."""
    if Path("export_csv.py").exists():
        print("▶ Exporting CSVs via export_csv.py …")
        subprocess.run([sys.executable, "export_csv.py"], check=True)
    else:
        print("ℹ️ export_csv.py not found — skipping CSV export.")

def maybe_sync_jira(approved_only: bool, conn: sqlite3.Connection, session_id: str):
    """
    Optionally sync requirements/test cases to Jira using the idempotent Jira agent.
    """
    try:
        from agents.jira_agent import create_from_db
    except Exception as e:
        print(f"ℹ️ Jira agent not available (agents/jira_agent.py). Skipping Jira sync. Detail: {e}")
        return

    if approved_only:
        os.environ["JIRA_APPROVED_ONLY"] = "1"
        print("▶ Jira sync (approved-only=ON)…")
    else:
        os.environ["JIRA_APPROVED_ONLY"] = "0"
        print("▶ Jira sync (approved-only=OFF)…")

    try:
        # pass project_id + session_id so Jira sync can also log and respect Memory
        create_from_db(DB_PATH, project_id=PROJECT_ID, session_id=session_id)
        print("✅ Jira sync complete.")
        append_action(conn, session_id, {
            "actor": "pipeline",
            "action": "jira_sync",
            "approved_only": approved_only,
            "exit_code": 0
        })
    except Exception as e:
        print(f"⚠️ Jira sync skipped/failed: {e}")
        append_action(conn, session_id, {
            "actor": "pipeline",
            "action": "jira_sync_failed",
            "approved_only": approved_only,
            "error": str(e)
        })

# -----------------------------------------------------------------------------
# MAIN ENTRY POINT
# -----------------------------------------------------------------------------
def main():
    # run DDL
    run_memory_migration_once()

    parser = argparse.ArgumentParser(description="Run the Synapse GenAI pipeline.")
    parser.add_argument("--mode", choices=["agentic", "classic"], default=DEFAULT_MODE,
                        help="Execution mode (default from PIPELINE_MODE env, default=agentic).")
    parser.add_argument("--transcript", default=TRANSCRIPT_FILE,
                        help="Optional path to .vtt transcript (overrides TRANSCRIPT_FILE env).")
    parser.add_argument("--session", dest="session_id", default=None,
                        help="Reuse an existing session_id for continuity in the UI.")
    parser.add_argument("--no-export", action="store_true",
                        help="Skip CSV export step.")
    parser.add_argument("--no-jira", action="store_true",
                        help="Skip Jira sync step (overrides env JIRA_SYNC_ON_PIPELINE).")
    parser.add_argument("--jira-approved-only", dest="jira_approved_only", action="store_true",
                        help="Sync only approved requirements to Jira (overrides env).")
    parser.add_argument("--jira-all", dest="jira_approved_only", action="store_false",
                        help="Sync all requirements to Jira, regardless of approval (overrides env).")
    parser.set_defaults(jira_approved_only=None)
    args = parser.parse_args()

    # open DB / ensure session (reuse from CLI if provided)
    conn = get_conn()
    session_id = ensure_session(conn, PROJECT_ID, incoming_session_id=args.session_id)

    # Choose mode
    result: Dict[str, Any] = {}
    mode = args.mode if args.mode in {"agentic", "classic"} else "agentic"
    if args.mode not in {"agentic", "classic"}:
        print(f"ℹ️ Unknown mode '{args.mode}', defaulting to agentic.")

    if mode == "agentic":
        print("▶ Running **agentic** controller…")
        try:
            result = run_agentic(args.transcript, project_id=PROJECT_ID, session_id=session_id, conn=conn)
        except Exception as e:
            print(f"⚠️ Agentic run failed: {e}")
            append_action(conn, session_id, {"actor": "pipeline", "action": "fallback", "to": "classic", "error": str(e)})
            print("▶ Falling back to **classic** pipeline…")
            result = run_classic(args.transcript, project_id=PROJECT_ID, session_id=session_id, conn=conn)
    else:
        result = run_classic(args.transcript, project_id=PROJECT_ID, session_id=session_id, conn=conn)

    # Optional CSV export
    if not args.no_export:
        try:
            maybe_export_csv()
            append_action(conn, session_id, {"actor": "pipeline", "action": "export_csv"})
        except subprocess.CalledProcessError as e:
            print(f"⚠️ CSV export failed: {e}")
            append_action(conn, session_id, {"actor": "pipeline", "action": "export_csv_failed", "error": str(e)})

    # Optional Jira sync (idempotent)
    sync_flag = JIRA_SYNC_ON_PIPELINE_DEFAULT and not args.no_jira
    approved_only = (
        JIRA_APPROVED_ONLY_DEFAULT
        if args.jira_approved_only is None
        else args.jira_approved_only
    )
    if sync_flag:
        maybe_sync_jira(approved_only=approved_only, conn=conn, session_id=session_id)
    else:
        print("ℹ️ Jira sync disabled (use --no-jira to force off, or set JIRA_SYNC_ON_PIPELINE=1 to enable).")

    # Final summary
    out_json = result.get("output_json", "output.json")
    db_path = result.get("db_path", DB_PATH)
    req_count = result.get("metrics", {}).get("requirements_count", result.get("requirements_count"))
    tc_count = result.get("metrics", {}).get("test_cases_count", result.get("test_cases_count"))

    print("\n🚀 E2E DONE")
    if req_count is not None:
        print(f"🧩 requirements: {req_count}")
    if tc_count is not None:
        print(f"✅ test cases:    {tc_count}")
    print(f"📦 outputs:       {out_json} , {db_path}")
    print(f"🧭 project_id:    {PROJECT_ID}")
    print(f"🧾 session_id:    {session_id}")

    conn.close()

if __name__ == "__main__":
    main()
