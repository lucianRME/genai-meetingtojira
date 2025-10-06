#!/usr/bin/env python3
"""
run_pipeline.py

Runs the GenAI Requirements → BDD → Persist flow.

- Default mode: "agentic" (multi-agent controller)
- Fallback / optional mode: "classic" (direct run_pipeline from generate_req_bdd.py)
- Always tries to export CSVs via export_csv.py if present
"""

from __future__ import annotations
import os
import sys
import subprocess
import argparse
from pathlib import Path

DEFAULT_MODE = os.getenv("PIPELINE_MODE", "agentic").lower()  # agentic | classic
TRANSCRIPT_FILE = os.getenv("TRANSCRIPT_FILE")  # optional override via env

def run_agentic(transcript_path: str | None) -> dict:
    """Run the multi-agent controller flow."""
    from agentic_controller import Controller
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

    flow = Controller(
        steps=[IngestAgent(), RequirementAgent(), ReviewAgent(), TestAgent(), PersistAgent()],
        on_step=log,
    )
    result = flow.run({"transcript_path": transcript_path})
    print("🎯 Agentic run complete.")
    return result

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

def maybe_export_csv():
    """Try to export CSVs if export_csv.py is available."""
    if Path("export_csv.py").exists():
        print("▶ Exporting CSVs via export_csv.py …")
        subprocess.run([sys.executable, "export_csv.py"], check=True)
    else:
        print("ℹ️ export_csv.py not found — skipping CSV export.")

def main():
    parser = argparse.ArgumentParser(description="Run the GenAI pipeline.")
    parser.add_argument("--mode", choices=["agentic", "classic"], default=DEFAULT_MODE,
                        help="Execution mode (default from PIPELINE_MODE env, default=agentic).")
    parser.add_argument("--transcript", default=TRANSCRIPT_FILE,
                        help="Optional path to .vtt transcript (overrides TRANSCRIPT_FILE env).")
    parser.add_argument("--no-export", action="store_true",
                        help="Skip CSV export step.")
    args = parser.parse_args()

    # Choose mode (agentic preferred)
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

    # Optional export
    if not args.no_export:
        try:
            maybe_export_csv()
        except subprocess.CalledProcessError as e:
            print(f"⚠️ CSV export failed: {e}")

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