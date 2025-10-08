# app/review.py
from __future__ import annotations
import sqlite3, datetime, os
from flask import Blueprint, render_template, request, redirect, url_for, flash

bp = Blueprint("review", __name__, url_prefix="/review")
DB_PATH = os.getenv("REPO_DB_PATH", "repo.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@bp.route("/")
def index():
    status = request.args.get("status", "draft")  # all|draft|approved
    q = request.args.get("q", "").strip()

    sql = """
      SELECT id, title, COALESCE(approved,0) AS approved,
             COALESCE(status,'draft') AS status,
             COALESCE(description,'') AS description,
             COALESCE(criteria,'') AS criteria,
             COALESCE(review_notes,'') AS review_notes,
             COALESCE(jira_key,'') AS jira_key
      FROM requirements
    """
    where = []
    params = []

    if status == "draft":
        where.append("COALESCE(approved,0) = 0")
    elif status == "approved":
        where.append("COALESCE(approved,0) = 1")

    if q:
        where.append("(id LIKE ? OR title LIKE ? OR description LIKE ? OR criteria LIKE ?)")
        params += [f"%{q}%"] * 4

    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"

    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return render_template("review/index.html", rows=rows, status=status, q=q)

@bp.route("/<req_id>")
def detail(req_id: str):
    conn = get_db()
    req = conn.execute("""
        SELECT id, title, COALESCE(description,'') AS description,
               COALESCE(criteria,'') AS criteria,
               COALESCE(approved,0) AS approved,
               COALESCE(status,'draft') AS status,
               COALESCE(review_notes,'') AS review_notes,
               COALESCE(reviewer,'') AS reviewer,
               COALESCE(reviewed_at,'') AS reviewed_at,
               COALESCE(jira_key,'') AS jira_key
        FROM requirements WHERE id = ?
    """, (req_id,)).fetchone()
    conn.close()
    if not req:
        flash(f"Requirement {req_id} not found", "error")
        return redirect(url_for("review.index"))
    return render_template("review/detail.html", req=req)

@bp.route("/<req_id>", methods=["POST"])
def update(req_id: str):
    approved = 1 if request.form.get("approved") == "on" else 0
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    criteria = request.form.get("criteria", "").strip()
    review_notes = request.form.get("review_notes", "").strip()
    status = "ready" if approved else "draft"
    reviewer = request.form.get("reviewer", "").strip()
    reviewed_at = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    conn = get_db()
    conn.execute("""
        UPDATE requirements
           SET title=?, description=?, criteria=?, review_notes=?,
               approved=?, status=?, reviewer=?, reviewed_at=?
         WHERE id=?
    """, (title, description, criteria, review_notes,
          approved, status, reviewer, reviewed_at, req_id))
    conn.commit(); conn.close()
    flash(f"Saved {req_id} (approved={approved})", "success")
    return redirect(url_for("review.detail", req_id=req_id))

@bp.route("/bulk", methods=["POST"])
def bulk():
    action = request.form.get("action", "")
    ids = request.form.getlist("ids")
    if not ids:
        flash("No items selected", "error")
        return redirect(url_for("review.index"))
    conn = get_db()
    if action == "approve":
        conn.executemany("UPDATE requirements SET approved=1, status='ready' WHERE id=?", [(i,) for i in ids])
        msg = f"Approved {len(ids)} item(s)."
    elif action == "unapprove":
        conn.executemany("UPDATE requirements SET approved=0, status='draft' WHERE id=?", [(i,) for i in ids])
        msg = f"Unapproved {len(ids)} item(s)."
    else:
        msg = "No action performed."
    conn.commit(); conn.close()
    flash(msg, "success")
    return redirect(url_for("review.index"))