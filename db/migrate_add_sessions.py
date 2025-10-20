# db/migrate_add_sessions.py
import sqlite3, os, time

DB = os.getenv("DB_PATH", "repo.db")

DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  user_label TEXT,            -- optional, e.g. email or nickname
  summary TEXT DEFAULT ''     -- rolling compact summary (<= ~2000 chars)
);

CREATE TABLE IF NOT EXISTS session_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  ts INTEGER NOT NULL,
  action_type TEXT NOT NULL,  -- e.g. 'ingest', 'extract_requirements', 'jira_sync', 'ui_click'
  payload TEXT DEFAULT '',    -- JSON string with small details
  FOREIGN KEY(session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_actions_session_ts ON session_actions(session_id, ts DESC);

-- optional KV for small bits you want to rehydrate quickly (e.g., last_project_id)
CREATE TABLE IF NOT EXISTS session_state (
  session_id TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT,
  PRIMARY KEY (session_id, key),
  FOREIGN KEY(session_id) REFERENCES sessions(session_id)
);
"""

if __name__ == "__main__":
    con = sqlite3.connect(DB)
    con.executescript(DDL)
    con.commit()
    con.close()
    print("✅ sessions tables ready")