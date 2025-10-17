"""
agents/jira_agent.py

Enhanced Jira synchronization for Synapse:
- Reads requirements and test cases from repo.db
- Creates/updates Jira issues deterministically using labels (and stored jira_key)
- Writes Jira keys back into the DB
- Includes requirement Description + Acceptance Criteria in Stories
- Includes Gherkin in Tasks (code block)
- Links each Test Task to its parent Requirement Story using issueLink

New:
- Optional Memory-aware, LLM-assisted summaries (titles) using infra.memory.prompt_hydrator
  Toggle with env: JIRA_USE_LLM_TITLES (default '1')
"""

import os
import re
import sqlite3
from typing import Dict, Any, Optional, Tuple
import requests

# NEW: memory + LLM helpers
from infra.memory import prompt_hydrator
from generate_req_bdd import _chat, MODEL, TEMPERATURE

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

    def link_issues(self, *, inward_key: str, outward_key: str, link_type: str = "Relates") -> None:
        """
        Create an issue link between inward_key <-link_type-> outward_key.
        For symmetric types like 'Relates', direction doesn't matter.
        If a duplicate link error occurs, it's ignored.
        """
        payload = {
            "type": {"name": link_type},
            "inwardIssue": {"key": inward_key},
            "outwardIssue": {"key": outward_key},
        }
        try:
            self._request("POST", "/rest/api/3/issueLink", json=payload)
        except requests.HTTPError as e:
            # Common duplicate case returns 400; safe to ignore
            status = getattr(e.response, "status_code", None)
            if status == 400:
                print(f"ℹ️ Link {inward_key}—{link_type}—{outward_key} may already exist; skipping.")
            else:
                print(f"ℹ️ Issue link creation skipped for {inward_key} ←{link_type}→ {outward_key}: {e}")


# ---------------- Memory-aware LLM title helpers (optional) ----------------

def _maybe_llm_summary_for_requirement(conn: sqlite3.Connection, project_id: str, session_id: Optional[str],
                                       req_id: str, title: str, description: str, criteria: str) -> Optional[str]:
    """
    Use Memory to craft a concise, action-oriented Story summary.
    Returns None on any failure so caller can fallback.
    """
    if os.getenv("JIRA_USE_LLM_TITLES", "1") != "1":
        return None
    try:
        base_system = (
            "You are a Jira Title Assistant. Follow [Memory] settings (tone, jira_story_prefix). "
            "Write a succinct, action-oriented Story summary (≤ 110 chars). British English."
        )
        system_prompt = prompt_hydrator(conn, base_system_prompt=base_system,
                                        project_id=project_id, session_id=session_id)
        user = (
            "Create a Jira Story summary line for this requirement.\n"
            f"Requirement ID: {req_id}\n"
            f"Original title: {title}\n"
            f"Description: {description}\n"
            f"Acceptance criteria (free text): {criteria}\n"
            "Output ONLY the summary line, no quotes, no extra text."
        )
        resp = _chat(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user}],
            model=MODEL, temperature=max(0.0, min(0.4, TEMPERATURE))
        )
        s = (resp.choices[0].message.content or "").strip().splitlines()[0]
        # hard trim
        return s[:110] if s else None
    except Exception as e:
        print(f"ℹ️ LLM summary skipped for {req_id}: {e}")
        return None

def _maybe_llm_summary_for_test(conn: sqlite3.Connection, project_id: str, session_id: Optional[str],
                                req_id: str, scenario_type: str) -> Optional[str]:
    """
    Use Memory to craft a concise Task title for a test case.
    Returns None on failure so caller can fallback.
    """
    if os.getenv("JIRA_USE_LLM_TITLES", "1") != "1":
        return None
    try:
        base_system = (
            "You are a Jira Title Assistant. Follow [Memory] settings. "
            "Write a succinct Task title for a test case (≤ 110 chars). British English."
        )
        system_prompt = prompt_hydrator(conn, base_system_prompt=base_system,
                                        project_id=project_id, session_id=session_id)
        user = (
            "Create a Jira Task title for a BDD test.\n"
            f"Requirement ID: {req_id}\n"
            f"Scenario type: {scenario_type}\n"
            "Output ONLY the title line."
        )
        resp = _chat(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user}],
            model=MODEL, temperature=0.2
        )
        s = (resp.choices[0].message.content or "").strip().splitlines()[0]
        return s[:110] if s else None
    except Exception as e:
        print(f"ℹ️ LLM test title skipped for {req_id}::{scenario_type}: {e}")
        return None


# ---------------- Main sync ----------------

def create_from_db(db_path: str, *, project_id: Optional[str] = None, session_id: Optional[str] = None):
    """
    Sync requirements & test cases from SQLite to Jira.
    project_id/session_id are optional (used for Memory). If omitted, project_id falls back to env PROJECT_ID.
    """
    if os.getenv("JIRA_INTEGRATION", "1") != "1":
        print("ℹ️ JIRA_INTEGRATION=0 → skipping Jira sync.")
        return

    jira_url = os.environ.get("JIRA_URL")
    jira_user = os.environ.get("JIRA_USER") or os.environ.get("JIRA_EMAIL")
    jira_token = os.environ.get("JIRA_API_TOKEN") or os.environ.get("JIRA_TOKEN")
    jira_project = os.environ.get("JIRA_PROJECT", "SCRUM")
    approved_only = os.getenv("JIRA_APPROVED_ONLY", "1") == "1"
    link_type = os.getenv("JIRA_LINK_TYPE", "Relates")
    project_id = project_id or os.getenv("PROJECT_ID", "primark")

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

    print(f"📤 Syncing {len(req_rows)} requirements to Jira…")
    for rid, req_id, title, description, criteria, jira_key in req_rows:
        if not req_id:
            print(f"= Skip requirement rowid={rid}: missing id")
            continue

        label = _req_label(req_id)

        # DEFAULT deterministic summary
        default_summary = f"[{req_id}] {title or 'Untitled requirement'}"
        # Try Memory-aware LLM summary (optional)
        llm_summary = _maybe_llm_summary_for_requirement(conn, project_id, session_id, req_id, title or "", description, criteria)
        summary = llm_summary or default_summary

        content = [
            _adf_h("Requirement", 2),
            _adf_p(f"ID: {req_id}"),
            _adf_p(f"Title: {title or '—'}"),
        ]
        if description:
            content += [_adf_h("Description", 3), _adf_p(description)]
        if criteria:
            content += [_adf_h("Acceptance Criteria", 3), _adf_p(criteria)]
        if not (description or criteria):
            content.append(_adf_p("No detailed description or criteria provided."))
        content += [_adf_h("Sync", 3), _adf_p("Auto-synced by Synapse pipeline.")]
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

    # Refresh mapping requirement → jira_key after requirement sync
    c.execute("SELECT id, COALESCE(jira_key,'') FROM requirements")
    parent_map = dict(c.fetchall())

    # ---- Test cases (dedup latest per req_id+scenario_type; include Gherkin, parent keys if present) ----
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
                   COALESCE(t.jira_key,'') AS jira_key,
                   COALESCE(r.jira_key,'') AS parent_key
            FROM test_cases t
            JOIN latest l ON l.rowid = t.rowid
            JOIN requirements r ON r.id = t.requirement_id
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
                   COALESCE(t.jira_key,'') AS jira_key,
                   COALESCE(r.jira_key,'') AS parent_key
            FROM test_cases t
            JOIN latest l ON l.rowid = t.rowid
            JOIN requirements r ON r.id = t.requirement_id
            ORDER BY t.rowid
        """)
    tc_rows = c.fetchall()

    print(f"📤 Syncing {len(tc_rows)} test cases to Jira…")
    for tid, req_id, scenario_type, gherkin, jira_key, parent_key in tc_rows:
        if not (req_id and scenario_type):
            print(f"= Skip test rowid={tid}: missing requirement_id/scenario_type")
            continue

        label = _tc_label(req_id, scenario_type)
        ext = f"TC::{req_id}::{scenario_type}"

        # Default deterministic summary
        default_summary = f"[{ext}] {scenario_type.capitalize()} for {req_id}"
        # Memory-aware LLM title (optional)
        llm_summary = _maybe_llm_summary_for_test(conn, project_id, session_id, req_id, scenario_type)
        summary = llm_summary or default_summary

        content = [
            _adf_h("Test Case", 2),
            _adf_p(f"Requirement: {req_id}"),
            _adf_p(f"Scenario type: {scenario_type}"),
            _adf_h("Gherkin", 3),
            _adf_code(gherkin or "", language="gherkin"),
            _adf_h("Sync", 3),
            _adf_p("Auto-synced by Synapse pipeline (BDD/Gherkin)."),
        ]
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

            # Link to parent Story if we have a parent key (or can find one in parent_map)
            parent = parent_key or parent_map.get(req_id, "")
            if parent:
                try:
                    ja.link_issues(inward_key=parent, outward_key=key, link_type=link_type)
                    print(f"🔗 Linked test {key} to story {parent} via '{link_type}'.")
                except requests.HTTPError as e:
                    print(f"ℹ️ Linking skipped for test {key} → story {parent}: {e}")
            else:
                print(f"ℹ️ No parent Jira key found for requirement {req_id}; link skipped.")

        except requests.HTTPError as e:
            print(f"❌ Failed test {ext} ({label}): {e}")

    conn.close()
    print("✅ Jira sync complete.")