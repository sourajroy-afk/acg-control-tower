"""
Plant configuration and case reference data.

This is the only non-uploaded data in the system. It is configuration
(process design, benchmark ranges, KPI targets, improvement levers and
their assumed effects) rather than operational data. Every job, stage
record, delay log, vendor and purchase order comes from an upload.

Sources for benchmark ranges and lever impact assumptions are carried on
each object so they can be shown in the UI next to the number they justify.
"""

# seq, name, short, acg_weeks, industry_low, industry_high, best_low, best_high, wip_capacity, owner
STAGES = [
    (1, "Engineering Design",      "DESIGN",  4, 2, 3, 1, 2, 3, "Design & Engineering"),
    (2, "Material Sourcing",       "SOURCE",  9, 5, 7, 2, 4, 6, "Supply Chain"),
    (3, "Fabrication",             "FAB",     8, 5, 7, 3, 5, 4, "Fabrication Shop"),
    (4, "Assembly & Integration",  "ASSY",    5, 3, 4, 2, 3, 3, "Assembly"),
    (5, "Electrical / Automation", "E&A",     4, 3, 4, 2, 3, 2, "Automation"),
    (6, "Testing, FAT & Dispatch", "FAT",     4, 2, 3, 1, 2, 2, "Quality & Logistics"),
]

# metric, unit, current FY24-25, Y1, Y2, Y3, lower_is_better
KPI_TARGETS = [
    ("Total Lead Time",                 "weeks",      34,   27,  22,  17,  1),
    ("On-Time Delivery",                "%",          72,   90,  95,  98,  0),
    ("First Time Right",                "%",          78,   90,  95,  97,  0),
    ("Rework %",                        "%",           9,    8,   5,   3,  1),
    ("WIP Inventory",                   "days",       27,   20,  15,  12,  1),
    ("Cost of Poor Quality",            "% of sales", 3.25, 2.5, 2.0, 1.5, 1),
    ("Vendor On-Time Delivery",         "%",          68,   85,  92,  95,  0),
]

DELAY_CATEGORIES = [
    "Methods", "Materials", "Machines-Equipment",
    "Manpower", "Measurement", "Material Movement",
]

ITEM_CATEGORIES = [
    "Castings & Fabrication", "Drives & Motors", "Instrumentation",
    "Electricals & Panels", "Bought-out Assemblies", "Consumables", "General",
]

# ----------------------------------------------------------------- Levers
# Each lever reduces specific stage durations by a percentage at full
# adoption. Percentages sit inside the impact bands used in the deck:
# planning 10-15%, procurement 10-20%, quality 10-15%, flow 5-10%,
# standardisation 10-15% lead-time reduction.
LEVERS = [
    {
        "id": "aps",
        "name": "Advanced Planning & Scheduling (finite capacity)",
        "year": 1,
        "invest_cr": 1.6,
        "stage_pct": {1: 10, 2: 8, 3: 8, 4: 8, 5: 8, 6: 6},
        "rework_pct": 0,
        "note": "Removes queue and buffer time created by manual planning; releases work to a capacity-checked plan.",
    },
    {
        "id": "visibility",
        "name": "Real-time visibility: ERP-MES shopfloor dashboards & alerts",
        "year": 1,
        "invest_cr": 2.2,
        "stage_pct": {1: 5, 2: 6, 3: 7, 4: 7, 5: 6, 6: 8},
        "rework_pct": 5,
        "note": "Exception alerts cut reaction time on slipping stages. This tool is the delivery vehicle for this lever.",
    },
    {
        "id": "vmi",
        "name": "Critical item list, VMI & long-term agreements",
        "year": 1,
        "invest_cr": 0.9,
        "stage_pct": {2: 25},
        "rework_pct": 0,
        "note": "Consumption-based replenishment on the top-value critical items removes the RFQ-PO-lead-time chain.",
    },
    {
        "id": "dual_source",
        "name": "Dual sourcing & supplier base optimisation",
        "year": 2,
        "invest_cr": 0.7,
        "stage_pct": {2: 12},
        "rework_pct": 3,
        "note": "Breaks single-source dependency on castings and drives, the two longest procurement tails.",
    },
    {
        "id": "ftr",
        "name": "First Time Right programme (weld/fitment standard work)",
        "year": 1,
        "invest_cr": 1.1,
        "stage_pct": {3: 14, 4: 12},
        "rework_pct": 45,
        "note": "In-process checks and standard work at the two stages where rework concentrates.",
    },
    {
        "id": "cellular",
        "name": "Cellular layout, line balancing & point-of-use storage",
        "year": 2,
        "invest_cr": 2.4,
        "stage_pct": {3: 10, 4: 10, 5: 6},
        "rework_pct": 5,
        "note": "Cuts internal movement, staging and queue time between work centres.",
    },
    {
        "id": "modular",
        "name": "Modular platforms & standard BOMs",
        "year": 3,
        "invest_cr": 2.0,
        "stage_pct": {1: 35, 2: 10, 3: 6},
        "rework_pct": 10,
        "note": "Configure-to-order from proven modules instead of engineering each order from scratch.",
    },
    {
        "id": "fat",
        "name": "FAT slot planning & parallel documentation",
        "year": 2,
        "invest_cr": 0.5,
        "stage_pct": {6: 28},
        "rework_pct": 0,
        "note": "Books customer FAT slots at order entry and moves validation paperwork off the critical path.",
    },
    {
        "id": "predictive",
        "name": "Predictive analytics for capacity & delivery risk",
        "year": 3,
        "invest_cr": 1.5,
        "stage_pct": {1: 4, 2: 6, 3: 6, 4: 6, 5: 6, 6: 6},
        "rework_pct": 8,
        "note": "Forecasts load and flags at-risk orders early enough to re-sequence rather than expedite.",
    },
]

# Financial assumptions used by the lever simulator. Every one is shown in
# the UI so a reviewer can challenge it.
FIN_ASSUMPTIONS = {
    "annual_orders": 16,                 # MT equipment orders per year (case: 12-18)
    "inventory_carrying_pct": 14.0,      # % per annum on WIP + raw material value
    "expedite_pct_of_sales": 0.6,        # freight, overtime, premium buys
    "throughput_margin_pct": 22.0,       # contribution margin on additional throughput
    "capacity_release_cap_pct": 30.0,    # max share of freed capacity convertible to sales
}

# Reference practices shown on the benchmarking page (from the deck).
BEST_PRACTICES = [
    ("Bosch", "15-25% LT reduction", ["Standard module platforms", "Advanced planning (IBP + APS)",
                                      "Supplier collaboration & VMI", "Digital shopfloor tracking"]),
    ("GE Healthcare", "25-40% LT reduction", ["Early supplier involvement", "Cellular layout & flow",
                                              "Lean + Six Sigma rework reduction"]),
    ("Siemens", "25-40% LT reduction", ["Modularisation & configuration", "Digital twin & simulation",
                                        "Integrated MES traceability", "FAT parallelisation"]),
    ("Danaher", "20-30% LT reduction", ["Value stream mapping", "Pull-based scheduling",
                                        "SMED & quick changeovers", "Visual management & KPI discipline"]),
]

UPLOAD_COLUMNS = [
    "job_no", "customer", "equipment_type", "order_value_lakh", "priority", "order_date",
    "committed_dispatch_date", "stage_seq", "stage_name", "planned_start", "planned_end",
    "actual_start", "actual_end", "rework", "rework_hours", "rework_cost_lakh",
    "delay_category", "delay_reason", "delay_days",
]

PO_COLUMNS = [
    "po_no", "job_no", "vendor", "item", "item_category", "critical",
    "value_lakh", "po_date", "promised_date", "received_date", "single_source", "vendor_location",
]


# ------------------------------------------------------- ingestion tolerance
# Header aliases, so an export straight out of SAP or a planning sheet loads
# without anyone renaming columns first. Keys are normalised (lower case,
# spaces and punctuation collapsed to underscore) before lookup.
COLUMN_ALIASES = {
    "job_no": ["order_no", "order_number", "job_number", "so_no", "sales_order",
               "sales_order_no", "work_order", "wo_no", "project_no", "job"],
    "customer": ["customer_name", "sold_to", "client", "party", "customer_desc"],
    "equipment_type": ["equipment", "machine", "machine_type", "product",
                       "product_type", "material_desc", "equipment_desc"],
    "order_value_lakh": ["order_value", "value_lakh", "po_value", "order_value_inr_lakh",
                         "net_value", "amount_lakh"],
    "priority": ["order_priority", "expedite", "rush"],
    "order_date": ["po_date", "po_confirmation_date", "order_confirmation_date",
                   "so_date", "booking_date", "order_dt"],
    "committed_dispatch_date": ["committed_date", "promise_date", "customer_promise_date",
                                "commit_date", "due_date", "delivery_date", "cdd"],
    "stage_seq": ["stage_no", "stage", "operation_no", "op_no", "step", "seq"],
    "stage_name": ["stage_desc", "operation", "operation_desc", "process_stage", "activity"],
    "planned_start": ["plan_start", "sched_start", "scheduled_start", "basic_start"],
    "planned_end": ["plan_end", "plan_finish", "sched_end", "scheduled_finish", "basic_finish"],
    "actual_start": ["act_start", "actual_start_date", "start_date", "actual_begin"],
    "actual_end": ["act_end", "actual_finish", "actual_end_date", "finish_date", "completion_date"],
    "rework": ["rework_flag", "is_rework", "rework_yn"],
    "rework_hours": ["rework_hrs", "rw_hours"],
    "rework_cost_lakh": ["rework_cost", "rw_cost_lakh"],
    "delay_category": ["cause_category", "reason_category", "fishbone_category", "category"],
    "delay_reason": ["cause", "reason", "delay_desc", "remarks"],
    "delay_days": ["delay", "days_lost", "slip_days", "variance_days"],
    # purchase order file
    "po_no": ["purchase_order", "po_number", "po"],
    "vendor": ["vendor_name", "supplier", "supplier_name", "vendor_desc"],
    "item": ["item_desc", "material", "material_desc", "part", "part_desc", "description"],
    "item_category": ["category", "commodity", "material_group", "item_group"],
    "critical": ["critical_item", "is_critical", "critical_yn"],
    "value_lakh": ["po_value_lakh", "value", "net_value"],
    "promised_date": ["vendor_promise_date", "promise_date", "committed_date", "expected_date"],
    "received_date": ["grn_date", "receipt_date", "actual_receipt_date", "gr_date"],
    "single_source": ["sole_source", "single_sourced"],
    "vendor_location": ["location", "vendor_city", "city"],
}

# Settings that Plant Setup is allowed to edit, with a label and unit.
EDITABLE_SETTINGS = [
    ("annual_orders", "Equipment orders per year", "orders"),
    ("inventory_carrying_pct", "Inventory carrying cost", "% p.a."),
    ("expedite_pct_of_sales", "Expedite, freight and overtime", "% of sales"),
    ("throughput_margin_pct", "Contribution margin on throughput", "%"),
    ("capacity_release_cap_pct", "Freed capacity convertible to sales", "% cap"),
]


# ------------------------------------------------------------- access control
# Roles mirror the lever owners and governance roles in the deck (section 07
# "People & governance", section 06 "Decisions & owners") plus Admin and a
# read-only Viewer for executives who should see the tower but not edit it.
ROLES = ["Admin", "Plant Head", "Engineering", "Procurement", "Quality",
         "Production", "Automation", "Sales", "Planner", "Viewer"]

# Allowed to edit Plant Setup, run a full data reset and read the Audit Trail.
ADMIN_ROLES = ["Admin", "Plant Head"]

# Every role except Viewer can sign off work on the shop floor and upload
# plant data - a pilot with five lever owners plus planners on the ground.
WRITE_ROLES = [r for r in ROLES if r != "Viewer"]

# Where each role lands right after signing in, so the tool opens on the
# page that role actually works from rather than always the shared Control
# Tower. A deep link (?next=) always wins over this.
ROLE_LANDING = {
    "Sales": "schedule",          # capable-to-promise, and now the quote log
    "Procurement": "procurement",
    "Quality": "root_cause",
    "Production": "floor",
    "Automation": "floor",
    "Planner": "schedule",
    "Engineering": "jobs_list",
}

# Action-board "owner" labels (as written by engine.action_board, which
# mirrors config.STAGES.owner_function and the two hard-coded categories
# below) that belong to each role, so a functional account's Control Tower
# opens on its own exception queue instead of everyone's. Renaming a
# stage's owner function in Plant Setup changes what that stage's items are
# tagged with, so keep it in one of these strings if the queue should still
# pick it up. Plant Head, Admin and Viewer intentionally see everything and
# are not filtered.
ROLE_OWNER_LABELS = {
    "Engineering": ["Design & Engineering"],
    "Procurement": ["Supply Chain"],
    "Production": ["Fabrication Shop", "Assembly"],
    "Automation": ["Automation"],
    "Quality": ["Quality & Logistics"],
    "Planner": ["Production Planning"],
}

# Roles allowed to log a customer quote and mark it won or lost.
QUOTE_ROLES = ["Sales", "Plant Head", "Admin"]

# Roles allowed to place or release a quality hold on a stage.
QUALITY_HOLD_ROLES = ["Quality", "Plant Head", "Admin"]

# Basic brute-force protection: an account locks itself out for this many
# minutes after this many consecutive failed sign-ins.
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_MINUTES = 15

# A delay log at or past this many days fires a webhook alert (if one is
# configured in Plant Setup) instead of waiting to be seen on a dashboard.
DELAY_ALERT_THRESHOLD_DAYS = 7

# Seeded on first run so the tool is usable without a separate identity
# system during the pilot. Real deployment should replace this with SSO /
# ACG's own directory - see README "Where it goes next".
DEMO_PASSWORD = "AcgShirwal26"
DEMO_USERS = [
    ("admin",       "System Administrator", "Admin"),
    ("planthead",   "Plant Head",            "Plant Head"),
    ("engineering", "Engineering Lead",       "Engineering"),
    ("procurement", "Procurement Lead",       "Procurement"),
    ("quality",     "Quality Lead",           "Quality"),
    ("production",  "Production Lead",        "Production"),
    ("automation",  "Automation Lead",        "Automation"),
    ("sales",       "Sales Lead",             "Sales"),
    ("planner",     "Production Planner",     "Planner"),
    ("viewer",      "Executive Viewer",       "Viewer"),
]


# ------------------------------------------------------- business case (deck)
# Numbers as presented to ACG in section 08/09/14 of the deck, kept here so
# the app can show the same case it was built to justify. Everything here is
# the deck's own figures, not something the app computes from uploaded data -
# each one is labelled "sourced", "assumption" or "modelled" per the deck's
# own source register (appendix A7), and stays static until ACG Finance
# validates it during the pilot.
BUSINESS_CASE = {
    "investment_cr": 5.5,
    "run_rate_ebitda_cr": 6.4,
    "payback_months": 18,
    "margin_points": 5.3,
    "cumulative_net_cr": [("Year 1", -0.9), ("Year 2", 1.0), ("Year 3", 6.4)],
    "spend_breakdown": [
        ("Control Tower & SAP link", 0.8),
        ("Advanced Planning & Scheduling", 0.9),
        ("IIoT shop-floor sensing", 0.6),
        ("Fixtures, kitting, point-of-use storage", 1.2),
        ("Module platform engineering", 1.2),
        ("Supplier development", 0.2),
        ("Capability building & PMO", 0.6),
    ],
    "assumptions": [
        ("Processing-equipment revenue base", "₹120 Cr (range ₹100–150 Cr)", "Assumption",
         "~20% of ACG Pam's ₹465–538 Cr"),
        ("Orders and order value", "48 orders × ₹2.5 Cr", "Assumption", "Typical HSM / FBE / GT mix"),
        ("Contribution margin", "30%", "Assumption", "Team estimate, to confirm with ACG Finance"),
        ("Win-rate uplift", "+2 pts on ~₹480 Cr of quotes", "Assumption", "Quote-to-win by promised lead time"),
        ("Cost of poor quality", "3.25% → 1.5% of sales", "Assumption", "Round-1 baseline"),
        ("Liquidated damages exposure", "0.5% of order value per week late", "Assumption", "Typical contract terms"),
        ("Customer advance", "30% at PO", "Assumption", "Typical Indian capital-equipment terms"),
    ],
    "scenarios": [
        ("Conservative", "22 wk", "₹3.2 Cr", "~month 37", "1.0×",
         "₹100 Cr base, +1 pt win-rate, COPQ to 2.0%, half the LD savings, 10% capex overrun"),
        ("Base case", "20 wk", "₹6.4 Cr", "~month 18", "2.1×", "As modelled in the business case above"),
        ("Upside", "18 wk", "₹9.8 Cr", "~month 13", "3.3×", "₹150 Cr base, +3 pts win-rate"),
    ],
    "what_a_week_is_worth": [
        ("EBITDA a year per week of lead time removed", "₹46 lakh", "₹6.4 Cr ÷ 14 weeks"),
        ("Order book sitting in each week of lead time", "₹2.3 Cr", "₹120 Cr ÷ 52 weeks"),
        ("Gross WIP per week of post-material lead time", "₹1.5 Cr", "material + half of conversion"),
        ("LD exposure per order, per week late", "₹1.25 lakh", "0.5% of a ₹2.5 Cr order"),
    ],
    "the_ask": "Approve a 90-day, ₹0.6 Cr pilot on 4 live GT X•ONE / FBE orders, name five lever "
               "owners, and run a weekly Control Tower review chaired by the Plant Head. Go / no-go "
               "for plant roll-out at Day 90.",
}

RISK_REGISTER = [
    ("Customer changes after freeze", "High",
     "Change clause re-quotes price and date; the configurator cuts the need for changes", "Sales / Engineering"),
    ("Suppliers resist frame contracts / VMI", "Medium",
     "Pool volume across ACGE plants; Control Tower vendor scorecards", "Procurement"),
    ("Pharma capex dip strands the long-lead buffer", "Medium",
     "Buffer only multi-use items; consignment; monthly re-sizing", "Procurement"),
    ("Incomplete SAP time-stamps", "Medium",
     "Tolerant ingestion plus tablet sign-off; one data owner per stage", "Plant IT"),
    ("Shop-floor change fatigue", "Low",
     "Borrow the Packaging Shirwal Lighthouse team; FTR-linked incentives", "Plant Head"),
]

# 90-day pilot proof points (deck section 06 / appendix A6)
PILOT_PROOF_POINTS = [
    ("P80 promise accuracy", "≥85%"),
    ("Weeks saved vs baseline stage times", "≥3 wk"),
    ("Kit completeness at release", "≥95%"),
]
