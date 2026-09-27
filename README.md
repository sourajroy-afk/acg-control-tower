# ACG Engineering, Shirwal Plant — Lead Time Control Tower

A working operations tool for the case *Reducing Lead Time for Processing
Equipment at ACG's Shirwal Plant*. The deck says the Year 1 lever is real-time
visibility, finite-capacity planning and root-cause discipline. This is that
lever built, not described: the plant loads its planning and procurement
exports, and the tool computes lead time, delivery risk, bottlenecks, vendor
performance, root causes and the financial case from that data.

Signed-in accounts with the deck's own lever-owner roles, an audit trail, a
live business-case page and a guided tour make it something ACG could put in
front of a plant team rather than only a reviewer — see "Accounts & roles"
and "Guided tour" below.

## Run it

```bash
pip install -r requirements.txt
python app.py            # http://127.0.0.1:5000
```

The database and reference tables are created on first run, along with ten
demo accounts (see "Accounts & roles"). Sign in, then the tool opens empty and
offers one button: **Load demo dataset**. That loads two ordinary
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
| Capacity & Promise | Stage load against WIP capacity for the next 8-20 weeks, bottleneck ranking, a capable-to-promise date for a new enquiry, and Sales' quote log with win/loss tracking |
| Procurement | Vendor scorecard, procurement cycle by item category, and the expedite list of overdue POs |
| **Vendor Risk** | A forward-looking risk score per vendor — late-delivery rate, single-source dependency, open critical POs — distinct from the historical scorecard on Procurement |
| Root Cause | Live Pareto of delay days, a cause-by-stage matrix, rework concentration, and active quality holds |
| Benchmarking | Live stage times against the industry and best-in-class ranges, plus reference practices |
| Lever Simulator | Drag adoption on the nine improvement levers and watch lead time, KPI position, benefit and payback rebuild |
| **Predictive Risk** | A logistic regression trained live on the plant's own dispatched-order history, scoring every open order's probability of missing commitment, with the driving factors shown |
| Roadmap & KPIs | The three-year plan, live KPI tracking against Year 1/2/3 targets, and the risk register |
| **Business Case** | The deck's own numbers, live in the tool: the &#8377;5.5 Cr investment, &#8377;6.4 Cr run-rate EBITDA, payback, a scenario stress test and the risk register behind the pilot ask |
| Shop Floor | Record a stage start or completion, rework and delay cause, signed and timestamped to your account |
| **Compliance** | Design docs, weld log, IQ/OQ documentation, FAT slot and dispatch docs, checked off per open order |
| Plant Setup | Stage list, base weeks, WIP capacity, KPI targets, financial assumptions and the alert webhook, edited without touching code — Admin / Plant Head only |
| Data Ops | Uploads, templates, record counts, ingestion history, rejected-row downloads, reset |
| **Audit Trail** | Every floor entry, upload and setting change, who did it and when — Admin / Plant Head only |
| **Notifications** | Every webhook alert the tool has tried to send, sent or failed, with the error if it failed — Admin / Plant Head only |
| **My Account** | Change your password; create or revoke a read-only API key |

## Accounts & roles

The tool is signed in, not open. Roles mirror the lever owners and governance
structure in the deck (section 07, "People & governance"): **Admin**, **Plant
Head**, **Engineering**, **Procurement**, **Quality**, **Production**,
**Automation**, **Sales**, **Planner**, and a read-only **Viewer** for
executives who should see the tower but never edit it.

- Every role except Viewer can sign off shop-floor work and upload plant data.
- Only Admin and Plant Head can edit **Plant Setup**, run a full **data
  reset**, or read the **Audit Trail**.
- Every write — a shop-floor sign-off, an upload, a plant-setting change — is
  attributed to the signed-in account, not a typed name.

Ten demo accounts are seeded on first run, one per role, all sharing the demo
password shown on the sign-in page (`AcgShirwal26`, also in `config.py` as
`DEMO_PASSWORD`). Click a role chip on the sign-in page to fill the form.
**Replace this with ACG's own directory / SSO before a real plant roll-out** —
see "Where it goes next".

## What each role actually gets

Signing in is not just a gate — each role opens on the page and the queue it
actually works from, so the tool is something a department uses daily rather
than a shared screen everyone has to filter for themselves:

| Role | Lands on | Gets |
|---|---|---|
| Plant Head, Admin | Control Tower | Everything: full action board, Plant Setup, data reset, Audit Trail |
| Engineering | Order Book | "Your queue" filtered to Engineering Design exceptions |
| Procurement | Procurement | "Your queue" filtered to sourcing/PO exceptions; vendor scorecard |
| Quality | Root Cause | "Your queue" filtered to testing/dispatch exceptions; can place or release a **quality hold** on any stage |
| Production | Shop Floor | "Your queue" filtered to fabrication/assembly exceptions |
| Automation | Shop Floor | "Your queue" filtered to electrical/automation exceptions |
| Planner | Capacity & Promise | "Your queue" filtered to delivery-risk exceptions; capacity grid |
| Sales | Capacity & Promise | The quote tool: get a P80 promise date for a new enquiry, log it, and mark it Won/Lost |
| Viewer | Control Tower | Read-only — sees everything, changes nothing |

Two features give this real teeth rather than just a filtered view:

- **Quality hold.** Quality (or Plant Head/Admin) can place a hold on any
  stage with a reason. A held stage cannot be marked complete on Shop Floor —
  the write is refused with the reason — until Quality releases it. This is
  the "quality at source" lever (deck appendix A4) enforced in the tool, not
  only described. Active holds show on the Root Cause page and on the order
  itself.
- **Sales quote log.** Every capable-to-promise quote Sales gives a customer
  is logged with its P80 date, then marked Won or Lost. The Business Case
  page shows the resulting win rate overall and split by promised lead time
  (&#8804;20 weeks vs &gt;20), which is exactly the assumption the deck asks
  ACG Finance to validate ("customers buy a date") — measured here instead
  of only modelled.

"Your queue" reads the same `owner_function` already set per stage in Plant
Setup, mapped to roles in `config.ROLE_OWNER_LABELS`. Renaming a stage's
owner function there should keep the mapping in mind if a queue should keep
matching it.

## Guided tour

A "?" icon in the top bar (and an automatic prompt on first visit to the
Control Tower) starts a guided tour that spotlights every page and panel in
turn, navigating for you and resuming automatically as it moves between
pages. Admin-only pages (Plant Setup, Audit Trail) only appear in the tour
for accounts that can actually open them. Useful for a first-time reviewer,
a plant walkthrough, or the demo video. Skip anytime with the button or Esc;
reopen it from the "?" icon whenever.

## Predictive delay risk

**Predictive Risk** trains a small logistic regression, from scratch, on the
plant's own dispatched orders every time the page loads - six features
(running rate vs plan, delay days logged, rework incidents, share of POs
delivered late, open critical POs, expedite priority), standardised and
fitted with a deliberately strong L2 penalty so a few dozen orders don't
produce a model that swings to 0% or 100% on everything. It scores every
open order's probability of missing its committed dispatch date and shows
the two or three features that drove that number - never a bare score with
no explanation. This is `config.LEVERS`' Year-3 "predictive analytics for
capacity & delivery risk" lever, built now instead of waited for.

It says plainly when it isn't ready: fewer than `engine.MIN_TRAINING_ORDERS`
(10) dispatched orders, or a history that is all-late or all-on-time, and
the page explains what is missing rather than guessing. The in-sample hit
rate shown is exactly that - measured on the data it trained on, not a
held-out set - and the page says so.

## Search, integrations & alerts

- **Search everything.** Press **Ctrl/Cmd+K** anywhere in the tool, or click
  the search box in the top bar, to jump straight to an order, a purchase
  order or a vendor by number or name (`/api/search`).
- **API keys.** From your account page, generate a read-only bearer token
  for `/api/kpis`, `/api/board`, `/api/promise` and `/api/search` - point
  Power BI, a scheduled script or another dashboard at the plant's live
  numbers without a browser session. The raw token is shown once, at
  creation; only its hash is stored, so a leaked database leaks nothing
  usable. Revoke a key from the same page and it stops working immediately.
- **Slack / Teams alerts.** Plant Setup takes an incoming-webhook URL (any
  Slack-compatible one, including Teams' Workflows connector). Once set, the
  tool posts there when Quality places or releases a hold, and when a delay
  of `config.DELAY_ALERT_THRESHOLD_DAYS` (7) or more days is logged on the
  shop floor - the daily-exception governance the deck asks for (section
  06), pushed to wherever the plant already talks rather than only shown on
  a dashboard someone has to remember to open. A "Send test alert" button
  confirms it is wired up. A webhook that can't be reached is logged and
  ignored - it never breaks the sign-off or the hold it was reporting on.
- **Notifications** (Admin / Plant Head) shows every alert attempt the
  webhook has made - sent or failed, with the error if it failed - so
  whether an alert actually reached the channel never depends on someone
  checking Slack.

## Vendor Risk

Distinct from the historical performance scorecard on Procurement: **Vendor
Risk** scores how much exposure each supplier represents to orders still in
flight. `engine.vendor_risk()` weights late-delivery rate (40), single-source
dependency (25), open critical POs already overdue (up to 20) and other
overdue POs (up to 15) into a 0-100 score, banded Low/Medium/High/Critical.
Weighted by hand, not learned, so the weights are visible on the page to
argue with. This is the deck's dual-sourcing lever (L2) and long-lead buffer
(appendix A3) pointed at live procurement data instead of a one-time review.

## Compliance

A documentation-readiness checklist per open order - design docs, weld log,
IQ/OQ documentation, customer FAT slot, dispatch docs - the deck's "digital
IQ/OQ templates" and parallel-documentation lever (L5) and the turnover-
package traceability in appendix A4. Quality, Automation, Plant Head and
Admin accounts can check an item off; everyone can see the readiness
percentage per order and how many orders already in Electrical/Automation
or later still have no FAT slot booked - the deck's own example exception
(SO-4490). Informational today: it does not yet block a stage from
completing the way a quality hold does - see "Where it goes next".

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
app.py                  routes, auth, webhooks, API keys, ingestion, exports
engine.py               KPI, risk, capacity, vendor risk, promise, simulation and predictive-risk models
config.py               stages, KPI targets, levers, roles, business case, compliance items, financial assumptions
schema.sql              SQLite schema, incl. users, api_keys, quotes, job_compliance, alert_log, stage_events
generate_demo_data.py   builds the two demo CSV files
demo_data/              the demo upload files
templates/              Jinja2 templates (login, audit, business_case, predictive, vendor_risk, compliance among them)
static/css/style.css    ACG-themed stylesheet, incl. login page, guided tour and search palette
static/js/tour.js       the guided tour engine
static/js/search.js     the command-palette search (Ctrl/Cmd+K)
```

## Security & operations

Hardening that a real pilot deployment needs, not just a reviewer demo:

- **CSRF protection.** Every POST form carries a per-session token, checked
  on every state-changing request; a forged or stale request is refused with
  a plain "refresh and try again" message instead of silently succeeding.
- **Session secret.** `ACG_SECRET` should be set to a fixed, secret value in
  any deployment with more than one worker or that needs sessions to survive
  a restart. If it is not set, the tool generates a random one at startup
  and logs a warning — safe for a single local process, wrong for production.
- **Login lockout.** An account locks itself out for `LOGIN_LOCKOUT_MINUTES`
  (15) after `LOGIN_MAX_ATTEMPTS` (5) consecutive wrong passwords, both in
  `config.py`.
- **Self-service password change** at the account page (click your name in
  the sidebar). **Admin/Plant Head account management** on the Audit Trail
  page: reset anyone's password or disable/re-enable an account, without
  touching the database directly.
- **`/healthz`** returns `{"status": "ok"}` (200) once the database is
  reachable, or 503 if not — for a load balancer or Cloud Run readiness probe.
  It needs no sign-in, unlike every other route.
- **`ACG_DEMO_PASSWORD`** overrides the shared demo password shown on the
  sign-in page, so a pilot can ship with its own without editing code.

## Where it goes next

- Replace the seeded demo accounts with ACG's own directory / SSO (Azure AD,
  Okta, or SAP identity) instead of the shared demo password.
- Replace SQLite with Postgres and this becomes multi-user for the plant at
  real concurrency, and lets the audit trail hold years of history. It also
  removes the one limitation of the current login-lockout/disable model:
  because sessions are signed cookies with no server-side session store, a
  disabled account's already-open browser session stays valid until it
  expires or the person signs out — there is nowhere yet to revoke it early.
- Point the ingestion at a scheduled export from SAP so it refreshes each morning
  instead of being uploaded by hand.
- Add stage-level labour hours to turn the capacity grid from order counts into
  true finite-capacity scheduling.
- Extend the webhook alerts to a daily digest of the full action board, not
  only hold and delay events, and add email/SMS as channels alongside
  Slack/Teams.
- Validate the predictive-risk model on held-out orders rather than only
  in-sample, once there is enough dispatched history to split one off.
- Rate-limit login attempts by IP as well as by account, and add two-factor
  sign-in once real plant data is in the system.
- Make the Compliance checklist a hard gate on the Testing, FAT & Dispatch
  stage the way a quality hold already is, once ACG confirms which items
  should actually block dispatch versus only warn.
- Compliance and Vendor Risk are both read against live data on every
  request; at real plant scale (hundreds of open orders, thousands of POs)
  they are candidates for the same kind of caching the KPI queries would
  also need.
