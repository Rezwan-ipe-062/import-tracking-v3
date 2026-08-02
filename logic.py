"""Business derivation layer for Anchor.

Pure functions that turn the merged master rows into the decision-support labels
the UI renders: data-confidence, suggested follow-up owners, risk snapshots and
RDD exposure horizons. These are *suggestions* derived from Primary Reason and
field presence; they never replace the backend-approved Urgency classification.
"""

from datetime import date, datetime

# Suggested follow-up / owner mapping, keyed on substrings found in Primary Reason.
# Matched in order, first hit wins. Always treated as "suggested", never assignment.
FOLLOWUP_MAP = [
    ("RDD missing", "Confirm RDD in BD Tracker; urgency cannot be assessed.",
     "Planning data owner"),
    ("Route unknown", "Validate source country/route mapping before applying thresholds.",
     "Planning master-data owner"),
    ("Status complete but open quantity remains",
     "Reconcile receipt status and Open PO quantity before treating the PO as closed.",
     "Planning + Logistics reconciliation"),
    ("ETA later than RDD", "Confirm recovery plan and assess planning/production impact.",
     "Planning + Logistics review"),
    ("ETA within", "Obtain updated shipment ETA from logistics/origin.",
     "Origin logistics / relevant import coordination owner"),
    ("LC", "Confirm LC completion with Bangladesh Logistics / Order Management.",
     "Bangladesh Logistics / Order Management"),
    ("ETD", "Obtain booking / confirmed departure schedule from origin logistics.",
     "Origin logistics / supplier / import coordination owner"),
    ("OBL", "Confirm documentation with the document coordination team.",
     "Document coordination team"),
    ("Final", "Confirm final document receipt with the document coordination team.",
     "Document coordination team"),
    ("No BD", "Validate whether the process is unstarted or the data is missing.",
     "BD Tracker source-data owner"),
    ("No Eagle", "Validate whether the shipment process is unstarted or data is missing.",
     "Eagle Eye source-data owner"),
    ("No EE", "Validate whether the shipment process is unstarted or data is missing.",
     "Eagle Eye source-data owner"),
]


def suggested_followup(primary_reason: str):
    """Return (action_text, owner) suggested for a Primary Reason value."""
    reason = str(primary_reason or "")
    for pattern, action, owner in FOLLOWUP_MAP:
        if pattern.lower() in reason.lower():
            return action, owner
    if reason and "Urgent" not in reason and "Critical" not in reason:
        return "Review the PO record and confirm the next required milestone.", reason.strip()
    return "Review the PO in full context before deciding.", "Planning team"


def data_confidence(row, headers, fresh: bool = True, in_reconciliation=False):
    """Return a high/medium/low confidence label for one master row."""
    def has(col):
        if col not in headers:
            return False
        v = row[headers.index(col)]
        return not (v is None or (isinstance(v, float) and v != v))

    rdd = has("RDD")
    country = has("Import Country")
    reason = str(row[headers.index("Primary Reason")] if "Primary Reason" in headers else "")

    if not rdd or not country:
        return "Low"
    if in_reconciliation:
        return "Low"
    if not fresh:
        return "Low"
    # High needs milestone evidence for the decisive stage and no critical gaps.
    if any(k in reason for k in ("Route unknown", "RDD missing")):
        return "Low"
    # Some shipment/document evidence missing -> Medium.
    has_eta = has("BD Tracker ETA") or has("EE ETA")
    if not has_eta:
        return "Medium"
    return "High"


# --------------------------------------------------------------------------- #
# Risk snapshot helpers (distinct-PO counting discipline)
# --------------------------------------------------------------------------- #

_URGENCY_ORDER = ["Critical", "Urgent", "Data Review", "Monitor"]


def active_rows(master, headers):
    """Rows that are in the operational Active population."""
    if "Population Status" not in headers:
        return list(master)
    i = headers.index("Population Status")
    return [r for r in master if str(r[i]).strip().lower() == "active"]


def distinct_poids(master, headers):
    if "Purchasing Document" not in headers:
        return 0
    i = headers.index("Purchasing Document")
    return len({str(r[i]) for r in master if r[i] is not None and str(r[i]).strip()})


def qty_by_unit(master, headers, population_only=True):
    """Open requirement grouped by Order Unit. Never blends KG + L."""
    qcol = "Still to be Delivered (Qty)"
    ucol = "Order Unit"
    if qcol not in headers or ucol not in headers:
        return {}
    qi = headers.index(qcol)
    ui = headers.index(ucol)
    out = {}
    for r in master:
        if population_only:
            if "Population Status" in headers:
                if str(r[headers.index("Population Status")].strip()) != "Active":
                    continue
            else:
                continue
        try:
            q = float(r[qi])
        except (TypeError, ValueError):
            q = 0.0
        unit = str(r[ui]).strip() or "N/A"
        out[unit] = out.get(unit, 0.0) + q
    return out


def rdd_horizon(days_from_today):
    """Classify RDD offset into named buckets used by Action Centre + Risk."""
    if days_from_today is None:
        return "Unknown"
    if days_from_today < 0:
        return "Overdue"
    if days_from_today <= 7:
        return "0-7d"
    if days_from_today <= 30:
        return "8-30d"
    if days_from_today <= 60:
        return "31-60d"
    return ">60d"


def rdd_offset(rdd_value, today=None):
    """Return whole days from today to RDD (negative = overdue), or None."""
    if rdd_value is None:
        return None
    if isinstance(rdd_value, datetime):
        dt = rdd_value.date()
    elif isinstance(rdd_value, date):
        dt = rdd_value
    else:
        return None
    today = today or date.today()
    return (dt - today).days