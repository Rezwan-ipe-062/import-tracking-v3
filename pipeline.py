"""Anchor -> existing backend bridge.

Calls the untouched Phase-3 scripts (clean_open_po / clean_bd_tracker /
clean_eagle_eye / clean_merge / rule_engine) in memory. We only add the UI
layer; the cleaning, merge and urgency logic is exactly what the scripts
already produce. Uploaded files are staged as temp xlsx so the existing
``clean_to_rows()`` can read them by path, then cleared.
"""

import datetime
import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
# scripts/ may sit beside the app (repo layout) or one level up (local layout).
_scripts_candidates = [APP_DIR / "scripts", APP_DIR.parent / "scripts"]
SCRIPTS_DIR = next((p for p in _scripts_candidates if p.is_dir()), _scripts_candidates[0])
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import clean_open_po          # noqa: E402
import clean_bd_tracker        # noqa: E402
import clean_eagle_eye         # noqa: E402
import clean_merge             # noqa: E402
import rule_engine             # noqa: E402

import store                    # noqa: E402

CONTROL_SHEETS = ("Exceptions", "Unmatched BD", "Unmatched EE",
                  "Cleaning Log", "Freshness", "Release", "Reconciliation")

# UI-facing labels for the four uploads.
UP = {
    "open":   {"key": "open",   "label": "Open PO",       "required": True},
    "tracker": {"key": "tracker", "label": "BD Tracker", "required": True},
    "ee":     {"key": "ee",     "label": "Eagle Eye",   "required": True},
    "threshold": {"key": "threshold", "label": "Country Thresholds",
                  "required": False},
}

FRESH_DAYS = 3


def stage_upload(upload, key):
    """Write an uploaded file to the temp work dir and return (path, meta)."""
    if upload is None:
        return None, None
    name = getattr(upload, "name", "") or "upload.xlsx"
    path = store.work_path(name)
    bytes_ = upload.getvalue()
    path.write_bytes(bytes_)
    size = len(bytes_)
    return path, {"filename": name, "size_bytes": size}


def freshness_state(meta):
    """Return ("current"|"stale", note) for the latest saved view."""
    refreshed = meta.get("refreshed_at")
    if not refreshed:
        return "stale", "no refresh recorded"
    try:
        dt = datetime.datetime.fromisoformat(str(refreshed))
    except (TypeError, ValueError):
        return "stale", "refresh time unreadable"
    days = (datetime.datetime.now() - dt).days
    if days > FRESH_DAYS:
        return "stale", f"source files {days} days old"
    return "current", f"refreshed {dt:%d %b %Y %H:%M}"


def run(source_paths: dict, threshold_path=None):
    paths = dict(source_paths)
    paths["threshold"] = threshold_path
    """Execute the merge pipeline on staged paths.

    ``source_paths`` maps key->Path for open/tracker/ee and ``threshold`` is
    the optional Path. Returns the context dict consumed by the app:
    {master, meta, control, ee, bd, op, summary, refresh}.
    """
    refresh = datetime.datetime.now().replace(microsecond=0)

# -- thresholds (optional) ---------------------------------------------
    thresholds = None
    threshold_src = "built-in defaults"
    thresholds_filename = ""
    if threshold_path is not None and threshold_path.exists():
        thresholds = rule_engine.load_country_thresholds(threshold_path)
        if thresholds is not None:
            threshold_src = str(threshold_path)
            thresholds_filename = os.path.basename(str(threshold_path))

    # -- clean each source -----------------------------------------
    _, openpo_rows, op_info = clean_open_po.clean_to_rows(source_paths["open"])
    _, bd_rows, bd_info = clean_bd_tracker.clean_to_rows(source_paths["tracker"])
    _, ee_rows, ee_info = clean_eagle_eye.clean_to_rows(source_paths["ee"])

    # -- merge + urgency + control sheets --------------------------
    merged, summary = clean_merge.merge_rows(
        openpo_rows, bd_rows, ee_rows,
        op_info, bd_info, ee_info, thresholds=thresholds)

    control = clean_merge.build_control_sheets(
        op_info, bd_info, ee_info,
        openpo_rows, bd_rows, ee_rows,
        [source_paths["open"], source_paths["tracker"], source_paths["ee"]])
    control["Release"] = clean_merge.build_release_sheet(
        summary, refresh, thresholds, threshold_src)
    control["Reconciliation"] = clean_merge.build_reconciliation(merged, summary)

    meta = {
        "version": clean_merge.PIPELINE_VERSION,
        "refreshed_at": refresh,
        "threshold_filename": thresholds_filename,
        "threshold_version": thresholds_filename or "built-in defaults",
        "open_po_count": summary["po_count"],
        "source_files": _source_files(source_paths, refresh),
        "master_headers": clean_merge.MERGE_COLUMNS,
        "bd_headers": clean_bd_tracker.OUT_COLUMNS,
        "ee_headers": clean_eagle_eye.OUT_COLUMNS,
        "op_headers": clean_open_po.OUT_COLUMNS,
    }
    return {
        "master": merged,
        "master_headers": clean_merge.MERGE_COLUMNS,
        "control": control,
        "ee_rows": ee_rows,
        "bd_rows": bd_rows,
        "op_rows": openpo_rows,
        "summary": summary,
        "meta": meta,
        "thresholds": thresholds,
    }


def _source_files(paths: dict, refresh: datetime.datetime) -> list:
    out = []
    for key in ("open", "tracker", "ee", "threshold"):
        p = paths.get(key)
        if p is None:
            out.append({"key": key, "filename": "", "loaded_at": None})
            continue
        try:
            mt = datetime.datetime.fromtimestamp(os.path.getmtime(p))
        except OSError:
            mt = refresh
        out.append({"key": key,
                    "filename": os.path.basename(str(p)),
                    "loaded_at": mt.isoformat()})
    return out


def build_context_from_disk():
    """Re-hydrate a saved view into the context the UI reads."""
    meta = store.load_view_meta()
    master_csv = store.read_saved_csv("master.csv")
    control = {}
    for name in ("Exceptions", "Unmatched BD", "Unmatched EE",
                 "Cleaning Log", "Freshness", "Release", "Reconciliation"):
        rows = store.read_saved_csv(_slug(name) + ".csv")
        if rows:
            control[name] = (rows[0], rows[1:], set())

    def _rows(fn, hdr_key):
        data = store.read_saved_csv(fn)
        if not data:
            return [], meta.get(hdr_key, [])
        return data[1:], data[0]

    bd_rows, bd_headers = _rows("bd.csv", "bd_headers")
    ee_rows, ee_headers = _rows("ee.csv", "ee_headers")
    op_rows, op_headers = _rows("op.csv", "op_headers")
    meta = dict(meta)
    meta["bd_headers"] = bd_headers
    meta["ee_headers"] = ee_headers
    meta["op_headers"] = op_headers
    return {
        "master": master_csv[1:] if master_csv else [],
        "master_headers": master_csv[0] if master_csv else _master_headers(),
        "control": control,
        "ee_rows": ee_rows,
        "bd_rows": bd_rows,
        "op_rows": op_rows,
        "meta": meta,
        "is_restored": True,
    }


def _slug(name):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)


def _master_headers():
    return clean_merge.MERGE_COLUMNS