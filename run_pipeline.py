#!/usr/bin/env python3
"""
run_pipeline.py

Runs the FlowMind GenAI pipeline (Requirements → BDD → Persist → Jira).

Modes:
- Default: "agentic" (multi-agent controller via agents/agentic_controller.py)
- Fallback: "classic" (direct generate_req_bdd.py pipeline)
- Always tries CSV export (export_csv.py) and optional Jira sync (idempotent).
"""

from __future__ import annotations
import os
import sys
import subprocess
import argparse
from pathlib import Path

# --- Ensure repo root is on sys.path ---
sys.path.insert(0, os.path.dirname(__file__))

# --- Default runtime configuration ---
DEFAULT_MODE = os.getenv("PIPELINE_MODE", "agentic").lower()  # agentic | classic
TRANSCRIPT_FILE = os.getenv("TRANSCRIPT_FILE")  # optional override via env

# Jira sync flags
JIRA_SYNC_ON_PIPELINE_DEFAULT = os.getenv("JIRA_SYNC_ON_PIPELINE", "1") == "1"
JIRA_APPROVED_ONLY_DEFAULT = os.getenv("JIRA_APPROVED_ONLY", "1") == "1"


# -----------------------------------------------------------------------------
# AGENTIC MODE
# -----------------------------------------------------------------------------
def run_agentic(transcript_path: str | None) -> dict:
    """Run the multi-agent controller flow (Ingest → Req → Review → Tests → Persist)."""

    # ✅ Correct import path (uses agents/agentic_controller.py)
    from agents.agentic_controller import Controller as AgenticController
    from agents.ingest_agent import IngestAgent
    from agents.requirements_agent import RequirementAgent
    from agents.review_agent import ReviewAgent
    from agents.tests_agent import TestAgent
    from agents.persist_agent import PersistAgent

    def log(step, state):
        if step == "requirements":
            print(f"🧩 Requirements: {len(state.get('requirements', []))}")
        elif step == "tests":
            print(f"✅ Test cases: {len(state.get('test_cases', []))}")

    # Initialize the full multi-agent flow
    flow = AgenticController(
        steps=[
            IngestAgent(),
            RequirementAgent(),
            ReviewAgent(),
            TestAgent(),
            PersistAgent(),
        ],
        on_step=log,
    )

    result = flow.run({"transcript_path": transcript_path})
    print("🎯 Agentic run complete.")
    return result


# -----------------------------------------------------------------------------
# CLASSIC MODE (Fallback)
# -----------------------------------------------------------------------------
def run_classic(transcript_path: str | None) -> dict:
    """Run the original single-pipeline function, in-process if available, else subprocess."""
    print("▶ Running classic pipeline (in-process)…")
    try:
        import generate_req_bdd as core
        return core.run_pipeline(transcript_path)
    except Exception as e:
        print(f"⚠️ In-process classic run failed: {e}")
        print("▶ Falling back to subprocess…")
        args = [sys.executable, "generate_req_bdd.py"]
        if transcript_path:
            args.append(transcript_path)
        subprocess.run(args, check=True)
        return {"output_json": "output.json", "db_path": "repo.db"}  # minimal summary


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


def maybe_sync_jira(approved_only: bool):
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
        create_from_db("repo.db")
        print("✅ Jira sync complete.")
    except Exception as e:
        print(f"⚠️ Jira sync skipped/failed: {e}")


# -----------------------------------------------------------------------------
# MAIN ENTRY POINT
# -----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Run the FlowMind GenAI pipeline.")
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

    # Choose mode
    result = {}
    if args.mode == "agentic":
        print("▶ Running **agentic** controller…")
        try:
            result = run_agentic(args.transcript)
        except Exception as e:
            print(f"⚠️ Agentic run failed: {e}")
            print("▶ Falling back to **classic** pipeline…")
            result = run_classic(args.transcript)
    else:
        result = run_classic(args.transcript)

    # Optional CSV export
    if not args.no_export:
        try:
            maybe_export_csv()
        except subprocess.CalledProcessError as e:
            print(f"⚠️ CSV export failed: {e}")

    # Optional Jira sync (idempotent)
    sync_flag = JIRA_SYNC_ON_PIPELINE_DEFAULT and not args.no_jira
    approved_only = (
        JIRA_APPROVED_ONLY_DEFAULT
        if args.jira_approved_only is None
        else args.jira_approved_only
    )
    if sync_flag:
        maybe_sync_jira(approved_only=approved_only)
    else:
        print("ℹ️ Jira sync disabled (use --no-jira to force off, or set JIRA_SYNC_ON_PIPELINE=1 to enable).")

    # Final summary
    out_json = result.get("output_json", "output.json")
    db_path = result.get("db_path", "repo.db")
    req_count = result.get("metrics", {}).get("requirements_count", result.get("requirements_count"))
    tc_count = result.get("metrics", {}).get("test_cases_count", result.get("test_cases_count"))

    print("\n🚀 E2E DONE")
    if req_count is not None:
        print(f"🧩 requirements: {req_count}")
    if tc_count is not None:
        print(f"✅ test cases:    {tc_count}")
    print(f"📦 outputs:       {out_json} , {db_path}")


if __name__ == "__main__":
    main()