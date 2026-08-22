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
    source TEXT NOT NULL DEFAULT 'floor',   -- floor / upload
    at TEXT NOT NULL DEFAULT (datetime('now'))
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
