-- ACG Engineering, Shirwal Plant
-- Lead Time Control Tower  |  schema

CREATE TABLE IF NOT EXISTS stages (
    id INTEGER PRIMARY KEY,
    seq INTEGER NOT NULL,
    name TEXT NOT NULL,
    short_name TEXT NOT NULL,
    acg_weeks REAL NOT NULL,
    industry_low REAL NOT NULL,
    industry_high REAL NOT NULL,
    best_in_class_low REAL NOT NULL,
    best_in_class_high REAL NOT NULL,
    wip_capacity INTEGER NOT NULL DEFAULT 4,   -- jobs the stage can hold concurrently
    owner_function TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_no TEXT UNIQUE NOT NULL,
    customer TEXT NOT NULL,
    equipment_type TEXT NOT NULL,
    order_value_lakh REAL NOT NULL DEFAULT 0,
    order_date TEXT NOT NULL,
    committed_dispatch_date TEXT NOT NULL,
    actual_dispatch_date TEXT,
    priority TEXT NOT NULL DEFAULT 'Standard',
    status TEXT NOT NULL DEFAULT 'In Progress',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS job_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    stage_id INTEGER NOT NULL REFERENCES stages(id),
    planned_start TEXT,
    planned_end TEXT,
    actual_start TEXT,
    actual_end TEXT,
    status TEXT NOT NULL DEFAULT 'Pending',
    rework INTEGER NOT NULL DEFAULT 0,
    rework_hours REAL DEFAULT 0,
    rework_cost_lakh REAL DEFAULT 0,
    quality_hold INTEGER NOT NULL DEFAULT 0,
    quality_hold_reason TEXT NOT NULL DEFAULT '',
    quality_hold_by TEXT NOT NULL DEFAULT '',
    UNIQUE(job_id, stage_id)
);

CREATE TABLE IF NOT EXISTS delay_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_stage_id INTEGER NOT NULL REFERENCES job_stages(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    reason TEXT NOT NULL,
    delay_days REAL NOT NULL DEFAULT 0,
    logged_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    item_category TEXT NOT NULL DEFAULT 'General',
    location TEXT DEFAULT '',
    single_source INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_no TEXT UNIQUE NOT NULL,
    job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
    item TEXT NOT NULL,
    item_category TEXT NOT NULL DEFAULT 'General',
    critical INTEGER NOT NULL DEFAULT 0,
    value_lakh REAL NOT NULL DEFAULT 0,
    po_date TEXT NOT NULL,
    promised_date TEXT NOT NULL,
    received_date TEXT
);

CREATE TABLE IF NOT EXISTS kpi_targets (
    id INTEGER PRIMARY KEY,
    metric TEXT NOT NULL,
    unit TEXT NOT NULL,
    current_fy2425 REAL,
    year1 REAL,
    year2 REAL,
    year3 REAL,
    lower_is_better INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS ingest_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    kind TEXT NOT NULL,
    rows_read INTEGER NOT NULL DEFAULT 0,
    added INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    duplicates INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    uploaded_by TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_js_job ON job_stages(job_id);
CREATE INDEX IF NOT EXISTS idx_js_stage ON job_stages(stage_id);
CREATE INDEX IF NOT EXISTS idx_po_job ON purchase_orders(job_id);
CREATE INDEX IF NOT EXISTS idx_po_vendor ON purchase_orders(vendor_id);

-- Who recorded what on the floor, and when. Every stage sign-off made in the
-- app writes one row here, so a date can always be traced back to a person.
CREATE TABLE IF NOT EXISTS stage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_stage_id INTEGER NOT NULL REFERENCES job_stages(id) ON DELETE CASCADE,
    action TEXT NOT NULL,              -- Started / Completed / Corrected / Rework logged / Delay logged
    detail TEXT DEFAULT '',
    recorded_by TEXT NOT NULL DEFAULT 'unknown',
    recorded_by_role TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'floor',   -- floor / upload
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Signed-in accounts. Every write in the app (floor sign-off, upload, plant
-- setup change) is attributed to one of these, which is what makes the
-- audit trail and the lever-owner roles in the deck meaningful rather than
-- a typed name that anyone could enter.
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at TEXT,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT
);

-- Rows an upload could not accept, kept against the ingest batch so whoever
-- loaded the file can download exactly what failed and why.
CREATE TABLE IF NOT EXISTS ingest_rejects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ingest_id INTEGER REFERENCES ingest_log(id) ON DELETE CASCADE,
    row_no INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL,
    raw TEXT NOT NULL DEFAULT ''
);

-- Planning and financial assumptions editable from Plant Setup. Falls back to
-- the defaults in config.py when a key is absent.
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_se_js ON stage_events(job_stage_id);
CREATE INDEX IF NOT EXISTS idx_ir_batch ON ingest_rejects(ingest_id);

-- Sales' capable-to-promise quotes, logged so quote-to-win by promised lead
-- time (the deck's own win-rate assumption, appendix A7) becomes something
-- the pilot can actually measure instead of only assume.
CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer TEXT NOT NULL,
    equipment_type TEXT NOT NULL,
    p50_weeks REAL NOT NULL,
    p80_weeks REAL NOT NULL,
    p50_date TEXT NOT NULL,
    p80_date TEXT NOT NULL,
    quoted_by TEXT NOT NULL DEFAULT '',
    quoted_by_role TEXT NOT NULL DEFAULT '',
    quoted_at TEXT NOT NULL DEFAULT (datetime('now')),
    outcome TEXT NOT NULL DEFAULT 'Open',   -- Open / Won / Lost
    decided_at TEXT,
    job_no TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_quotes_outcome ON quotes(outcome);

-- Personal access tokens for read-only system integration (Power BI, a
-- scheduled SAP job, another dashboard). The raw token is shown once at
-- creation and never stored - only its hash, so a leaked database still
-- does not leak usable credentials.
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label TEXT NOT NULL DEFAULT '',
    token_hash TEXT UNIQUE NOT NULL,
    token_prefix TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);

-- Documentation/readiness checklist per order (design docs, weld log, IQ/OQ,
-- FAT slot, dispatch docs). A row's presence means that item is done; there
-- is no "not done" row to avoid pre-populating every job x item combination.
CREATE TABLE IF NOT EXISTS job_compliance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    item_key TEXT NOT NULL,
    done_by TEXT NOT NULL DEFAULT '',
    done_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(job_id, item_key)
);
CREATE INDEX IF NOT EXISTS idx_compliance_job ON job_compliance(job_id);

-- Every webhook alert attempt, so "did that alert actually fire" has an
-- answer without checking the Slack channel.
CREATE TABLE IF NOT EXISTS alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    success INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    sent_at TEXT NOT NULL DEFAULT (datetime('now'))
);
