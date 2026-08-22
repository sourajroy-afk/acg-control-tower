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
import io
import json
import os
import re
import sqlite3
from datetime import date, datetime, timedelta

import pandas as pd
from flask import (Flask, Response, flash, g, jsonify, redirect,
                   render_template, request, url_for)

import engine as E
from config import (BEST_PRACTICES, COLUMN_ALIASES, DELAY_CATEGORIES,
                    EDITABLE_SETTINGS, FIN_ASSUMPTIONS, ITEM_CATEGORIES,
                    KPI_TARGETS, LEVERS, PO_COLUMNS, STAGES, UPLOAD_COLUMNS)

DB = os.environ.get("ACG_DB", "acg.db")
app = Flask(__name__)
app.secret_key = os.environ.get("ACG_SECRET", "acg-shirwal-control-tower")


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


def init_db():
    conn = get_conn()
    with open(os.path.join(os.path.dirname(__file__), "schema.sql")) as f:
        conn.executescript(f.read())
    if conn.execute("SELECT COUNT(*) c FROM stages").fetchone()["c"] == 0:
        conn.executemany(
            """INSERT INTO stages (seq, name, short_name, acg_weeks, industry_low, industry_high,
                                   best_in_class_low, best_in_class_high, wip_capacity, owner_function)
               VALUES (?,?,?,?,?,?,?,?,?,?)""", STAGES)
    if conn.execute("SELECT COUNT(*) c FROM kpi_targets").fetchone()["c"] == 0:
        conn.executemany(
            """INSERT INTO kpi_targets (metric, unit, current_fy2425, year1, year2, year3, lower_is_better)
               VALUES (?,?,?,?,?,?,?)""", KPI_TARGETS)
    conn.commit()
    conn.close()


@app.context_processor
def inject_globals():
    db = get_db()
    kpis = E.compute_kpis(db)
    spine = E.stage_actuals(db)
    return {
        "today_str": date.today().strftime("%d %b %Y"),
        "spine": spine,
        "spine_total": round(sum(s["live"] for s in spine), 1),
        "spine_bench": round(sum(s["benchmark"] for s in spine), 1),
        "g_kpis": kpis,
        "has_data": kpis["jobs_total"] > 0,
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


ALIAS_LOOKUP = {}
for _canon, _alts in COLUMN_ALIASES.items():
    ALIAS_LOOKUP[_canon] = _canon
    for _a in _alts:
        ALIAS_LOOKUP[_a] = _canon


def _norm_header(c):
    c = re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()).strip("_")
    return ALIAS_LOOKUP.get(c, c)


def normalise_columns(df):
    """Map whatever the export called its columns onto the canonical names,
    without dropping anything it did not recognise."""
    df.columns = [_norm_header(c) for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()]
    return df


def read_table(file_storage):
    name = (file_storage.filename or "").lower()
    data = file_storage.read()
    if name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(data), dtype=str)
    else:
        df = pd.read_csv(io.BytesIO(data), dtype=str, sep=None, engine="python")
    return normalise_columns(df)


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


def log_ingest(filename, kind, s):
    db = get_db()
    added = s.get("jobs_added", 0) + s.get("stages_added", 0) + s.get("pos_added", 0) + s.get("vendors_added", 0)
    updated = s.get("jobs_updated", 0) + s.get("stages_updated", 0) + s.get("pos_updated", 0)
    dup = s.get("stages_duplicate", 0) + s.get("delay_duplicate", 0) + s.get("pos_duplicate", 0)
    rejects = s.get("rejects", [])
    cur = db.execute("""INSERT INTO ingest_log (filename, kind, rows_read, added, updated, duplicates, errors)
                        VALUES (?,?,?,?,?,?,?)""",
                     (filename, kind, s.get("rows_read", 0), added, updated, dup, len(rejects)))
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


def record_stage_event(db, job_id, stage_id, form, who, source="floor"):
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
    db.execute("""INSERT INTO stage_events (job_stage_id, action, detail, recorded_by, source)
                  VALUES (?,?,?,?,?)""", (row["id"], action, detail, who, source))

    cat, reason = (form.get("delay_category") or "").strip(), (form.get("delay_reason") or "").strip()
    days = _num(form.get("delay_days"))
    logged_delay = False
    if cat and reason and days > 0:
        dup = db.execute("""SELECT id FROM delay_logs WHERE job_stage_id=? AND category=? AND reason=?
                            AND delay_days=?""", (row["id"], cat, reason, days)).fetchone()
        if not dup:
            db.execute("INSERT INTO delay_logs (job_stage_id, category, reason, delay_days) VALUES (?,?,?,?)",
                       (row["id"], cat, reason, days))
            db.execute("""INSERT INTO stage_events (job_stage_id, action, detail, recorded_by, source)
                          VALUES (?,?,?,?,?)""",
                       (row["id"], "Delay logged", f"{cat}: {reason} (+{days:g}d)", who, source))
            logged_delay = True

    recompute_job_status(db, job_id)
    db.commit()

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
    return render_template("dashboard.html", kpis=kpis, board=board[:8], summary=summary,
                           pareto=par, total_delay=total_delay, actions=actions,
                           action_count=action_count, trend=trend, bottlenecks=bottlenecks,
                           proc=proc, reasons=E.top_reasons(db, 5))


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


@app.route("/schedule")
def schedule():
    db = get_db()
    horizon = int(request.args.get("weeks", 12))
    load, week_labels = E.stage_load(db, horizon)
    bottlenecks = E.bottleneck_ranking(db)
    quote = E.promise_quote(db)
    board = E.job_board(db)
    return render_template("schedule.html", load=load, week_labels=week_labels, horizon=horizon,
                           bottlenecks=bottlenecks, quote=quote, board=board[:14])


@app.route("/procurement")
def procurement():
    db = get_db()
    summary, by_cat = E.procurement_summary(db)
    return render_template("procurement.html", summary=summary, by_cat=by_cat,
                           vendors=E.vendor_scorecard(db), exceptions=E.po_exceptions(db))


@app.route("/root-cause")
def root_cause():
    db = get_db()
    par, total = E.pareto(db)
    return render_template("root_cause.html", pareto=par, total_delay=total,
                           reasons=E.top_reasons(db, 12), matrix=E.delay_matrix(db),
                           rework=E.rework_by_stage(db), kpis=E.compute_kpis(db))


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


# ------------------------------------------------------------------ data ops
@app.route("/data", methods=["GET", "POST"])
def data_ops():
    db = get_db()
    if request.method == "POST":
        kind = request.form.get("kind", "jobs")
        file = request.files.get("file")
        if not file or not file.filename:
            flash("Choose a CSV or Excel file first.", "error")
            return redirect(url_for("data_ops"))
        if not file.filename.lower().endswith((".csv", ".xlsx", ".xls")):
            flash("Upload a .csv, .xlsx or .xls file.", "error")
            return redirect(url_for("data_ops"))
        try:
            df = read_table(file)
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
        batch = log_ingest(file.filename, kind, s)
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
    base = os.path.join(os.path.dirname(__file__), "demo_data")
    try:
        jobs_df = pd.read_csv(os.path.join(base, "job_stages_demo.csv"), dtype=str)
        pos_df = pd.read_csv(os.path.join(base, "purchase_orders_demo.csv"), dtype=str)
    except FileNotFoundError:
        flash("Demo files are not in the demo_data folder.", "error")
        return redirect(url_for("data_ops"))
    a = ingest_jobs(jobs_df)
    b = ingest_pos(pos_df)
    log_ingest("job_stages_demo.csv", "jobs", a)
    log_ingest("purchase_orders_demo.csv", "pos", b)
    flash(f"Demo dataset loaded through the normal ingestion path: {a['jobs_added']} jobs, "
          f"{a['stages_added']} stage records, {b['pos_added']} purchase orders.", "success")
    return redirect(url_for("dashboard"))


@app.route("/data/reset", methods=["POST"])
def reset_data():
    db = get_db()
    for t in ("delay_logs", "job_stages", "purchase_orders", "jobs", "vendors", "ingest_log"):
        db.execute(f"DELETE FROM {t}")
    db.execute("DELETE FROM sqlite_sequence WHERE name NOT IN ('stages','kpi_targets')")
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
        try:
            job_id = int(request.form.get("job_id") or 0)
            stage_id = int(request.form.get("stage_id") or 0)
        except ValueError:
            flash("Pick an order and a stage.", "error")
            return redirect(url_for("floor"))
        who = (request.form.get("recorded_by") or "").strip()
        if not who:
            flash("Enter your name so the entry can be traced back.", "error")
            return redirect(url_for("floor"))
        msg, cat = record_stage_event(db, job_id, stage_id, request.form, who)
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
            db.execute("DELETE FROM settings")
            flash("Assumptions reset to the defaults shipped with the tool.", "success")
        db.commit()
        return redirect(url_for("setup"))

    stages = db.execute("SELECT * FROM stages ORDER BY seq").fetchall()
    targets = db.execute("SELECT * FROM kpi_targets ORDER BY id").fetchall()
    cfg = E.get_settings(db)
    overridden = {r["key"] for r in db.execute("SELECT key FROM settings").fetchall()}
    return render_template("setup.html", stages=stages, targets=targets, cfg=cfg,
                           editable=EDITABLE_SETTINGS, overridden=overridden,
                           defaults=FIN_ASSUMPTIONS,
                           total_weeks=round(sum(s["acg_weeks"] for s in stages), 1))


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


init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
