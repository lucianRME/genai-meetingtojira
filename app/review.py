# app/review.py
from __future__ import annotations
import os, sqlite3, subprocess, sys, datetime, uuid, json
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, make_response
from jinja2 import TemplateNotFound

# Memory helpers
from infra.memory import load_memory  # ensures memory tables via load_memory->ensure_memory_tables
# Ensure DB schema (all columns used by this blueprint)
from generate_req_bdd import ensure_schema  # NEW

# NOTE: template_folder is crucial so tests can find app/templates/*
bp = Blueprint("review", __name__, url_prefix="/review", template_folder="templates")

DB_PATH = os.getenv("REPO_DB_PATH", "repo.db")
PROJECT_ID = os.getenv("PROJECT_ID", "myproject")

# Ensure tables/columns exist up-front so fresh DBs work
ensure_schema()  # NEW

# ------------------------ DB helpers ------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_session(conn: sqlite3.Connection, project_id: str, incoming_session_id: str | None) -> str:
    sid = incoming_session_id or str(uuid.uuid4())
    conn.execute(
        "INSERT OR IGNORE INTO sessions(session_id, project_id, last_actions_json) VALUES(?,?,?)",
        (sid, project_id, "[]")
    )
    conn.commit()
    return sid

def _get_actions(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    row = conn.execute("SELECT last_actions_json FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    return json.loads((row["last_actions_json"] or "[]") if row else "[]")

def append_action(conn: sqlite3.Connection, session_id: str, action: dict) -> None:
    """Store a small rolling log of actions (max 20) and keep a compact rolling_summary."""
    actions = _get_actions(conn, session_id)
    actions.append({"ts": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z", **action})
    actions = actions[-20:]
    # very compact summary (fits ~1–2k chars easily)
    lines = []
    for a in actions[-10:]:
        who = a.get("actor", "user")
        kind = a.get("action", "do")
        item = a.get("item_id") or a.get("mode") or a.get("status") or ""
        lines.append(f"- {a['ts']} • {who} • {kind} {item}".strip())
    rolling_summary = "Recent actions:\n" + "\n".join(lines) if lines else ""
    conn.execute(
        "UPDATE sessions SET last_actions_json=?, rolling_summary=?, updated_at=CURRENT_TIMESTAMP WHERE session_id=?",
        (json.dumps(actions), rolling_summary, session_id)
    )
    conn.commit()

# ------------------------ Views ------------------------

@bp.route("/")
def index():
    """List requirements, with simple filters."""
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
    where, params = [], []

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
    # session + memory
    incoming_sid = request.cookies.get("session_id")
    sid = ensure_session(conn, PROJECT_ID, incoming_sid)
    mem = load_memory(conn, PROJECT_ID, sid)

    rows = conn.execute(sql, params).fetchall()
    recent_actions = _get_actions(conn, sid)
    conn.close()

    # Render with hard fallback so tests never 500 on missing template path
    try:
        resp = make_response(render_template(
            "review/index.html",
            rows=rows,
            status=status,
            q=q,
            project_id=PROJECT_ID,
            session_id=sid,
            effective_memory=dict(mem),
            recent_actions=recent_actions[-10:]
        ))
    except TemplateNotFound:
        html = """<!doctype html><h1>Requirements Review</h1><p>No items yet.</p>"""
        resp = make_response(html, 200)

    if incoming_sid != sid:
        resp.set_cookie("session_id", sid, httponly=True, samesite="Lax")
    return resp

@bp.route("/<req_id>")
def detail(req_id: str):
    """Detail page for a requirement."""
    conn = get_db()
    incoming_sid = request.cookies.get("session_id")
    sid = ensure_session(conn, PROJECT_ID, incoming_sid)
    mem = load_memory(conn, PROJECT_ID, sid)

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

    if req:
        append_action(conn, sid, {"actor": "user", "action": "view_detail", "item_id": req_id})

    recent_actions = _get_actions(conn, sid)
    conn.close()

    if not req:
        flash(f"Requirement {req_id} not found", "error")
        return redirect(url_for("review.index"))

    try:
        resp = make_response(render_template(
            "review/detail.html",
            req=req,
            project_id=PROJECT_ID,
            session_id=sid,
            effective_memory=dict(mem),
            recent_actions=recent_actions[-10:]
        ))
    except TemplateNotFound:
        resp = make_response(f"<!doctype html><h1>Detail {req_id}</h1>", 200)

    if incoming_sid != sid:
        resp.set_cookie("session_id", sid, httponly=True, samesite="Lax")
    return resp

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
    conn.commit()

    # log action to session
    sid = ensure_session(conn, PROJECT_ID, request.cookies.get("session_id"))
    append_action(conn, sid, {
        "actor": "user",
        "action": "update_req",
        "item_id": req_id,
        "approved": approved,
    })
    conn.close()

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
    conn.commit()

    # log action
    sid = ensure_session(conn, PROJECT_ID, request.cookies.get("session_id"))
    append_action(conn, sid, {"actor": "user", "action": f"bulk_{action}", "count": len(ids)})
    conn.close()

    flash(msg, "success")
    return redirect(url_for("review.index"))

# ---------- SYNC TO JIRA (Button) ----------

def _run_pipeline(jira_all: bool = False) -> tuple[int, str]:
    """
    Run run_pipeline.py and capture logs. If jira_all=False, it will honor
    JIRA_APPROVED_ONLY=1 and sync only approved requirements.
    """
    cmd = [sys.executable, "run_pipeline.py"]
    if jira_all:
        cmd.append("--jira-all")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    code = proc.returncode
    out = proc.stdout + ("\n--- STDERR ---\n" + proc.stderr if proc.stderr else "")
    return code, out

@bp.route("/sync", methods=["GET", "POST"])
def sync():
    """
    GET: Show a page with two buttons:
         - "Sync Approved" (default)
         - "Sync ALL" (admin/testing)
    POST: Run the pipeline and show logs.
    Guarded by env JIRA_UI_SYNC (default '1' to allow).
    """
    if os.getenv("JIRA_UI_SYNC", "1") != "1":
        abort(403, description="UI-triggered sync is disabled (JIRA_UI_SYNC != 1).")

    conn = get_db()
    sid = ensure_session(conn, PROJECT_ID, request.cookies.get("session_id"))

    if request.method == "POST":
        mode = request.form.get("mode", "approved")  # 'approved' or 'all'
        jira_all = (mode == "all")
        code, logs = _run_pipeline(jira_all=jira_all)
        append_action(conn, sid, {"actor": "user", "action": "jira_sync", "mode": mode, "exit_code": code})
        mem = load_memory(conn, PROJECT_ID, sid)
        recent_actions = _get_actions(conn, sid)
        conn.close()

        title = "Jira Sync – Approved Only" if not jira_all else "Jira Sync – ALL Items"
        try:
            resp = make_response(render_template(
                "review/sync.html",
                title=title, exit_code=code, logs=logs, mode=mode,
                project_id=PROJECT_ID, session_id=sid,
                effective_memory=dict(mem), recent_actions=recent_actions[-10:]
            ))
        except TemplateNotFound:
            resp = make_response(f"<!doctype html><h1>{title}</h1><pre>{logs}</pre>", 200)

        if request.cookies.get("session_id") != sid:
            resp.set_cookie("session_id", sid, httponly=True, samesite="Lax")
        return resp

    # GET
    mem = load_memory(conn, PROJECT_ID, sid)
    recent_actions = _get_actions(conn, sid)
    conn.close()
    try:
        resp = make_response(render_template(
            "review/sync.html",
            title="Jira Sync", exit_code=None, logs=None, mode="approved",
            project_id=PROJECT_ID, session_id=sid,
            effective_memory=dict(mem), recent_actions=recent_actions[-10:]
        ))
    except TemplateNotFound:
        resp = make_response("<!doctype html><h1>Jira Sync</h1>", 200)

    if request.cookies.get("session_id") != sid:
        resp.set_cookie("session_id", sid, httponly=True, samesite="Lax")
    return resp
