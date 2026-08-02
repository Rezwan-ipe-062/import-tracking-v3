"""Rule engine for the Import Visibility Master urgency layer (Phase 3).

Pure, UI-agnostic logic that turns one merged master row (the BD milestone
dates plus the Eagle Eye shipment data) into an urgency classification:

    Critical     - a deadline has already been crossed: act now.
    Urgent       - inside the warning window: act before it becomes Critical.
    Monitor      - nothing is due yet.
    Data Review  - the file cannot support a reliable decision (RDD missing,
                   or source country unknown where a route threshold is needed).

Every threshold is read from a default table (``DEFAULT_THRESHOLDS``) that
mirrors the starter business rules agreed with the business (``table.md``).
A country manager can override these per route group through an editable
``Country Thresholds.xlsx`` (see ``load_country_thresholds``); the engine
falls back to the defaults whenever the file is missing or unreadable.

Two safeguards from the business are baked in:

    1. A blank field is never Critical just because it is blank - a China PO
       with no LC date and an RDD four months out stays Monitor.
    2. Source country is only used after Product Master confirmation. Until
       the Product Master file is wired in, the route group comes from the
       Eagle Eye ``From`` column (shipment origin). Rows without a From are
       ``Data Review`` whenever the LC/ETD rules need a route.

The Final Docs / OBL "after ATD" rules were dropped: the SAP extract leaves
ATD blank on most rows, so they cannot be assessed reliably.

The engine is deterministic: same row in, same classification out. It imports
nothing from the cleaning/merging layer, so it can be unit-tested in
isolation and re-used by a future interactive UI unchanged.
"""

import datetime

# ---------------------------------------------------------------- routes
# Eagle Eye "From" country code -> route group used by the LC/ETD thresholds.
ROUTE_MAP = {
    "IN": "India",
    "TH": "ASEAN",
    "SG": "ASEAN",
    "CN": "ChinaEA",
    "KR": "ChinaEA",
    "DE": "Europe",
    "IT": "Europe",
    "CH": "Europe",
    "NL": "Europe",
    "GB": "Europe",
    "US": "Europe",
}

# ---------------------------------------------------------- default table
# Starter business thresholds (from the agreed business-rule table). Each
# entry is (urgent_window_days, critical_window_days) measured as days from
# today to RDD. A milestone that is missing inside the urgent window is
# Urgent; inside the critical window it is Critical.
DEFAULT_THRESHOLDS = {
    "LC": {
        "India": (45, 30),
        "ASEAN": (60, 45),
        "ChinaEA": (75, 60),
        "Europe": (120, 90),
    },
    "ETD": {
        "India": (30, 20),
        "ASEAN": (40, 30),
        "ChinaEA": (55, 45),
        "Europe": (90, 75),
    },
}

# ETA rules are route-independent (days relative to expected departure).
ETA_URGENT_DAYS = 14
ETA_CRITICAL_DAYS = 7

# Overall-status tokens treated as "shipment complete". These are intentionally
# conservative: a row must explicitly indicate completion, never a blank.
COMPLETED_KEYWORDS = ("completed", "full set received")

# Open-quantity population status. Only a validated open quantity > 0 is
# "Active"; everything else (blank, zero, negative, non-numeric, formula
# text) is "Quantity Review" and is excluded from risk totals.

# --------------------------------------------------------- EE stage order
# Eagle Eye statuses are a monotonic 1..6 pipeline. A PO's current stage is
# the highest stage present across its container rows.
EE_STAGE_ORDER = [
    "1 Pending TP Flag / CCR",
    "2 To be booked",
    "3 Booked",
    "4 Sailed",
    "5 Arrived at Port",
    "6 Arrived at Door",
]
EE_STAGE_RANK = {s: i for i, s in enumerate(EE_STAGE_ORDER)}

SEVERITY_RANK = {"Critical": 3, "Urgent": 2, "Monitor": 1, "Data Review": 0}


def route_of(from_code):
    """Route group for an Eagle Eye From code, or None when unknown/blank."""
    if not from_code:
        return None
    return ROUTE_MAP.get(str(from_code).strip().upper())


def current_ee_stage(statuses):
    """Highest EE stage rank across a PO's container rows.

    ``statuses`` is an iterable of Eagle Eye Status strings. Returns the stage
    number 1..6, or None when no known status is present.
    """
    best = None
    for s in statuses or ():
        s = str(s).strip()
        if s in EE_STAGE_RANK:
            rank = EE_STAGE_RANK[s]
            if best is None or rank > best:
                best = rank
    return None if best is None else best + 1


def load_country_thresholds(path):
    """Load per-route thresholds from an editable ``Country Thresholds.xlsx``.

    Expected layout (first sheet): a header row, then one row per route group
    with columns, in any order:

        Route Group | LC - Urgent (days) | LC - Critical (days)
        | ETD - Urgent (days) | ETD - Critical (days)

    Returns the thresholds dict in the same shape as ``DEFAULT_THRESHOLDS``.
    Missing/blank cells or a missing file fall back to the defaults per cell.
    """
    thresholds = {
        "LC": {r: list(DEFAULT_THRESHOLDS["LC"][r]) for r in DEFAULT_THRESHOLDS["LC"]},
        "ETD": {r: list(DEFAULT_THRESHOLDS["ETD"][r]) for r in DEFAULT_THRESHOLDS["ETD"]},
    }
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
    except Exception:
        return thresholds
    if not rows:
        return thresholds

    header = [str(c).strip().lower() if c is not None else "" for c in rows[0]]
    key_idx = _find_col(header, ("route group", "route", "group"))
    lc_u_idx = _find_col(header, ("lc", "urgent"))
    lc_c_idx = _find_col(header, ("lc", "critical"))
    etd_u_idx = _find_col(header, ("etd", "urgent"))
    etd_c_idx = _find_col(header, ("etd", "critical"))

    def num(v):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v)
        try:
            return int(float(str(v).strip()))
        except (TypeError, ValueError):
            return None

    for row in rows[1:]:
        if key_idx is None or key_idx >= len(row):
            continue
        route = _norm_route(row[key_idx])
        if route is None:
            continue
        for kind, u_idx, c_idx in (("LC", lc_u_idx, lc_c_idx),
                                   ("ETD", etd_u_idx, etd_c_idx)):
            u = num(row[u_idx]) if u_idx is not None and u_idx < len(row) else None
            c = num(row[c_idx]) if c_idx is not None and c_idx < len(row) else None
            if u is not None or c is not None:
                cur = thresholds[kind][route]
                if u is not None:
                    cur[0] = u
                if c is not None:
                    cur[1] = c
    return thresholds


def _find_col(header, keys):
    """Column index whose name contains every key token, or None."""
    for i, h in enumerate(header):
        if all(k in h for k in keys):
            return i
    return None


def _norm_route(v):
    """Normalise a route-group label to a DEFAULT_THRESHOLDS key, or None."""
    if v is None:
        return None
    s = "".join(c for c in str(v).lower() if c.isalnum())
    mapping = {
        "india": "India",
        "asean": "ASEAN",
        "thailandasean": "ASEAN",
        "chinaea": "ChinaEA",
        "chinaeastasia": "ChinaEA",
        "china": "ChinaEA",
        "europe": "Europe",
        "europelonghaul": "Europe",
        "longhaul": "Europe",
    }
    return mapping.get(s)


def population_status(open_qty):
    """Population status for one PO-material open quantity.

    ``Active``   - numeric open quantity > 0 (validated, still to deliver).
    ``Quantity Review`` - blank / zero / negative / non-numeric / formula
    text. Such rows are excluded from risk totals and must be reviewed.
    """
    if open_qty is None or open_qty == "":
        return "Quantity Review"
    try:
        q = float(open_qty)
    except (TypeError, ValueError):
        return "Quantity Review"
    return "Active" if q > 0 else "Quantity Review"


def is_completed_status(status):
    """True when an Overall Status explicitly says the shipment is complete."""
    if not status:
        return False
    s = str(status).strip().lower()
    return any(k in s for k in COMPLETED_KEYWORDS)


# -------------------------------------------------------------- classify
def classify(rdd, lc, etd_bd, etd_ee, eta_bd, eta_ee, from_code,
             today=None, thresholds=None, status=None, open_qty=None):
    """Classify one merged master row.

    Returns ``(severity, reason)``. ``severity`` is one of
    ``Critical`` / ``Urgent`` / ``Monitor`` / ``Data Review`` and ``reason`` a
    short human-readable string describing the deciding rule.

    Date arguments (all optional, None/blank means missing):

        rdd      - BD RDD (required to be Monitor or better)
        lc       - BD LC Date
        etd_bd   - BD ETD (schedule)
        etd_ee   - EE ETD (planned departure, earliest per PO)
        eta_bd   - BD Tracker ETA
        eta_ee   - EE ETA (earliest source)
        from_code- Eagle Eye From country code (source of the route group)

    ``today`` defaults to the real current date; ``thresholds`` defaults to
    the built-in ``DEFAULT_THRESHOLDS``.

    Reconciliation arguments:

        status   - BD Overall Status. A completed status with a positive
                   remaining open quantity is sent to ``Data Review`` rather
                   than being marked safe.
        open_qty - Open PO quantity (per PO-material line). Positive value
                   while ``status`` says complete => reconciliation.
    """
    today = today or datetime.date.today()
    th = thresholds or DEFAULT_THRESHOLDS

    rdd = _as_date(rdd)
    if rdd is None:
        return "Data Review", "RDD missing"

    # Reconciliation: a confirmed-complete status must not mask a positive
    # open quantity still on the PO, and vice versa - send it to review.
    if is_completed_status(status) and population_status(open_qty) == "Active":
        return "Data Review", "Status complete but open quantity remains"

    route = route_of(from_code)
    lc = _as_date(lc)
    etd_bd = _as_date(etd_bd)
    etd_ee = _as_date(etd_ee)
    eta_bd = _as_date(eta_bd)
    eta_ee = _as_date(eta_ee)

    etd = etd_bd or etd_ee
    days_to_rdd = (rdd - today).days

    # Route-dependent rules (LC / ETD missing) need a known route.
    if route is not None:
        if lc is None:
            urgent, critical = th["LC"][route]
            if days_to_rdd <= critical:
                return "Critical", ("LC missing, RDD in %dd (<= %dd critical)"
                                    % (days_to_rdd, critical))
            if days_to_rdd <= urgent:
                return "Urgent", ("LC missing, RDD in %dd (<= %dd urgent)"
                                  % (days_to_rdd, urgent))
        if etd is None:
            urgent, critical = th["ETD"][route]
            if days_to_rdd <= critical:
                return "Critical", ("ETD missing, RDD in %dd (<= %dd critical)"
                                    % (days_to_rdd, critical))
            if days_to_rdd <= urgent:
                return "Urgent", ("ETD missing, RDD in %dd (<= %dd urgent)"
                                  % (days_to_rdd, urgent))
    else:
        # Route unknown: the LC/ETD route rules cannot be assessed.
        if lc is None or etd is None:
            return "Data Review", "Route unknown - LC/ETD rule not assessable"

    # Route-independent rules -------------------------------------------------
    eta = eta_ee or eta_bd
    if eta is not None and eta > rdd:
        return "Critical", "ETA later than RDD"
    if eta is None:
        departure = etd or lc
        if departure is not None:
            days_to_dep = (departure - today).days
            if days_to_dep <= ETA_CRITICAL_DAYS:
                return "Critical", "No ETA within %dd of departure" % ETA_CRITICAL_DAYS
            if days_to_dep <= ETA_URGENT_DAYS:
                return "Urgent", "No ETA within %dd of departure" % ETA_URGENT_DAYS

    return "Monitor", "Monitor"


def _as_date(v):
    """None / blank -> None; datetime.date/datetime kept as date."""
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    return None
