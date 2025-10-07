"""
agents/jira_agent.py

Idempotent Jira synchronization for FlowMind:
- Reads requirements and test cases from repo.db
- Creates/updates Jira issues deterministically using labels
- Writes Jira keys back into the DB

SQLite schema expected:
  requirements(
      id TEXT PRIMARY KEY,           -- e.g., 'REQ-001'  (external stable ID)
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
      requirement_id TEXT,           -- e.g., 'REQ-001' (parent requirement.id)
      scenario_type TEXT,            -- e.g., 'positive' | 'negative' | 'regression'
      gherkin TEXT,
      tags TEXT,
      jira_key TEXT
  )

Environment variables supported:
  JIRA_URL                 (required) e.g., https://yourdomain.atlassian.net
  JIRA_USER or JIRA_EMAIL  (required) Jira account email/username for API
  JIRA_API_TOKEN or JIRA_TOKEN (required) personal API token
  JIRA_PROJECT             (optional, default='SCRUM') target project key
  JIRA_INTEGRATION         (optional, '1' to enable; default '1')
  JIRA_APPROVED_ONLY       (optional, '1' to sync only approved requirements; default '1')

Typical use:
  from agents.jira_agent import create_from_db
  create_from_db("repo.db")
"""

import os
import re
import sqlite3
from typing import Dict, Any, Optional, Tuple
import requests


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def _slug(s: str) -> str:
    """Make a short, label-safe slug."""
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] or "na"


def _adf_p(text: str) -> Dict[str, Any]:
    """Create a simple ADF paragraph node."""
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _adf_doc(*nodes) -> Dict[str, Any]:
    """Wrap nodes in an ADF document."""
    return {"type": "doc", "version": 1, "content": list(nodes)}


def _req_label(req_id: str) -> str:
    return f"req-{(req_id or '').lower()}"


def _tc_label(req_id: str, scenario_type: str) -> str:
    return f"tc-{(req_id or '').lower()}-{_slug(scenario_type)}"


# -----------------------------------------------------------------------------
# Jira API helper
# -----------------------------------------------------------------------------

class JiraAgent:
    """Lightweight Jira client with idempotent upsert-by-label semantics."""

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
        """
        Try Jira Cloud v3 search with POST.
        Some tenants return 410 for GET /rest/api/*/search; POST is supported.
        Return the first issue key or None.
        """
        try:
            r = self._request("POST", "/rest/api/3/search",
                              json={"jql": jql, "maxResults": 2})
            issues = r.json().get("issues", [])
            return issues[0]["key"] if issues else None
        except requests.HTTPError as e:
            # If search is unavailable (410/403/etc.), log and continue without search.
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
        """
        Create or update an issue deterministically.
        Preference order:
          1) If existing_key provided (from DB), update it directly.
          2) Else, try to find by label via JQL search (best-effort).
          3) Else, create a new issue.
        Returns (jira_key, created_bool).
        """
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

        # 1) Update by known issue key if we have it
        if existing_key:
            try:
                self._request("PUT", f"/rest/api/3/issue/{existing_key}", json=payload_update)
                return existing_key, False
            except requests.HTTPError as e:
                print(f"ℹ️ Existing key {existing_key} not updatable ({e}). Will try search/create.")

        # 2) Try to locate by label (best-effort)
        jql = f'project = {self.project_key} AND labels = "{label}"'
        found = self._jql_search_one(jql)
        if found:
            self._request("PUT", f"/rest/api/3/issue/{found}", json=payload_update)
            return found, False

        # 3) Create new issue
        r = self._request("POST", "/rest/api/3/issue", json=payload_create)
        return r.json()["key"], True


# -----------------------------------------------------------------------------
# Main sync entrypoint
# -----------------------------------------------------------------------------

def create_from_db(db_path: str):
    """
    Read all requirements & test cases from the local SQLite DB and
    upsert them into Jira. Idempotent (based on deterministic labels).

    Respects:
      - JIRA_INTEGRATION (default '1' → enabled)
      - JIRA_APPROVED_ONLY (default '1' → only approved requirements; tests are included
        when their parent requirement is approved)
    """
    # Integration toggle
    if os.getenv("JIRA_INTEGRATION", "1") != "1":
        print("ℹ️ JIRA_INTEGRATION=0 → skipping Jira sync.")
        return

    # Resolve environment variables and validate
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

    ja = JiraAgent(
        base_url=jira_url,
        email=jira_user,
        api_token=jira_token,
        project_key=jira_project,
    )

    # Open DB
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # ---------------- Requirements ----------------
    if approved_only:
        c.execute("""
            SELECT rowid, id AS req_id, title, COALESCE(jira_key, '')
            FROM requirements
            WHERE COALESCE(approved, 0) = 1
            ORDER BY rowid
        """)
    else:
        c.execute("""
            SELECT rowid, id AS req_id, title, COALESCE(jira_key, '')
            FROM requirements
            ORDER BY rowid
        """)
    req_rows = c.fetchall()

    # ---------------- Test cases ----------------
    # Deduplicate: keep latest per (requirement_id, scenario_type) using rowid
    # If approved_only → include tests only for approved parent requirements
    if approved_only:
        c.execute("""
            WITH latest AS (
              SELECT MAX(tc.rowid) AS rowid
              FROM test_cases tc
              JOIN requirements r ON r.id = tc.requirement_id
              WHERE COALESCE(r.approved, 0) = 1
              GROUP BY tc.requirement_id, tc.scenario_type
            )
            SELECT t.rowid, t.requirement_id AS req_id,
                   t.scenario_type, COALESCE(t.jira_key, '')
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
            SELECT t.rowid, t.requirement_id AS req_id,
                   t.scenario_type, COALESCE(t.jira_key, '')
            FROM test_cases t
            JOIN latest l ON l.rowid = t.rowid
            ORDER BY t.rowid
        """)
    tc_rows = c.fetchall()

    # ---------------- Sync requirements ----------------
    print(f"📤 Syncing {len(req_rows)} requirements to Jira…")
    for rid, req_id, title, jira_key in req_rows:
        if not req_id:
            print(f"= Skip requirement rowid={rid}: missing id")
            continue

        label = _req_label(req_id)
        summary = f"[{req_id}] {title or 'Untitled requirement'}"
        desc = _adf_doc(
            _adf_p(f"Auto-synced by FlowMind – requirement {req_id}."),
            _adf_p("Source: meeting transcript → pipeline → SQLite → Jira.")
        )

        try:
            key, created = ja.upsert_issue(
                label=label,
                summary=summary,
                description_adf=desc,
                issue_type_name="Story",
                existing_key=(jira_key or None)  # prefer direct update if we know the key
            )
            print(f"{'✅ Created' if created else '↻ Updated'} requirement: {key} ({label})")
            if not jira_key or jira_key != key:
                c.execute("UPDATE requirements SET jira_key=? WHERE rowid=?", (key, rid))
                conn.commit()
        except requests.HTTPError as e:
            print(f"❌ Failed requirement {req_id} ({label}): {e}")

    # ---------------- Sync test cases ----------------
    print(f"📤 Syncing {len(tc_rows)} test cases to Jira…")
    for tid, req_id, scenario_type, jira_key in tc_rows:
        if not (req_id and scenario_type):
            print(f"= Skip test rowid={tid}: missing requirement_id/scenario_type")
            continue

        label = _tc_label(req_id, scenario_type)
        ext = f"TC::{req_id}::{scenario_type}"
        summary = f"[{ext}] {scenario_type.capitalize()} for {req_id}"
        desc = _adf_doc(
            _adf_p(f"Auto-synced test case for {req_id}. Scenario: {scenario_type}."),
            _adf_p("Generated by FlowMind pipeline (BDD/Gherkin).")
        )

        try:
            key, created = ja.upsert_issue(
                label=label,
                summary=summary,
                description_adf=desc,
                issue_type_name="Task",
                existing_key=(jira_key or None)  # prefer direct update if we know the key
            )
            print(f"{'✅ Created' if created else '↻ Updated'} test: {key} ({label})")
            if not jira_key or jira_key != key:
                c.execute("UPDATE test_cases SET jira_key=? WHERE rowid=?", (key, tid))
                conn.commit()
        except requests.HTTPError as e:
            print(f"❌ Failed test {ext} ({label}): {e}")

    conn.close()
    print("✅ Jira sync complete.")