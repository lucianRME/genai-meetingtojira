"""
agents/jira_agent.py

Enhanced Jira synchronization for FlowMind:
- Reads requirements and test cases from repo.db
- Creates/updates Jira issues deterministically using labels (and stored jira_key)
- Writes Jira keys back into the DB
- Includes requirement Description + Acceptance Criteria in Stories
- Includes Gherkin in Tasks (code block)

SQLite schema expected:
  requirements(
      id TEXT PRIMARY KEY,
      title TEXT,
      description TEXT,
      criteria TEXT,
      priority TEXT,
      epic TEXT,
      approved INTEGER DEFAULT 0,
      jira_key TEXT
  )

  test_cases(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      requirement_id TEXT,
      scenario_type TEXT,
      gherkin TEXT,
      tags TEXT,
      jira_key TEXT
  )

Env:
  JIRA_URL (req)
  JIRA_USER or JIRA_EMAIL (req)
  JIRA_API_TOKEN or JIRA_TOKEN (req)
  JIRA_PROJECT (opt, default 'SCRUM')
  JIRA_INTEGRATION (opt, '1' default)
  JIRA_APPROVED_ONLY (opt, '1' default)
  JIRA_SKIP_SEARCH (opt, '0' default)  # if '1', never JQL search
"""

import os
import re
import sqlite3
from typing import Dict, Any, Optional, Tuple
import requests


# ---------------- ADF & helpers ----------------

def _slug(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] or "na"

def _adf_p(text: str) -> Dict[str, Any]:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}

def _adf_h(text: str, level: int = 2) -> Dict[str, Any]:
    level = min(max(level,1),6)
    return {"type":"heading","attrs":{"level":level},"content":[{"type":"text","text":text}]}

def _adf_code(code: str, language: str = "gherkin") -> Dict[str, Any]:
    return {"type":"codeBlock","attrs":{"language":language},"content":[{"type":"text","text":code or ""}]}

def _adf_doc(*nodes) -> Dict[str, Any]:
    return {"type": "doc", "version": 1, "content": list(nodes)}

def _req_label(req_id: str) -> str:
    return f"req-{(req_id or '').lower()}"

def _tc_label(req_id: str, scenario_type: str) -> str:
    return f"tc-{(req_id or '').lower()}-{_slug(scenario_type)}"


# ---------------- Jira client ----------------

class JiraAgent:
    def __init__(self, base_url: str, email: str, api_token: str, project_key: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.auth = (email, api_token)
        self.project_key = project_key
        self.timeout = timeout
        self._session = requests.Session()

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        r = self._session.request(method, url, auth=self.auth, timeout=self.timeout, **kwargs)
        r.raise_for_status()
        return r

    def _jql_search_one(self, jql: str) -> Optional[str]:
        if os.getenv("JIRA_SKIP_SEARCH", "0") == "1":
            return None
        try:
            r = self._request("POST", "/rest/api/3/search", json={"jql": jql, "maxResults": 2})
            issues = r.json().get("issues", [])
            return issues[0]["key"] if issues else None
        except requests.HTTPError as e:
            print(f"ℹ️ JQL search unavailable ({e}). Will skip search for this item.")
            return None

    def upsert_issue(
        self,
        *,
        label: str,
        summary: str,
        description_adf: Dict[str, Any],
        issue_type_name: str,
        existing_key: Optional[str] = None
    ) -> Tuple[str, bool]:
        payload_update = {
            "fields": {
                "summary": summary,
                "issuetype": {"name": issue_type_name},
                "labels": [label, "genai-sync"],
                "description": description_adf,
            }
        }
        payload_create = {
            "fields": {
                "project": {"key": self.project_key},
                **payload_update["fields"],
            }
        }

        # 1) Prefer updating by known key
        if existing_key:
            try:
                self._request("PUT", f"/rest/api/3/issue/{existing_key}", json=payload_update)
                return existing_key, False
            except requests.HTTPError as e:
                print(f"ℹ️ Existing key {existing_key} not updatable ({e}). Will try search/create.")

        # 2) Best-effort label search
        jql = f'project = {self.project_key} AND labels = "{label}"'
        found = self._jql_search_one(jql)
        if found:
            self._request("PUT", f"/rest/api/3/issue/{found}", json=payload_update)
            return found, False

        # 3) Create
        r = self._request("POST", "/rest/api/3/issue", json=payload_create)
        return r.json()["key"], True


# ---------------- Main sync ----------------

def create_from_db(db_path: str):
    if os.getenv("JIRA_INTEGRATION", "1") != "1":
        print("ℹ️ JIRA_INTEGRATION=0 → skipping Jira sync.")
        return

    jira_url = os.environ.get("JIRA_URL")
    jira_user = os.environ.get("JIRA_USER") or os.environ.get("JIRA_EMAIL")
    jira_token = os.environ.get("JIRA_API_TOKEN") or os.environ.get("JIRA_TOKEN")
    jira_project = os.environ.get("JIRA_PROJECT", "SCRUM")
    approved_only = os.getenv("JIRA_APPROVED_ONLY", "1") == "1"

    missing = [k for k, v in {
        "JIRA_URL": jira_url,
        "JIRA_USER/JIRA_EMAIL": jira_user,
        "JIRA_API_TOKEN/JIRA_TOKEN": jira_token,
    }.items() if not v]
    if missing:
        raise RuntimeError(f"Missing Jira environment variables: {', '.join(missing)}")

    print(f"🔐 Connecting to Jira project '{jira_project}' as {jira_user}…")
    ja = JiraAgent(jira_url, jira_user, jira_token, jira_project)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # ---- Requirements (include description + criteria) ----
    if approved_only:
        c.execute("""
            SELECT rowid, id AS req_id, title,
                   COALESCE(description,''), COALESCE(criteria,''),
                   COALESCE(jira_key,'')
            FROM requirements
            WHERE COALESCE(approved, 0) = 1
            ORDER BY rowid
        """)
    else:
        c.execute("""
            SELECT rowid, id AS req_id, title,
                   COALESCE(description,''), COALESCE(criteria,''),
                   COALESCE(jira_key,'')
            FROM requirements
            ORDER BY rowid
        """)
    req_rows = c.fetchall()

    # ---- Test cases (dedup latest per req_id+scenario_type; include Gherkin) ----
    if approved_only:
        c.execute("""
            WITH latest AS (
              SELECT MAX(tc.rowid) AS rowid
              FROM test_cases tc
              JOIN requirements r ON r.id = tc.requirement_id
              WHERE COALESCE(r.approved, 0) = 1
              GROUP BY tc.requirement_id, tc.scenario_type
            )
            SELECT t.rowid,
                   t.requirement_id AS req_id,
                   t.scenario_type,
                   COALESCE(t.gherkin,'') AS gherkin,
                   COALESCE(t.jira_key,'') AS jira_key
            FROM test_cases t
            JOIN latest l ON l.rowid = t.rowid
            ORDER BY t.rowid
        """)
    else:
        c.execute("""
            WITH latest AS (
              SELECT MAX(rowid) AS rowid
              FROM test_cases
              GROUP BY requirement_id, scenario_type
            )
            SELECT t.rowid,
                   t.requirement_id AS req_id,
                   t.scenario_type,
                   COALESCE(t.gherkin,'') AS gherkin,
                   COALESCE(t.jira_key,'') AS jira_key
            FROM test_cases t
            JOIN latest l ON l.rowid = t.rowid
            ORDER BY t.rowid
        """)
    tc_rows = c.fetchall()

    # ---- Sync requirements ----
    print(f"📤 Syncing {len(req_rows)} requirements to Jira…")
    for rid, req_id, title, description, criteria, jira_key in req_rows:
        if not req_id:
            print(f"= Skip requirement rowid={rid}: missing id")
            continue

        label = _req_label(req_id)
        summary = f"[{req_id}] {title or 'Untitled requirement'}"

        content = []
        content.append(_adf_h("Requirement", 2))
        content.append(_adf_p(f"ID: {req_id}"))
        content.append(_adf_p(f"Title: {title or '—'}"))
        if description:
            content.append(_adf_h("Description", 3))
            content.append(_adf_p(description))
        if criteria:
            content.append(_adf_h("Acceptance Criteria", 3))
            content.append(_adf_p(criteria))
        if not (description or criteria):
            content.append(_adf_p("No detailed description or criteria provided."))
        content.append(_adf_h("Sync", 3))
        content.append(_adf_p("Auto-synced by FlowMind pipeline."))

        desc = _adf_doc(*content)

        try:
            key, created = ja.upsert_issue(
                label=label,
                summary=summary,
                description_adf=desc,
                issue_type_name="Story",
                existing_key=(jira_key or None)
            )
            print(f"{'✅ Created' if created else '↻ Updated'} requirement: {key} ({label})")
            if not jira_key or jira_key != key:
                c.execute("UPDATE requirements SET jira_key=? WHERE rowid=?", (key, rid))
                conn.commit()
        except requests.HTTPError as e:
            print(f"❌ Failed requirement {req_id} ({label}): {e}")

    # ---- Sync test cases (include Gherkin) ----
    print(f"📤 Syncing {len(tc_rows)} test cases to Jira…")
    for tid, req_id, scenario_type, gherkin, jira_key in tc_rows:
        if not (req_id and scenario_type):
            print(f"= Skip test rowid={tid}: missing requirement_id/scenario_type")
            continue

        label = _tc_label(req_id, scenario_type)
        ext = f"TC::{req_id}::{scenario_type}"
        summary = f"[{ext}] {scenario_type.capitalize()} for {req_id}"

        content = []
        content.append(_adf_h("Test Case", 2))
        content.append(_adf_p(f"Requirement: {req_id}"))
        content.append(_adf_p(f"Scenario type: {scenario_type}"))
        content.append(_adf_h("Gherkin", 3))
        content.append(_adf_code(gherkin or "", language="gherkin"))
        content.append(_adf_h("Sync", 3))
        content.append(_adf_p("Auto-synced by FlowMind pipeline (BDD/Gherkin)."))

        desc = _adf_doc(*content)

        try:
            key, created = ja.upsert_issue(
                label=label,
                summary=summary,
                description_adf=desc,
                issue_type_name="Task",
                existing_key=(jira_key or None)
            )
            print(f"{'✅ Created' if created else '↻ Updated'} test: {key} ({label})")
            if not jira_key or jira_key != key:
                c.execute("UPDATE test_cases SET jira_key=? WHERE rowid=?", (key, tid))
                conn.commit()
        except requests.HTTPError as e:
            print(f"❌ Failed test {ext} ({label}): {e}")

    conn.close()
    print("✅ Jira sync complete.")
