from __future__ import annotations
import os
import sqlite3
from flask import Flask, render_template_string, redirect

# IMPORTANT: because we're inside the "app" package now, use a relative import
from .review import bp as review_bp

# The templates are outside the package (../templates), point Flask to them
app = Flask(__name__, template_folder="../templates")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev")

# Mount the richer review UI at /review
app.register_blueprint(review_bp)

DB_PATH = os.getenv("REPO_DB_PATH", "repo.db")

# ---- keep your minimal home page at "/" ----
TEMPLATE = """
<h1>Requirements for Review (Draft only)</h1>
<p><a href="/review/">Open full Review UI</a></p>
{% for r in reqs %}
<div style="border:1px solid #ccc; padding:10px; margin:10px;">
  <h3>{{r[0]}} - {{r[1]}}</h3>
  <p>{{r[2]}}</p>
  <b>Criteria:</b><pre>{{r[3]}}</pre>
  <form action="/approve/{{r[0]}}" method="post">
    <button>Approve</button>
  </form>
</div>
{% endfor %}
"""

@app.route("/")
def home():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id,title,description,criteria FROM requirements WHERE COALESCE(approved,0)=0")
    reqs = cur.fetchall()
    conn.close()
    return render_template_string(TEMPLATE, reqs=reqs)

@app.route("/approve/<req_id>", methods=["POST"])
def approve(req_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE requirements SET approved=1 WHERE id=?", (req_id,))
    conn.commit(); conn.close()
    return redirect("/")

def create_app():
    # If you prefer factory pattern elsewhere
    return app

if __name__ == "__main__":
    # When running directly (rare), still works:
    app.run(host="0.0.0.0", port=5000, debug=True)