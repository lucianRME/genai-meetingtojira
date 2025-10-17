# app/app.py
from __future__ import annotations

# ⬇️ Add this BEFORE importing agents/review
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import sqlite3
from flask import Flask, render_template_string, redirect, request, flash, get_flashed_messages
from agents.jira_agent import create_from_db  # reuse your Jira sync logic
from review import bp as review_bp            # NEW: register /review blueprint


DB_PATH = os.getenv("REPO_DB_PATH", "repo.db")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev")

# --- NEW: run memory/session migration once at startup ------------------------
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
      .req { border:1px solid #ddd; padding:15px; margin:10px 0; border-radius:8px; background:#fff; }
      .meta { color:#666; font-size:0.9em; margin-bottom:8px; }
      button { background:#0078d7; color:white; border:none; padding:6px 12px; border-radius:4px; cursor:pointer; }
      button:hover { background:#005ea3; }
      .approved { background:#e8ffe8; border-color:#b2dfb2; }
      .sync-btn { margin:20px 0; background:#4CAF50; }
      .banner { padding:10px; border-radius:6px; margin-bottom:15px; }
      .success { background:#e8ffe8; border:1px solid #b2dfb2; color:#2b662b; }
      .error { background:#ffe8e8; border:1px solid #dfb2b2; color:#a33; }
      .fadeout { animation: fadeout 3s forwards; }
      @keyframes fadeout {
        0% {opacity:1;}
        80% {opacity:1;}
        100% {opacity:0; display:none;}
      }
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

    <form action="/sync" method="post">
      <button type="submit" class="sync-btn">🔄 Sync Approved to Jira</button>
    </form>

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
      <form action="/approve/{{r[0]}}" method="post">
        <button>Approve</button>
      </form>
      {% else %}
      <form action="/unapprove/{{r[0]}}" method="post">
        <button style="background:#c0392b;">Unapprove</button>
      </form>
      {% endif %}
    </div>
    {% endfor %}
</body>
</html>
"""

@app.route("/")
def home():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id,title,description,criteria,priority,epic,approved FROM requirements ORDER BY id")
    reqs = cur.fetchall()
    conn.close()
    return render_template_string(TEMPLATE, reqs=reqs, get_flashed_messages=get_flashed_messages)

@app.route("/approve/<req_id>", methods=["POST"])
def approve(req_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE requirements SET approved=1 WHERE id=?", (req_id,))
    conn.commit(); conn.close()
    flash(f"Requirement {req_id} approved ✅", "success")
    return redirect("/")

@app.route("/unapprove/<req_id>", methods=["POST"])
def unapprove(req_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE requirements SET approved=0 WHERE id=?", (req_id,))
    conn.commit(); conn.close()
    flash(f"Requirement {req_id} unapproved ❌", "error")
    return redirect("/")

@app.route("/sync", methods=["POST"])
def sync_to_jira():
    try:
        create_from_db(DB_PATH)
        flash("✅ Jira sync complete — approved items pushed successfully.", "success")
    except Exception as e:
        flash(f"❌ Jira sync failed: {e}", "error")
    return redirect("/")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)