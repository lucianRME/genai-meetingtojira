#!/usr/bin/env python3
"""
export_csv.py

Exports requirements and test cases from repo.db to versioned CSVs for Jira import.

Features:
- Adds new fields: priority, epic, tags
- Automatically creates 'output/' folder
- Timestamps CSVs (e.g., output/requirements_20251006_2030.csv)
- Backward compatible with old DBs
"""

import os
import sqlite3
import pandas as pd
from datetime import datetime

# Ensure output folder exists
os.makedirs("output", exist_ok=True)
ts = datetime.now().strftime("%Y%m%d_%H%M")

conn = sqlite3.connect("repo.db")

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
reqs = pd.read_sql(reqs_query, conn)
req_csv = f"output/requirements_{ts}.csv"
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
tests = pd.read_sql(tests_query, conn)
tests_csv = f"output/test_cases_{ts}.csv"
tests.to_csv(tests_csv, index=False)

conn.close()

print(f"✅ Exported: {req_csv}, {tests_csv}")