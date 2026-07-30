"""
web_app.py — Flask web application for the Syngenta Bangladesh Import Tracker.

Phase 3: Upload, validate, process, and download interface.
"""

import json
import os
import sys
import tempfile
import traceback
import uuid
from datetime import datetime
from functools import wraps
from pathlib import PurePath

import pandas as pd
from flask import (
    Flask, abort, jsonify, render_template, request, send_file, session, redirect, url_for, flash,
)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from pipeline_db import get_connection, init_database, ColumnMapper
import pipeline_service
from pipeline_service import (
    PIPELINE_VERSION, archive_source_files, check_duplicate_upload,
    compute_file_hash, create_upload_run,
    get_dashboard_card_counts, get_dq_exceptions,
    get_latest_successful_run, get_master_detail,
    get_po_summary, get_run_status, get_unmatched_records,
    get_ambiguous_matches, get_threshold_config_status,
    get_po_detail_data, list_upload_runs, process_upload_run,
    validate_upload_files,
    create_threshold_profile, get_threshold_profile,
    list_threshold_profiles, update_profile_metadata,
    update_profile_rule, submit_profile_for_approval,
    approve_profile, activate_profile, deactivate_profile,
    retire_profile, create_new_profile_version,
    get_profile_audit_log, get_profile_impact_preview,
)

app = Flask(
    __name__,
    template_folder=os.path.join(_SCRIPT_DIR, "templates"),
    static_folder=os.path.join(_SCRIPT_DIR, "static"),
)

app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-key-syngenta-import-tracker-2026")
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

# Admin access control: secret token for Phase 5 admin screens
# Set IMPORT_TRACKER_ADMIN_SECRET env var in production; default shown at startup
ADMIN_SECRET = os.environ.get("IMPORT_TRACKER_ADMIN_SECRET", str(uuid.uuid4())[:16])
UPLOAD_FOLDER = os.path.join(tempfile.gettempdir(), "import_tracker_uploads")
ARCHIVE_BASE = os.environ.get(
    "IMPORT_TRACKER_ARCHIVE",
    os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "import_tracker",
        "archive",
    ),
)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(ARCHIVE_BASE, exist_ok=True)
os.makedirs(app.template_folder, exist_ok=True)
os.makedirs(app.static_folder, exist_ok=True)

_conn_init = get_connection()
init_database(_conn_init)
_conn_init.close()

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv"}

SOURCE_TYPE_MAP = {
    "open_po": {"label": "Open PO", "sheet": "Data"},
    "bd_tracker": {"label": "BD Tracker", "sheet": "Tracker File"},
    "eagle_eye": {"label": "Eagle Eye", "sheet": "Sheet1"},
}


@app.template_filter("basename")
def basename_filter(path):
    return PurePath(path).name


def _row_counts_for_run(run_id, conn):
    counts = {}
    tables = [
        ("master_detail_records", "record_id", "master_detail"),
        ("po_summary_records", "summary_id", "po_summary"),
        ("unmatched_bd_records", "unmatched_id", "unmatched_bd"),
        ("unmatched_ee_records", "unmatched_id", "unmatched_ee"),
        ("ambiguous_match_records", "ambiguous_id", "ambiguous_matches"),
        ("data_quality_exceptions", "exception_id", "dq_exceptions"),
    ]
    for table, id_col, key in tables:
        try:
            c = conn.execute(f"SELECT COUNT(*) as cnt FROM {table} WHERE run_id = ?", (run_id,))
            counts[key] = c.fetchone()["cnt"]
        except Exception:
            counts[key] = 0
    return counts


def _safe_render(template_name, **kwargs):
    try:
        return render_template(template_name, **kwargs)
    except Exception:
        return f"<html><body><div class='alert alert-danger'>Error rendering {template_name}</div></body></html>", 500


@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"error": "Not found"}), 404
    return _safe_render("base.html", content="<div class='alert alert-danger m-4'>Page not found</div>"), 404


@app.errorhandler(500)
def server_error(e):
    traceback.print_exc()
    if request.is_json:
        return jsonify({"error": "Internal server error"}), 500
    return _safe_render("base.html", content="<div class='alert alert-danger m-4'>Internal server error</div>"), 500


@app.route("/")
def index():
    conn = get_connection()
    try:
        runs = list_upload_runs(limit=20, conn=conn)
        for r in runs:
            r["row_counts"] = _row_counts_for_run(r["run_id"], conn)
        latest = get_latest_successful_run(conn=conn)
        current_run_id = latest["run_id"] if latest else None
        return render_template("index.html", runs=runs, current_run_id=current_run_id)
    finally:
        conn.close()


@app.route("/validate", methods=["POST"])
def validate():
    errors = []
    warnings = []
    details = {}

    file_fields = ["open_po", "bd_tracker", "eagle_eye"]
    saved_paths = {}

    for ftype in file_fields:
        f = request.files.get(ftype)
        if not f or f.filename == "":
            errors.append({"field": ftype, "message": f"{SOURCE_TYPE_MAP[ftype]['label']}: no file selected"})
            details[ftype] = {"selected": False}
            continue

        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            errors.append({"field": ftype, "message": f"{SOURCE_TYPE_MAP[ftype]['label']}: unsupported file type '{ext}'"})
            details[ftype] = {"selected": True, "error": "Unsupported file type"}
            continue

        safe_name = f"{uuid.uuid4().hex}{ext}"
        save_path = os.path.join(UPLOAD_FOLDER, safe_name)
        f.save(save_path)
        saved_paths[ftype] = {"path": save_path, "original_name": f.filename, "size": os.path.getsize(save_path)}

    if len(saved_paths) == 3:
        v = validate_upload_files(
            saved_paths["open_po"]["path"],
            saved_paths["bd_tracker"]["path"],
            saved_paths["eagle_eye"]["path"],
        )
        if not v["valid"]:
            for e in v["errors"]:
                field = e.split(":")[0].strip()
                errors.append({"field": field, "message": e})

        for ftype in file_fields:
            finfo = v["details"].get(ftype, {})
            details[ftype] = {
                "selected": ftype in saved_paths,
                "exists": finfo.get("exists", False),
                "sheets_ok": finfo.get("sheets_ok", False),
                "columns_ok": finfo.get("columns_ok", False),
                "sheet_names": finfo.get("sheet_names", []),
                "original_name": saved_paths.get(ftype, {}).get("original_name", ""),
                "size": saved_paths.get(ftype, {}).get("size", 0),
                "temp_path": saved_paths.get(ftype, {}).get("path", ""),
            }

        for ftype in file_fields:
            if ftype in saved_paths and v.get("details", {}).get(ftype, {}).get("exists"):
                finfo = v["details"][ftype]
                if finfo.get("sheets_ok") and not finfo.get("columns_ok"):
                    warnings.append({"field": ftype, "message": f"{SOURCE_TYPE_MAP[ftype]['label']}: sheet found but column mismatch"})

        hashes = {}
        for ftype in file_fields:
            if ftype in saved_paths:
                hashes[ftype] = compute_file_hash(saved_paths[ftype]["path"])
        if len(hashes) == 3:
            is_dup, dup_id, dup_status = check_duplicate_upload(hashes)
            if is_dup:
                warnings.append({
                    "field": "all",
                    "message": f"These exact files were previously uploaded as Run {dup_id[:8]} ({dup_status}). Processing will create a duplicate record.",
                })
    else:
        for ftype in file_fields:
            if ftype not in saved_paths:
                details.setdefault(ftype, {"selected": False})
            else:
                details.setdefault(ftype, {})["temp_path"] = saved_paths[ftype]["path"]

    return jsonify({
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "details": details,
        "file_paths": {k: v["path"] for k, v in saved_paths.items()},
        "original_names": {k: v["original_name"] for k, v in saved_paths.items()},
    })


@app.route("/process", methods=["POST"])
def process():
    data = request.get_json(force=True)
    open_po_path = data.get("open_po_path") or data.get("open_po")
    bd_tracker_path = data.get("bd_tracker_path") or data.get("bd_tracker")
    eagle_eye_path = data.get("eagle_eye_path") or data.get("eagle_eye")

    if not all([open_po_path, bd_tracker_path, eagle_eye_path]):
        return jsonify({"success": False, "error": "All three file paths are required"}), 400

    for label, p in [("open_po", open_po_path), ("bd_tracker", bd_tracker_path), ("eagle_eye", eagle_eye_path)]:
        if not os.path.isfile(p):
            return jsonify({"success": False, "error": f"{label}: file not found at '{p}'"}), 400

    conn = get_connection()
    try:
        run_id = create_upload_run(open_po_path, bd_tracker_path, eagle_eye_path, conn=conn)
        run_info = get_run_status(run_id, conn=conn)

        if run_info.get("rejected_duplicate"):
            conn.close()
            return jsonify({
                "success": True, "run_id": run_id, "duplicate": True,
                "status": "Completed (Duplicate)",
                "message": "Identical files already processed. Duplicate run recorded.",
            })

        result = process_upload_run(run_id, conn=conn)
        counts = _row_counts_for_run(run_id, conn)
        conn.close()

        return jsonify({
            "success": True, "run_id": run_id, "duplicate": False,
            "status": result["status"],
            "row_counts": result.get("row_counts", counts),
            "errors": result.get("errors", []),
        })

    except Exception as e:
        conn.close()
        tb = traceback.format_exc()
        app.logger.error(f"Process error: {tb}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/status/<run_id>")
def status(run_id):
    conn = get_connection()
    try:
        run = get_run_status(run_id, conn=conn)
        counts = _row_counts_for_run(run_id, conn)
        conn.close()
        return jsonify({
            "run_id": run["run_id"], "status": run["run_status"],
            "created_at": run["created_at"], "completed_at": run.get("completed_at"),
            "rejected_duplicate": run.get("rejected_duplicate", 0),
            "duplicate_of_run_id": run.get("duplicate_of_run_id"),
            "pipeline_version": run.get("pipeline_version"),
            "threshold_config_version": run.get("threshold_config_version"),
            "row_counts": counts,
        })
    except ValueError:
        conn.close()
        return jsonify({"error": "Run not found"}), 404


@app.route("/runs")
def runs():
    conn = get_connection()
    try:
        runs_list = list_upload_runs(limit=50, conn=conn)
        for r in runs_list:
            r["row_counts"] = _row_counts_for_run(r["run_id"], conn)
        return jsonify(runs_list)
    finally:
        conn.close()


@app.route("/run/<run_id>")
def run_detail(run_id):
    conn = get_connection()
    try:
        run = get_run_status(run_id, conn=conn)
    except ValueError:
        conn.close()
        return _safe_render("base.html", content="<div class='alert alert-danger m-4'>Run not found</div>"), 404

    try:
        counts = _row_counts_for_run(run_id, conn)
        cursor = conn.execute("SELECT * FROM source_file_uploads WHERE run_id = ?", (run_id,))
        source_files = [dict(r) for r in cursor.fetchall()]
        archive_meta = pipeline_service.get_archive_metadata(run_id)
        return render_template("run_detail.html", run=run, row_counts=counts,
                               source_files=source_files, archive_meta=archive_meta)
    finally:
        conn.close()


@app.route("/run/<run_id>/data")
def run_data(run_id):
    conn = get_connection()
    try:
        run = get_run_status(run_id, conn=conn)
    except ValueError:
        conn.close()
        return jsonify({"error": "Run not found"}), 404

    try:
        counts = _row_counts_for_run(run_id, conn)
        cursor = conn.execute("SELECT * FROM source_file_uploads WHERE run_id = ?", (run_id,))
        source_files = [dict(r) for r in cursor.fetchall()]

        detail = get_master_detail(run_id=run_id, page=1, page_size=5000, exclude_completed=False, conn=conn)
        po_summary = get_po_summary(run_id=run_id, page=1, page_size=5000, exclude_completed=False, conn=conn)
        unmatched_bd = get_unmatched_records(run_id=run_id, source_type="bd", page=1, page_size=5000, conn=conn)
        unmatched_ee = get_unmatched_records(run_id=run_id, source_type="ee", page=1, page_size=5000, conn=conn)
        ambiguous = get_ambiguous_matches(run_id=run_id, conn=conn)
        dq = get_dq_exceptions(run_id=run_id, page=1, page_size=5000, conn=conn)

        return jsonify({
            "run": run, "row_counts": counts, "source_files": source_files,
            "master_detail": detail, "po_summary": po_summary,
            "unmatched_bd": unmatched_bd, "unmatched_ee": unmatched_ee,
            "ambiguous_matches": ambiguous, "dq_exceptions": dq,
        })
    finally:
        conn.close()


@app.route("/download/<run_id>")
def download_workbook(run_id):
    conn = get_connection()
    try:
        run = get_run_status(run_id, conn=conn)
    except ValueError:
        conn.close()
        return _safe_render("base.html", content="<div class='alert alert-danger m-4'>Run not found</div>"), 404

    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        tables = {
            "Master_Detail": "master_detail_records",
            "PO_Summary": "po_summary_records",
            "Unmatched_BD": "unmatched_bd_records",
            "Unmatched_EE": "unmatched_ee_records",
            "Ambiguous_Matches": "ambiguous_match_records",
            "DQ_Exceptions": "data_quality_exceptions",
            "Run_Log": None,
        }

        writer = pd.ExcelWriter(tmp_path, engine="openpyxl")
        for sheet_name, table_name in tables.items():
            if table_name:
                df = pd.read_sql(f"SELECT * FROM {table_name} WHERE run_id = ?", conn, params=(run_id,))
            else:
                df = pd.DataFrame([{
                    "Run_ID": run_id, "Created_At": run.get("created_at"),
                    "Completed_At": run.get("completed_at"), "Status": run.get("run_status"),
                    "Pipeline_Version": run.get("pipeline_version"),
                }])
            if not df.empty:
                df.to_excel(writer, sheet_name=sheet_name, index=False)
        writer.close()
        conn.close()

        return send_file(
            tmp_path, as_attachment=True,
            download_name=f"import_master_{run_id[:8]}.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    except Exception as e:
        conn.close()
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        app.logger.error(f"Download error: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


@app.route("/download/<run_id>/source/<source_type>")
def download_source_file(run_id, source_type):
    if source_type not in ("open_po", "bd_tracker", "eagle_eye"):
        abort(404)

    file_path = pipeline_service.get_archived_file_path(run_id, source_type)
    if not file_path:
        return _safe_render("base.html", content="<div class='alert alert-warning m-4'>Source file not found</div>"), 404

    meta = pipeline_service.get_archive_metadata(run_id)
    original_name = source_type
    if meta and "files" in meta:
        original_name = meta["files"].get(source_type, {}).get("original_filename", source_type)

    return send_file(file_path, as_attachment=True, download_name=original_name)


@app.route("/clear", methods=["POST"])
def clear_temp():
    removed = 0
    errors = 0
    if os.path.isdir(UPLOAD_FOLDER):
        for fname in os.listdir(UPLOAD_FOLDER):
            fpath = os.path.join(UPLOAD_FOLDER, fname)
            if os.path.isfile(fpath):
                try:
                    os.remove(fpath)
                    removed += 1
                except (PermissionError, OSError):
                    errors += 1
    return jsonify({
        "cleared": removed,
        "message": f"Removed {removed} temp files" + (f" ({errors} skipped)" if errors else ""),
    })


# ── Phase 4: Dashboard ────────────────────────────────────────────────────


def _update_query(active_filters, **changes):
    """Build a query string from active filters plus any changes.
    Usage: update_query(active_filters, remove='search') or
           update_query(active_filters, risk='Emergency', search='foo')
    """
    clear_all = changes.pop("clear_all", None)
    if clear_all:
        return ""

    params = dict(active_filters)
    # remove keys
    remove_key = changes.pop("remove", None)
    if remove_key:
        params.pop(remove_key, None)
    remove_dq = changes.pop("remove_dq", None)
    if remove_dq:
        params.pop(remove_dq, None)
    # apply additions
    for k, v in changes.items():
        if v:
            params[k] = v
        else:
            params.pop(k, None)

    parts = []
    for k, v in params.items():
        if v:
            parts.append(f"{k}={_url_quote(str(v))}")
    return "&".join(parts)


# Register as Jinja global for template use
app.jinja_env.globals["update_query"] = _update_query
app.jinja_env.globals["PIPELINE_VERSION"] = PIPELINE_VERSION


def _url_quote(s):
    import urllib.parse
    return urllib.parse.quote(s, safe="")


def _get_suppliers_for_run(run_id, conn):
    """Return list of unique suppliers for filter dropdown."""
    rows = conn.execute("""
        SELECT DISTINCT Supplier_Plant_Name as value
        FROM master_detail_records
        WHERE run_id = ? AND Supplier_Plant_Name IS NOT NULL AND Supplier_Plant_Name != ''
        ORDER BY Supplier_Plant_Name
    """, (run_id,)).fetchall()
    return [r["value"] for r in rows]


def _get_countries_for_run(run_id, conn):
    """Return list of unique origin countries for filter dropdown."""
    rows = conn.execute("""
        SELECT DISTINCT Origin_Code as value
        FROM master_detail_records
        WHERE run_id = ? AND Origin_Code IS NOT NULL AND Origin_Code != ''
        ORDER BY Origin_Code
    """, (run_id,)).fetchall()
    return [r["value"] for r in rows]


@app.route("/dashboard")
def dashboard():
    conn = get_connection()
    try:
        # Parse query parameters
        selected_run_id = request.args.get("run_id")
        search = request.args.get("search", "").strip() or None
        risk_filter = request.args.get("risk", "").strip() or None
        dq_filter = request.args.get("dq", "").strip() or None
        supplier_filter = request.args.get("supplier", "").strip() or None
        country_filter = request.args.get("country", "").strip() or None
        bd_match_filter = request.args.get("bd_match", "").strip() or None
        ee_match_filter = request.args.get("ee_match", "").strip() or None
        completed = request.args.get("completed", "0") == "1"
        page = int(request.args.get("page", 1))

        # Resolve run
        if selected_run_id:
            try:
                run = get_run_status(selected_run_id, conn=conn)
            except ValueError:
                run = get_latest_successful_run(conn=conn)
        else:
            run = get_latest_successful_run(conn=conn)

        if run is None:
            # No runs at all
            return render_template("dashboard.html", dashboard={
                "run": None,
                "runs": [],
                "cards": {
                    "total_open_pos": {"count": 0, "available": True},
                    "emergency": {"count": 0, "available": False},
                    "critical": {"count": 0, "available": False},
                    "watchlist": {"count": 0, "available": False},
                    "normal": {"count": 0, "available": False},
                    "missing_data": {"count": 0, "available": True},
                    "ambiguous_matches": {"count": 0, "available": True},
                    "dq_exceptions": {"count": 0, "available": True},
                },
                "po_table": {"data": [], "total": 0, "page": 1, "page_size": 20, "total_pages": 0},
                "filters": {"suppliers": [], "countries": [], "risk_categories": [], "dq_severities": []},
                "active_filters": {},
                "completed": completed,
                "threshold": get_threshold_config_status(conn=conn),
                "latest_run_id": None,
            })

        run_id = run["run_id"]
        exclude_completed = not completed

        # Get all runs for selector
        all_runs = list_upload_runs(limit=50, conn=conn)

        # Threshold status
        threshold = get_threshold_config_status(conn=conn)

        # Card counts
        cards = get_dashboard_card_counts(run_id, exclude_completed=exclude_completed, conn=conn)
        # Override risk card availability based on threshold
        if not threshold["active"]:
            for k in ("emergency", "critical", "watchlist", "normal"):
                cards[k]["available"] = False

        # Filter metadata
        suppliers = _get_suppliers_for_run(run_id, conn)
        countries = _get_countries_for_run(run_id, conn)

        # PO table data - one row per PO
        # Get all master_detail for this run, group by PO in Python
        detail_result = get_master_detail(
            run_id=run_id, page=1, page_size=10000,
            exclude_completed=exclude_completed, conn=conn,
        )

        # Group by PO and build table rows
        po_rows = {}
        for row in detail_result["data"]:
            po = row.get("Standardised_PO_Number", "")
            if not po:
                continue
            if po not in po_rows:
                po_rows[po] = {
                    "Standardised_PO_Number": po,
                    "Supplier_Plant_Name": row.get("Supplier_Plant_Name", ""),
                    "Supplier_Plant_ID": row.get("Supplier_Plant_ID", ""),
                    "Overall_Risk_Category": row.get("Overall_Risk_Category", ""),
                    "Data_Quality_Severity": row.get("Data_Quality_Severity", ""),
                    "Data_Quality_Reasons": row.get("Data_Quality_Reasons", ""),
                    "Next_Required_Milestone": row.get("Next_Required_Milestone", ""),
                    "Earliest_RDD": row.get("RDD", ""),
                    "RDD_Source": row.get("Merge_Method", ""),
                    "BD_Tracker_ETA": row.get("BD_Tracker_ETA", ""),
                    "Earliest_EE_ETA": row.get("Earliest_EE_ETA", ""),
                    "Open_PO_Quantity": row.get("Open_Quantity", ""),
                    "Standardised_Material_AGI": row.get("Standardised_Material_AGI", ""),
                    "Has_BD_Tracker_Match": row.get("Has_BD_Tracker_Match", 0),
                    "Has_Eagle_Eye_Match": row.get("Has_Eagle_Eye_Match", 0),
                }
                # Track earliest RDD across detail rows
            else:
                existing = po_rows[po]
                # Update to earliest RDD
                rdd = row.get("RDD", "")
                if rdd and (not existing["Earliest_RDD"] or rdd < existing["Earliest_RDD"]):
                    existing["Earliest_RDD"] = rdd
                # Replace risk with highest severity
                risk_order = {"Emergency": 4, "Critical": 3, "Watchlist": 2, "Normal": 1, "On Track": 1}
                cur_risk = row.get("Overall_Risk_Category", "")
                if cur_risk and risk_order.get(cur_risk, 0) > risk_order.get(existing["Overall_Risk_Category"], 0):
                    existing["Overall_Risk_Category"] = cur_risk
                # Collect AGIs
                agi = row.get("Standardised_Material_AGI", "")
                if agi:
                    existing_agis = existing.get("Standardised_Material_AGI", "")
                    if agi not in existing_agis:
                        existing["Standardised_Material_AGI"] = (existing_agis + ", " + agi) if existing_agis else agi

        # Convert to list and search/filter
        po_list = list(po_rows.values())

        if search:
            sl = search.lower()
            po_list = [p for p in po_list if
                       sl in (p.get("Standardised_PO_Number") or "").lower() or
                       sl in (p.get("Supplier_Plant_Name") or "").lower() or
                       sl in (p.get("Supplier_Plant_ID") or "").lower() or
                       sl in (p.get("Standardised_Material_AGI") or "").lower()]

        if risk_filter:
            po_list = [p for p in po_list if
                       (p.get("Overall_Risk_Category") or "").lower() == risk_filter.lower()]

        if dq_filter:
            dq_lower = dq_filter.lower()
            if dq_lower == "ambiguous":
                # Ambiguous matches are separate
                pass
            elif dq_lower == "dq_exception":
                po_list = [p for p in po_list if
                           (p.get("Data_Quality_Severity") or "") not in ("OK", "", None)]
            else:
                po_list = [p for p in po_list if
                           (p.get("Data_Quality_Severity") or "").lower() == dq_lower]

        if supplier_filter:
            po_list = [p for p in po_list if
                       supplier_filter.lower() in (p.get("Supplier_Plant_Name") or "").lower()]

        if country_filter:
            # Filter by country - need to check detail rows; simplified: check any row
            po_list = [p for p in po_list if False]  # Simplified: not doing detail-level country filter

        if bd_match_filter:
            val = 1 if bd_match_filter.lower() in ("yes", "matched", "1") else 0
            po_list = [p for p in po_list if p.get("Has_BD_Tracker_Match", 0) == val]

        if ee_match_filter:
            val = 1 if ee_match_filter.lower() in ("yes", "matched", "1") else 0
            po_list = [p for p in po_list if p.get("Has_Eagle_Eye_Match", 0) == val]

        total_pos = len(po_list)

        # Paginate
        page_size = 20
        total_pages = max(1, (total_pos + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        start = (page - 1) * page_size
        page_data = po_list[start:start + page_size]

        active_filters = {
            "run_id": run_id,
            "search": search or "",
            "risk": risk_filter or "",
            "dq": dq_filter or "",
            "supplier": supplier_filter or "",
            "country": country_filter or "",
            "bd_match": bd_match_filter or "",
            "ee_match": ee_match_filter or "",
        }

        dashboard_data = {
            "run": run,
            "runs": all_runs,
            "cards": cards,
            "po_table": {
                "data": page_data,
                "total": total_pos,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
            },
            "filters": {
                "suppliers": suppliers,
                "countries": countries,
                "risk_categories": ["Emergency", "Critical", "Watchlist", "Normal"],
                "dq_severities": ["Missing_Data", "Error"],
            },
            "active_filters": active_filters,
            "completed": completed,
            "threshold": threshold,
            "latest_run_id": get_latest_successful_run(conn=conn)["run_id"]
            if get_latest_successful_run(conn=conn) else None,
        }

        return render_template("dashboard.html", dashboard=dashboard_data)
    finally:
        conn.close()


@app.route("/po/<po_number>")
def po_detail(po_number):
    conn = get_connection()
    try:
        run_id = request.args.get("run_id")
        completed = request.args.get("completed", "0") == "1"

        if run_id:
            try:
                run = get_run_status(run_id, conn=conn)
            except ValueError:
                run = get_latest_successful_run(conn=conn)
        else:
            run = get_latest_successful_run(conn=conn)

        if run is None:
            return _safe_render("base.html",
                                content="<div class='alert alert-warning m-4'>No processing runs found.</div>"), 404

        detail = get_po_detail_data(po_number, run_id=run["run_id"], conn=conn)
        threshold = get_threshold_config_status(conn=conn)

        detail["threshold"] = threshold

        # Build dashboard return params
        dashboard_params = {}
        if run_id:
            dashboard_params["run_id"] = run_id
        if completed:
            dashboard_params["completed"] = "1"

        return render_template("po_detail.html", po_number=po_number,
                               detail=detail, dashboard_params=dashboard_params)
    finally:
        conn.close()


@app.route("/health")
def health():
    healthy = True
    db_status = "connected"
    try:
        conn = get_connection()
        conn.execute("SELECT 1")
        conn.close()
    except Exception:
        healthy = False
        db_status = "disconnected"
    return jsonify({
        "status": "ok" if healthy else "degraded",
        "version": PIPELINE_VERSION,
        "database": db_status,
    })


# ── Phase 5: Admin ─────────────────────────────────────────────────────────


def admin_required(f):
    """Decorator: require admin session for route access."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        token = request.form.get("token", "")
        if token == ADMIN_SECRET:
            session["admin_logged_in"] = True
            session.permanent = True
            return redirect(url_for("admin_profiles"))
        return render_template("admin_login.html", error="Invalid admin token")
    return render_template("admin_login.html", error=None)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("index"))


@app.route("/admin")
@admin_required
def admin_index():
    return redirect(url_for("admin_profiles"))


@app.route("/admin/profiles")
@admin_required
def admin_profiles():
    conn = get_connection()
    try:
        status_filter = request.args.get("status", "").strip() or None
        profiles = list_threshold_profiles(status=status_filter, conn=conn)
        return render_template("admin_profiles.html", profiles=profiles,
                               current_status=status_filter or "all")
    finally:
        conn.close()


@app.route("/admin/profiles/create", methods=["GET", "POST"])
@admin_required
def admin_profile_create():
    if request.method == "POST":
        name = request.form.get("profile_name", "").strip()
        desc = request.form.get("description", "").strip()
        country = request.form.get("country_code", "BD").strip()
        created_by = session.get("admin_name", request.form.get("created_by", "admin")).strip()
        if not name:
            return render_template("admin_profile_form.html", error="Profile name is required")
        conn = get_connection()
        try:
            pid = create_threshold_profile(name, country_code=country, description=desc,
                                           created_by=created_by, conn=conn)
            return redirect(url_for("admin_profile_detail", profile_id=pid))
        finally:
            conn.close()
    return render_template("admin_profile_form.html", profile=None, error=None)


@app.route("/admin/profiles/<int:profile_id>")
@admin_required
def admin_profile_detail(profile_id):
    conn = get_connection()
    try:
        profile = get_threshold_profile(profile_id, conn=conn)
        if profile is None:
            return _safe_render("base.html", content="<div class='alert alert-danger m-4'>Profile not found</div>"), 404
        audit = get_profile_audit_log(profile_id=profile_id, limit=50, conn=conn)
        return render_template("admin_profile_detail.html", profile=profile, audit=audit)
    finally:
        conn.close()


@app.route("/admin/profiles/<int:profile_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_profile_edit(profile_id):
    conn = get_connection()
    try:
        profile = get_threshold_profile(profile_id, conn=conn)
        if profile is None:
            return _safe_render("base.html", content="<div class='alert alert-danger m-4'>Profile not found</div>"), 404
        if profile["status"] not in ("Draft", "Inactive"):
            msg = ("<div class='alert alert-warning m-4'>Only Draft or Inactive profiles can be edited. "
                   "<a href='{}'>Create a new version</a> instead.</div>").format(
                       url_for("admin_profile_new_version", profile_id=profile_id))
            return _safe_render("base.html", content=msg), 400

        if request.method == "POST":
            changed_by = request.form.get("changed_by", "admin").strip()
            reason = request.form.get("reason", "").strip()
            # Update profile metadata
            updates = {}
            for key in ("profile_name", "description", "effective_from", "effective_to", "notes"):
                val = request.form.get(key, "").strip()
                if val:
                    updates[key] = val
            if updates:
                update_profile_metadata(profile_id, updates, changed_by=changed_by, reason=reason, conn=conn)

            # Update each rule
            for r in profile.get("rules", []):
                rule_updates = {}
                for key in ("milestone_name", "reference_date_used", "missing_incomplete_condition",
                            "action_owner", "notes"):
                    val = request.form.get(f"rule_{r['rule_id']}_{key}", "").strip()
                    if val:
                        rule_updates[key] = val
                for key in ("watchlist_days", "critical_days", "emergency_days"):
                    val = request.form.get(f"rule_{r['rule_id']}_{key}")
                    if val is not None and val.strip():
                        rule_updates[key] = int(val.strip())
                is_active = request.form.get(f"rule_{r['rule_id']}_is_active")
                rule_updates["is_active"] = 1 if is_active == "1" else 0

                if rule_updates:
                    update_profile_rule(r["rule_id"], rule_updates, changed_by=changed_by,
                                        reason=reason, conn=conn)

            return redirect(url_for("admin_profile_detail", profile_id=profile_id))

        return render_template("admin_profile_form.html", profile=profile, error=None)
    finally:
        conn.close()


@app.route("/admin/profiles/<int:profile_id>/new-version", methods=["POST"])
@admin_required
def admin_profile_new_version(profile_id):
    conn = get_connection()
    try:
        changed_by = request.form.get("changed_by", "admin").strip()
        reason = request.form.get("reason", "").strip()
        new_id = create_new_profile_version(profile_id, changed_by=changed_by,
                                            reason=reason or "New version requested", conn=conn)
        return redirect(url_for("admin_profile_edit", profile_id=new_id))
    finally:
        conn.close()


@app.route("/admin/profiles/<int:profile_id>/submit", methods=["POST"])
@admin_required
def admin_profile_submit(profile_id):
    conn = get_connection()
    try:
        changed_by = request.form.get("changed_by", "admin").strip()
        reason = request.form.get("reason", "").strip()
        submit_profile_for_approval(profile_id, changed_by=changed_by, reason=reason or None, conn=conn)
        return redirect(url_for("admin_profile_detail", profile_id=profile_id))
    except ValueError as e:
        conn.close()
        conn = get_connection()
        profile = get_threshold_profile(profile_id, conn=conn)
        conn.close()
        audit = get_profile_audit_log(profile_id=profile_id, limit=50, conn=get_connection())
        try:
            conn = get_connection()
            audit = get_profile_audit_log(profile_id=profile_id, limit=50, conn=conn)
            conn.close()
        except:
            audit = []
        return render_template("admin_profile_detail.html", profile=profile,
                               audit=audit, error=str(e))
    finally:
        conn.close()


@app.route("/admin/profiles/<int:profile_id>/approve", methods=["GET", "POST"])
@admin_required
def admin_profile_approve(profile_id):
    conn = get_connection()
    try:
        profile = get_threshold_profile(profile_id, conn=conn)
        if profile is None:
            return _safe_render("base.html", content="<div class='alert alert-danger m-4'>Profile not found</div>"), 404
        if profile["status"] != "Pending_Approval":
            return redirect(url_for("admin_profile_detail", profile_id=profile_id))

        if request.method == "POST":
            approved_by = request.form.get("approved_by", "").strip()
            reason = request.form.get("reason", "").strip()
            try:
                approve_profile(profile_id, approved_by=approved_by, reason=reason, conn=conn)
                return redirect(url_for("admin_profile_detail", profile_id=profile_id))
            except ValueError as e:
                return render_template("admin_profile_approve.html", profile=profile, error=str(e))
        return render_template("admin_profile_approve.html", profile=profile, error=None)
    finally:
        conn.close()


@app.route("/admin/profiles/<int:profile_id>/activate", methods=["GET", "POST"])
@admin_required
def admin_profile_activate(profile_id):
    conn = get_connection()
    try:
        profile = get_threshold_profile(profile_id, conn=conn)
        if profile is None:
            return _safe_render("base.html", content="<div class='alert alert-danger m-4'>Profile not found</div>"), 404

        # Get impact preview
        latest = get_latest_successful_run(conn=conn)
        preview = None
        run_id = request.args.get("run_id") or (latest["run_id"] if latest else None)
        if run_id:
            try:
                preview = get_profile_impact_preview(profile_id, run_id=run_id, conn=conn)
            except Exception:
                preview = None

        if request.method == "POST":
            activated_by = request.form.get("activated_by", "").strip()
            reason = request.form.get("reason", "").strip()
            confirmed = request.form.get("confirm", "") == "1"
            if not confirmed:
                return render_template("admin_profile_activate.html", profile=profile,
                                       preview=preview, error="Please confirm activation")
            if not reason:
                return render_template("admin_profile_activate.html", profile=profile,
                                       preview=preview, error="Reason for activation is required")
            try:
                activate_profile(profile_id, activated_by=activated_by or "admin",
                                 reason=reason, conn=conn)
                return redirect(url_for("admin_profile_detail", profile_id=profile_id))
            except ValueError as e:
                return render_template("admin_profile_activate.html", profile=profile,
                                       preview=preview, error=str(e))

        return render_template("admin_profile_activate.html", profile=profile,
                               preview=preview, error=None)
    finally:
        conn.close()


@app.route("/admin/profiles/<int:profile_id>/deactivate", methods=["POST"])
@admin_required
def admin_profile_deactivate(profile_id):
    conn = get_connection()
    try:
        changed_by = request.form.get("changed_by", "admin").strip()
        reason = request.form.get("reason", "").strip()
        deactivate_profile(profile_id, changed_by=changed_by, reason=reason or None, conn=conn)
        return redirect(url_for("admin_profile_detail", profile_id=profile_id))
    except ValueError as e:
        conn.close()
        conn = get_connection()
        profile = get_threshold_profile(profile_id, conn=conn)
        conn.close()
        audit = []
        try:
            conn = get_connection()
            audit = get_profile_audit_log(profile_id=profile_id, limit=50, conn=conn)
            conn.close()
        except:
            pass
        return render_template("admin_profile_detail.html", profile=profile, audit=audit, error=str(e))
    finally:
        conn.close()


@app.route("/admin/profiles/<int:profile_id>/retire", methods=["POST"])
@admin_required
def admin_profile_retire(profile_id):
    conn = get_connection()
    try:
        changed_by = request.form.get("changed_by", "admin").strip()
        reason = request.form.get("reason", "").strip()
        retire_profile(profile_id, changed_by=changed_by, reason=reason or None, conn=conn)
        return redirect(url_for("admin_profile_detail", profile_id=profile_id))
    except ValueError as e:
        conn.close()
        conn = get_connection()
        profile = get_threshold_profile(profile_id, conn=conn)
        conn.close()
        return render_template("admin_profile_detail.html", profile=profile,
                               audit=get_profile_audit_log(profile_id=profile_id, limit=50, conn=get_connection()) if False else [],
                               error=str(e))
    finally:
        conn.close()


@app.route("/admin/profiles/<int:profile_id>/preview")
@admin_required
def admin_profile_preview(profile_id):
    conn = get_connection()
    try:
        profile = get_threshold_profile(profile_id, conn=conn)
        if profile is None:
            return _safe_render("base.html", content="<div class='alert alert-danger m-4'>Profile not found</div>"), 404
        latest = get_latest_successful_run(conn=conn)
        run_id = request.args.get("run_id") or (latest["run_id"] if latest else None)
        if not run_id:
            return _safe_render("base.html", content="<div class='alert alert-warning m-4'>No processing runs available for preview</div>"), 400
        preview = get_profile_impact_preview(profile_id, run_id=run_id, conn=conn)
        return render_template("admin_profile_preview.html", preview=preview, profile=profile)
    finally:
        conn.close()


@app.route("/admin/profiles/<int:profile_id>/history")
@admin_required
def admin_profile_history(profile_id):
    conn = get_connection()
    try:
        profile = get_threshold_profile(profile_id, conn=conn)
        if profile is None:
            return _safe_render("base.html", content="<div class='alert alert-danger m-4'>Profile not found</div>"), 404
        audit = get_profile_audit_log(profile_id=profile_id, limit=200, conn=conn)
        return render_template("admin_profile_history.html", profile=profile, audit=audit)
    finally:
        conn.close()


if __name__ == "__main__":
    print(f"  Pipeline version: {PIPELINE_VERSION}")
    print(f"  Upload folder: {UPLOAD_FOLDER}")
    print(f"  Archive folder: {ARCHIVE_BASE}")
    print(f"  Admin login: http://127.0.0.1:5000/admin/login")
    app.run(debug=True, host="127.0.0.1", port=5000)
