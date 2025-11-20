# tests/conftest.py
import os, sys, pathlib, sqlite3, json
import pytest

# Put repo root on sys.path so 'app', 'agents', 'run_pipeline', etc. import cleanly.
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

@pytest.fixture(autouse=True)
def _env_isolation(monkeypatch, tmp_path):
    db = tmp_path / "repo.db"
    monkeypatch.setenv("REPO_DB_PATH", str(db))
    monkeypatch.setenv("PROJECT_ID", "myproject")
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")      # will be stubbed
    monkeypatch.setenv("JIRA_INTEGRATION", "0")        # never hit Jira in tests
    monkeypatch.setenv("SMALLTALK_FILTER", "1")
    monkeypatch.setenv("SMALLTALK_LLM_CLASSIFIER", "0")
    return

@pytest.fixture
def sample_vtt(tmp_path, monkeypatch):
    p = tmp_path / "meeting.vtt"
    p.write_text(
        "WEBVTT\n\n"
        "1\n00:00:00.000 --> 00:00:02.000\nGood morning everyone!\n\n"
        "2\n00:00:02.000 --> 00:00:05.000\nWe need acceptance criteria for checkout.\n"
    )
    monkeypatch.setenv("TRANSCRIPT_FILE", str(p))
    return str(p)

@pytest.fixture
def db_conn():
    db = os.environ["REPO_DB_PATH"]
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    # ensure a minimal sessions schema so session helpers don’t explode
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS sessions(
      session_id TEXT PRIMARY KEY,
      project_id TEXT,
      rolling_summary TEXT,
      last_actions_json TEXT,
      updated_at TEXT
    );
    """)
    conn.commit()
    yield conn
    conn.close()

# Some tests expect 'db' instead of 'db_conn'
@pytest.fixture
def db(db_conn):
    return db_conn

@pytest.fixture
def stub_chat(monkeypatch):
    import generate_req_bdd as core
    def _fake_chat(messages, model=None, temperature=None):
        text = "\n".join(m["content"] for m in messages if m["role"] != "system")
        if "Extract 3–6" in text:
            content = json.dumps([{
                "id": "REQ-001",
                "title": "Checkout requires AC",
                "description": "Add AC for checkout flow",
                "acceptance_criteria": ["Given user...", "When they pay...", "Then order complete"],
                "priority": "High",
                "epic": "Checkout"
            }])
        else:
            content = json.dumps([{
                "requirement_id": "REQ-001",
                "scenario_type": "positive",
                "gherkin": "Scenario: success\nGiven user\nWhen pays\nThen ok",
                "tags": ["@positive"]
            }])
        choice = type("c", (), {"message": type("m", (), {"content": content})})()
        return type("Resp", (), {"choices": [choice]})()
    monkeypatch.setattr(core, "_chat", _fake_chat)
    return