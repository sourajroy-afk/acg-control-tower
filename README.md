# ACG Engineering, Shirwal Plant — Lead Time Control Tower

A working operations tool for the case *Reducing Lead Time for Processing
Equipment at ACG's Shirwal Plant*. The deck says the Year 1 lever is real-time
visibility, finite-capacity planning and root-cause discipline. This is that
lever built, not described: the plant loads its planning and procurement
exports, and the tool computes lead time, delivery risk, bottlenecks, vendor
performance, root causes and the financial case from that data.

## Run it

```bash
pip install -r requirements.txt
python app.py            # http://127.0.0.1:5000
```

The database and reference tables are created on first run. The tool opens
empty and offers one button: **Load demo dataset**. That loads two ordinary
CSV files from `demo_data/` through the same ingestion path as a real upload,
giving 24 months of plant history (37 orders, 180 stage records, 188 purchase
orders, 14 vendors).

Regenerate the demo files any time with `python generate_demo_data.py`. They
are dated relative to today, so the tool never looks stale.

## What each page does

| Page | Question it answers |
|---|---|
| Control Tower | How is order-to-dispatch performing, which orders will miss, and what has to be fixed today |
| Order Book | Every order across the six stages with a forecast dispatch date and a risk band |
| Order detail | Plan against actual per stage, the delay log, and the purchase orders blocking it |
| Capacity & Promise | Stage load against WIP capacity for the next 8-20 weeks, bottleneck ranking, and a capable-to-promise date for a new enquiry |
| Procurement | Vendor scorecard, procurement cycle by item category, and the expedite list of overdue POs |
| Root Cause | Live Pareto of delay days, a cause-by-stage matrix, and rework concentration |
| Benchmarking | Live stage times against the industry and best-in-class ranges, plus reference practices |
| Lever Simulator | Drag adoption on the nine improvement levers and watch lead time, KPI position, benefit and payback rebuild |
| Roadmap & KPIs | The three-year plan, live KPI tracking against Year 1/2/3 targets, and the risk register |
| Shop Floor | Record a stage start or completion, rework and delay cause, signed and timestamped |
| Plant Setup | Stage list, base weeks, WIP capacity, KPI targets and financial assumptions, edited without touching code |
| Data Ops | Uploads, templates, record counts, ingestion history, rejected-row downloads, reset |

## The models behind the numbers

Nothing on the screen is a stored constant except the plant configuration in
`config.py` (six stages with their base-case and benchmark weeks, KPI targets,
lever definitions, financial assumptions).

**Delivery forecast.** For each open order: remaining stage work is the
base-case duration of the stages still to come, scaled by how fast this
particular order has been running against plan, plus queue time wherever the
next stages already hold more orders than their WIP capacity. Forecast dispatch
is compared with the customer commitment and banded: on track, watch (1-14
days late), at risk (15-42), critical (over 42).

**Capable-to-promise.** P50 and P80 stage durations from completed history,
plus the queue in front of each stage today. Quote the customer the P80 date
and plan internally to P50.

**Bottleneck ranking.** Weeks over best-in-class, weighted by current stage
utilisation and logged delay days. This is why the ranking is not simply the
longest stage.

**Lever simulator.** Stage effects stack multiplicatively, so two levers on the
same stage never double count the same week. The financial build-up is working
capital released from WIP and its carrying cost, cost of poor quality avoided,
expedite and freight avoided, and margin on the throughput freed capacity can
carry, capped at 30% conversion. Every assumption is printed under the panel
and editable in `config.py`.

**What is deliberately not modelled.** WIP inventory in days of supply needs
the stores ledger, which the upload format does not carry, so the roadmap shows
it unmeasured rather than estimated. Cost of poor quality here is shop-floor
rework only, not scrap or warranty. OEE needs machine-level run data.

## Recording work in the app

The plant does not have to re-upload a file to move an order forward. **Shop
Floor** takes one stage at a time: pick the order and stage, enter the start or
completion date, tick rework, tag a delay cause, and sign it with a name. The
same panel sits on every order detail page, pre-selected to that order's next
stage.

Every entry goes through the same validation as an upload: the date must parse
and cannot be in the future, a completion cannot precede its start, and a stage
cannot be closed before the one ahead of it has started. Corrections are
allowed and kept. Each write lands in `stage_events` with who recorded it, when,
and whether it came from the floor or an upload, so any date on the dashboard can
be traced back to a person.

## Configuration without editing code

**Plant Setup** edits the six stages (name, base weeks, industry and
best-in-class ranges, WIP capacity, owning function), the KPI targets for
Year 1/2/3, and the financial assumptions behind the simulator. Assumptions
left untouched are shown as defaults, so a reviewer can see exactly what the
plant changed. `config.py` still holds the shipped defaults; the database
overrides them.

## Upload format

Two files, both CSV or Excel, both re-uploadable. Identical rows are skipped as
duplicates, changed values on an existing record are treated as corrections,
new rows are added, and every load is recorded in the ingestion history.

- **Stage progress** — one row per order per stage. Required: `job_no`,
  `customer`, `equipment_type`, `order_date`, and either `stage_seq` or
  `stage_name`. Optional: values, priority, plan and actual dates, rework,
  delay category, reason and days.
- **Purchase orders** — one row per PO line. Required: `po_no`, `vendor`,
  `item`, `po_date`, `promised_date`. Optional: `job_no` to link the PO to an
  order, category, criticality, value, received date, single-source flag.

Templates for both are downloadable from Data Ops. Full column reference is on
the same page.

**It accepts what the plant actually exports.** Headers are mapped through an
alias table, so "Order No", "Sales Order", "PO Date", "Basic Finish", "GRN Date"
and similar load without anyone renaming a column. Dates parse day-first or ISO
(14-06-2026, 14/06/2026, 14.06.26, 14-Jun-2026, 2026-06-14) and Excel serial
numbers. A row that still cannot be read is rejected on its own, with the reason
and its spreadsheet line number, while the rest of the file loads. The rejected
rows are downloadable as a CSV from the ingestion history, so they can be fixed
and re-uploaded on their own.

## Demo script for the video

1. Control Tower — lead time, OTD, FTR, COPQ and the trend against the roadmap
   glide path; the action board is the daily huddle list.
2. Order Book — pick a critical order, open it, show plan against actual and the
   overdue purchase order underneath it.
3. Capacity & Promise — show the red cells where a stage is over capacity, then
   the P80 promise date a planner can quote today.
4. Root Cause — the fishbone from the deck as a live Pareto and a cause-by-stage
   matrix.
5. Lever Simulator — drag the Year 1 levers to full adoption and show lead time
   fall toward 27 weeks with the benefit and payback recalculated.

## Files

```
app.py                  routes, ingestion, exports
engine.py               KPI, risk, capacity, vendor, promise and simulation models
config.py               stages, KPI targets, levers, financial assumptions
schema.sql              SQLite schema
generate_demo_data.py   builds the two demo CSV files
demo_data/              the demo upload files
templates/              Jinja2 templates
static/css/style.css    ACG-themed stylesheet
```

## Where it goes next

- Add login so `recorded_by` is the signed-in user rather than a typed name.
- Replace SQLite with Postgres and this becomes multi-user for the plant.
- Point the ingestion at a scheduled export from SAP so it refreshes each morning
  instead of being uploaded by hand.
- Add stage-level labour hours to turn the capacity grid from order counts into
  true finite-capacity scheduling.
