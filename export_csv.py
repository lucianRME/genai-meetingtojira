#!/usr/bin/env python3
"""
export_csv.py

Exports requirements and test cases from repo.db to versioned CSVs for Jira import.

Features:
- Adds new fields: priority, epic, tags
- Automatically creates 'output/' folder
- Timestamps CSVs (e.g., output/requirements_YYYYMMDD_HHMM.csv)
- Backward compatible with old DBs
"""

import os
import sqlite3
from datetime import datetime
from typing import Tuple, Optional

import pandas as pd


def _open_conn(db_path: str = "repo.db") -> sqlite3.Connection:
    # Ensure parent dir exists (in case a custom path is used)
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    return sqlite3.connect(db_path)


def _timestamp(ts: Optional[str] = None) -> str:
    return ts or datetime.now().strftime("%Y%m%d_%H%M")


def export_csv(
    out_dir: str = "output",
    db_path: str = "repo.db",
    ts: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Export requirements and test cases to CSV.

    Returns:
        (requirements_csv_path, test_cases_csv_path)
    """
    os.makedirs(out_dir, exist_ok=True)
    ts_str = _timestamp(ts)

    conn = _open_conn(db_path)
    try:
        # ----------------------------
        # Export Requirements (Stories)
        # ----------------------------
        reqs_query = """
          SELECT 
            id AS IssueKey, 
            title AS Summary, 
            description AS Description, 
            'Story' AS IssueType, 
            COALESCE(priority, '') AS Priority,
            COALESCE(epic, '') AS EpicLink,
            CASE approved WHEN 1 THEN 'Approved' ELSE 'Draft' END AS Status,
            COALESCE(criteria, '') AS AcceptanceCriteria
          FROM requirements
        """
        reqs = pd.read_sql_query(reqs_query, conn)
        req_csv = os.path.join(out_dir, f"requirements_{ts_str}.csv")
        reqs.to_csv(req_csv, index=False)

        # ----------------------------
        # Export Test Cases
        # ----------------------------
        tests_query = """
          SELECT 
            requirement_id AS LinkedRequirement, 
            scenario_type AS ScenarioType, 
            gherkin AS Gherkin,
            COALESCE(tags, '') AS Tags
          FROM test_cases
        """
        tests = pd.read_sql_query(tests_query, conn)
        tests_csv = os.path.join(out_dir, f"test_cases_{ts_str}.csv")
        tests.to_csv(tests_csv, index=False)

    finally:
        conn.close()

    print(f"✅ Exported: {req_csv}, {tests_csv}")
    return req_csv, tests_csv


if __name__ == "__main__":
    # Simple CLI usage without adding dependencies
    import argparse

    parser = argparse.ArgumentParser(description="Export repo.db tables to CSV.")
    parser.add_argument("--db", dest="db_path", default="repo.db", help="Path to SQLite DB (default: repo.db)")
    parser.add_argument("--out", dest="out_dir", default="output", help="Output directory (default: output)")
    parser.add_argument(
        "--ts",
        dest="ts",
        default=None,
        help="Timestamp override (YYYYMMDD_HHMM). Default is current time.",
    )
    args = parser.parse_args()

    export_csv(out_dir=args.out_dir, db_path=args.db_path, ts=args.ts)
