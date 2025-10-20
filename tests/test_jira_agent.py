# tests/test_jira_agent.py
import os, sqlite3, json
import requests_mock
from agents.jira_agent import create_from_db

def _seed(db):
    conn = sqlite3.connect(db)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS requirements(
        id TEXT PRIMARY KEY, title TEXT, description TEXT, criteria TEXT,
        priority TEXT, epic TEXT, approved INTEGER DEFAULT 1, jira_key TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS test_cases(
        id INTEGER PRIMARY KEY AUTOINCREMENT, requirement_id TEXT,
        scenario_type TEXT, gherkin TEXT, tags TEXT, jira_key TEXT)""")
    c.execute("INSERT OR REPLACE INTO requirements(id,title,description,criteria,priority,epic,approved) VALUES(?,?,?,?,?,?,1)",
              ("REQ-001","Checkout","desc","A\nB\nC","High","Checkout"))
    c.execute("INSERT INTO test_cases(requirement_id,scenario_type,gherkin,tags) VALUES(?,?,?,?)",
              ("REQ-001","positive","Scenario: ok\nGiven x\nWhen y\nThen z", json.dumps(["@positive"])))
    conn.commit(); conn.close()

def test_jira_sync_idempotent(tmp_path, monkeypatch):
    db = tmp_path/"repo.db"
    os.environ["REPO_DB_PATH"] = str(db)
    os.environ["JIRA_INTEGRATION"] = "1"
    os.environ["JIRA_URL"] = "https://example.atlassian.net"
    os.environ["JIRA_USER"] = "u@example.com"
    os.environ["JIRA_API_TOKEN"] = "token"
    os.environ["JIRA_PROJECT"] = "SCRUM"
    _seed(str(db))
    with requests_mock.Mocker() as m:
        # search returns none -> create, then update on next run
        m.post("https://example.atlassian.net/rest/api/3/search", json={"issues":[]})
        m.post("https://example.atlassian.net/rest/api/3/issue", json={"key":"SCRUM-1"})
        m.put(requests_mock.ANY, status_code=204)
        create_from_db(str(db), project_id="primark", session_id="sid-1")
        # second run should detect hash and skip creating/updating (idempotent)
        create_from_db(str(db), project_id="primark", session_id="sid-1")