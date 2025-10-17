# tests/conftest.py
import os, sqlite3, pathlib, sys, json, shutil
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

@pytest.fixture(autouse=True)
def _env_isolation(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("REPO_DB_PATH", str(db_path))
    monkeypatch.setenv("PROJECT_ID", "primark")
    # disable Jira during tests
    monkeypatch.setenv("JIRA_SYNC_ON_PIPELINE", "0")
    # default transcript (can override in tests)
    monkeypatch.setenv("TRANSCRIPT_FILE", "")
    yield

@pytest.fixture
def db(tmp_path):
    db_path = os.environ["REPO_DB_PATH"]
    conn = sqlite3.connect(db_path)
    ddl = (REPO_ROOT / "infra" / "memory.sql").read_text(encoding="utf-8")
    conn.executescript(ddl)
    # minimal requirements table for app UI
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS requirements(
        id TEXT PRIMARY KEY,
        title TEXT, description TEXT, criteria TEXT,
        priority TEXT, epic TEXT, approved INTEGER DEFAULT 0
    );
    INSERT OR REPLACE INTO requirements(id,title,description,criteria,priority,epic,approved)
    VALUES ('REQ-001','Demo','Desc','Given…\nWhen…\nThen…','P2','Login',0);
    """)
    conn.commit()
    conn.close()
    return db_path

@pytest.fixture
def sample_vtt(tmp_path, monkeypatch):
    vtt = tmp_path / "meeting.vtt"
    vtt.write_text("""WEBVTT

00:00:01.000 --> 00:00:02.000
Alice: Let's align.

00:00:02.000 --> 00:00:04.000
Bob: OTP expires in 10 minutes.
""", encoding="utf-8")
    monkeypatch.setenv("TRANSCRIPT_FILE", str(vtt))
    return str(vtt)