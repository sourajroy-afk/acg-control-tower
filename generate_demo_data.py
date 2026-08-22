"""
Builds the two demo CSV files in demo_data/.

The output is ordinary upload files: the app ingests them through the same
code path as a real plant export, so nothing about the demo bypasses the
normal ingestion, dedup or KPI logic. Regenerate any time with:

    python generate_demo_data.py

Shape of the dataset: 24 months of make-to-order equipment at one plant,
16 orders a year, a lead time that starts near the case base case of 34
weeks and improves slowly, procurement as the dominant delay driver, and
rework concentrated in fabrication and assembly.
"""
import csv
import os
import random
from datetime import date, timedelta

random.seed(2026)

TODAY = date.today()
OUT = os.path.join(os.path.dirname(__file__), "demo_data")
os.makedirs(OUT, exist_ok=True)

STAGES = [
    (1, "Engineering Design", 4),
    (2, "Material Sourcing", 9),
    (3, "Fabrication", 8),
    (4, "Assembly & Integration", 5),
    (5, "Electrical / Automation", 4),
    (6, "Testing, FAT & Dispatch", 4),
]

CUSTOMERS = [
    ("Sunrise Pharma Ltd", "Pune"), ("MedCap Industries", "Ahmedabad"),
    ("Vitagen Nutraceuticals", "Hyderabad"), ("BioForm Labs", "Baddi"),
    ("Cureline Pharmaceuticals", "Vadodara"), ("Wellness Capsules Inc", "Colombo"),
    ("Apex BioSciences", "Sikkim"), ("Nutricore Formulations", "Indore"),
    ("Zenith Pharma Equipment", "Dubai"), ("Solace Life Sciences", "Chennai"),
    ("Nordic Pharma Works", "Copenhagen"), ("Helix Formulations", "Goa"),
]

EQUIPMENT = [
    "Rapid Mixer Granulator", "High-Shear Granulator", "Fluid Bed Processor",
    "Tablet Coating System", "Octagonal Blender", "Multi-Mill & Sifter Line",
    "Granulation & Drying Line", "Coater with AHU",
]

REASONS = {
    1: [("Methods", "Multiple engineering approval cycles"), ("Methods", "Customer specification change mid-design"),
        ("Manpower", "Design engineer committed to another order"), ("Methods", "Non-standard BOM created from scratch")],
    2: [("Materials", "Vendor delivery slipped past promise"), ("Materials", "Long procurement cycle on imported item"),
        ("Materials", "Single-source casting unavailable"), ("Methods", "RFQ to PO approval cycle"),
        ("Materials", "Incoming quality rejection at GRN")],
    3: [("Machines-Equipment", "Capacity constraint at machining centre"), ("Machines-Equipment", "Unplanned breakdown"),
        ("Manpower", "Certified welder unavailable"), ("Measurement", "Delay in in-process inspection sign-off"),
        ("Material Movement", "Component waiting in queue between work centres")],
    4: [("Material Movement", "Sub-assembly staged away from line"), ("Manpower", "Fitter shortage on second shift"),
        ("Measurement", "Fitment deviation found at assembly"), ("Machines-Equipment", "Alignment fixture occupied")],
    5: [("Manpower", "PLC programmer shared across two lines"), ("Materials", "Panel components short-shipped"),
        ("Methods", "Loop check documentation rework")],
    6: [("Methods", "Customer FAT slot rescheduled"), ("Measurement", "Validation documentation pending"),
        ("Material Movement", "Packing and transport slot delay"), ("Manpower", "Test engineer availability")],
}

VENDORS = [
    # name, category, location, single_source, base_lead_days, reliability (0-1), avg_slip
    ("Precision Castings Pvt Ltd", "Castings & Fabrication", "Kolhapur", 1, 42, 0.48, 12),
    ("Shirwal Fabricators", "Castings & Fabrication", "Shirwal", 0, 21, 0.78, 4),
    ("Deccan Drives & Gears", "Drives & Motors", "Pune", 1, 49, 0.52, 10),
    ("Bharat Motors & Drives", "Drives & Motors", "Nashik", 0, 35, 0.78, 5),
    ("Insto Controls", "Instrumentation", "Bengaluru", 0, 28, 0.8, 3),
    ("Vega Sensors India", "Instrumentation", "Chennai", 1, 56, 0.55, 9),
    ("Krishna Panels & Switchgear", "Electricals & Panels", "Pune", 0, 30, 0.8, 5),
    ("Elektra Automation", "Electricals & Panels", "Pune", 0, 38, 0.72, 7),
    ("Sanghvi Stainless", "Castings & Fabrication", "Mumbai", 0, 24, 0.88, 3),
    ("Alfa Seals & Gaskets", "Consumables", "Vadodara", 0, 18, 0.9, 2),
    ("Continental Bearings", "Bought-out Assemblies", "Jamnagar", 0, 32, 0.7, 6),
    ("Eurotech Valves GmbH", "Bought-out Assemblies", "Import", 1, 84, 0.5, 16),
    ("Sai Machining Works", "Castings & Fabrication", "Satara", 0, 26, 0.84, 4),
    ("Nirmal Pneumatics", "Bought-out Assemblies", "Pune", 0, 22, 0.87, 3),
]

ITEMS = {
    "Castings & Fabrication": ["SS316 bowl casting", "Chopper housing", "Vessel shell", "Machined base frame"],
    "Drives & Motors": ["Main drive gearbox", "Impeller drive motor", "Servo drive assembly"],
    "Instrumentation": ["Temperature probe set", "Load cell assembly", "Pressure transmitter"],
    "Electricals & Panels": ["PLC control panel", "HMI station", "Power distribution panel"],
    "Bought-out Assemblies": ["Butterfly valve set", "Bearing block set", "Pneumatic actuator kit"],
    "Consumables": ["Gasket & seal kit", "Filter bag set"],
}


def improvement(order_dt):
    """Plant runs slightly better over time: 1.06x of plan two years ago, 0.9x now."""
    months = (TODAY.year - order_dt.year) * 12 + (TODAY.month - order_dt.month)
    return 0.86 + 0.006 * min(months, 24)


def make_dataset():
    job_rows, po_rows = [], []
    start = TODAY - timedelta(days=760)
    job_seq = 0
    po_seq = 0

    d = start
    while d < TODAY + timedelta(days=60):
        job_seq += 1
        order_dt = d
        customer, _ = random.choice(CUSTOMERS)
        equip = random.choice(EQUIPMENT)
        job_no = f"SHW-{order_dt.strftime('%y')}{job_seq:03d}"
        value = round(random.uniform(48, 210), 1)
        priority = "Expedite" if random.random() < 0.18 else "Standard"
        committed = order_dt + timedelta(weeks=30 if priority == "Expedite" else 34)
        drift = improvement(order_dt)
        # a realistic share of live orders is in trouble: a stalled stage, a
        # slipped vendor, an engineering change. Those are the orders the
        # control tower exists to surface.
        trouble = (TODAY - order_dt).days < 330 and random.random() < 0.45
        if trouble:
            drift *= random.uniform(1.2, 1.45)

        cursor = order_dt
        rows_for_job = []
        for seq, name, bench in STAGES:
            planned_start = cursor
            dur_weeks = bench * drift * random.uniform(0.82, 1.18)
            if seq == 2 and random.random() < 0.18:
                dur_weeks *= random.uniform(1.15, 1.45)    # procurement tail
            actual_start = cursor
            actual_end = actual_start + timedelta(days=round(dur_weeks * 7))

            started = actual_start <= TODAY
            finished = actual_end <= TODAY

            rework = "N"
            rw_hours = rw_cost = ""
            if finished and random.random() < (0.16 if seq in (3, 4) else 0.04):
                rework = "Y"
                rw_hours = round(random.uniform(8, 46), 1)
                rw_cost = round(value * random.uniform(0.008, 0.035), 2)

            cat = reason = days = ""
            over_days = (actual_end - (planned_start + timedelta(weeks=bench))).days
            if finished and over_days > 2:
                cat, reason = random.choice(REASONS[seq])
                days = over_days

            rows_for_job.append({
                "job_no": job_no, "customer": customer, "equipment_type": equip,
                "order_value_lakh": value, "priority": priority,
                "order_date": order_dt.isoformat(), "committed_dispatch_date": committed.isoformat(),
                "stage_seq": seq, "stage_name": name, "planned_start": "", "planned_end": "",
                "actual_start": actual_start.isoformat() if started else "",
                "actual_end": actual_end.isoformat() if finished else "",
                "rework": rework, "rework_hours": rw_hours, "rework_cost_lakh": rw_cost,
                "delay_category": cat, "delay_reason": reason, "delay_days": days,
            })
            cursor = actual_end
            if not finished:
                break

        job_rows.extend(rows_for_job)

        # purchase orders raised when material sourcing starts
        sourcing_start = order_dt + timedelta(days=round(STAGES[0][2] * drift * 7))
        for _ in range(random.randint(3, 6)):
            v = random.choice(VENDORS)

            name, vcat, loc, single, base_lead, rel, slip = v
            item = random.choice(ITEMS[vcat])
            po_seq += 1
            po_date = sourcing_start + timedelta(days=random.randint(0, 12))
            promised = po_date + timedelta(days=base_lead + random.randint(-4, 8))
            if random.random() < (rel - 0.15 if trouble else rel):
                received = promised - timedelta(days=random.randint(0, 5))
            else:
                extra = random.randint(45, 120) if (trouble and random.random() < 0.45) else 0
                received = promised + timedelta(days=max(1, int(random.gauss(slip, 5))) + extra)
            # orders in trouble usually have at least one part still awaited
            if trouble and promised < TODAY and random.random() < 0.45:
                received = TODAY + timedelta(days=random.randint(5, 40))

            po_rows.append({
                "po_no": f"PO-{po_seq:05d}", "job_no": job_no, "vendor": name, "item": item,
                "item_category": vcat, "critical": "Y" if vcat in ("Castings & Fabrication", "Drives & Motors") else "N",
                "value_lakh": round(value * random.uniform(0.03, 0.14), 2),
                "po_date": po_date.isoformat(), "promised_date": promised.isoformat(),
                "received_date": received.isoformat() if received <= TODAY else "",
                "single_source": "Y" if single else "N", "vendor_location": loc,
            })

        d += timedelta(days=random.randint(16, 30))

    return job_rows, po_rows


def write(rows, path, fields):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return path


if __name__ == "__main__":
    jobs, pos = make_dataset()
    write(jobs, os.path.join(OUT, "job_stages_demo.csv"), list(jobs[0].keys()))
    write(pos, os.path.join(OUT, "purchase_orders_demo.csv"), list(pos[0].keys()))
    print(f"job_stages_demo.csv: {len(jobs)} rows / {len({r['job_no'] for r in jobs})} jobs")
    print(f"purchase_orders_demo.csv: {len(pos)} rows / {len({r['vendor'] for r in pos})} vendors")
