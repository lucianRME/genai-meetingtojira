from flask import Flask, render_template_string, redirect
import sqlite3

app = Flask(__name__)

TEMPLATE = """
<h1>Requirements for Review</h1>
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
    conn = sqlite3.connect("repo.db")
    cur = conn.cursor()
    cur.execute("SELECT id,title,description,criteria FROM requirements WHERE approved=0")
    reqs = cur.fetchall()
    conn.close()
    return render_template_string(TEMPLATE, reqs=reqs)

@app.route("/approve/<req_id>", methods=["POST"])
def approve(req_id):
    conn = sqlite3.connect("repo.db")
    cur = conn.cursor()
    cur.execute("UPDATE requirements SET approved=1 WHERE id=?",(req_id,))
    conn.commit(); conn.close()
    return redirect("/")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)