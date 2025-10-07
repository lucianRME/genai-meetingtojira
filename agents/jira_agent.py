#!/usr/bin/env python3
import os, json, requests, sqlite3
from dotenv import load_dotenv

load_dotenv()

JIRA_URL = os.getenv("JIRA_URL", "").rstrip("/")
JIRA_USER = os.getenv("JIRA_USER")
JIRA_TOKEN = os.getenv("JIRA_API_TOKEN") or os.getenv("JIRA_TOKEN")
JIRA_PROJECT = os.getenv("JIRA_PROJECT", "SCRUM")
JIRA_INTEGRATION = os.getenv("JIRA_INTEGRATION", "1") == "1"

def _auth():
    return (JIRA_USER, JIRA_TOKEN)

# ---------- ADF helpers ----------
def adf_paragraph(text: str):
    return {"type":"paragraph","content":[{"type":"text","text": text or ""}] if text else []}

def adf_bullet_list(items):
    return {"type":"bulletList","content":[{"type":"listItem","content":[adf_paragraph(str(i))]} for i in (items or []) if str(i).strip()]}

def make_adf_description(heading=None, body=None, bullets=None):
    content=[]
    if heading:
        content.append({"type":"paragraph","content":[{"type":"text","text":heading,"marks":[{"type":"strong"}]}]})
    if body:
        content.append(adf_paragraph(body))
    if bullets:
        content.append(adf_bullet_list(bullets))
    return {"type":"doc","version":1,"content": content or [adf_paragraph("")]}

# ---------- Jira helpers ----------
def get_creatable_issue_types(project_key: str):
    url = f"{JIRA_URL}/rest/api/3/issue/createmeta"
    params = {"projectKeys": project_key, "expand": "projects.issuetypes.fields"}
    r = requests.get(url, auth=_auth(), params=params, timeout=30); r.raise_for_status()
    data = r.json()
    projects = data.get("projects", []) or data.get("values", [])
    if not projects: return []
    return [{"id": t.get("id"), "name": t.get("name")} for t in projects[0].get("issuetypes", [])]

def resolve_issue_type_id(preferred_names=None):
    preferred = preferred_names or ["Story","User Story","Task","Ticket","Issue"]
    avail = get_creatable_issue_types(JIRA_PROJECT)
    if not avail: raise SystemExit(f"No creatable issue types for '{JIRA_PROJECT}'.")
    idx = {t["name"].lower(): t["id"] for t in avail if t.get("id") and t.get("name")}
    for name in preferred:
        if name.lower() in idx: return idx[name.lower()], name
    first = avail[0]; return first["id"], first["name"]

def jql_search_first_key(jql: str):
    url = f"{JIRA_URL}/rest/api/3/search"
    params = {"jql": jql, "maxResults": 1, "fields": "key"}
    r = requests.get(url, auth=_auth(), params=params, timeout=30)
    if r.status_code != 200: return None
    issues = r.json().get("issues", [])
    return issues[0]["key"] if issues else None

def create_or_get_issue(summary, description_text=None, bullets=None, issue_type_hint="Story", labels=None, unique_label=None):
    """
    Idempotent creation: if issue with unique_label exists -> return it; else create.
    """
    labels = (labels or [])[:]
    if unique_label and unique_label not in labels:
        labels.append(unique_label)

    # Idempotency check via JQL on unique label within project
    if unique_label:
        key = jql_search_first_key(f'project = {JIRA_PROJECT} AND labels = "{unique_label}" ORDER BY created DESC')
        if key:
            print(f"↩︎ Reusing existing issue: {key} (label={unique_label})")
            return key

    if not JIRA_INTEGRATION:
        print(f"[SIMULATION] Would create issue in {JIRA_PROJECT}: {summary} ({issue_type_hint}) labels={labels}")
        return f"SIM-{abs(hash(summary))%1000}"

    issue_type_id, chosen_name = resolve_issue_type_id(preferred_names=[issue_type_hint,"Story","Task"])
    url = f"{JIRA_URL}/rest/api/3/issue"
    headers = {"Content-Type": "application/json"}
    adf = make_adf_description(heading="Description", body=description_text, bullets=bullets)

    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT},
            "summary": (summary or "")[:254],
            "issuetype": {"id": issue_type_id},
            "description": adf,
            "labels": list(set(labels or []))
        }
    }

    resp = requests.post(url, auth=_auth(), headers=headers, data=json.dumps(payload), timeout=30)
    if resp.status_code == 201:
        key = resp.json().get("key")
        print(f"✅ Created Jira issue: {key}  (type={chosen_name}, label={unique_label})")
        return key

    print(f"⚠️ Failed to create issue ({resp.status_code}): {resp.text}")
    return None

# ---------- Use from DB (optional) ----------
def create_from_db(db_path="repo.db"):
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ensure jira_key columns exist (best-effort)
    try: cur.execute("ALTER TABLE requirements ADD COLUMN jira_key TEXT")
    except: pass
    try: cur.execute("ALTER TABLE test_cases ADD COLUMN jira_key TEXT")
    except: pass

    # 1) Requirements -> Stories (idempotent)
    for r in cur.execute("SELECT id, title, description, criteria, COALESCE(jira_key,'') as jira_key FROM requirements ORDER BY id"):
        req_id = r["id"]
        if r["jira_key"]:
            print(f"= Skip {req_id}: already linked to {r['jira_key']}")
            continue
        ac = (r["criteria"] or "").split("\n") if r["criteria"] else []
        unique = f"req-{req_id}".lower()  # unique label
        key = create_or_get_issue(
            summary=r["title"] or req_id,
            description_text=r["description"],
            bullets=ac,
            issue_type_hint="Story",
            labels=["flowmind","genai"],
            unique_label=unique
        )
        if key:
            cur.execute("UPDATE requirements SET jira_key=? WHERE id=?", (key, req_id))
            conn.commit()

    # 2) Test cases -> Tasks/Subtasks/etc. (basic example: create Tasks per test)
    for t in cur.execute("SELECT requirement_id, scenario_type, gherkin, COALESCE(jira_key,'') as jira_key FROM test_cases"):
        if t["jira_key"]:
            print(f"= Skip test for {t['requirement_id']}:{t['scenario_type']} already linked to {t['jira_key']}")
            continue
        unique = f"tc-{t['requirement_id']}-{t['scenario_type']}".lower()
        summary = f"Test: {t['scenario_type']} for {t['requirement_id']}"
        key = create_or_get_issue(
            summary=summary,
            description_text=t["gherkin"],
            bullets=None,
            issue_type_hint="Task",
            labels=["flowmind","genai"],
            unique_label=unique
        )
        if key:
            cur.execute(
                "UPDATE test_cases SET jira_key=? WHERE requirement_id=? AND scenario_type=?",
                (key, t["requirement_id"], t["scenario_type"])
            )
            conn.commit()

    conn.close()

if __name__ == "__main__":
    print("▶ Jira integration (idempotent) …")
    # Example single test:
    # create_or_get_issue("FlowMind Test Story", "Created via API.", bullets=["AC1","AC2","AC3"], issue_type_hint="Story", labels=["flowmind","genai"], unique_label="req-REQ-TEST")
    # Or drive from DB:
    create_from_db("repo.db")