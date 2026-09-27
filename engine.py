"""
Analytics engine for the Lead Time Control Tower.

Everything here is computed from the operational tables (jobs, job_stages,
delay_logs, purchase_orders). Nothing is stored pre-aggregated, so every
number on every page reflects the latest upload.
"""
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median

import numpy as np

from config import FIN_ASSUMPTIONS, LEVERS

RISK_BANDS = [
    (0, "On track", "good"),
    (14, "Watch", "warn"),
    (42, "At risk", "bad"),
    (10 ** 6, "Critical", "crit"),
]


def parse_date(s):
    if s in (None, "", "None"):
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


def _weeks(a, b):
    return (b - a).days / 7.0


DATE_FORMATS = [
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%y", "%d/%m/%y",
    "%Y/%m/%d", "%m/%d/%Y", "%d.%m.%y", "%d-%b-%Y", "%d %b %Y", "%d-%B-%Y",
    "%d %B %Y", "%d-%b-%y", "%d %b %y",
    "%b %d, %Y", "%Y%m%d",
]


def parse_any_date(v):
    """
    Parse a date the way a plant export actually writes it: ISO, Indian
    day-first with any separator, short year, month names, or an Excel
    serial number. Returns a date, or raises ValueError with the raw value
    so the caller can put it in the rejected-rows file.
    """
    if v in (None, "", "None"):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    raw = str(v).strip()
    if not raw:
        return None
    # Excel / Google Sheets serial number (days since 1899-12-30)
    if re.fullmatch(r"\d{5}(\.\d+)?", raw):
        return date(1899, 12, 30) + timedelta(days=int(float(raw)))
    txt = raw.split("T")[0].split(" ")[0] if re.match(r"\d{4}-\d{2}-\d{2}", raw) else raw
    for fmt in DATE_FORMATS:
        try:
            d = datetime.strptime(txt, fmt).date()
            # a two-digit year before 2000 in this context means 20xx
            if d.year < 1970:
                d = d.replace(year=d.year + 100)
            return d
        except ValueError:
            continue
    raise ValueError(f"unrecognised date '{raw}'")


# ------------------------------------------------------------------ settings
def get_settings(db):
    """Financial and planning assumptions, overridable from Plant Setup."""
    out = dict(FIN_ASSUMPTIONS)
    try:
        for r in db.execute("SELECT key, value FROM settings").fetchall():
            if r["key"] in out:
                try:
                    out[r["key"]] = float(r["value"])
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass
    return out


def band_for(days_late):
    for limit, label, tone in RISK_BANDS:
        if days_late <= limit:
            return label, tone
    return "Critical", "crit"


# ------------------------------------------------------------- stage actuals
def stage_actuals(db):
    """Live duration statistics per stage, from completed stage records."""
    rows = db.execute(
        """SELECT s.id, s.seq, s.name, s.short_name, s.acg_weeks, s.wip_capacity,
                  s.industry_low, s.industry_high, s.best_in_class_low, s.best_in_class_high,
                  js.actual_start, js.actual_end
           FROM stages s LEFT JOIN job_stages js
             ON js.stage_id = s.id AND js.status='Complete'
            AND js.actual_start IS NOT NULL AND js.actual_end IS NOT NULL
           ORDER BY s.seq"""
    ).fetchall()

    dur = defaultdict(list)
    meta = {}
    for r in rows:
        meta[r["seq"]] = r
        if r["actual_start"] and r["actual_end"]:
            d = _weeks(parse_date(r["actual_start"]), parse_date(r["actual_end"]))
            if d >= 0:
                dur[r["seq"]].append(d)

    out = []
    for seq in sorted(meta):
        r = meta[seq]
        vals = sorted(dur[seq])
        n = len(vals)
        p50 = round(median(vals), 2) if n else None
        p80 = round(vals[min(n - 1, int(0.8 * n))], 2) if n else None
        live = p50 if p50 is not None else r["acg_weeks"]
        out.append({
            "id": r["id"], "seq": seq, "name": r["name"], "short": r["short_name"],
            "benchmark": r["acg_weeks"], "capacity": r["wip_capacity"],
            "industry_low": r["industry_low"], "industry_high": r["industry_high"],
            "best_low": r["best_in_class_low"], "best_high": r["best_in_class_high"],
            "p50": p50, "p80": p80, "samples": n, "live": round(live, 2),
            "has_data": n > 0,
            "vs_benchmark": round(live - r["acg_weeks"], 2),
            "gap_vs_best": round(live - r["best_in_class_high"], 2),
        })
    return out


def plant_slip_factor(stats):
    """How the plant is actually running against its own base-case plan."""
    live = sum(s["live"] for s in stats if s["has_data"])
    bench = sum(s["benchmark"] for s in stats if s["has_data"])
    if not bench:
        return 1.0
    return max(0.5, min(2.5, live / bench))


# ------------------------------------------------------------------- KPIs
def compute_kpis(db):
    today = date.today()
    jobs = db.execute("SELECT * FROM jobs").fetchall()
    js = db.execute(
        """SELECT js.*, s.name AS stage_name, s.seq AS stage_seq, s.acg_weeks
           FROM job_stages js JOIN stages s ON js.stage_id=s.id"""
    ).fetchall()

    dispatched = [j for j in jobs if j["status"] == "Dispatched" and j["actual_dispatch_date"]]
    on_time = [j for j in dispatched if j["actual_dispatch_date"] <= j["committed_dispatch_date"]]
    otd = round(100 * len(on_time) / len(dispatched), 1) if dispatched else None

    done = [r for r in js if r["status"] == "Complete"]
    rw = [r for r in done if r["rework"]]
    ftr = round(100 * (len(done) - len(rw)) / len(done), 1) if done else None
    rework_pct = round(100 * len(rw) / len(done), 1) if done else None

    order_value = sum(j["order_value_lakh"] or 0 for j in jobs)
    rework_cost = sum(r["rework_cost_lakh"] or 0 for r in js)
    copq = round(100 * rework_cost / order_value, 2) if order_value else None

    # WIP value: order value earned into the shop so far, at an assumed 60%
    # material and conversion share of order value.
    wip_days = []
    wip_value = 0.0
    for j in jobs:
        if j["status"] != "In Progress":
            continue
        mine = [r for r in js if r["job_id"] == j["id"]]
        starts = [parse_date(r["actual_start"]) for r in mine if r["actual_start"]]
        if starts:
            wip_days.append((today - min(starts)).days)
            done_n = len([r for r in mine if r["status"] == "Complete"])
            wip_value += (j["order_value_lakh"] or 0) * (done_n / 6.0) * 0.6
    wip = round(sum(wip_days) / len(wip_days), 1) if wip_days else None

    lts = []
    for j in dispatched:
        od, dd = parse_date(j["order_date"]), parse_date(j["actual_dispatch_date"])
        if od and dd:
            lts.append(_weeks(od, dd))
    avg_lt = round(sum(lts) / len(lts), 1) if lts else None
    best_lt = round(min(lts), 1) if lts else None

    # last 90 days vs the 90 before it, so the trend is visible on the KPI tile
    recent, prior = [], []
    for j in dispatched:
        dd = parse_date(j["actual_dispatch_date"])
        wk = _weeks(parse_date(j["order_date"]), dd)
        age = (today - dd).days
        if age <= 90:
            recent.append(wk)
        elif age <= 180:
            prior.append(wk)
    lt_delta = None
    if recent and prior:
        lt_delta = round(sum(recent) / len(recent) - sum(prior) / len(prior), 1)

    po_rows = db.execute("SELECT promised_date, received_date FROM purchase_orders").fetchall()
    closed = [p for p in po_rows if p["received_date"]]
    po_ot = [p for p in closed if p["received_date"] <= p["promised_date"]]
    vendor_otd = round(100 * len(po_ot) / len(closed), 1) if closed else None

    cfg = get_settings(db)
    annual_sales = (order_value / len(jobs) * cfg["annual_orders"]) if jobs else 0
    wip_cover_days = round(wip_value / (annual_sales / 365), 1) if annual_sales else None

    return {
        "otd_pct": otd, "ftr_pct": ftr, "rework_pct": rework_pct, "copq_pct": copq,
        "wip_days": wip, "wip_value_lakh": round(wip_value, 1), "wip_cover_days": wip_cover_days,
        "avg_lead_time_weeks": avg_lt, "best_lead_time_weeks": best_lt,
        "lt_delta_90d": lt_delta, "vendor_otd_pct": vendor_otd,
        "jobs_total": len(jobs),
        "jobs_dispatched": len(dispatched),
        "jobs_in_progress": len([j for j in jobs if j["status"] == "In Progress"]),
        "order_value_lakh": round(order_value, 1),
        "rework_cost_lakh": round(rework_cost, 2),
        "rework_hours": round(sum(r["rework_hours"] or 0 for r in js), 1),
    }


# ------------------------------------------------------------- risk engine
def job_board(db, only_open=True):
    """
    Per-job forecast. Predicted dispatch = today + remaining benchmark work
    scaled by how this job (or the plant) is actually running, plus a queue
    penalty where the next stages are already at WIP capacity.
    """
    today = date.today()
    stats = stage_actuals(db)
    by_seq = {s["seq"]: s for s in stats}
    plant_f = plant_slip_factor(stats)

    wip_count = defaultdict(int)
    for r in db.execute(
        "SELECT s.seq AS seq, COUNT(*) c FROM job_stages js JOIN stages s ON s.id=js.stage_id "
        "WHERE js.status='In Progress' GROUP BY s.seq"
    ).fetchall():
        wip_count[r["seq"]] = r["c"]

    jobs = db.execute("SELECT * FROM jobs ORDER BY order_date").fetchall()
    rows = db.execute(
        """SELECT js.*, s.seq AS seq, s.name AS stage_name, s.acg_weeks, s.wip_capacity
           FROM job_stages js JOIN stages s ON s.id=js.stage_id ORDER BY s.seq"""
    ).fetchall()
    # delay days for every job in one pass, instead of a query per job
    delay_by_job = defaultdict(float)
    for r in db.execute(
        """SELECT js.job_id AS job_id, COALESCE(SUM(d.delay_days),0) v FROM delay_logs d
           JOIN job_stages js ON js.id=d.job_stage_id GROUP BY js.job_id"""
    ).fetchall():
        delay_by_job[r["job_id"]] = r["v"]
    by_job = defaultdict(list)
    for r in rows:
        by_job[r["job_id"]].append(r)

    out = []
    for j in jobs:
        if only_open and j["status"] != "In Progress":
            continue
        st = by_job.get(j["id"], [])
        done = [r for r in st if r["status"] == "Complete"]
        cur = next((r for r in st if r["status"] == "In Progress"), None)
        seen = {r["seq"] for r in st}

        planned_w = sum(r["acg_weeks"] for r in done)
        actual_w = sum(_weeks(parse_date(r["actual_start"]), parse_date(r["actual_end"]))
                       for r in done if r["actual_start"] and r["actual_end"])
        factor = max(0.6, min(3.0, actual_w / planned_w)) if planned_w and actual_w else plant_f

        remaining = 0.0
        queue_pen = 0.0
        if cur:
            elapsed = _weeks(parse_date(cur["actual_start"]), today) if cur["actual_start"] else 0
            remaining += max(0.3, cur["acg_weeks"] * factor - elapsed)
            pending_seqs = [s for s in range(cur["seq"] + 1, 7)]
        else:
            done_max = max((r["seq"] for r in done), default=0)
            pending_seqs = [s for s in range(done_max + 1, 7)]
        for sq in pending_seqs:
            remaining += by_seq[sq]["benchmark"] * factor
            over = wip_count[sq] - by_seq[sq]["capacity"]
            if over > 0:
                queue_pen += 0.4 * over

        committed = parse_date(j["committed_dispatch_date"])
        if j["status"] == "Dispatched" and j["actual_dispatch_date"]:
            # Delivered orders are measured, not forecast.
            predicted = parse_date(j["actual_dispatch_date"])
            variance = (predicted - committed).days
            label = "Delivered on time" if variance <= 0 else "Delivered late"
            tone = "good" if variance <= 0 else "bad"
            remaining = queue_pen = 0.0
        else:
            predicted = today + timedelta(weeks=remaining + queue_pen)
            variance = (predicted - committed).days
            label, tone = band_for(variance)

        stage_bar = []
        for sq in range(1, 7):
            r = next((x for x in st if x["seq"] == sq), None)
            stage_bar.append(r["status"] if r else "Pending")

        delay_days = delay_by_job.get(j["id"], 0.0)

        out.append({
            "job": j, "progress": len(done), "stage_bar": stage_bar,
            "current_stage": cur["stage_name"] if cur else ("Dispatched" if j["status"] == "Dispatched" else "Not started"),
            "current_seq": cur["seq"] if cur else 0,
            "days_in_stage": (today - parse_date(cur["actual_start"])).days if cur and cur["actual_start"] else None,
            "stage_overdue": (cur is not None and cur["planned_end"] is not None
                              and parse_date(cur["planned_end"]) < today),
            "predicted": predicted.isoformat(), "committed": j["committed_dispatch_date"],
            "variance_days": variance, "risk": label, "tone": tone,
            "factor": round(factor, 2), "queue_weeks": round(queue_pen, 1),
            "delay_days": round(delay_days, 1),
            "stages_recorded": len(seen),
        })
    out.sort(key=lambda r: -r["variance_days"])
    return out


def risk_summary(board):
    counts = defaultdict(int)
    value_at_risk = 0.0
    for r in board:
        counts[r["risk"]] += 1
        if r["variance_days"] > 0:
            value_at_risk += r["job"]["order_value_lakh"] or 0
    return {
        "counts": {k: counts.get(k, 0) for k in ["On track", "Watch", "At risk", "Critical"]},
        "value_at_risk_lakh": round(value_at_risk, 1),
        "open_jobs": len(board),
    }


# ------------------------------------------------------------ capacity load
def stage_load(db, weeks_ahead=12):
    """Planned load per stage per week vs WIP capacity, from plan dates."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    weeks = [monday + timedelta(weeks=i) for i in range(weeks_ahead)]
    stats = stage_actuals(db)

    rows = db.execute(
        """SELECT js.planned_start, js.planned_end, js.status, s.seq
           FROM job_stages js JOIN stages s ON s.id=js.stage_id
           JOIN jobs j ON j.id=js.job_id
           WHERE j.status='In Progress' AND js.planned_start IS NOT NULL"""
    ).fetchall()

    grid = {s["seq"]: [0] * weeks_ahead for s in stats}
    for r in rows:
        if r["status"] == "Complete":
            continue
        ps, pe = parse_date(r["planned_start"]), parse_date(r["planned_end"])
        if not ps or not pe:
            continue
        for i, w in enumerate(weeks):
            if ps <= w + timedelta(days=6) and pe >= w:
                grid[r["seq"]][i] += 1

    out = []
    for s in stats:
        cells = []
        for i, w in enumerate(weeks):
            load = grid[s["seq"]][i]
            util = round(100 * load / s["capacity"], 0) if s["capacity"] else 0
            tone = "free" if util <= 60 else ("ok" if util <= 90 else ("tight" if util <= 110 else "over"))
            cells.append({"week": w.strftime("%d %b"), "load": load, "util": util, "tone": tone})
        peak = max((c["util"] for c in cells), default=0)
        out.append({"seq": s["seq"], "name": s["name"], "short": s["short"],
                    "capacity": s["capacity"], "cells": cells, "peak_util": peak})
    return out, [w.strftime("%d %b") for w in weeks]


def bottleneck_ranking(db):
    """Ranks stages by the time they add over best-in-class, weighted by load."""
    stats = stage_actuals(db)
    wip = defaultdict(int)
    for r in db.execute(
        "SELECT s.seq seq, COUNT(*) c FROM job_stages js JOIN stages s ON s.id=js.stage_id "
        "WHERE js.status='In Progress' GROUP BY s.seq"
    ).fetchall():
        wip[r["seq"]] = r["c"]
    delay = defaultdict(float)
    for r in db.execute(
        """SELECT s.seq seq, COALESCE(SUM(d.delay_days),0) v FROM delay_logs d
           JOIN job_stages js ON js.id=d.job_stage_id JOIN stages s ON s.id=js.stage_id
           GROUP BY s.seq"""
    ).fetchall():
        delay[r["seq"]] = r["v"]
    out = []
    for s in stats:
        excess = max(0.0, s["live"] - s["best_high"])
        util = round(100 * wip[s["seq"]] / s["capacity"], 0) if s["capacity"] else 0
        score = round(excess * (1 + util / 100) + delay[s["seq"]] / 14, 2)
        out.append({**s, "wip": wip[s["seq"]], "util": util,
                    "excess_weeks": round(excess, 2), "delay_days": round(delay[s["seq"]], 1),
                    "score": score})
    out.sort(key=lambda r: -r["score"])
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


# ------------------------------------------------------------- root cause
def pareto(db):
    rows = db.execute(
        "SELECT category, SUM(delay_days) d, COUNT(*) n FROM delay_logs GROUP BY category ORDER BY d DESC"
    ).fetchall()
    total = sum(r["d"] for r in rows) or 1
    cum, out = 0, []
    for r in rows:
        cum += r["d"]
        out.append({"category": r["category"], "days": round(r["d"], 1), "occurrences": r["n"],
                    "pct": round(100 * r["d"] / total, 1), "cum_pct": round(100 * cum / total, 1)})
    return out, round(total, 1)


def top_reasons(db, limit=10):
    rows = db.execute(
        """SELECT reason, category, SUM(delay_days) d, COUNT(*) n FROM delay_logs
           GROUP BY reason, category ORDER BY d DESC LIMIT ?""", (limit,)
    ).fetchall()
    return [{"reason": r["reason"], "category": r["category"],
             "total_days": round(r["d"], 1), "occurrences": r["n"]} for r in rows]


def delay_matrix(db):
    """Fishbone category x process stage — where each root cause actually bites."""
    rows = db.execute(
        """SELECT s.seq, s.short_name short, d.category, SUM(d.delay_days) v
           FROM delay_logs d JOIN job_stages js ON js.id=d.job_stage_id
           JOIN stages s ON s.id=js.stage_id GROUP BY s.seq, d.category"""
    ).fetchall()
    stages = db.execute("SELECT seq, short_name short FROM stages ORDER BY seq").fetchall()
    cats = sorted({r["category"] for r in rows})
    grid = {c: {s["seq"]: 0.0 for s in stages} for c in cats}
    for r in rows:
        grid[r["category"]][r["seq"]] = round(r["v"], 1)
    mx = max((v for c in grid.values() for v in c.values()), default=0) or 1
    return {
        "stages": [s["short"] for s in stages],
        "rows": [{"category": c,
                  "cells": [{"v": grid[c][s["seq"]], "heat": round(grid[c][s["seq"]] / mx, 2)} for s in stages],
                  "total": round(sum(grid[c].values()), 1)} for c in cats],
    }


def rework_by_stage(db):
    rows = db.execute(
        """SELECT s.seq, s.name, SUM(js.rework_hours) h, SUM(js.rework_cost_lakh) c,
                  SUM(CASE WHEN js.rework THEN 1 ELSE 0 END) inc, COUNT(*) tot
           FROM job_stages js JOIN stages s ON s.id=js.stage_id
           WHERE js.status='Complete' GROUP BY s.seq, s.name ORDER BY s.seq"""
    ).fetchall()
    return [{"name": r["name"], "hours": round(r["h"] or 0, 1), "cost": round(r["c"] or 0, 2),
             "incidents": r["inc"] or 0, "completed": r["tot"],
             "rate": round(100 * (r["inc"] or 0) / r["tot"], 1) if r["tot"] else 0} for r in rows]


# --------------------------------------------------------------- procurement
def vendor_scorecard(db):
    rows = db.execute(
        """SELECT v.id, v.name, v.item_category, v.location, v.single_source,
                  COUNT(p.id) pos, SUM(p.value_lakh) spend,
                  SUM(CASE WHEN p.received_date IS NOT NULL THEN 1 ELSE 0 END) closed,
                  SUM(CASE WHEN p.received_date IS NOT NULL AND p.received_date <= p.promised_date THEN 1 ELSE 0 END) ontime,
                  SUM(CASE WHEN p.received_date IS NOT NULL
                           THEN MAX(julianday(p.received_date)-julianday(p.promised_date),0) ELSE 0 END) late_days,
                  SUM(CASE WHEN p.received_date IS NULL AND p.promised_date < date('now') THEN 1 ELSE 0 END) overdue,
                  SUM(CASE WHEN p.critical=1 THEN 1 ELSE 0 END) crit
           FROM vendors v LEFT JOIN purchase_orders p ON p.vendor_id=v.id
           GROUP BY v.id ORDER BY spend DESC"""
    ).fetchall()
    out = []
    for r in rows:
        closed = r["closed"] or 0
        otd = round(100 * (r["ontime"] or 0) / closed, 1) if closed else None
        avg_late = round((r["late_days"] or 0) / closed, 1) if closed else None
        score = None
        if otd is not None:
            score = round(max(0, min(100, otd - (avg_late or 0) * 1.5 - (r["overdue"] or 0) * 3)), 0)
        out.append({
            "id": r["id"], "name": r["name"], "category": r["item_category"],
            "location": r["location"], "single_source": r["single_source"],
            "pos": r["pos"] or 0, "spend": round(r["spend"] or 0, 1), "critical_pos": r["crit"] or 0,
            "otd": otd, "avg_late_days": avg_late, "overdue": r["overdue"] or 0, "score": score,
        })
    return out


def po_exceptions(db, limit=40):
    rows = db.execute(
        """SELECT p.*, v.name vendor, j.job_no, j.customer
           FROM purchase_orders p JOIN vendors v ON v.id=p.vendor_id
           LEFT JOIN jobs j ON j.id=p.job_id
           WHERE p.received_date IS NULL ORDER BY p.promised_date"""
    ).fetchall()
    today = date.today()
    out = []
    for r in rows:
        pd = parse_date(r["promised_date"])
        days = (today - pd).days
        out.append({
            "po_no": r["po_no"], "vendor": r["vendor"], "item": r["item"], "job_no": r["job_no"],
            "customer": r["customer"], "value": r["value_lakh"], "critical": r["critical"],
            "promised": r["promised_date"], "days_late": days, "category": r["item_category"],
        })
    out.sort(key=lambda r: -r["days_late"])
    return out[:limit]


def procurement_summary(db):
    row = db.execute(
        """SELECT COUNT(*) pos, SUM(value_lakh) spend,
                  SUM(CASE WHEN received_date IS NULL THEN 1 ELSE 0 END) open_pos,
                  SUM(CASE WHEN received_date IS NULL AND promised_date < date('now') THEN 1 ELSE 0 END) overdue,
                  SUM(CASE WHEN received_date IS NULL AND promised_date < date('now') THEN value_lakh ELSE 0 END) overdue_value,
                  SUM(CASE WHEN critical=1 THEN 1 ELSE 0 END) critical
           FROM purchase_orders"""
    ).fetchone()
    lead = db.execute(
        """SELECT item_category cat, COUNT(*) n,
                  AVG(julianday(COALESCE(received_date, date('now')))-julianday(po_date)) days,
                  AVG(CASE WHEN received_date IS NOT NULL
                      THEN julianday(received_date)-julianday(promised_date) END) slip
           FROM purchase_orders GROUP BY item_category ORDER BY days DESC"""
    ).fetchall()
    return dict(row), [{"category": r["cat"], "n": r["n"],
                        "avg_days": round(r["days"] or 0, 1),
                        "avg_slip": round(r["slip"] or 0, 1)} for r in lead]


# ------------------------------------------------------------- promise date
def promise_quote(db, equipment_type=None, start=None):
    """
    Capable-to-promise date for a new order: live P50 and P80 stage durations
    plus the queue in front of each stage today.
    """
    start = start or date.today()
    stats = stage_actuals(db)
    wip = defaultdict(int)
    for r in db.execute(
        "SELECT s.seq seq, COUNT(*) c FROM job_stages js JOIN stages s ON s.id=js.stage_id "
        "WHERE js.status='In Progress' GROUP BY s.seq"
    ).fetchall():
        wip[r["seq"]] = r["c"]

    lines, p50_total, p80_total = [], 0.0, 0.0
    for s in stats:
        p50 = s["p50"] if s["p50"] is not None else s["benchmark"]
        p80 = s["p80"] if s["p80"] is not None else s["benchmark"] * 1.2
        over = max(0, wip[s["seq"]] - s["capacity"])
        q = round(0.4 * over, 2)
        p50_total += p50 + q
        p80_total += p80 + q
        lines.append({"name": s["name"], "short": s["short"], "p50": round(p50, 1),
                      "p80": round(p80, 1), "queue": q, "wip": wip[s["seq"]],
                      "capacity": s["capacity"], "samples": s["samples"]})
    return {
        "equipment_type": equipment_type,
        "start": start.isoformat(),
        "p50_weeks": round(p50_total, 1), "p80_weeks": round(p80_total, 1),
        "p50_date": (start + timedelta(weeks=p50_total)).isoformat(),
        "p80_date": (start + timedelta(weeks=p80_total)).isoformat(),
        "base_case_date": (start + timedelta(weeks=34)).isoformat(),
        "lines": lines,
        "samples": min((l["samples"] for l in lines), default=0),
    }


def quote_stats(db):
    """Win rate overall and by promised lead time band, so Sales activity
    validates (or challenges) the deck's win-rate-uplift assumption instead
    of it staying a one-off estimate."""
    rows = db.execute("SELECT * FROM quotes ORDER BY id DESC").fetchall()
    decided = [r for r in rows if r["outcome"] in ("Won", "Lost")]
    won = [r for r in decided if r["outcome"] == "Won"]

    def _rate(subset):
        d = [r for r in subset if r["outcome"] in ("Won", "Lost")]
        w = [r for r in d if r["outcome"] == "Won"]
        return round(100 * len(w) / len(d), 1) if d else None

    fast = [r for r in rows if r["p80_weeks"] <= 20]
    slow = [r for r in rows if r["p80_weeks"] > 20]
    return {
        "total": len(rows), "open": len([r for r in rows if r["outcome"] == "Open"]),
        "decided": len(decided), "won": len(won),
        "win_rate": _rate(rows),
        "win_rate_fast": _rate(fast), "win_rate_slow": _rate(slow),
        "recent": rows[:20],
    }


# ----------------------------------------------------------------- trend
def lead_time_trend(db):
    rows = db.execute(
        """SELECT strftime('%Y-%m', actual_dispatch_date) m, order_date, actual_dispatch_date, committed_dispatch_date
           FROM jobs WHERE status='Dispatched' AND actual_dispatch_date IS NOT NULL
           ORDER BY actual_dispatch_date"""
    ).fetchall()
    by_month = defaultdict(list)
    otd = defaultdict(lambda: [0, 0])
    for r in rows:
        by_month[r["m"]].append(_weeks(parse_date(r["order_date"]), parse_date(r["actual_dispatch_date"])))
        otd[r["m"]][1] += 1
        if r["actual_dispatch_date"] <= r["committed_dispatch_date"]:
            otd[r["m"]][0] += 1
    out = []
    for m in sorted(by_month):
        vals = by_month[m]
        ok, tot = otd[m]
        out.append({"month": m, "avg_weeks": round(sum(vals) / len(vals), 1), "jobs": len(vals),
                    "otd": round(100 * ok / tot, 1) if tot else None})
    for i, r in enumerate(out):
        window = out[max(0, i - 2): i + 1]
        r["rolling3"] = round(sum(x["avg_weeks"] for x in window) / len(window), 1)
    return out


# ------------------------------------------------------------ action board
def _all_action_items(db):
    """Every open exception, unsorted-limit, for the daily huddle and for
    role-filtered queues. See action_board() and actions_for_role()."""
    today = date.today()
    items = []

    for r in db.execute(
        """SELECT j.job_no, j.customer, j.order_value_lakh, s.name stage, s.owner_function owner,
                  js.planned_end, js.actual_start
           FROM job_stages js JOIN stages s ON s.id=js.stage_id JOIN jobs j ON j.id=js.job_id
           WHERE js.status='In Progress' AND js.planned_end IS NOT NULL AND js.planned_end < date('now')"""
    ).fetchall():
        d = (today - parse_date(r["planned_end"])).days
        items.append({"type": "Stage overdue", "ref": r["job_no"], "owner": r["owner"],
                      "detail": f"{r['stage']} past plan by {d} days ({r['customer']})",
                      "severity": d, "value": r["order_value_lakh"] or 0})

    for r in db.execute(
        """SELECT p.po_no, p.item, p.promised_date, p.value_lakh, p.critical, v.name vendor, j.job_no
           FROM purchase_orders p JOIN vendors v ON v.id=p.vendor_id LEFT JOIN jobs j ON j.id=p.job_id
           WHERE p.received_date IS NULL AND p.promised_date < date('now')"""
    ).fetchall():
        d = (today - parse_date(r["promised_date"])).days
        items.append({"type": "PO overdue", "ref": r["po_no"], "owner": "Supply Chain",
                      "detail": f"{r['item']} from {r['vendor']} late by {d} days"
                                + (f" (blocks {r['job_no']})" if r["job_no"] else ""),
                      "severity": d * (2 if r["critical"] else 1), "value": r["value_lakh"] or 0})

    for r in job_board(db):
        if r["variance_days"] > 14:
            items.append({"type": "Delivery risk", "ref": r["job"]["job_no"], "owner": "Production Planning",
                          "detail": f"forecast {r['variance_days']} days past commitment, now in {r['current_stage']}",
                          "severity": r["variance_days"], "value": r["job"]["order_value_lakh"] or 0})

    items.sort(key=lambda x: (-x["severity"], -x["value"]))
    return items


def action_board(db, limit=12):
    """One prioritised exception list for the daily production huddle."""
    items = _all_action_items(db)
    return items[:limit], len(items)


def actions_for_role(db, owner_labels, limit=8):
    """The subset of the action board owned by one functional role, so a
    signed-in account can see its own queue rather than everyone's."""
    if not owner_labels:
        return [], 0
    items = [i for i in _all_action_items(db) if i["owner"] in owner_labels]
    return items[:limit], len(items)


# --------------------------------------------------------- lever simulator
def simulate(db, adoption):
    """
    adoption: {lever_id: 0..100}. Returns the modelled stage profile, the new
    total lead time, and the financial effect, all anchored on live actuals.
    """
    stats = stage_actuals(db)
    kpis = compute_kpis(db)
    base = {s["seq"]: s["live"] for s in stats}
    base_total = sum(base.values())

    # multiplicative stacking, so two levers on the same stage do not double count
    factor = {s: 1.0 for s in base}
    rework_factor = 1.0
    invest = 0.0
    applied = []
    for lv in LEVERS:
        a = max(0, min(100, float(adoption.get(lv["id"], 0)))) / 100.0
        if a <= 0:
            continue
        invest += lv["invest_cr"] * a
        for seq, pct in lv["stage_pct"].items():
            factor[seq] *= (1 - (pct / 100.0) * a)
        rework_factor *= (1 - (lv["rework_pct"] / 100.0) * a)
        applied.append({"id": lv["id"], "name": lv["name"], "year": lv["year"], "adoption": round(a * 100)})

    new = {s: round(base[s] * factor[s], 2) for s in base}
    new_total = sum(new.values())
    cut_pct = round(100 * (base_total - new_total) / base_total, 1) if base_total else 0

    a = get_settings(db)
    annual_value = kpis["order_value_lakh"] or 0
    if kpis["jobs_total"]:
        avg_order = annual_value / kpis["jobs_total"]
    else:
        avg_order = 0
    annual_sales_lakh = avg_order * a["annual_orders"]

    lt_cut = (base_total - new_total) / base_total if base_total else 0
    wip_value = kpis["wip_value_lakh"] or 0
    inventory_release = wip_value * lt_cut
    carry_saving = inventory_release * a["inventory_carrying_pct"] / 100
    rework_now = (kpis["rework_cost_lakh"] or 0)
    rework_annual = rework_now / max(1, kpis["jobs_total"]) * a["annual_orders"]
    copq_saving = rework_annual * (1 - rework_factor)
    expedite_saving = annual_sales_lakh * a["expedite_pct_of_sales"] / 100 * lt_cut
    extra_throughput = annual_sales_lakh * min(lt_cut, a["capacity_release_cap_pct"] / 100)
    margin_gain = extra_throughput * a["throughput_margin_pct"] / 100

    annual_benefit = carry_saving + copq_saving + expedite_saving + margin_gain
    invest_lakh = invest * 100
    payback = round(invest_lakh / (annual_benefit / 12), 1) if annual_benefit > 0 else None

    return {
        "stages": [{"seq": s["seq"], "name": s["name"], "short": s["short"],
                    "base": round(base[s["seq"]], 2), "new": new[s["seq"]],
                    "cut": round(base[s["seq"]] - new[s["seq"]], 2),
                    "best_high": s["best_high"]} for s in stats],
        "base_total": round(base_total, 1), "new_total": round(new_total, 1),
        "cut_weeks": round(base_total - new_total, 1), "cut_pct": cut_pct,
        "rework_factor": round(rework_factor, 3),
        "rework_pct_new": round((kpis["rework_pct"] or 0) * rework_factor, 1),
        "invest_cr": round(invest, 2), "applied": applied,
        "financials": {
            "inventory_release": round(inventory_release, 1),
            "carry_saving": round(carry_saving, 1),
            "copq_saving": round(copq_saving, 1),
            "expedite_saving": round(expedite_saving, 1),
            "margin_gain": round(margin_gain, 1),
            "annual_benefit": round(annual_benefit, 1),
            "annual_benefit_cr": round(annual_benefit / 100, 2),
            "payback_months": payback,
            "annual_sales_lakh": round(annual_sales_lakh, 1),
        },
    }


# --------------------------------------------------- predictive delay risk
FEATURE_LABELS = {
    "factor": "Running slower than plan",
    "delay_days": "Delay days logged so far",
    "rework_events": "Rework incidents on this order",
    "po_late_rate": "Share of its POs delivered late",
    "critical_po_open": "Critical POs still open and overdue",
    "expedite": "Expedite priority",
}
MIN_TRAINING_ORDERS = 10


def _risk_features(db):
    """Per-job feature table shared by training (dispatched orders, with a
    known late/on-time label) and scoring (open orders, label unknown)."""
    board = job_board(db, only_open=False)
    rework_by_job = defaultdict(int)
    for r in db.execute("SELECT job_id, SUM(rework) c FROM job_stages GROUP BY job_id").fetchall():
        rework_by_job[r["job_id"]] = r["c"] or 0

    po_by_job = defaultdict(lambda: {"total": 0, "late": 0, "critical_open": 0})
    for r in db.execute(
        """SELECT job_id, COUNT(*) total,
                  SUM(CASE WHEN received_date IS NOT NULL AND received_date > promised_date
                           THEN 1 ELSE 0 END) late,
                  SUM(CASE WHEN critical=1 AND received_date IS NULL AND promised_date < date('now')
                           THEN 1 ELSE 0 END) critical_open
           FROM purchase_orders WHERE job_id IS NOT NULL GROUP BY job_id"""
    ).fetchall():
        po_by_job[r["job_id"]] = {"total": r["total"] or 0, "late": r["late"] or 0,
                                  "critical_open": r["critical_open"] or 0}

    def row_for(r):
        j = r["job"]
        po = po_by_job[j["id"]]
        po_late_rate = (po["late"] / po["total"]) if po["total"] else 0.0
        return [r["factor"], r["delay_days"], rework_by_job[j["id"]], po_late_rate,
                po["critical_open"], 1.0 if (j["priority"] or "").lower() == "expedite" else 0.0]

    dispatched = [r for r in board if r["job"]["status"] == "Dispatched"]
    open_orders = [r for r in board if r["job"]["status"] == "In Progress"]
    return dispatched, open_orders, row_for


def delay_risk_model(db):
    """
    Probability an open order finishes past its committed dispatch date,
    from a small logistic regression trained live on the plant's own
    dispatched-order history - the deck's Year-3 "predictive analytics for
    capacity & delivery risk" lever (config.LEVERS), activated now rather
    than waited for. Retrained on every call (cheap at plant scale), so it
    reflects whatever is currently uploaded, and every prediction carries
    the features that drove it rather than a black-box number.

    This is in-sample fitted, not validated on held-out orders - a
    genuine limitation of scoring on the plant's own limited history,
    stated plainly rather than dressed up as more than it is.
    """
    dispatched, open_orders, row_for = _risk_features(db)
    n = len(dispatched)
    if n < MIN_TRAINING_ORDERS:
        return {"trained": False, "reason": "insufficient_history",
                "n_dispatched": n, "min_required": MIN_TRAINING_ORDERS}

    X = np.array([row_for(r) for r in dispatched], dtype=float)
    y = np.array([1.0 if r["variance_days"] > 0 else 0.0 for r in dispatched], dtype=float)
    if len(set(y.tolist())) < 2:
        return {"trained": False, "reason": "single_class", "n_dispatched": n,
                "min_required": MIN_TRAINING_ORDERS}

    mu, sigma = X.mean(axis=0), X.std(axis=0)
    sigma[sigma == 0] = 1.0
    Xs = (X - mu) / sigma
    Xb = np.hstack([np.ones((n, 1)), Xs])

    # l2 is deliberately strong: with only a few dozen dispatched orders to
    # learn from, an unregularised fit finds a perfectly separating line and
    # saturates every probability to 0 or 100 - confident-looking and wrong.
    # This keeps the model closer to its actual, limited evidence.
    w = np.zeros(Xb.shape[1])
    lr, l2, iters = 0.3, 6.0, 800
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(Xb @ w)))
        grad = Xb.T @ (p - y) / n
        grad[1:] += l2 * w[1:] / n
        w -= lr * grad

    p_train = 1 / (1 + np.exp(-(Xb @ w)))
    hit_rate = float(np.mean((p_train > 0.5) == (y > 0.5)))

    predictions = []
    for r in open_orders:
        xs = (np.array(row_for(r), dtype=float) - mu) / sigma
        prob = float(1 / (1 + np.exp(-(w[0] + xs @ w[1:]))))
        contributions = sorted(zip(FEATURE_LABELS, xs * w[1:]), key=lambda t: -abs(t[1]))
        top = [(FEATURE_LABELS[name], round(float(val), 2)) for name, val in contributions
               if abs(val) > 0.05][:3]
        predictions.append({
            "job_no": r["job"]["job_no"], "customer": r["job"]["customer"],
            "current_stage": r["current_stage"], "committed": r["committed"],
            "probability": round(prob * 100, 1), "top_factors": top,
        })
    predictions.sort(key=lambda p: -p["probability"])

    return {
        "trained": True, "n_dispatched": n, "n_open": len(open_orders),
        "hit_rate_in_sample": round(hit_rate * 100, 1),
        "weights": {FEATURE_LABELS[k]: round(float(v), 2) for k, v in zip(FEATURE_LABELS, w[1:])},
        "predictions": predictions,
    }
