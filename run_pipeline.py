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
- A rolling action log is maintained (last 20), with a compact rolling_summary of recent actions.
- A transcript mini-summary is stored in memory_session once per run.
- A compact context (rolling summary + transcript mini-summary) is injected into agent prompts.

Includes:
- Session capability: ensure session_id, log recent actions (rolling), store rolling_summary
- Memory/Session DDL auto-run on startup (uses infra/memory.sql)
- UTC-aware timestamps
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

# -----------------------------------------------------------------------------
# DB + Session helpers
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

def ensure_session(conn: sqlite3.Connection, project_id: str, incoming_session_id: str | None) -> str:
    sid = incoming_session_id or str(uuid.uuid4())
    # sessions table: session_id, project_id, rolling_summary, last_actions_json, updated_at
    conn.execute(
        "INSERT OR IGNORE INTO sessions(session_id, project_id, rolling_summary, last_actions_json) VALUES(?,?,?,?)",
        (sid, project_id, "", "[]")
    )
    conn.commit()
    return sid

def _get_actions(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    row = conn.execute("SELECT last_actions_json FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    return json.loads((row["last_actions_json"] or "[]") if row else "[]")

def append_action(conn: sqlite3.Connection, session_id: str, action: dict) -> None:
    """
    Store a small rolling log of actions (max 20) and keep a compact rolling_summary (~1–2k chars).
    """
    actions = _get_actions(conn, session_id)
    actions.append({"ts": _now_utc_iso(), **action})
    actions = actions[-20:]
    # compact summary of last 10 actions
    lines = []
    for a in actions[-10:]:
        who = a.get("actor", "system")
        kind = a.get("action", "do")
        item = a.get("item") or a.get("item_id") or a.get("mode") or a.get("step") or ""
        line = f"- {a['ts']} • {who} • {kind} {item}".strip()
        lines.append(line[:220])  # keep each line concise
    rolling_summary = "Recent actions:\n" + "\n".join(lines) if lines else ""
    conn.execute(
        "UPDATE sessions SET last_actions_json=?, rolling_summary=?, updated_at=CURRENT_TIMESTAMP WHERE session_id=?",
        (json.dumps(actions), rolling_summary, session_id)
    )
    conn.commit()

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

def get_session_snapshot(conn: sqlite3.Connection, session_id: str) -> dict:
    row = conn.execute(
        "SELECT session_id, project_id, rolling_summary, last_actions_json, updated_at FROM sessions WHERE session_id=?",
        (session_id,),
    ).fetchone()
    if not row:
        return {}
    # pull commonly used KV items
    tx_sum = session_get(conn, session_id, "last_transcript_summary", "")
    ui_state = session_get(conn, session_id, "ui_state", "")
    return {
        "session_id": row["session_id"],
        "project_id": row["project_id"],
        "rolling_summary": row["rolling_summary"] or "",
        "last_actions": json.loads(row["last_actions_json"] or "[]"),
        "last_transcript_summary": tx_sum,
        "ui_state": ui_state,
        "updated_at": row["updated_at"],
    }

def get_compact_context(conn: sqlite3.Connection, session_id: str, max_chars: int = 1800) -> str:
    """
    Build a compact context string from rolling summary + last transcript mini-summary.
    """
    snap = get_session_snapshot(conn, session_id)
    parts: List[str] = []
    if snap.get("rolling_summary"):
        parts.append(snap["rolling_summary"])
    if snap.get("last_transcript_summary"):
        parts.append("Transcript summary:\n" + snap["last_transcript_summary"])
    text = "\n\n".join([p for p in parts if p])
    if len(text) > max_chars:
        text = text[:max_chars] + "…"
    return text

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
        # Not enough room for ellipsis + content; fall back to truncation
        return text[: max_len]

    # Split the available space between head and tail (60/40 split)
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
        create_from_db(DB_PATH)
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
