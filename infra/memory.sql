-- memory scopes
CREATE TABLE IF NOT EXISTS memory_global(
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS memory_project(
  project_id TEXT,
  key TEXT,
  value TEXT,
  PRIMARY KEY(project_id, key)
);

CREATE TABLE IF NOT EXISTS memory_session(
  session_id TEXT,
  key TEXT,
  value TEXT,
  PRIMARY KEY(session_id, key)
);

-- session context
CREATE TABLE IF NOT EXISTS sessions(
  session_id TEXT PRIMARY KEY,
  project_id TEXT,
  rolling_summary TEXT,
  last_actions_json TEXT,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);