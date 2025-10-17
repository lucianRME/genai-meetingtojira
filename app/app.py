# app/app.py
from __future__ import annotations

# Ensure repo root on path
import os, sys

import sqlite3
import subprocess
from pathlib import Path
from flask import (
    Flask, render_template_string, redirect, request, flash,
    get_flashed_messages, make_response, jsonify, url_for
)

# Reuse Jira and review UI
from agents.jira_agent import create_from_db
from app.review import bp as review_bp

# Import session helpers from pipeline
from run_pipeline import (
    get_conn as rp_get_conn,
    ensure_session as rp_ensure_session,
    get_session_snapshot,
    PROJECT_ID,
)

DB_PATH = os.getenv("REPO_DB_PATH", "repo.db")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev")

# --- One-time memory migration ------------------------------------------------
def run_memory_migration_once():
    ddl_path = os.path.join("infra", "memory.sql")
    if os.path.exists(ddl_path):
        conn = sqlite3.connect(DB_PATH)
        with open(ddl_path, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()
        conn.close()
run_memory_migration_once()

# --- Register blueprint -------------------------------------------------------
app.register_blueprint(review_bp)  # exposes /review

TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <title>Synapse – Requirements Review</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 40px; background: #fafafa; }
    h1 { color: #333; }
    .panel { background:#fff; border:1px solid #ddd; border-radius:8px; padding:16px; margin: 0 0 20px 0; }
    .row { display:flex; gap:16px; flex-wrap:wrap; }
    .col { flex:1 1 380px; min-width:300px; }
    pre { white-space: pre-wrap; word-wrap: break-word; }
    .req { border:1px solid #ddd; padding:15px; margin:10px 0; border-radius:8px; background:#fff; }
    .meta { color:#666; font-size:0.9em; margin-bottom:8px; }
    button { background:#0078d7; color:white; border:none; padding:6px 12px; border-radius:4px; cursor:pointer; }
    button:hover { background:#005ea3; }
    .approved { background:#e8ffe8; border-color:#b2dfb2; }
    .sync-btn { margin:10px 8px 0 0; background:#4CAF50; }
    .resume-btn { margin:10px 0 0 0; background:#6c5ce7; }
    .banner { padding:10px; border-radius:6px; margin-bottom:15px; }
    .success { background:#e8ffe8; border:1px solid #b2dfb2; color:#2b662b; }
    .error { background:#ffe8e8; border:1px solid #dfb2b2; color:#a33; }
    .fadeout { animation: fadeout 3s forwards; }
    @keyframes fadeout { 0%{opacity:1;} 80%{opacity:1;} 100%{opacity:0; display:none;} }
    .small { color:#666; font-size: 0.9em; }
    ul.actionlist { list-style: none; padding-left: 0; }
    ul.actionlist li { padding: 4px 0; border-bottom: 1px dashed #eee; }
    .pill { display:inline-block; padding:2px 8px; border-radius:999px; background:#eef; color:#334; font-size:12px; }
  </style>
</head>
<body>
  <h1>Requirements Review</h1>

  {% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
      {% for category, message in messages %}
        <div class="banner {{category}} fadeout">{{message}}</div>
      {% endfor %}
    {% endif %}
  {% endwith %}

  <!-- Session Panel -->
  <div class="panel">
    <div class="row">
      <div class="col">
        <h3>Session</h3>
        <div class="small">
          <div>Session ID: <span class="pill">{{ session_id }}</span></div>
          <div>Project: <b>{{ project_id }}</b></div>
          <div>Updated: {{ snapshot.updated_at or '—' }}</div>
        </div>

        <form action="/sync" method="post" style="display:inline-block">
          <button type="submit" class="sync-btn">🔄 Sync Approved to Jira</button>
        </form>
        <form action="/run" method="post" style="display:inline-block">
          <button type="submit" class="resume-btn">▶️ Resume Last Run</button>
        </form>

        <h4>Recent actions</h4>
        {% if snapshot.rolling_summary %}
          <pre>{{ snapshot.rolling_summary }}</pre>
        {% else %}
          <div class="small">No actions yet.</div>
        {% endif %}
        {% if snapshot.last_actions %}
          <ul class="actionlist">
            {% for a in snapshot.last_actions[-10:] %}
              <li>{{ a.ts }} • {{ a.actor }} • {{ a.action }} {% if a.step %}{{ a.step }}{% elif a.mode %}{{ a.mode }}{% endif %}</li>
            {% endfor %}
          </ul>
        {% endif %}
      </div>

      <div class="col">
        <h3>Transcript summary</h3>
        {% if snapshot.last_transcript_summary %}
          <pre>{{ snapshot.last_transcript_summary[:2000] }}</pre>
          {% if snapshot.last_transcript_summary|length > 2000 %}
            <div class="small">…truncated</div>
          {% endif %}
        {% else %}
          <div class="small">No transcript summary captured yet.</div>
        {% endif %}

        {% if transcript_preview %}
          <h4>Transcript (raw)</h4>
          <pre>{{ transcript_preview }}</pre>
          {% if transcript_truncated %}
            <div class="small">…truncated</div>
          {% endif %}
        {% endif %}
      </div>
    </div>
  </div>

  <!-- Requirements List -->
  {% for r in reqs %}
    <div class="req {% if r[6]==1 %}approved{% endif %}">
      <h3>{{r[0]}} — {{r[1]}}</h3>
      <p>{{r[2]}}</p>
      <b>Criteria:</b>
      <pre>{{r[3]}}</pre>
      <div class="meta">
        Priority: {{r[4] or 'N/A'}} | Epic: {{r[5] or 'N/A'}} | Approved: {{'✅' if r[6]==1 else '❌'}}
      </div>

      {% if r[6]==0 %}
        <form action="/approve/{{r[0]}}" method="post"><button>Approve</button></form>
      {% else %}
        <form action="/unapprove/{{r[0]}}" method="post"><button style="background:#c0392b;">Unapprove</button></form>
      {% endif %}
    </div>
  {% endfor %}
</body>
</html>
"""

def _get_or_create_session(conn_sqlite):
    """Return a valid session_id (cookie or new), and the snapshot."""
    sid = request.cookies.get("session_id")
    if not sid:
        sid = rp_ensure_session(conn_sqlite, PROJECT_ID, None)
    else:
        rp_ensure_session(conn_sqlite, PROJECT_ID, sid)  # ensure row exists
    snap = get_session_snapshot(conn_sqlite, sid) or {}
    return sid, snap

def _get_transcript_preview(sid: str, limit: int = 6000) -> tuple[str, bool]:
    """Read the last transcript path from memory_session and return a safe preview."""
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT value FROM memory_session WHERE session_id=? AND key='last_transcript_path'",
        (sid,)
    ).fetchone()
    conn.close()
    if not row or not row["value"]:
        return "", False
    path = row["value"]
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return "", False
    text = raw.replace("\r\n", "\n")
    truncated = len(text) > limit
    return (text[:limit], truncated)

@app.route("/")
def home():
    conn = rp_get_conn()
    sid, snap = _get_or_create_session(conn)

    # Requirements
    cur = conn.cursor()
    cur.execute("SELECT id,title,description,criteria,priority,epic,approved FROM requirements ORDER BY id")
    reqs = cur.fetchall()
    conn.close()

    # Transcript preview
    preview, truncated = _get_transcript_preview(sid)

    html = render_template_string(
        TEMPLATE,
        reqs=reqs,
        project_id=PROJECT_ID,
        session_id=sid,
        snapshot=snap,
        transcript_preview=preview,
        transcript_truncated=truncated,
        get_flashed_messages=get_flashed_messages,
    )
    resp = make_response(html)
    resp.set_cookie("session_id", sid, max_age=60*60*24*365, samesite="Lax")
    return resp

@app.get("/api/session")
def api_session():
    """JSON session context (handy for front-end polling later)."""
    conn = rp_get_conn()
    sid, snap = _get_or_create_session(conn)
    conn.close()
    resp = make_response(jsonify({"ok": True, "session_id": sid, "session": snap, "project_id": PROJECT_ID}))
    resp.set_cookie("session_id", sid, max_age=60*60*24*365, samesite="Lax")
    return resp

@app.route("/run", methods=["POST"])
def resume_last_run():
    """Resume the agentic pipeline using the current cookie session_id."""
    sid = request.cookies.get("session_id")
    if not sid:
        flash("No session_id cookie present. Refresh the page and try again.", "error")
        return redirect(url_for("home"))

    cmd = [sys.executable, "run_pipeline.py", "--session", sid]
    # If you want to force-include a transcript every time from env:
    transcript = os.getenv("TRANSCRIPT_FILE")
    if transcript:
        cmd += ["--transcript", transcript]

    try:
        subprocess.run(cmd, check=True)
        flash("✅ Agentic pipeline completed (resumed this session).", "success")
    except subprocess.CalledProcessError as e:
        flash(f"❌ Pipeline failed: {e}", "error")

    return redirect(url_for("home"))

@app.route("/approve/<req_id>", methods=["POST"])
def approve(req_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE requirements SET approved=1 WHERE id=?", (req_id,))
    conn.commit(); conn.close()
    flash(f"Requirement {req_id} approved ✅", "success")
    return redirect(url_for("home"))

@app.route("/unapprove/<req_id>", methods=["POST"])
def unapprove(req_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE requirements SET approved=0 WHERE id=?", (req_id,))
    conn.commit(); conn.close()
    flash(f"Requirement {req_id} unapproved ❌", "error")
    return redirect(url_for("home"))

@app.route("/sync", methods=["POST"])
def sync_to_jira():
    try:
        create_from_db(DB_PATH)
        flash("✅ Jira sync complete — approved items pushed successfully.", "success")
    except Exception as e:
        flash(f"❌ Jira sync failed: {e}", "error")
    return redirect(url_for("home"))

@app.get("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    # 0.0.0.0 so Codespaces/containers expose the port
    app.run(host="0.0.0.0", port=5000, debug=True)
