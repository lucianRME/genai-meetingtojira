import sqlite3, pandas as pd

conn = sqlite3.connect("repo.db")

# Export Requirements (Story)
reqs = pd.read_sql("""
  SELECT 
    id AS IssueKey, 
    title AS Summary, 
    description AS Description, 
    'Story' AS IssueType, 
    CASE approved WHEN 1 THEN 'Approved' ELSE 'Draft' END AS Status
  FROM requirements
""", conn)
reqs.to_csv("requirements.csv", index=False)

# Export Test Cases (generic)
tests = pd.read_sql("""
  SELECT 
    requirement_id AS LinkedRequirement, 
    scenario_type AS ScenarioType, 
    gherkin AS Gherkin
  FROM test_cases
""", conn)
tests.to_csv("test_cases.csv", index=False)

conn.close()
print("✅ Exported: requirements.csv, test_cases.csv")