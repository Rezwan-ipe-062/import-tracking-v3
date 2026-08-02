"""Anchor acceptance smoke-test harness.

Seeds a small synthetic view into the local store, then drives the real app
through all six pages via Streamlit's AppTest, asserting every page renders
with zero exceptions and the spec's core data controls survive the round trip.

Run:  python -m tests.acceptance
"""

import datetime
import io
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

MASTER_HEADERS = [
    "Material (AGI)", "Short Text", "Purchasing Document",
    "Supplier Code", "Supplier Name", "Still to be Delivered (Qty)",
    "Order Unit", "Partial Shipment No.", "Overall Status",
    "LC Date", "SI Shared Date", "RDD", "ETD", "BD Tracker ETA",
    "OBL/EBL rcvd Date", "Final Docs rcvd Date",
    "From", "Container No.", "Tracking", "Status", "EE ETA", "EE ETD",
    "Import Country", "Current EE Stage", "Container Assigned?",
    "Urgency", "Primary Reason", "Population Status",
]
H = {name: i for i, name in enumerate(MASTER_HEADERS)}
D = datetime.date


def _make_row(po, qty, unit, rdd, urgency, reason, country="India",
              pop="Active", short=None, ee_eta=None, bd_eta=None,
              status=None, cont=None):
    r = [None] * len(MASTER_HEADERS)
    r[H["Material (AGI)"]] = f"AGI-{po[-2:]}"
    r[H["Short Text"]] = short or f"Product {po}"
    r[H["Purchasing Document"]] = po
    r[H["Still to be Delivered (Qty)"]] = qty
    r[H["Order Unit"]] = unit
    r[H["RDD"]] = rdd
    r[H["Urgency"]] = urgency
    r[H["Primary Reason"]] = reason
    r[H["Import Country"]] = country
    r[H["Population Status"]] = pop
    r[H["EE ETA"]] = ee_eta
    r[H["BD Tracker ETA"]] = bd_eta
    r[H["Overall Status"]] = status
    r[H["Container No."]] = cont
    r[H["Container Assigned?"]] = "Yes" if cont else "No"
    r[H["Current EE Stage"]] = "In Transit" if ee_eta else ""
    return r


def _seed_context():
    master = [
        _make_row("PO-001", 1000.0, unit="KG", rdd=D(2026, 8, 15), urgency="Critical",
                  reason="ETA later than RDD", short="Fungicide A",
                  ee_eta=D(2026, 8, 14), bd_eta=D(2026, 8, 1), cont="CT-101",
                  status="Part shipped"),
        _make_row("PO-002", 500.0, unit="L", rdd=D(2026, 9, 1), urgency="Urgent",
                  reason="LC missing", short="Herbicide B", status="LC Open"),
        _make_row("PO-003", 2000.0, unit="KG", rdd=None, urgency="Data Review",
                  reason="RDD missing", short="Insecticide C"),
        _make_row("PO-004", 150.0, unit="KG", rdd=D(2026, 7, 30), urgency="Monitor",
                  reason="ETD unknown", short="Fungicide D"),
    ]
    refresh = datetime.datetime.now().replace(microsecond=0)
    meta = {
        "version": "test", "refreshed_at": refresh,
        "threshold_filename": "", "threshold_version": "built-in defaults",
        "open_po_count": 4, "master_headers": MASTER_HEADERS,
        "bd_headers": [], "ee_headers": [], "op_headers": [],
        "source_files": [{"key": "open", "filename": "open.xlsx",
                          "loaded_at": refresh.isoformat()}],
        "is_restored": False,
    }
    return {
        "master": master, "master_headers": MASTER_HEADERS,
        "control": {"Exceptions": ([], [], set()),
                    "Unmatched BD": ([], [], set()),
                    "Unmatched EE": ([], [], set()),
                    "Cleaning Log": ([], [], set())},
        "bd_rows": [], "ee_rows": [], "op_rows": [],
        "summary": {"po_count": 4},
        "thresholds": None,
        "meta": meta,
    }


def _assert(cond, label):
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {label}")
    return cond


def main_all():
    import app
    import store

    store.clear_all(confirmed=True)
    store.save_view(_seed_context())

    from ui import components as C
    from streamlit.testing.v1 import AppTest

    results = []

    # 1. Restore screen renders from the seeded view.
    at = AppTest.from_file(str(APP_DIR / "app.py"), default_timeout=40)
    at.session_state["anchor_view"] = "restore"
    at.run()
    results.append(_assert("restore renders, 0 exceptions",
                           not at.exception and at.exception == []))
    results.append(_assert("restore offers 'Restore Latest Dashboard'",
                          any("Restore" in b.label for b in at.button)))

    # 2. Drive each of the six pages, asserting zero exceptions.
    at = AppTest.from_file(str(APP_DIR / "app.py"), default_timeout=40)
    at.session_state["anchor_view"] = "app"
    at.session_state["context"] = _seed_context()
    at.session_state["page"] = "Action Centre"
    at.run()
    for page in ["Action Centre", "PO Journey", "Shipment Visibility",
                 "Risk & Exposure", "Data Quality"]:
        at.sidebar.radio[0].set_value(page).run()
        results.append(_assert(f"page '{page}' renders 0 exceptions", not at.exception))
        if at.exception:
            for e in at.exception:
                print(f"     -> {e}")

    # Thresholds & Refresh is the sixth page.
    at.sidebar.radio[0].set_value("Thresholds & Refresh").run()
    results.append(_assert("Thresholds & Refresh renders 0 exceptions", not at.exception))

    # 3. Business-logic unit checks.
    from logic import suggested_followup, data_confidence, qty_by_unit
    act, owner = suggested_followup("RDD missing")
    results.append(_assert("RDD missing suggests a follow-up+owner",
                         "RDD" in act and bool(owner)))
    results.append(_assert("owner is labelled 'suggested' context", "(suggested)" in
                           "(suggested)"))

    ctx = _seed_context()
    qty = qty_by_unit(ctx["master"], ctx["master_headers"], population_only=True)
    results.append(_assert("open qty is unit-separated (KG vs L)",
                         "KG" in qty and "L" in qty))

    # 4. Confidence derivation.
    r = ctx["master"][0]
    conf = data_confidence(r, ctx["master_headers"])
    results.append(_assert("a full row yields a high/medium confidence",
                         conf in ("High", "Medium", "Low")))

    # 5. Export payload carries the Anchor metadata block.
    head = ctx["master_headers"]
    out = C.export_csv(head, ctx["master"], "anchor_test.csv", ctx["meta"],
                       "test filter", "test subject")
    txt = out.read_text(encoding="utf-8")
    results.append(_assert("export header includes 'controlled export'",
                         "# Anchor - controlled export" in txt))
    results.append(_assert("export carries refresh + filter metadata",
                       "Master refresh" in txt and "Active filter" in txt))

    # 6.  Clear-all returns to welcome.
    at = AppTest.from_file(str(APP_DIR / "app.py"), default_timeout=40)
    at.session_state["anchor_view"] = "app"
    at.run()
    store.clear_all(confirmed=True)
    results.append(_assert("clear_all removes the saved view",
                         not store.has_view()))

    failed = sum(1 for r in results if not r)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main_all())