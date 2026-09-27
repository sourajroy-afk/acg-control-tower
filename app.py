"""
ACG Engineering, Shirwal Plant
Lead Time Control Tower

Run:
    pip install -r requirements.txt
    python app.py            ->  http://127.0.0.1:5000

The database and its reference tables are created on first run. All
operational data (jobs, stage progress, delay logs, vendors, purchase
orders) arrives through Data Ops -> upload, or through the bundled
demo dataset, which is loaded through exactly the same ingestion path.
"""
import hashlib
import io
import json
import logging
import os
import re
import secrets
import sqlite3
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from functools import wraps

import pandas as pd
from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import engine as E
from config import (ADMIN_ROLES, BEST_PRACTICES, BUSINESS_CASE,
                    COMPLIANCE_ITEMS, COMPLIANCE_ROLES, DELAY_ALERT_THRESHOLD_DAYS,
                    DELAY_CATEGORIES, DEMO_PASSWORD, DEMO_USERS, EDITABLE_SETTINGS,
                    FIN_ASSUMPTIONS, ITEM_CATEGORIES, JOB_COLUMN_ALIASES,
                    KPI_TARGETS, LEVERS, LOGIN_LOCKOUT_MINUTES, LOGIN_MAX_ATTEMPTS,
                    PILOT_PROOF_POINTS, PO_COLUMNS, PO_COLUMN_ALIASES,
                    QUALITY_HOLD_ROLES, QUOTE_ROLES, RISK_REGISTER,
                    ROLE_LANDING, ROLE_OWNER_LABELS, ROLES, SOURCE_REGISTER, STAGES,
                    UPLOAD_COLUMNS, WRITE_ROLES)

DB = os.environ.get("ACG_DB", "acg.db")
DEMO_PASSWORD = os.environ.get("ACG_DEMO_PASSWORD", DEMO_PASSWORD)
app = Flask(__name__)

_secret = os.environ.get("ACG_SECRET")
if not _secret:
    _secret = secrets.token_hex(32)
    logging.getLogger(__name__).warning(
        "ACG_SECRET is not set - using a random session key generated for this process. "
        "Every worker/restart will invalidate existing sessions. Set ACG_SECRET to a fixed, "
        "secret value before deploying with more than one worker.")
app.secret_key = _secret


# ------------------------------------------------------------------ database
def get_conn():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = get_conn()
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _ensure_column(conn, table, column, decl):
    """Add a column to a table already on disk from an older schema version."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db():
    conn = get_conn()
    with open(os.path.join(os.path.dirname(__file__), "schema.sql")) as f:
        conn.executescript(f.read())
    _ensure_column(conn, "stage_events", "recorded_by_role", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "ingest_log", "uploaded_by", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "job_stages", "quality_hold", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "job_stages", "quality_hold_reason", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "job_stages", "quality_hold_by", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "users", "failed_attempts", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "users", "locked_until", "TEXT")
    if conn.execute("SELECT COUNT(*) c FROM stages").fetchone()["c"] == 0:
        conn.executemany(
            """INSERT INTO stages (seq, name, short_name, acg_weeks, industry_low, industry_high,
                                   best_in_class_low, best_in_class_high, wip_capacity, owner_function)
               VALUES (?,?,?,?,?,?,?,?,?,?)""", STAGES)
    if conn.execute("SELECT COUNT(*) c FROM kpi_targets").fetchone()["c"] == 0:
        conn.executemany(
            """INSERT INTO kpi_targets (metric, unit, current_fy2425, year1, year2, year3, lower_is_better)
               VALUES (?,?,?,?,?,?,?)""", KPI_TARGETS)
    if conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0:
        pw_hash = generate_password_hash(DEMO_PASSWORD)
        conn.executemany(
            "INSERT INTO users (username, name, role, password_hash) VALUES (?,?,?,?)",
            [(u, n, r, pw_hash) for u, n, r in DEMO_USERS])
    conn.commit()
    conn.close()


# --------------------------------------------------------------- webhook alerts
def get_webhook_url(db):
    row = db.execute("SELECT value FROM settings WHERE key='webhook_url'").fetchone()
    return (row["value"] or "").strip() if row else ""


def send_webhook(db, text, event_type="general"):
    """Best-effort Slack/Teams-compatible incoming-webhook post. Never raises
    into the caller - an unreachable webhook must not break a stage sign-off
    or a quality-hold action. Every attempt is logged to alert_log so
    Notifications can answer "did that alert actually fire" without checking
    the channel."""
    url = get_webhook_url(db)
    if not url:
        return False
    ok, error = True, ""
    try:
        req = urllib.request.Request(
            url, data=json.dumps({"text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=3)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as exc:
        ok, error = False, str(exc)
        logging.getLogger(__name__).warning("Webhook alert failed: %s", exc)
    db.execute("INSERT INTO alert_log (event_type, message, success, error) VALUES (?,?,?,?)",
               (event_type, text, 1 if ok else 0, error))
    db.commit()
    return ok


# ------------------------------------------------------------------- accounts
PUBLIC_ENDPOINTS = {"login", "static", "healthz"}


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _api_key_user(token):
    db = get_conn()
    row = db.execute(
        """SELECT k.id key_id, u.id, u.active FROM api_keys k JOIN users u ON u.id=k.user_id
           WHERE k.token_hash=? AND k.active=1 AND u.active=1""", (_hash_token(token),)).fetchone()
    if row:
        db.execute("UPDATE api_keys SET last_used_at=datetime('now') WHERE id=?", (row["key_id"],))
        db.commit()
    db.close()
    return row


@app.before_request
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
        return
    if request.path.startswith("/api/"):
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            if _api_key_user(auth[7:].strip()):
                return None
            return jsonify({"error": "invalid, inactive or revoked API key"}), 401
    if not session.get("user_id"):
        return redirect(url_for("login", next=request.path))


def get_csrf_token():
    tok = session.get("_csrf")
    if not tok:
        tok = secrets.token_hex(16)
        session["_csrf"] = tok
    return tok


@app.before_request
def csrf_protect():
    if request.method != "POST" or request.endpoint in PUBLIC_ENDPOINTS - {"login"}:
        return
    sent = request.form.get("_csrf", "")
    known = session.get("_csrf", "")
    if not known or not secrets.compare_digest(sent, known):
        abort(400, description="This form has expired or was submitted from somewhere else. "
                               "Go back, refresh the page and try again.")


def role_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if session.get("role") not in roles:
                flash("Your role does not have access to that page.", "error")
                return redirect(url_for("dashboard"))
            return fn(*a, **kw)
        return wrapper
    return deco


def current_user():
    if not session.get("user_id"):
        return None
    return {"id": session["user_id"], "username": session["username"],
            "name": session["name"], "role": session["role"]}


@app.route("/healthz")
def healthz():
    try:
        get_conn().execute("SELECT 1").fetchone()
    except Exception as exc:
        return jsonify({"status": "error", "detail": str(exc)}), 503
    return jsonify({"status": "ok"})


@app.route("/login", methods=["GET", "POST"])
def login():
    get_csrf_token()
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        db = get_conn()
        user = db.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()

        if user and user["locked_until"] and datetime.fromisoformat(user["locked_until"]) > datetime.now():
            wait = datetime.fromisoformat(user["locked_until"]) - datetime.now()
            db.close()
            flash(f"Too many failed attempts. Try again in {max(1, wait.seconds // 60)} minute(s).", "error")
            return render_template("login.html", demo_users=DEMO_USERS, demo_password=DEMO_PASSWORD)

        if user and check_password_hash(user["password_hash"], password):
            db.execute("""UPDATE users SET last_login_at=datetime('now'), failed_attempts=0, locked_until=NULL
                          WHERE id=?""", (user["id"],))
            db.commit()
            db.close()
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["name"] = user["name"]
            session["role"] = user["role"]
            nxt = request.args.get("next")
            if nxt and nxt.startswith("/"):
                return redirect(nxt)
            return redirect(url_for(ROLE_LANDING.get(user["role"], "dashboard")))

        if user:
            attempts = user["failed_attempts"] + 1
            lock_sql, lock_val = "", None
            if attempts >= LOGIN_MAX_ATTEMPTS:
                lock_val = (datetime.now() + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)).isoformat()
                lock_sql = ", locked_until=?"
                db.execute(f"UPDATE users SET failed_attempts=?{lock_sql} WHERE id=?",
                           (attempts, lock_val, user["id"]) if lock_val else (attempts, user["id"]))
            else:
                db.execute("UPDATE users SET failed_attempts=? WHERE id=?", (attempts, user["id"]))
            db.commit()
        db.close()
        flash("Incorrect username or password.", "error")
    return render_template("login.html", demo_users=DEMO_USERS, demo_password=DEMO_PASSWORD)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Signed out.", "success")
    return redirect(url_for("login"))


@app.route("/account", methods=["GET", "POST"])
def account():
    if request.method == "POST":
        current_pw = request.form.get("current_password") or ""
        new_pw = request.form.get("new_password") or ""
        confirm_pw = request.form.get("confirm_password") or ""
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if not check_password_hash(user["password_hash"], current_pw):
            flash("Current password is incorrect.", "error")
        elif len(new_pw) < 8:
            flash("New password must be at least 8 characters.", "error")
        elif new_pw != confirm_pw:
            flash("New password and confirmation do not match.", "error")
        else:
            db.execute("UPDATE users SET password_hash=? WHERE id=?",
                       (generate_password_hash(new_pw), user["id"]))
            db.commit()
            flash("Password changed.", "success")
        return redirect(url_for("account"))
    db = get_db()
    keys = db.execute("""SELECT id, label, token_prefix, active, created_at, last_used_at
                         FROM api_keys WHERE user_id=? ORDER BY id DESC""", (session["user_id"],)).fetchall()
    return render_template("account.html", api_keys=keys)


@app.route("/account/api-keys", methods=["POST"])
def create_api_key():
    label = (request.form.get("label") or "").strip() or "Untitled key"
    token = secrets.token_urlsafe(30)
    db = get_db()
    db.execute("""INSERT INTO api_keys (user_id, label, token_hash, token_prefix) VALUES (?,?,?,?)""",
               (session["user_id"], label, _hash_token(token), token[:8]))
    db.commit()
    flash(f"NEWKEY:{token}", "apikey")
    return redirect(url_for("account"))


@app.route("/account/api-keys/<int:key_id>/revoke", methods=["POST"])
def revoke_api_key(key_id):
    db = get_db()
    row = db.execute("SELECT * FROM api_keys WHERE id=? AND user_id=?", (key_id, session["user_id"])).fetchone()
    if row is None:
        flash("That API key does not exist on your account.", "error")
    else:
        db.execute("UPDATE api_keys SET active=0 WHERE id=?", (key_id,))
        db.commit()
        flash(f"API key '{row['label']}' revoked.", "success")
    return redirect(url_for("account"))


@app.context_processor
def inject_globals():
    csrf = {"csrf_token": get_csrf_token}
    if not session.get("user_id"):
        return {"current_user": None, "source_register": SOURCE_REGISTER, **csrf}
    db = get_db()
    kpis = E.compute_kpis(db)
    spine = E.stage_actuals(db)
    _, action_count = E.action_board(db)
    return {
        "today_str": date.today().strftime("%d %b %Y"),
        "spine": spine,
        "spine_total": round(sum(s["live"] for s in spine), 1),
        "spine_bench": round(sum(s["benchmark"] for s in spine), 1),
        "g_kpis": kpis,
        "has_data": kpis["jobs_total"] > 0,
        "current_user": current_user(),
        "is_admin": session.get("role") in ADMIN_ROLES,
        "can_write": session.get("role") in WRITE_ROLES,
        "can_hold": session.get("role") in QUALITY_HOLD_ROLES,
        "g_action_count": action_count,
        "source_register": SOURCE_REGISTER,
        **csrf,
    }


# ----------------------------------------------------------------- ingestion
def _clean(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (pd.Timestamp, datetime, date)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _truthy(v):
    return str(v).strip().lower() in ("y", "yes", "true", "1", "1.0") if v is not None else False


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _build_alias_lookup(aliases):
    lookup = {}
    for canon, alts in aliases.items():
        lookup[canon] = canon
        for a in alts:
            lookup[a] = canon
    return lookup


JOB_ALIAS_LOOKUP = _build_alias_lookup(JOB_COLUMN_ALIASES)
PO_ALIAS_LOOKUP = _build_alias_lookup(PO_COLUMN_ALIASES)


def _norm_header(c, lookup):
    c = re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()).strip("_")
    return lookup.get(c, c)


def normalise_columns(df, kind="jobs"):
    """Map whatever the export called its columns onto the canonical names
    for this upload kind, without dropping anything it did not recognise."""
    lookup = PO_ALIAS_LOOKUP if kind == "pos" else JOB_ALIAS_LOOKUP
    df.columns = [_norm_header(c, lookup) for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()]
    return df


def read_table(file_storage, kind="jobs"):
    name = (file_storage.filename or "").lower()
    data = file_storage.read()
    if name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(data), dtype=str)
    else:
        df = pd.read_csv(io.BytesIO(data), dtype=str, sep=None, engine="python")
    return normalise_columns(df, kind)


def _reject(s, row, row_no, reason):
    """Keep the failing row and the reason, for the rejected-rows download."""
    s["errors"].append(f"Row {row_no}: {reason}")
    try:
        raw = json.dumps({k: (None if pd.isna(v) else str(v)) for k, v in dict(row).items()})
    except Exception:
        raw = ""
    s["rejects"].append({"row_no": row_no, "reason": reason, "raw": raw})


def _date_or_reject(row, field, s, row_no, required=False):
    """Returns (value, ok). Rejects the row only if the date is unreadable,
    or missing when it is required."""
    try:
        d = E.parse_any_date(_clean(row.get(field)))
    except ValueError as exc:
        _reject(s, row, row_no, f"{field}: {exc}")
        return None, False
    if required and d is None:
        _reject(s, row, row_no, f"{field} is required and empty")
        return None, False
    return d, True


def ingest_jobs(df):
    """One row = one job's progress on one stage."""
    db = get_db()
    s = {"rows_read": len(df), "jobs_added": 0, "jobs_updated": 0, "jobs_unchanged": 0,
         "stages_added": 0, "stages_updated": 0, "stages_duplicate": 0,
         "delay_added": 0, "delay_duplicate": 0, "rejected": 0, "errors": [], "rejects": []}
    df = df.reset_index(drop=True)
    row_no_of = {}

    missing = [c for c in ("job_no", "customer", "equipment_type", "order_date") if c not in df.columns]
    if missing:
        s["errors"].append("Missing required column(s): " + ", ".join(missing))
        return s

    stages = db.execute("SELECT * FROM stages ORDER BY seq").fetchall()
    by_seq = {r["seq"]: r for r in stages}
    by_name = {r["name"].strip().lower(): r for r in stages}
    total_bench = sum(r["acg_weeks"] for r in stages)

    for i in range(len(df)):
        row_no_of[i] = i + 2          # +2 so it matches the spreadsheet line

    blank = df[df["job_no"].map(_clean).isna()]
    for idx, r in blank.iterrows():
        _reject(s, r, row_no_of.get(idx, 0), "job_no is empty")

    for job_no, group in df.groupby(df["job_no"].map(_clean)):
        if not job_no:
            continue
        first = group.iloc[0]
        first_no = row_no_of.get(group.index[0], 0)
        customer = _clean(first.get("customer"))
        equip = _clean(first.get("equipment_type"))
        priority = _clean(first.get("priority")) or "Standard"
        value = _num(_clean(first.get("order_value_lakh")))
        order_date, ok = _date_or_reject(first, "order_date", s, first_no, required=True)
        if not ok:
            continue
        committed, ok = _date_or_reject(first, "committed_dispatch_date", s, first_no)
        if not ok:
            committed = None
        committed = committed or order_date + timedelta(weeks=total_bench)

        row = db.execute("SELECT * FROM jobs WHERE job_no=?", (job_no,)).fetchone()
        if row is None:
            cur = db.execute(
                """INSERT INTO jobs (job_no, customer, equipment_type, order_value_lakh, priority,
                                     order_date, committed_dispatch_date, status)
                   VALUES (?,?,?,?,?,?,?, 'In Progress')""",
                (job_no, customer, equip, value, priority, order_date.isoformat(), committed.isoformat()))
            job_id = cur.lastrowid
            s["jobs_added"] += 1
        else:
            job_id = row["id"]
            changed = ((row["customer"] or "") != (customer or "")
                       or (row["equipment_type"] or "") != (equip or "")
                       or round(row["order_value_lakh"] or 0, 2) != round(value, 2)
                       or (row["priority"] or "") != priority
                       or row["order_date"] != order_date.isoformat()
                       or row["committed_dispatch_date"] != committed.isoformat())
            if changed:
                db.execute("""UPDATE jobs SET customer=?, equipment_type=?, order_value_lakh=?, priority=?,
                              order_date=?, committed_dispatch_date=? WHERE id=?""",
                           (customer, equip, value, priority, order_date.isoformat(),
                            committed.isoformat(), job_id))
                s["jobs_updated"] += 1
            else:
                s["jobs_unchanged"] += 1

        # default plan: benchmark durations chained from the order date
        plan, cursor = {}, order_date
        for st in stages:
            plan[st["seq"]] = (cursor, cursor + timedelta(weeks=st["acg_weeks"]))
            cursor = plan[st["seq"]][1]

        for idx, r in group.iterrows():
            rn = row_no_of.get(idx, 0)
            seq_raw, name_raw = _clean(r.get("stage_seq")), _clean(r.get("stage_name"))
            st = None
            if seq_raw:
                try:
                    st = by_seq.get(int(float(seq_raw)))
                except (ValueError, TypeError):
                    st = None
            if st is None and name_raw:
                st = by_name.get(name_raw.strip().lower())
            if st is None:
                _reject(s, r, rn, f"stage '{seq_raw or name_raw}' is not one of the six process stages")
                continue

            p_start, p_end = plan[st["seq"]]
            dates, ok = {}, True
            for field in ("planned_start", "planned_end", "actual_start", "actual_end"):
                dates[field], good = _date_or_reject(r, field, s, rn)
                ok = ok and good
            if not ok:
                continue
            ps = dates["planned_start"] or p_start
            pe = dates["planned_end"] or p_end
            a_start, a_end = dates["actual_start"], dates["actual_end"]
            if a_start and a_end and a_end < a_start:
                _reject(s, r, rn, f"{st['name']}: actual_end {a_end} is before actual_start {a_start}")
                continue

            status = "Complete" if a_end else ("In Progress" if a_start else "Pending")
            rework = 1 if _truthy(_clean(r.get("rework"))) else 0
            rw_h = _num(_clean(r.get("rework_hours")))
            rw_c = _num(_clean(r.get("rework_cost_lakh")))
            vals = (ps.isoformat(), pe.isoformat(),
                    a_start.isoformat() if a_start else None,
                    a_end.isoformat() if a_end else None,
                    status, rework, round(rw_h, 2), round(rw_c, 2))

            ex = db.execute("SELECT * FROM job_stages WHERE job_id=? AND stage_id=?",
                            (job_id, st["id"])).fetchone()
            if ex is None:
                cur = db.execute(
                    """INSERT INTO job_stages (job_id, stage_id, planned_start, planned_end, actual_start,
                       actual_end, status, rework, rework_hours, rework_cost_lakh)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""", (job_id, st["id"], *vals))
                js_id = cur.lastrowid
                s["stages_added"] += 1
            else:
                js_id = ex["id"]
                cmp_ex = (ex["planned_start"], ex["planned_end"], ex["actual_start"], ex["actual_end"],
                          ex["status"], ex["rework"], round(ex["rework_hours"] or 0, 2),
                          round(ex["rework_cost_lakh"] or 0, 2))
                if cmp_ex == vals:
                    s["stages_duplicate"] += 1
                else:
                    db.execute("""UPDATE job_stages SET planned_start=?, planned_end=?, actual_start=?,
                                  actual_end=?, status=?, rework=?, rework_hours=?, rework_cost_lakh=?
                                  WHERE id=?""", (*vals, js_id))
                    s["stages_updated"] += 1

            cat, reason = _clean(r.get("delay_category")), _clean(r.get("delay_reason"))
            days = _num(_clean(r.get("delay_days")))
            if cat and reason and days > 0:
                dup = db.execute("""SELECT id FROM delay_logs WHERE job_stage_id=? AND category=?
                                    AND reason=? AND delay_days=?""", (js_id, cat, reason, days)).fetchone()
                if dup:
                    s["delay_duplicate"] += 1
                else:
                    db.execute("INSERT INTO delay_logs (job_stage_id, category, reason, delay_days) VALUES (?,?,?,?)",
                               (js_id, cat, reason, days))
                    s["delay_added"] += 1

        now = db.execute("SELECT status, actual_end FROM job_stages WHERE job_id=?", (job_id,)).fetchall()
        if len(now) == len(stages) and all(x["status"] == "Complete" for x in now):
            db.execute("UPDATE jobs SET status='Dispatched', actual_dispatch_date=? WHERE id=?",
                       (max(x["actual_end"] for x in now), job_id))
        else:
            db.execute("UPDATE jobs SET status='In Progress', actual_dispatch_date=NULL WHERE id=?", (job_id,))

    db.commit()
    return s


def ingest_pos(df):
    """One row = one purchase order line."""
    db = get_db()
    s = {"rows_read": len(df), "vendors_added": 0, "pos_added": 0,
         "pos_updated": 0, "pos_duplicate": 0, "rejected": 0, "errors": [], "rejects": []}
    df = df.reset_index(drop=True)
    missing = [c for c in ("po_no", "vendor", "item", "po_date", "promised_date") if c not in df.columns]
    if missing:
        s["errors"].append("Missing required column(s): " + ", ".join(missing))
        return s

    for idx, r in df.iterrows():
        rn = idx + 2
        po_no = _clean(r.get("po_no"))
        vendor = _clean(r.get("vendor"))
        if not po_no or not vendor:
            _reject(s, r, rn, "po_no or vendor is empty")
            continue
        cat = _clean(r.get("item_category")) or "General"
        loc = _clean(r.get("vendor_location")) or ""
        single = 1 if _truthy(_clean(r.get("single_source"))) else 0

        v = db.execute("SELECT * FROM vendors WHERE name=?", (vendor,)).fetchone()
        if v is None:
            cur = db.execute("INSERT INTO vendors (name, item_category, location, single_source) VALUES (?,?,?,?)",
                             (vendor, cat, loc, single))
            vendor_id = cur.lastrowid
            s["vendors_added"] += 1
        else:
            vendor_id = v["id"]

        job_no = _clean(r.get("job_no"))
        job = db.execute("SELECT id FROM jobs WHERE job_no=?", (job_no,)).fetchone() if job_no else None
        po_date, ok1 = _date_or_reject(r, "po_date", s, rn, required=True)
        promised, ok2 = _date_or_reject(r, "promised_date", s, rn, required=True)
        received, ok3 = _date_or_reject(r, "received_date", s, rn)
        if not (ok1 and ok2 and ok3):
            continue

        vals = (job["id"] if job else None, vendor_id, _clean(r.get("item")), cat,
                1 if _truthy(_clean(r.get("critical"))) else 0, _num(_clean(r.get("value_lakh"))),
                po_date.isoformat(), promised.isoformat(), received.isoformat() if received else None)

        ex = db.execute("SELECT * FROM purchase_orders WHERE po_no=?", (po_no,)).fetchone()
        if ex is None:
            db.execute("""INSERT INTO purchase_orders (po_no, job_id, vendor_id, item, item_category,
                          critical, value_lakh, po_date, promised_date, received_date)
                          VALUES (?,?,?,?,?,?,?,?,?,?)""", (po_no, *vals))
            s["pos_added"] += 1
        else:
            cmp_ex = (ex["job_id"], ex["vendor_id"], ex["item"], ex["item_category"], ex["critical"],
                      round(ex["value_lakh"] or 0, 2), ex["po_date"], ex["promised_date"], ex["received_date"])
            cmp_new = (vals[0], vals[1], vals[2], vals[3], vals[4], round(vals[5], 2), vals[6], vals[7], vals[8])
            if cmp_ex == cmp_new:
                s["pos_duplicate"] += 1
            else:
                db.execute("""UPDATE purchase_orders SET job_id=?, vendor_id=?, item=?, item_category=?,
                              critical=?, value_lakh=?, po_date=?, promised_date=?, received_date=?
                              WHERE po_no=?""", (*vals, po_no))
                s["pos_updated"] += 1
    db.commit()
    return s


def log_ingest(filename, kind, s, uploaded_by=""):
    db = get_db()
    added = s.get("jobs_added", 0) + s.get("stages_added", 0) + s.get("pos_added", 0) + s.get("vendors_added", 0)
    updated = s.get("jobs_updated", 0) + s.get("stages_updated", 0) + s.get("pos_updated", 0)
    dup = s.get("stages_duplicate", 0) + s.get("delay_duplicate", 0) + s.get("pos_duplicate", 0)
    rejects = s.get("rejects", [])
    cur = db.execute("""INSERT INTO ingest_log (filename, kind, rows_read, added, updated, duplicates, errors,
                        uploaded_by) VALUES (?,?,?,?,?,?,?,?)""",
                     (filename, kind, s.get("rows_read", 0), added, updated, dup, len(rejects), uploaded_by))
    batch = cur.lastrowid
    for rj in rejects:
        db.execute("INSERT INTO ingest_rejects (ingest_id, row_no, reason, raw) VALUES (?,?,?,?)",
                   (batch, rj["row_no"], rj["reason"], rj["raw"]))
    db.commit()
    return batch


# --------------------------------------------------------- floor sign-off
def recompute_job_status(db, job_id):
    """A job is dispatched only when all six stages are complete."""
    n_stages = db.execute("SELECT COUNT(*) c FROM stages").fetchone()["c"]
    rows = db.execute("SELECT status, actual_end FROM job_stages WHERE job_id=?", (job_id,)).fetchall()
    if len(rows) == n_stages and all(r["status"] == "Complete" for r in rows):
        db.execute("UPDATE jobs SET status='Dispatched', actual_dispatch_date=? WHERE id=?",
                   (max(r["actual_end"] for r in rows), job_id))
    else:
        db.execute("UPDATE jobs SET status='In Progress', actual_dispatch_date=NULL WHERE id=?", (job_id,))


def default_plan(db, order_date):
    """Benchmark durations chained from the order date, used when a stage row
    has to be created on the floor rather than by upload."""
    plan, cursor = {}, order_date
    for st in db.execute("SELECT * FROM stages ORDER BY seq").fetchall():
        plan[st["id"]] = (cursor, cursor + timedelta(weeks=st["acg_weeks"]))
        cursor = plan[st["id"]][1]
    return plan


def record_stage_event(db, job_id, stage_id, form, who, source="floor", role=""):
    """
    Single write path for a stage sign-off. Same validation the upload uses:
    dates must parse, a completion cannot precede its start, and a stage
    cannot complete before the one before it started.
    Returns (message, category).
    """
    job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    stage = db.execute("SELECT * FROM stages WHERE id=?", (stage_id,)).fetchone()
    if not job or not stage:
        return "That order or stage no longer exists.", "error"

    try:
        a_start = E.parse_any_date(form.get("actual_start") or None)
        a_end = E.parse_any_date(form.get("actual_end") or None)
    except ValueError as exc:
        return f"Check the date: {exc}", "error"

    today = date.today()
    for label, d in (("start", a_start), ("completion", a_end)):
        if d and d > today:
            return f"The {label} date is in the future. Record work on the day it happens.", "error"
    if a_start and a_end and a_end < a_start:
        return "Completion date is before the start date.", "error"
    if not a_start and not a_end:
        return "Enter a start date, a completion date, or both.", "error"

    row = db.execute("SELECT * FROM job_stages WHERE job_id=? AND stage_id=?", (job_id, stage_id)).fetchone()
    if row is None:
        plan = default_plan(db, E.parse_date(job["order_date"]))
        ps, pe = plan[stage_id]
        cur = db.execute("""INSERT INTO job_stages (job_id, stage_id, planned_start, planned_end, status)
                            VALUES (?,?,?,?, 'Pending')""", (job_id, stage_id, ps.isoformat(), pe.isoformat()))
        row = db.execute("SELECT * FROM job_stages WHERE id=?", (cur.lastrowid,)).fetchone()

    prev = db.execute("""SELECT js.actual_start FROM job_stages js JOIN stages s ON s.id=js.stage_id
                         WHERE js.job_id=? AND s.seq=?""", (job_id, stage["seq"] - 1)).fetchone()
    if stage["seq"] > 1 and a_end and (prev is None or not prev["actual_start"]):
        return f"Stage {stage['seq'] - 1} has not started yet. Record it first.", "error"

    if a_end and row["quality_hold"]:
        return (f"{stage['name']} is on quality hold ({row['quality_hold_reason'] or 'no reason given'}). "
                f"Ask Quality to release it before this stage can complete."), "error"

    was = row["status"]
    new_start = a_start.isoformat() if a_start else row["actual_start"]
    new_end = a_end.isoformat() if a_end else row["actual_end"]
    status = "Complete" if new_end else ("In Progress" if new_start else "Pending")

    rework = 1 if form.get("rework") else 0
    rw_h = _num(form.get("rework_hours"))
    rw_c = _num(form.get("rework_cost_lakh"))
    if rework and not (rw_h or rw_c):
        rw_h = rw_h or 0

    db.execute("""UPDATE job_stages SET actual_start=?, actual_end=?, status=?,
                  rework=?, rework_hours=?, rework_cost_lakh=? WHERE id=?""",
               (new_start, new_end, status, rework or row["rework"],
                rw_h or (row["rework_hours"] or 0), rw_c or (row["rework_cost_lakh"] or 0), row["id"]))

    action = "Completed" if status == "Complete" else ("Started" if status == "In Progress" else "Updated")
    if was == status:
        action = "Corrected"
    detail = f"{stage['name']}: start {new_start or '-'}, end {new_end or '-'}"
    db.execute("""INSERT INTO stage_events (job_stage_id, action, detail, recorded_by, recorded_by_role, source)
                  VALUES (?,?,?,?,?,?)""", (row["id"], action, detail, who, role, source))

    cat, reason = (form.get("delay_category") or "").strip(), (form.get("delay_reason") or "").strip()
    days = _num(form.get("delay_days"))
    logged_delay = False
    if cat and reason and days > 0:
        dup = db.execute("""SELECT id FROM delay_logs WHERE job_stage_id=? AND category=? AND reason=?
                            AND delay_days=?""", (row["id"], cat, reason, days)).fetchone()
        if not dup:
            db.execute("INSERT INTO delay_logs (job_stage_id, category, reason, delay_days) VALUES (?,?,?,?)",
                       (row["id"], cat, reason, days))
            db.execute("""INSERT INTO stage_events (job_stage_id, action, detail, recorded_by, recorded_by_role,
                          source) VALUES (?,?,?,?,?,?)""",
                       (row["id"], "Delay logged", f"{cat}: {reason} (+{days:g}d)", who, role, source))
            logged_delay = True

    recompute_job_status(db, job_id)
    db.commit()

    if logged_delay and days >= DELAY_ALERT_THRESHOLD_DAYS:
        send_webhook(db, f":warning: Delay logged on {job['job_no']} ({stage['name']}): "
                         f"*{days:g} days* - {cat}: {reason}. Recorded by {who}.",
                    event_type="delay")

    msg = f"{job['job_no']} - {stage['name']} recorded as {status.lower()} by {who}."
    if rework:
        msg += " Rework logged."
    if logged_delay:
        msg += f" Delay of {days:g} days tagged to {cat}."
    return msg, "success"


# --------------------------------------------------------------------- views
@app.route("/")
def dashboard():
    db = get_db()
    kpis = E.compute_kpis(db)
    board = E.job_board(db)
    summary = E.risk_summary(board)
    par, total_delay = E.pareto(db)
    actions, action_count = E.action_board(db)
    trend = E.lead_time_trend(db)
    bottlenecks = E.bottleneck_ranking(db)
    proc, _ = E.procurement_summary(db)
    my_labels = ROLE_OWNER_LABELS.get(session.get("role"), [])
    my_actions, my_action_count = E.actions_for_role(db, my_labels)
    return render_template("dashboard.html", kpis=kpis, board=board[:8], summary=summary,
                           pareto=par, total_delay=total_delay, actions=actions,
                           action_count=action_count, trend=trend, bottlenecks=bottlenecks,
                           proc=proc, reasons=E.top_reasons(db, 5),
                           my_actions=my_actions, my_action_count=my_action_count)


SORTS = {
    "variance": ("Days late", lambda r: -r["variance_days"]),
    "value": ("Order value", lambda r: -(r["job"]["order_value_lakh"] or 0)),
    "committed": ("Committed date", lambda r: r["committed"]),
    "order_date": ("Order date", lambda r: r["job"]["order_date"]),
    "customer": ("Customer", lambda r: (r["job"]["customer"] or "").lower()),
    "delay": ("Delay days logged", lambda r: -r["delay_days"]),
}
PAGE_SIZE = 50


@app.route("/jobs")
def jobs_list():
    db = get_db()
    status = request.args.get("status", "open")
    risk = request.args.get("risk", "all")
    search = (request.args.get("search") or "").strip()
    sort = request.args.get("sort", "variance")
    if sort not in SORTS:
        sort = "variance"
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1

    board = E.job_board(db, only_open=False)
    rows = []
    for r in board:
        j = r["job"]
        if status == "open" and j["status"] != "In Progress":
            continue
        if status == "dispatched" and j["status"] != "Dispatched":
            continue
        if risk != "all" and r["risk"] != risk:
            continue
        if search:
            hay = f"{j['job_no']} {j['customer']} {j['equipment_type']}".lower()
            if search.lower() not in hay:
                continue
        rows.append(r)

    rows.sort(key=SORTS[sort][1])
    total = len(rows)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    window = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]

    return render_template("jobs.html", rows=window, status=status, risk=risk, search=search,
                           sort=sort, sorts=SORTS, page=page, pages=pages, total=total,
                           shown=len(window), page_size=PAGE_SIZE,
                           summary=E.risk_summary([r for r in board if r["job"]["status"] == "In Progress"]))


@app.route("/jobs/<int:job_id>")
def job_detail(job_id):
    db = get_db()
    job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if job is None:
        flash("That job number is not in the system.", "error")
        return redirect(url_for("jobs_list"))
    stages = db.execute(
        """SELECT js.*, s.name stage_name, s.seq stage_seq, s.acg_weeks, s.owner_function
           FROM job_stages js JOIN stages s ON s.id=js.stage_id WHERE js.job_id=? ORDER BY s.seq""",
        (job_id,)).fetchall()
    logs = {}
    for s in stages:
        logs[s["id"]] = db.execute("SELECT * FROM delay_logs WHERE job_stage_id=?", (s["id"],)).fetchall()
    pos = db.execute("""SELECT p.*, v.name vendor FROM purchase_orders p JOIN vendors v ON v.id=p.vendor_id
                        WHERE p.job_id=? ORDER BY p.promised_date""", (job_id,)).fetchall()
    forecast = next((r for r in E.job_board(db, only_open=False) if r["job"]["id"] == job_id), None)

    # plan vs actual bars, scaled against the job's own timeline
    anchor = E.parse_date(job["order_date"])
    span = max(1, (max([E.parse_date(s["planned_end"]) for s in stages if s["planned_end"]] +
                       [E.parse_date(s["actual_end"]) for s in stages if s["actual_end"]] +
                       [date.today()]) - anchor).days)
    bars = []
    for s in stages:
        ps, pe = E.parse_date(s["planned_start"]), E.parse_date(s["planned_end"])
        a_s, a_e = E.parse_date(s["actual_start"]), E.parse_date(s["actual_end"])
        bar = {"name": s["stage_name"], "seq": s["stage_seq"], "status": s["status"],
               "plan_left": 100 * (ps - anchor).days / span if ps else 0,
               "plan_w": max(1.0, 100 * ((pe - ps).days if ps and pe else 0) / span),
               "act_left": 100 * (a_s - anchor).days / span if a_s else None,
               "act_w": max(1.0, 100 * (((a_e or date.today()) - a_s).days if a_s else 0) / span),
               "slip": (a_e - pe).days if a_e and pe else None}
        bars.append(bar)
    events = db.execute(
        """SELECT se.*, s.name AS stage_name FROM stage_events se
           JOIN job_stages js ON js.id=se.job_stage_id
           JOIN stages s ON s.id=js.stage_id
           WHERE js.job_id=? ORDER BY se.id DESC LIMIT 12""", (job_id,)).fetchall()
    all_stages = db.execute("SELECT * FROM stages ORDER BY seq").fetchall()
    next_stage = next((s for s in stages if s["status"] != "Complete"), None)
    return render_template("job_detail.html", job=job, stages=stages, logs=logs, pos=pos,
                           forecast=forecast, bars=bars, events=events, all_stages=all_stages,
                           next_stage=next_stage, categories=DELAY_CATEGORIES,
                           today=date.today().isoformat())


@app.route("/jobs/<int:job_id>/stages/<int:stage_id>/hold", methods=["POST"])
@role_required(*QUALITY_HOLD_ROLES)
def toggle_quality_hold(job_id, stage_id):
    db = get_db()
    row = db.execute("SELECT * FROM job_stages WHERE job_id=? AND stage_id=?", (job_id, stage_id)).fetchone()
    if row is None:
        flash("That stage has no record yet - nothing to hold.", "error")
        return redirect(url_for("job_detail", job_id=job_id))
    who = f"{session['name']} ({session['role']})"
    stage = db.execute("SELECT name FROM stages WHERE id=?", (stage_id,)).fetchone()
    job = db.execute("SELECT job_no FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row["quality_hold"]:
        db.execute("""UPDATE job_stages SET quality_hold=0, quality_hold_reason='', quality_hold_by=''
                      WHERE id=?""", (row["id"],))
        db.execute("""INSERT INTO stage_events (job_stage_id, action, detail, recorded_by, recorded_by_role, source)
                      VALUES (?,?,?,?,?,?)""",
                   (row["id"], "Quality hold released", f"{stage['name']}", session["name"], session["role"], "floor"))
        flash(f"Quality hold released on {stage['name']}.", "success")
        db.commit()
        send_webhook(db, f":white_check_mark: Quality hold *released* on {job['job_no']} "
                         f"({stage['name']}) by {who}.", event_type="quality_hold")
    else:
        reason = (request.form.get("reason") or "").strip()
        if not reason:
            flash("Give a reason for the quality hold.", "error")
            return redirect(url_for("job_detail", job_id=job_id))
        db.execute("""UPDATE job_stages SET quality_hold=1, quality_hold_reason=?, quality_hold_by=? WHERE id=?""",
                   (reason, who, row["id"]))
        db.execute("""INSERT INTO stage_events (job_stage_id, action, detail, recorded_by, recorded_by_role, source)
                      VALUES (?,?,?,?,?,?)""",
                   (row["id"], "Quality hold placed", f"{stage['name']}: {reason}", session["name"], session["role"], "floor"))
        flash(f"Quality hold placed on {stage['name']}. It cannot be marked complete until released.", "warning")
        db.commit()
        send_webhook(db, f":lock: Quality hold *placed* on {job['job_no']} ({stage['name']}) by {who}: {reason}",
                    event_type="quality_hold")
    return redirect(url_for("job_detail", job_id=job_id))


@app.route("/schedule")
def schedule():
    db = get_db()
    horizon = int(request.args.get("weeks", 12))
    equipment_type = (request.args.get("equipment_type") or "").strip() or None
    customer = (request.args.get("customer") or "").strip()
    load, week_labels = E.stage_load(db, horizon)
    bottlenecks = E.bottleneck_ranking(db)
    quote = E.promise_quote(db, equipment_type)
    board = E.job_board(db)
    return render_template("schedule.html", load=load, week_labels=week_labels, horizon=horizon,
                           bottlenecks=bottlenecks, quote=quote, board=board[:14],
                           equipment_type=equipment_type or "", customer=customer,
                           can_quote=session.get("role") in QUOTE_ROLES, quotes=E.quote_stats(db))


@app.route("/schedule/quote", methods=["POST"])
@role_required(*QUOTE_ROLES)
def save_quote():
    db = get_db()
    customer = (request.form.get("customer") or "").strip()
    equipment_type = (request.form.get("equipment_type") or "").strip()
    if not customer or not equipment_type:
        flash("Enter a customer and equipment type before logging the quote.", "error")
        return redirect(url_for("schedule"))
    q = E.promise_quote(db, equipment_type)
    db.execute("""INSERT INTO quotes (customer, equipment_type, p50_weeks, p80_weeks, p50_date, p80_date,
                  quoted_by, quoted_by_role) VALUES (?,?,?,?,?,?,?,?)""",
               (customer, equipment_type, q["p50_weeks"], q["p80_weeks"], q["p50_date"], q["p80_date"],
                session["name"], session["role"]))
    db.commit()
    flash(f"Quote logged for {customer}: P80 promise {q['p80_date']} ({q['p80_weeks']} wk).", "success")
    return redirect(url_for("schedule"))


@app.route("/schedule/quote/<int:quote_id>/outcome", methods=["POST"])
@role_required(*QUOTE_ROLES)
def quote_outcome(quote_id):
    db = get_db()
    outcome = request.form.get("outcome")
    if outcome not in ("Won", "Lost", "Open"):
        flash("Not a valid quote outcome.", "error")
        return redirect(url_for("schedule"))
    job_no = (request.form.get("job_no") or "").strip()
    db.execute("""UPDATE quotes SET outcome=?, decided_at=datetime('now'), job_no=? WHERE id=?""",
               (outcome, job_no, quote_id))
    db.commit()
    flash(f"Quote marked {outcome}.", "success")
    return redirect(url_for("schedule"))


@app.route("/procurement")
def procurement():
    db = get_db()
    summary, by_cat = E.procurement_summary(db)
    return render_template("procurement.html", summary=summary, by_cat=by_cat,
                           vendors=E.vendor_scorecard(db), exceptions=E.po_exceptions(db))


@app.route("/vendor-risk")
def vendor_risk():
    return render_template("vendor_risk.html", risk=E.vendor_risk(get_db()))


@app.route("/compliance")
def compliance():
    return render_template("compliance.html", status=E.compliance_status(get_db()),
                           can_edit=session.get("role") in COMPLIANCE_ROLES)


@app.route("/jobs/<int:job_id>/compliance/<item_key>/toggle", methods=["POST"])
@role_required(*COMPLIANCE_ROLES)
def toggle_compliance(job_id, item_key):
    if item_key not in dict(COMPLIANCE_ITEMS):
        flash("Not a recognised checklist item.", "error")
        return redirect(url_for("compliance"))
    db = get_db()
    existing = db.execute("SELECT id FROM job_compliance WHERE job_id=? AND item_key=?",
                          (job_id, item_key)).fetchone()
    if existing:
        db.execute("DELETE FROM job_compliance WHERE id=?", (existing["id"],))
        flash("Unchecked.", "success")
    else:
        db.execute("INSERT INTO job_compliance (job_id, item_key, done_by) VALUES (?,?,?)",
                   (job_id, item_key, f"{session['name']} ({session['role']})"))
        flash("Checked off.", "success")
    db.commit()
    back = request.form.get("back")
    return redirect(back or url_for("compliance"))


@app.route("/root-cause")
def root_cause():
    db = get_db()
    par, total = E.pareto(db)
    holds = db.execute(
        """SELECT js.id, js.quality_hold_reason, js.quality_hold_by, j.id job_id, j.job_no, j.customer,
                  s.name stage_name
           FROM job_stages js JOIN jobs j ON j.id=js.job_id JOIN stages s ON s.id=js.stage_id
           WHERE js.quality_hold=1 ORDER BY js.id DESC""").fetchall()
    return render_template("root_cause.html", pareto=par, total_delay=total,
                           reasons=E.top_reasons(db, 12), matrix=E.delay_matrix(db),
                           rework=E.rework_by_stage(db), kpis=E.compute_kpis(db), holds=holds)


@app.route("/benchmark")
def benchmark():
    db = get_db()
    stats = E.stage_actuals(db)
    return render_template("benchmark.html", stats=stats, practices=BEST_PRACTICES,
                           total_live=round(sum(s["live"] for s in stats), 1),
                           total_best=sum(s["best_high"] for s in stats),
                           total_ind=sum(s["industry_high"] for s in stats))


@app.route("/simulator")
def simulator():
    db = get_db()
    adoption = {}
    for lv in LEVERS:
        adoption[lv["id"]] = request.args.get(lv["id"], type=float,
                                              default=100 if lv["year"] == 1 else 0)
    result = E.simulate(db, adoption)
    targets = {r["metric"]: dict(r) for r in db.execute("SELECT * FROM kpi_targets").fetchall()}
    return render_template("simulator.html", levers=LEVERS, adoption=adoption, result=result,
                           assumptions=FIN_ASSUMPTIONS, targets=targets, kpis=E.compute_kpis(db))


@app.route("/roadmap")
def roadmap():
    db = get_db()
    kpis = E.compute_kpis(db)
    live = {"Total Lead Time": kpis["avg_lead_time_weeks"], "On-Time Delivery": kpis["otd_pct"],
            "First Time Right": kpis["ftr_pct"], "Rework %": kpis["rework_pct"],
            "WIP Inventory": None, "Cost of Poor Quality": kpis["copq_pct"],
            "Vendor On-Time Delivery": kpis["vendor_otd_pct"]}
    rows = []
    for t in db.execute("SELECT * FROM kpi_targets").fetchall():
        v = live.get(t["metric"])
        pct = None
        if v is not None and t["current_fy2425"] is not None:
            span = (t["current_fy2425"] - t["year3"]) if t["lower_is_better"] else (t["year3"] - t["current_fy2425"])
            moved = (t["current_fy2425"] - v) if t["lower_is_better"] else (v - t["current_fy2425"])
            if span:
                pct = max(0, min(100, round(100 * moved / span, 1)))
        rows.append({**dict(t), "live": v, "progress_pct": pct})
    return render_template("roadmap.html", rows=rows, trend=E.lead_time_trend(db), levers=LEVERS)


@app.route("/predictive")
def predictive():
    return render_template("predictive.html", model=E.delay_risk_model(get_db()))


@app.route("/business-case")
def business_case():
    return render_template("business_case.html", case=BUSINESS_CASE, risks=RISK_REGISTER,
                           proof_points=PILOT_PROOF_POINTS, quotes=E.quote_stats(get_db()))


# ------------------------------------------------------------------ data ops
@app.route("/data", methods=["GET", "POST"])
def data_ops():
    db = get_db()
    if request.method == "POST":
        if session.get("role") not in WRITE_ROLES:
            flash("Viewers cannot upload data. Ask a plant team member to load this file.", "error")
            return redirect(url_for("data_ops"))
        kind = request.form.get("kind", "jobs")
        file = request.files.get("file")
        if not file or not file.filename:
            flash("Choose a CSV or Excel file first.", "error")
            return redirect(url_for("data_ops"))
        if not file.filename.lower().endswith((".csv", ".xlsx", ".xls")):
            flash("Upload a .csv, .xlsx or .xls file.", "error")
            return redirect(url_for("data_ops"))
        try:
            df = read_table(file, kind)
        except Exception as exc:
            flash(f"That file could not be read: {exc}", "error")
            return redirect(url_for("data_ops"))

        if kind == "pos":
            s = ingest_pos(df)
            msg = (f"{s['rows_read']} rows read. {s['pos_added']} purchase orders added, "
                   f"{s['pos_updated']} corrected, {s['pos_duplicate']} unchanged, "
                   f"{s['vendors_added']} new vendors.")
        else:
            s = ingest_jobs(df)
            msg = (f"{s['rows_read']} rows read. {s['jobs_added']} jobs added, {s['jobs_updated']} corrected, "
                   f"{s['stages_added']} stage records added, {s['stages_updated']} corrected, "
                   f"{s['stages_duplicate']} unchanged, {s['delay_added']} delay logs added.")
        batch = log_ingest(file.filename, kind, s, uploaded_by=f"{session['name']} ({session['role']})")
        n_rej = len(s.get("rejects", []))
        if n_rej:
            msg += f" {n_rej} row(s) rejected."
        flash(msg, "warning" if n_rej else "success")
        for err in s["errors"][:6]:
            flash(err, "error")
        if n_rej:
            flash(f"REJECTS:{batch}", "reject")
        return redirect(url_for("data_ops"))

    counts = {k: db.execute(f"SELECT COUNT(*) c FROM {k}").fetchone()["c"]
              for k in ("jobs", "job_stages", "delay_logs", "vendors", "purchase_orders")}
    history = db.execute(
        """SELECT il.*, (SELECT COUNT(*) FROM ingest_rejects r WHERE r.ingest_id=il.id) AS rejects
           FROM ingest_log il ORDER BY il.id DESC LIMIT 10""").fetchall()
    return render_template("data_ops.html", counts=counts, history=history,
                           job_columns=UPLOAD_COLUMNS, po_columns=PO_COLUMNS,
                           categories=DELAY_CATEGORIES, item_categories=ITEM_CATEGORIES)


@app.route("/data/demo", methods=["POST"])
def load_demo():
    if session.get("role") not in WRITE_ROLES:
        flash("Viewers cannot load data.", "error")
        return redirect(url_for("data_ops"))
    base = os.path.join(os.path.dirname(__file__), "demo_data")
    try:
        jobs_df = pd.read_csv(os.path.join(base, "job_stages_demo.csv"), dtype=str)
        pos_df = pd.read_csv(os.path.join(base, "purchase_orders_demo.csv"), dtype=str)
    except FileNotFoundError:
        flash("Demo files are not in the demo_data folder.", "error")
        return redirect(url_for("data_ops"))
    a = ingest_jobs(jobs_df)
    b = ingest_pos(pos_df)
    who = f"{session['name']} ({session['role']})"
    log_ingest("job_stages_demo.csv", "jobs", a, uploaded_by=who)
    log_ingest("purchase_orders_demo.csv", "pos", b, uploaded_by=who)
    flash(f"Demo dataset loaded through the normal ingestion path: {a['jobs_added']} jobs, "
          f"{a['stages_added']} stage records, {b['pos_added']} purchase orders.", "success")
    return redirect(url_for("dashboard"))


@app.route("/data/reset", methods=["POST"])
@role_required(*ADMIN_ROLES)
def reset_data():
    db = get_db()
    for t in ("delay_logs", "job_stages", "purchase_orders", "jobs", "vendors", "ingest_log"):
        db.execute(f"DELETE FROM {t}")
    db.execute("DELETE FROM sqlite_sequence WHERE name NOT IN ('stages','kpi_targets','users')")
    db.commit()
    flash("All operational data cleared. Stage configuration and KPI targets kept.", "success")
    return redirect(url_for("data_ops"))


def _csv(rows, header, filename):
    buf = io.StringIO()
    buf.write(",".join(header) + "\n")
    for r in rows:
        cells = []
        for v in r:
            v = "" if v is None else str(v)
            cells.append(f'"{v}"' if ("," in v or '"' in v) else v)
        buf.write(",".join(cells) + "\n")
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.route("/data/template/jobs.csv")
def template_jobs():
    ex = [["SHW-26014", "Sunrise Pharma Ltd", "High-Shear Granulator", "92", "Standard", "2026-02-02", "",
           "1", "Engineering Design", "", "", "2026-02-02", "2026-03-06", "N", "", "", "", "", ""],
          ["SHW-26014", "Sunrise Pharma Ltd", "High-Shear Granulator", "92", "Standard", "2026-02-02", "",
           "2", "Material Sourcing", "", "", "2026-03-06", "2026-05-18", "N", "", "",
           "Materials", "Vendor delivery delay", "6"],
          ["SHW-26014", "Sunrise Pharma Ltd", "High-Shear Granulator", "92", "Standard", "2026-02-02", "",
           "3", "Fabrication", "", "", "2026-05-18", "", "Y", "18", "1.4", "", "", ""]]
    return _csv(ex, UPLOAD_COLUMNS, "acg_job_stage_template.csv")


@app.route("/data/template/purchase_orders.csv")
def template_pos():
    ex = [["PO-26014-01", "SHW-26014", "Precision Castings Pvt Ltd", "SS316 bowl casting",
           "Castings & Fabrication", "Y", "12.5", "2026-03-08", "2026-04-19", "2026-04-27", "Y", "Kolhapur"],
          ["PO-26014-02", "SHW-26014", "Deccan Drives & Gears", "Main drive gearbox",
           "Drives & Motors", "Y", "8.2", "2026-03-08", "2026-04-26", "", "N", "Pune"]]
    return _csv(ex, PO_COLUMNS, "acg_purchase_order_template.csv")


@app.route("/jobs/export.csv")
def jobs_export():
    db = get_db()
    rows = []
    for r in E.job_board(db, only_open=False):
        j = r["job"]
        rows.append([j["job_no"], j["customer"], j["equipment_type"], j["order_value_lakh"], j["priority"],
                     j["order_date"], j["committed_dispatch_date"], j["actual_dispatch_date"] or "",
                     j["status"], r["current_stage"], f"{r['progress']}/6", r["predicted"],
                     r["variance_days"], r["risk"], r["delay_days"]])
    header = ["job_no", "customer", "equipment_type", "order_value_lakh", "priority", "order_date",
              "committed_dispatch_date", "actual_dispatch_date", "status", "current_stage",
              "stages_complete", "forecast_dispatch", "variance_days", "risk_band", "delay_days_logged"]
    return _csv(rows, header, "acg_job_forecast.csv")


# ------------------------------------------------------------- shop floor
@app.route("/floor", methods=["GET", "POST"])
def floor():
    db = get_db()
    if request.method == "POST":
        if session.get("role") not in WRITE_ROLES:
            flash("Viewers cannot record shop-floor entries.", "error")
            return redirect(url_for("floor"))
        try:
            job_id = int(request.form.get("job_id") or 0)
            stage_id = int(request.form.get("stage_id") or 0)
        except ValueError:
            flash("Pick an order and a stage.", "error")
            return redirect(url_for("floor"))
        who = f"{session['name']} ({session['role']})"
        msg, cat = record_stage_event(db, job_id, stage_id, request.form, who, role=session["role"])
        flash(msg, cat)
        back = request.form.get("back")
        return redirect(back or url_for("floor"))

    open_jobs = db.execute(
        "SELECT id, job_no, customer, equipment_type FROM jobs WHERE status='In Progress' ORDER BY job_no"
    ).fetchall()
    stages = db.execute("SELECT * FROM stages ORDER BY seq").fetchall()

    # what each open order is waiting on, so the huddle can work down the list
    queue = []
    for r in E.job_board(db):
        queue.append(r)
    recent = db.execute(
        """SELECT se.*, j.job_no, s.name AS stage_name
           FROM stage_events se
           JOIN job_stages js ON js.id=se.job_stage_id
           JOIN jobs j ON j.id=js.job_id
           JOIN stages s ON s.id=js.stage_id
           ORDER BY se.id DESC LIMIT 15""").fetchall()
    return render_template("floor.html", open_jobs=open_jobs, stages=stages, queue=queue[:14],
                           recent=recent, categories=DELAY_CATEGORIES, today=date.today().isoformat())


# ------------------------------------------------------------ plant setup
@app.route("/setup", methods=["GET", "POST"])
@role_required(*ADMIN_ROLES)
def setup():
    db = get_db()
    if request.method == "POST":
        what = request.form.get("what")
        if what == "stages":
            for st in db.execute("SELECT id FROM stages").fetchall():
                i = st["id"]
                if f"name_{i}" not in request.form:
                    continue          # row not in this submission, leave it alone
                db.execute("""UPDATE stages SET name=?, short_name=?, acg_weeks=?, industry_low=?,
                              industry_high=?, best_in_class_low=?, best_in_class_high=?,
                              wip_capacity=?, owner_function=? WHERE id=?""",
                           ((request.form.get(f"name_{i}") or "").strip() or "Stage",
                            (request.form.get(f"short_{i}") or "").strip().upper() or "STG",
                            _num(request.form.get(f"weeks_{i}"), 1),
                            _num(request.form.get(f"ind_lo_{i}")), _num(request.form.get(f"ind_hi_{i}")),
                            _num(request.form.get(f"best_lo_{i}")), _num(request.form.get(f"best_hi_{i}")),
                            int(_num(request.form.get(f"cap_{i}"), 1)) or 1,
                            (request.form.get(f"owner_{i}") or "").strip(), i))
            flash("Stage configuration saved. Plans, benchmarks and capacity now use these values.", "success")
        elif what == "kpis":
            for t in db.execute("SELECT id FROM kpi_targets").fetchall():
                i = t["id"]
                if f"base_{i}" not in request.form:
                    continue
                db.execute("""UPDATE kpi_targets SET current_fy2425=?, year1=?, year2=?, year3=? WHERE id=?""",
                           (_num(request.form.get(f"base_{i}")), _num(request.form.get(f"y1_{i}")),
                            _num(request.form.get(f"y2_{i}")), _num(request.form.get(f"y3_{i}")), i))
            flash("KPI targets saved.", "success")
        elif what == "settings":
            for key, _label, _unit in EDITABLE_SETTINGS:
                val = request.form.get(key)
                if val not in (None, ""):
                    db.execute("""INSERT INTO settings (key, value, updated_at) VALUES (?,?,datetime('now'))
                                  ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                                  updated_at=datetime('now')""", (key, str(_num(val))))
            flash("Assumptions saved. The simulator and benefit figures now use these.", "success")
        elif what == "settings_reset":
            db.executemany("DELETE FROM settings WHERE key=?", [(k,) for k, _l, _u in EDITABLE_SETTINGS])
            flash("Assumptions reset to the defaults shipped with the tool.", "success")
        elif what == "integrations":
            db.execute("""INSERT INTO settings (key, value, updated_at) VALUES ('webhook_url', ?, datetime('now'))
                          ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')""",
                       ((request.form.get("webhook_url") or "").strip(),))
            flash("Webhook URL saved.", "success")
        elif what == "integrations_test":
            if send_webhook(db, ":bell: Test alert from the ACG Shirwal Lead Time Control Tower. "
                                f"Sent by {session['name']} ({session['role']}).", event_type="test"):
                flash("Test alert sent - check the channel.", "success")
            else:
                flash("Could not reach that webhook URL. Check it and try again.", "error")
        db.commit()
        return redirect(url_for("setup"))

    stages = db.execute("SELECT * FROM stages ORDER BY seq").fetchall()
    targets = db.execute("SELECT * FROM kpi_targets ORDER BY id").fetchall()
    cfg = E.get_settings(db)
    overridden = {r["key"] for r in db.execute("SELECT key FROM settings").fetchall()}
    return render_template("setup.html", stages=stages, targets=targets, cfg=cfg,
                           editable=EDITABLE_SETTINGS, overridden=overridden,
                           defaults=FIN_ASSUMPTIONS, webhook_url=get_webhook_url(db),
                           delay_alert_threshold=DELAY_ALERT_THRESHOLD_DAYS,
                           total_weeks=round(sum(s["acg_weeks"] for s in stages), 1))


# ------------------------------------------------------------------- audit
@app.route("/notifications")
@role_required(*ADMIN_ROLES)
def notifications():
    db = get_db()
    alerts = db.execute("SELECT * FROM alert_log ORDER BY id DESC LIMIT 100").fetchall()
    sent = sum(1 for a in alerts if a["success"])
    failed = len(alerts) - sent
    return render_template("notifications.html", alerts=alerts, sent=sent, failed=failed,
                           webhook_url=get_webhook_url(db))


@app.route("/audit")
@role_required(*ADMIN_ROLES)
def audit():
    db = get_db()
    kind = request.args.get("kind", "all")

    events = []
    if kind in ("all", "floor"):
        for e in db.execute(
            """SELECT se.at, se.recorded_by, se.recorded_by_role, se.action, se.detail, se.source,
                      j.job_no, s.name AS stage_name
               FROM stage_events se
               JOIN job_stages js ON js.id=se.job_stage_id
               JOIN jobs j ON j.id=js.job_id
               JOIN stages s ON s.id=js.stage_id
               ORDER BY se.id DESC LIMIT 200""").fetchall():
            events.append({"at": e["at"], "who": e["recorded_by"], "role": e["recorded_by_role"],
                            "kind": "Floor" if e["source"] == "floor" else "Upload",
                            "what": f"{e['action']} · {e['job_no'] or ''} {e['stage_name']}",
                            "detail": e["detail"]})
    if kind in ("all", "upload"):
        for u in db.execute(
            """SELECT il.at, il.uploaded_by, il.filename, il.kind, il.rows_read, il.added, il.updated,
                      il.errors FROM ingest_log il ORDER BY il.id DESC LIMIT 100""").fetchall():
            events.append({"at": u["at"], "who": u["uploaded_by"] or "unattributed (legacy upload)",
                           "role": "", "kind": "Data upload",
                           "what": f"{u['filename']} ({u['kind']})",
                           "detail": f"{u['rows_read']} rows read, {u['added']} added, {u['updated']} corrected, "
                                     f"{u['errors']} rejected"})
    if kind in ("all", "setting"):
        for s in db.execute("SELECT key, value, updated_at FROM settings ORDER BY updated_at DESC").fetchall():
            events.append({"at": s["updated_at"], "who": "—", "role": "",
                           "kind": "Setting changed", "what": s["key"], "detail": f"set to {s['value']}"})

    events.sort(key=lambda e: e["at"] or "", reverse=True)
    now = datetime.now()
    users = []
    for u in db.execute("SELECT id, username, name, role, active, created_at, last_login_at, "
                        "failed_attempts, locked_until FROM users ORDER BY role, username").fetchall():
        locked = bool(u["locked_until"]) and datetime.fromisoformat(u["locked_until"]) > now
        users.append({**dict(u), "locked": locked})
    return render_template("audit.html", events=events[:150], kind=kind, users=users)


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@role_required(*ADMIN_ROLES)
def admin_reset_password(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None:
        flash("That account no longer exists.", "error")
        return redirect(url_for("audit"))
    new_pw = request.form.get("new_password") or ""
    if len(new_pw) < 8:
        flash("New password must be at least 8 characters.", "error")
        return redirect(url_for("audit"))
    db.execute("""UPDATE users SET password_hash=?, failed_attempts=0, locked_until=NULL WHERE id=?""",
               (generate_password_hash(new_pw), user_id))
    db.commit()
    flash(f"Password reset for {user['name']} ({user['username']}). Tell them the new password out of band.", "success")
    return redirect(url_for("audit"))


@app.route("/admin/users/<int:user_id>/toggle-active", methods=["POST"])
@role_required(*ADMIN_ROLES)
def admin_toggle_active(user_id):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if user is None:
        flash("That account no longer exists.", "error")
        return redirect(url_for("audit"))
    if user_id == session["user_id"]:
        flash("You cannot deactivate your own account.", "error")
        return redirect(url_for("audit"))
    new_state = 0 if user["active"] else 1
    db.execute("UPDATE users SET active=?, failed_attempts=0, locked_until=NULL WHERE id=?", (new_state, user_id))
    db.commit()
    flash(f"{user['name']} ({user['username']}) is now {'active' if new_state else 'deactivated'}. "
          f"{'' if new_state else 'Their next sign-in attempt will be refused; an existing browser session stays valid until it expires or they sign out.'}",
          "success")
    return redirect(url_for("audit"))


@app.route("/data/rejects/<int:batch>.csv")
def rejects_csv(batch):
    db = get_db()
    rows = db.execute("SELECT * FROM ingest_rejects WHERE ingest_id=? ORDER BY row_no", (batch,)).fetchall()
    out = []
    for r in rows:
        try:
            raw = json.loads(r["raw"] or "{}")
        except ValueError:
            raw = {}
        out.append([r["row_no"], r["reason"], " | ".join(f"{k}={v}" for k, v in raw.items() if v)])
    return _csv(out, ["row_in_file", "why_it_was_rejected", "original_row"],
                f"acg_rejected_rows_{batch}.csv")


@app.route("/api/kpis")
def api_kpis():
    return jsonify(E.compute_kpis(get_db()))


@app.route("/api/board")
def api_board():
    db = get_db()
    return jsonify([{k: v for k, v in r.items() if k != "job"} | {"job_no": r["job"]["job_no"]}
                    for r in E.job_board(db)])


@app.route("/api/promise")
def api_promise():
    return jsonify(E.promise_quote(get_db(), request.args.get("equipment_type")))


@app.route("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"jobs": [], "purchase_orders": []})
    db = get_db()
    like = f"%{q}%"
    jobs = db.execute(
        """SELECT id, job_no, customer, equipment_type, status FROM jobs
           WHERE job_no LIKE ? OR customer LIKE ? OR equipment_type LIKE ?
           ORDER BY order_date DESC LIMIT 8""", (like, like, like)).fetchall()
    pos = db.execute(
        """SELECT p.po_no, p.item, v.name vendor, p.job_id, j.job_no
           FROM purchase_orders p JOIN vendors v ON v.id=p.vendor_id LEFT JOIN jobs j ON j.id=p.job_id
           WHERE p.po_no LIKE ? OR p.item LIKE ? OR v.name LIKE ?
           ORDER BY p.po_date DESC LIMIT 8""", (like, like, like)).fetchall()
    return jsonify({
        "jobs": [{"id": r["id"], "job_no": r["job_no"], "customer": r["customer"],
                  "equipment_type": r["equipment_type"], "status": r["status"]} for r in jobs],
        "purchase_orders": [{"po_no": r["po_no"], "item": r["item"], "vendor": r["vendor"],
                             "job_id": r["job_id"], "job_no": r["job_no"]} for r in pos],
    })


@app.errorhandler(400)
def bad_request(err):
    flash(err.description if isinstance(err.description, str) else "That request could not be read.", "error")
    return redirect(request.referrer or url_for("dashboard"))


init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
