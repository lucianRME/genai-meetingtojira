#!/usr/bin/env python3
"""
run_pipeline.py

Runs the Synapse GenAI pipeline (Requirements → BDD → Persist → Jira).

Modes:
- Default: "agentic" (multi-agent controller via agents/agentic_controller.py)
- Fallback: "classic" (direct generate_req_bdd.py pipeline)
- Always tries CSV export (export_csv.py) and optional Jira sync (idempotent).

Includes:
- Session capability: ensure session_id, log recent actions (rolling), store rolling_summary
- Memory/Session DDL auto-run on startup
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
    conn.execute(
        "INSERT OR IGNORE INTO sessions(session_id, project_id, last_actions_json) VALUES(?,?,?)",
        (sid, project_id, "[]")
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
    # compact summary of last 10
    lines = []
    for a in actions[-10:]:
        who = a.get("actor", "system")
        kind = a.get("action", "do")
        item = a.get("item") or a.get("item_id") or a.get("mode") or a.get("step") or ""
        lines.append(f"- {a['ts']} • {who} • {kind} {item}".strip())
    rolling_summary = "Recent actions:\n" + "\n".join(lines) if lines else ""
    conn.execute(
        "UPDATE sessions SET last_actions_json=?, rolling_summary=?, updated_at=CURRENT_TIMESTAMP WHERE session_id=?",
        (json.dumps(actions), rolling_summary, session_id)
    )
    conn.commit()

# ----------------------------------------------------------------------------- 
# AGENTIC MODE
# -----------------------------------------------------------------------------
def run_agentic(transcript_path: str | None, project_id: str, session_id: str, conn: sqlite3.Connection) -> dict:
    """Run the multi-agent controller flow (Ingest → Req → Review → Tests → Persist)."""
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

    flow = AgenticController(
        steps=[IngestAgent(), RequirementAgent(), ReviewAgent(), TestAgent(), PersistAgent()],
        on_step=on_step,
    )

    # seed initial state with session/project and DB conn so agents can use memory
    initial_state = {
        "transcript_path": transcript_path,
        "project_id": project_id,
        "session_id": session_id,
        "conn": conn,
    }

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
        append_action(conn, session_id, {"actor": "pipeline", "action": "jira_sync", "approved_only": approved_only, "exit_code": 0})
    except Exception as e:
        print(f"⚠️ Jira sync skipped/failed: {e}")
        append_action(conn, session_id, {"actor": "pipeline", "action": "jira_sync_failed", "approved_only": approved_only, "error": str(e)})

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

    # open DB / ensure session
    conn = get_conn()
    session_id = ensure_session(conn, PROJECT_ID, incoming_session_id=None)

    # Choose mode
    result = {}
    if args.mode == "agentic":
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
