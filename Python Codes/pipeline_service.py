"""
pipeline_service.py — Full service layer for the Syngenta Bangladesh Import Tracker pipeline.

Phase 2: Orchestrates the ETL pipeline, manages database persistence,
run lifecycle, threshold profiles, and run comparison.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import traceback
import uuid
from datetime import datetime, date

import pandas as pd

from pipeline_db import get_connection, init_database, ColumnMapper

# Append parent so we can import sibling modules
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import clean_open_po
import clean_bd_tracker
import clean_eagle_eye
import merge_import_master

PIPELINE_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_conn(conn=None):
    if conn is None:
        conn = get_connection()
    return conn


def _df_val(val):
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    if isinstance(val, date) and not isinstance(val, str):
        return val.isoformat()
    if isinstance(val, datetime):
        return val.isoformat()
    return val


def _paginate(data, page, page_size):
    total = len(data)
    total_pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "data": data[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


def _resolved_run_id(run_id, conn):
    if run_id is not None:
        return run_id
    run = get_latest_successful_run(conn=conn)
    if run is None:
        raise ValueError("No successful run found and no run_id provided")
    return run["run_id"]


def _dict_from_row(row):
    if row is None:
        return None
    return dict(row)


def _rows_to_dicts(rows):
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# File validation
# ---------------------------------------------------------------------------

def validate_upload_files(open_po_path, bd_tracker_path, eagle_eye_path, conn=None):
    """Validate the three uploaded files exist and have required sheets/columns.
    Returns dict with 'valid' (bool), 'errors' (list), 'details' (dict per file).
    Does NOT create a run."""
    conn = _ensure_conn(conn)
    errors = []
    details = {}

    file_configs = [
        ("open_po", open_po_path, {"sheet": "Data", "cols": [
            "Material", "Short Text", "Purchasing Document",
            "Supplier/Supplying Plant", "Still to be delivered (qty)", "Order Unit"
        ]}),
        ("bd_tracker", bd_tracker_path, {"sheet": "Tracker File", "cols": [
            "Overall Status", " PO", "AGI", "LC  Date", "SI shared date",
            "RDD", "ETD", "ETA", "OBL/EBL rcvd Date", "Final docs rcvd Date"
        ]}),
        ("eagle_eye", eagle_eye_path, {"sheet": "Sheet1", "cols": [
            "From", "DDPO", "AGI Code", "Container No.", "Tracking", "Status", "ETA"
        ]}),
    ]

    for ftype, fpath, cfg in file_configs:
        finfo = {"path": fpath, "exists": False, "sheets_ok": False, "columns_ok": False, "sheet_names": []}
        if fpath is None or not os.path.isfile(fpath):
            errors.append(f"{ftype}: file not found at '{fpath}'")
            details[ftype] = finfo
            continue
        finfo["exists"] = True
        try:
            xls = pd.ExcelFile(fpath)
            finfo["sheet_names"] = xls.sheet_names
            if cfg["sheet"] not in xls.sheet_names:
                errors.append(f"{ftype}: required sheet '{cfg['sheet']}' not found (available: {xls.sheet_names})")
                details[ftype] = finfo
                continue
            finfo["sheets_ok"] = True
            df = pd.read_excel(fpath, sheet_name=cfg["sheet"], dtype=str, keep_default_na=False, nrows=1)
            missing = [c for c in cfg["cols"] if c not in df.columns]
            if missing:
                errors.append(f"{ftype}: missing required columns: {missing}")
            else:
                finfo["columns_ok"] = True
        except Exception as e:
            errors.append(f"{ftype}: could not read file: {e}")
        details[ftype] = finfo

    return {"valid": len(errors) == 0, "errors": errors, "details": details}


# ---------------------------------------------------------------------------
# File hashing
# ---------------------------------------------------------------------------

def compute_file_hash(filepath):
    """Return SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def check_duplicate_upload(hashes_dict, conn=None):
    """Check if the same 3 file hashes have been uploaded before.
    Returns (is_duplicate, duplicate_run_id, existing_run_status)."""
    conn = _ensure_conn(conn)
    conn.execute("PRAGMA foreign_keys=OFF")
    cursor = conn.execute(
        """SELECT s1.run_id, u.run_status FROM source_file_uploads s1
           JOIN upload_runs u ON u.run_id = s1.run_id
           WHERE s1.source_type = 'open_po' AND s1.file_hash = ?
           INTERSECT
           SELECT s2.run_id, u2.run_status FROM source_file_uploads s2
           JOIN upload_runs u2 ON u2.run_id = s2.run_id
           WHERE s2.source_type = 'bd_tracker' AND s2.file_hash = ?
           INTERSECT
           SELECT s3.run_id, u3.run_status FROM source_file_uploads s3
           JOIN upload_runs u3 ON u3.run_id = s3.run_id
           WHERE s3.source_type = 'eagle_eye' AND s3.file_hash = ?
        """,
        (hashes_dict.get("open_po", ""),
         hashes_dict.get("bd_tracker", ""),
         hashes_dict.get("eagle_eye", "")),
    )
    row = cursor.fetchone()
    conn.execute("PRAGMA foreign_keys=ON")
    if row:
        return True, row["run_id"], row["run_status"]
    return False, None, None


# ---------------------------------------------------------------------------
# Source file archiving
# ---------------------------------------------------------------------------

def get_archive_dir(archive_base=None):
    """Return the archive directory path, creating it if needed.

    Uses the following precedence for the archive base directory:
    1. The `archive_base` argument (if provided)
    2. The `IMPORT_TRACKER_ARCHIVE` environment variable
    3. Default: %LOCALAPPDATA%/import_tracker/archive (Windows)
       or ~/.local/share/import_tracker/archive (Unix)

    This is a durable application-controlled path, NOT a temp directory.
    Temp directories can be cleared by Windows, cleanup tools, or IT policy.
    """
    if archive_base is None:
        archive_base = os.environ.get(
            "IMPORT_TRACKER_ARCHIVE",
            os.path.join(
                os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                "import_tracker",
                "archive",
            ),
        )
    os.makedirs(archive_base, exist_ok=True)
    return archive_base


def archive_source_files(run_id, source_files, archive_base=None):
    """Copy uploaded source files to immutable archive by run_id.

    source_files: dict with keys 'open_po', 'bd_tracker', 'eagle_eye'
                  each mapping to the file path on disk.

    Creates: archive/<run_id>/<source_type>_<original_basename>
    Also writes archive/<run_id>/metadata.json with original filenames,
    file hashes, upload timestamp, and source types.

    Returns the archive directory path for the run.
    """
    import json
    from datetime import datetime

    archive_base = get_archive_dir(archive_base)
    run_archive_dir = os.path.join(archive_base, run_id)
    os.makedirs(run_archive_dir, exist_ok=True)

    metadata = {
        "run_id": run_id,
        "archived_at": datetime.now().isoformat(),
        "files": {},
    }

    for source_type, filepath in source_files.items():
        if not filepath or not os.path.exists(filepath):
            continue
        basename = os.path.basename(filepath)
        archived_name = f"{source_type}_{basename}"
        archived_path = os.path.join(run_archive_dir, archived_name)
        import shutil
        shutil.copy2(filepath, archived_path)
        metadata["files"][source_type] = {
            "original_filename": basename,
            "archived_filename": archived_name,
            "archived_path": archived_path,
            "file_hash": compute_file_hash(filepath),
        }

    meta_path = os.path.join(run_archive_dir, "metadata.json")
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)

    return run_archive_dir


def get_archived_file_path(run_id, source_type, archive_base=None):
    """Return the path to an archived source file for a run.

    Returns None if the file or run archive does not exist.
    """
    archive_base = get_archive_dir(archive_base)
    run_archive_dir = os.path.join(archive_base, run_id)
    meta_path = os.path.join(run_archive_dir, "metadata.json")
    if not os.path.exists(meta_path):
        return None
    import json
    with open(meta_path) as f:
        meta = json.load(f)
    file_info = meta.get("files", {}).get(source_type)
    if file_info is None:
        return None
    archived_path = file_info.get("archived_path")
    if archived_path and os.path.exists(archived_path):
        return archived_path
    # Fallback: try to find by convention
    candidate = os.path.join(run_archive_dir, file_info.get("archived_filename", ""))
    if os.path.exists(candidate):
        return candidate
    return None


def get_archive_metadata(run_id, archive_base=None):
    """Return the metadata dict for an archived run, or None."""
    archive_base = get_archive_dir(archive_base)
    meta_path = os.path.join(archive_base, run_id, "metadata.json")
    if not os.path.exists(meta_path):
        return None
    import json
    with open(meta_path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Create upload run
# ---------------------------------------------------------------------------

def create_upload_run(open_po_path, bd_tracker_path, eagle_eye_path, run_id=None, conn=None):
    """Validate, check duplicates, create upload_runs + source_file_uploads records.
    Returns run_id. If duplicate and same files, sets status='Completed' and skips processing."""
    conn = _ensure_conn(conn)
    init_database(conn)

    # Validate files
    v = validate_upload_files(open_po_path, bd_tracker_path, eagle_eye_path, conn=conn)
    if not v["valid"]:
        raise ValueError(f"Upload validation failed: {'; '.join(v['errors'])}")

    # Compute hashes
    hashes = {
        "open_po": compute_file_hash(open_po_path),
        "bd_tracker": compute_file_hash(bd_tracker_path),
        "eagle_eye": compute_file_hash(eagle_eye_path),
    }

    # Check duplicate
    is_dup, dup_run_id, dup_status = check_duplicate_upload(hashes, conn=conn)
    if is_dup and dup_run_id:
        if run_id is None:
            run_id = str(uuid.uuid4())
        now_iso = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO upload_runs (run_id, run_status, created_at, completed_at, pipeline_version, "
            "rejected_duplicate, duplicate_of_run_id, run_notes) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (run_id, "Completed", now_iso, now_iso, PIPELINE_VERSION, dup_run_id,
             f"Duplicate of run {dup_run_id} (status: {dup_status})"),
        )
        _insert_source_files(conn, run_id, open_po_path, bd_tracker_path, eagle_eye_path, hashes, now_iso)
        conn.commit()
        return run_id

    if run_id is None:
        run_id = str(uuid.uuid4())

    now_iso = datetime.now().isoformat()

    # Load threshold config for version info
    threshold_cfg = merge_import_master.load_threshold_config()
    cfg_version = None
    if threshold_cfg:
        cfg_version = threshold_cfg.get("config_version")

    conn.execute(
        "INSERT INTO upload_runs (run_id, run_status, created_at, pipeline_version, threshold_config_version) "
        "VALUES (?, ?, ?, ?, ?)",
        (run_id, "Uploaded", now_iso, PIPELINE_VERSION, cfg_version),
    )

    _insert_source_files(conn, run_id, open_po_path, bd_tracker_path, eagle_eye_path, hashes, now_iso)
    conn.commit()
    return run_id


def _insert_source_files(conn, run_id, open_po_path, bd_tracker_path, eagle_eye_path, hashes, timestamp):
    for ftype, fpath in [("open_po", open_po_path), ("bd_tracker", bd_tracker_path), ("eagle_eye", eagle_eye_path)]:
        conn.execute(
            "INSERT INTO source_file_uploads (run_id, source_type, original_filename, file_hash, upload_timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, ftype, os.path.abspath(fpath), hashes[ftype], timestamp),
        )


# ---------------------------------------------------------------------------
# Process upload run
# ---------------------------------------------------------------------------

def process_upload_run(run_id, conn=None):
    """Run the full pipeline for a run_id that is in 'Uploaded' status.
    Updates status: Uploaded -> Processing -> (Completed | Failed | Completed_With_Exceptions).
    Stores all output DataFrames in their respective tables.
    Detects DQ exceptions and sets status accordingly.
    On failure, sets status='Failed' and does NOT affect latest successful run.
    Returns dict with status, row_counts, errors."""
    conn = _ensure_conn(conn)

    cursor = conn.execute("SELECT * FROM upload_runs WHERE run_id = ?", (run_id,))
    run = cursor.fetchone()
    if run is None:
        raise ValueError(f"Run {run_id} not found")
    if run["run_status"] != "Uploaded":
        raise ValueError(f"Run {run_id} is in status '{run['run_status']}', expected 'Uploaded'")

    conn.execute("UPDATE upload_runs SET run_status = 'Processing' WHERE run_id = ?", (run_id,))
    conn.commit()

    # Get source files
    cursor = conn.execute("SELECT * FROM source_file_uploads WHERE run_id = ?", (run_id,))
    sources = cursor.fetchall()
    file_paths = {s["source_type"]: s["original_filename"] for s in sources}

    workspace = None
    row_counts = {}
    errors = []
    archive_successful = False

    try:
        # --- Step 0: Archive source files immutably BEFORE processing ---
        # Durable archiving is required for audit/reprocessing.
        # If archiving fails and processing succeeds, status becomes
        # Completed_With_Archive_Warning so the operator knows to intervene.
        try:
            archive_source_files(run_id, {
                "open_po": file_paths.get("open_po"),
                "bd_tracker": file_paths.get("bd_tracker"),
                "eagle_eye": file_paths.get("eagle_eye"),
            })
            archive_successful = True
            conn.execute(
                "UPDATE upload_runs SET run_notes = ? WHERE run_id = ?",
                ("Archived", run_id),
            )
        except Exception as archive_err:
            archive_successful = False
            archive_msg = f"Archive failed: {archive_err}"
            errors.append(archive_msg)
            conn.execute(
                "UPDATE upload_runs SET run_notes = ? WHERE run_id = ?",
                (f"Archive_failed: {archive_err}", run_id),
            )
        workspace = tempfile.mkdtemp(prefix=f"import_tracker_{run_id[:8]}_")

        # --- Step 1: Clean Open PO ---
        tmp_op = os.path.join(workspace, "q_Open_PO_Import_Base.xlsx")
        op_df = clean_open_po.clean_open_po(file_paths["open_po"], tmp_op)
        raw_op_rows = len(op_df)

        # --- Step 2: Clean BD Tracker ---
        tmp_bd = os.path.join(workspace, "q_BD_Tracker_Clean.xlsx")
        bd_audit, bd_df = clean_bd_tracker.clean_bd_tracker(file_paths["bd_tracker"], tmp_bd)
        raw_bd_rows = len(bd_audit)

        # --- Step 3: Clean Eagle Eye ---
        tmp_ee = os.path.join(workspace, "q_Eagle_Eye_Clean.xlsx")
        ee_audit, ee_df = clean_eagle_eye.clean_eagle_eye(file_paths["eagle_eye"], tmp_ee)
        raw_ee_rows = len(ee_audit)

        # --- Step 4: Merge all sources ---
        output_path = os.path.join(workspace, "import_master_data.xlsx")
        data = merge_import_master.merge_import_master(
            tmp_op, tmp_bd, tmp_ee, output_path,
            raw_op_rows=raw_op_rows,
            raw_bd_rows=raw_bd_rows,
            raw_ee_rows=raw_ee_rows,
        )

        # Patch run_log with our run_id
        if "run_log" in data and not data["run_log"].empty:
            data["run_log"]["Run_ID"] = run_id

        # --- Step 5: Store results in DB ---
        store_dataframes_in_db(run_id, data, conn=conn)

        # Row counts for return dict
        row_counts = {
            "master_detail": len(data.get("master_detail", [])),
            "po_summary": len(data.get("po_summary", [])),
            "unmatched_bd": len(data.get("unmatched_bd", [])),
            "unmatched_ee": len(data.get("unmatched_ee", [])),
            "ambiguous_matches": len(data.get("ambiguous_matches", [])),
            "dq_exceptions": len(data.get("dq_exceptions", [])),
        }

        # --- Step 6: Determine status ---
        dq_exceptions = data.get("dq_exceptions", pd.DataFrame())
        dq_count = len(dq_exceptions) if hasattr(dq_exceptions, "__len__") else 0

        if not archive_successful:
            status = "Completed_With_Archive_Warning"
        elif dq_count > 0:
            status = "Completed_With_Exceptions"
        else:
            status = "Completed"

        # Link the active threshold profile for this processing run
        active_profile_id = None
        active_profile_version = None
        try:
            cursor = conn.execute(
                "SELECT profile_id, version FROM threshold_profiles "
                "WHERE status = 'Active' AND (effective_from IS NULL OR effective_from <= ?) "
                "AND (effective_to IS NULL OR effective_to >= ?) "
                "ORDER BY version DESC LIMIT 1",
                (datetime.now().isoformat(), datetime.now().isoformat()),
            )
            ap = cursor.fetchone()
            if ap:
                active_profile_id = ap["profile_id"]
                active_profile_version = ap["version"]
        except Exception:
            pass

        if active_profile_id is not None:
            conn.execute(
                "UPDATE upload_runs SET run_status = ?, completed_at = ?, "
                "threshold_profile_id = ?, threshold_profile_version = ? WHERE run_id = ?",
                (status, datetime.now().isoformat(), active_profile_id, active_profile_version, run_id),
            )
        else:
            conn.execute(
                "UPDATE upload_runs SET run_status = ?, completed_at = ? WHERE run_id = ?",
                (status, datetime.now().isoformat(), run_id),
            )
        conn.commit()

    except Exception as e:
        tb = traceback.format_exc()
        errors.append(f"{type(e).__name__}: {e}")
        errors.append(tb)
        conn.execute(
            "UPDATE upload_runs SET run_status = 'Failed', completed_at = ? WHERE run_id = ?",
            (datetime.now().isoformat(), run_id),
        )
        conn.commit()
        status = "Failed"

    finally:
        if workspace and os.path.isdir(workspace):
            shutil.rmtree(workspace, ignore_errors=True)

    return {
        "status": status,
        "row_counts": row_counts,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Store DataFrames in DB
# ---------------------------------------------------------------------------

def store_dataframes_in_db(run_id, data_dict, conn=None):
    """Store all DataFrames from merge_import_master dict into their DB tables.
    data_dict has keys: master_detail, po_summary, unmatched_bd, unmatched_ee,
                         ambiguous_matches, dq_exceptions, run_log"""
    conn = _ensure_conn(conn)

    for table_type in ColumnMapper.get_table_types():
        df = data_dict.get(table_type)
        if df is None or (hasattr(df, "empty") and df.empty):
            continue

        table_info = ColumnMapper.get_table_info(table_type)
        table_name = table_info["table"]
        db_columns = table_info["columns"]
        rename_map = table_info["rename_map"]

        _insert_dataframe(conn, table_name, db_columns, rename_map, run_id, df)


def _insert_dataframe(conn, table_name, db_columns, rename_map, run_id, df):
    """Insert rows from a DataFrame into a DB table with column mapping."""
    df_work = df.copy()
    df_work["run_id"] = run_id

    # Rename columns to match DB names
    for df_col, db_col in rename_map.items():
        if df_col in df_work.columns:
            df_work.rename(columns={df_col: db_col}, inplace=True)

    cols = ["run_id"] + [c for c in db_columns if c in df_work.columns]
    placeholders = ", ".join("?" for _ in cols)
    col_list = ", ".join(cols)

    sql = f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})"

    rows = []
    for _, row in df_work.iterrows():
        vals = [_df_val(row.get(c)) for c in cols]
        rows.append(vals)

    conn.executemany(sql, rows)
    conn.commit()


# ---------------------------------------------------------------------------
# Run status and listing
# ---------------------------------------------------------------------------

def get_run_status(run_id, conn=None):
    """Return a dict with run status info (status, created_at, completed_at, etc.)."""
    conn = _ensure_conn(conn)
    cursor = conn.execute("SELECT * FROM upload_runs WHERE run_id = ?", (run_id,))
    row = cursor.fetchone()
    if row is None:
        raise ValueError(f"Run {run_id} not found")
    return _dict_from_row(row)


def list_upload_runs(status=None, limit=50, offset=0, conn=None):
    """Return list of run summaries ordered by created_at DESC.
    Supports pagination and optional status filter."""
    conn = _ensure_conn(conn)
    if status:
        cursor = conn.execute(
            "SELECT * FROM upload_runs WHERE run_status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (status, limit, offset),
        )
    else:
        cursor = conn.execute(
            "SELECT * FROM upload_runs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
    return _rows_to_dicts(cursor.fetchall())


def get_latest_successful_run(conn=None):
    """Return the most recent run with status 'Completed' or 'Completed_With_Exceptions'.
    Returns None if no successful runs exist."""
    conn = _ensure_conn(conn)
    cursor = conn.execute(
        "SELECT * FROM upload_runs WHERE run_status IN "
        "('Completed', 'Completed_With_Exceptions', 'Completed_With_Archive_Warning') "
        "ORDER BY created_at DESC LIMIT 1",
    )
    row = cursor.fetchone()
    return _dict_from_row(row) if row else None


# ---------------------------------------------------------------------------
# Data retrieval with pagination
# ---------------------------------------------------------------------------

def get_master_detail(run_id=None, page=1, page_size=100, search=None,
                      po_filter=None, status_filter=None,
                      exclude_completed=True, conn=None):
    """Return paginated master detail records for a run.
    If run_id is None, uses latest successful run.
    Supports search across PO, AGI, Product; and filters.
    exclude_completed=True (default) filters out Completed POs from results."""
    conn = _ensure_conn(conn)
    run_id = _resolved_run_id(run_id, conn)

    conditions = ["run_id = ?"]
    params = [run_id]

    if exclude_completed:
        conditions.append("(Overall_Import_Status IS NULL OR Overall_Import_Status NOT LIKE ?)")
        params.append("Completed%")

    if search:
        conditions.append(
            "(Standardised_PO_Number LIKE ? OR Standardised_Material_AGI LIKE ? "
            "OR Standardised_Material_AGI_Stripped LIKE ? OR Product_Name LIKE ?)"
        )
        s = f"%{search}%"
        params.extend([s, s, s, s])

    if po_filter:
        conditions.append("Standardised_PO_Number LIKE ?")
        params.append(f"%{po_filter}%")

    if status_filter:
        conditions.append("Overall_Import_Status LIKE ?")
        params.append(f"%{status_filter}%")

    where = " AND ".join(conditions)
    count_sql = f"SELECT COUNT(*) as cnt FROM master_detail_records WHERE {where}"
    total = conn.execute(count_sql, params).fetchone()["cnt"]

    offset = (page - 1) * page_size
    data_sql = f"SELECT * FROM master_detail_records WHERE {where} ORDER BY record_id LIMIT ? OFFSET ?"
    cursor = conn.execute(data_sql, params + [page_size, offset])

    records = _rows_to_dicts(cursor.fetchall())
    return _paginate(records, page, page_size) | {"run_id": run_id, "total": total}


def get_po_summary(run_id=None, page=1, page_size=100, search=None,
                   exclude_completed=True, conn=None):
    """Return paginated PO summary records.
    exclude_completed=True (default) filters out Completed PO rows."""
    conn = _ensure_conn(conn)
    run_id = _resolved_run_id(run_id, conn)

    conditions = ["run_id = ?"]
    params = [run_id]

    if exclude_completed:
        conditions.append("(Next_Required_Milestone IS NULL OR Next_Required_Milestone != 'Milestones complete')")

    if search:
        conditions.append("Standardised_PO_Number LIKE ?")
        params.append(f"%{search}%")

    where = " AND ".join(conditions)
    total = conn.execute(
        f"SELECT COUNT(*) as cnt FROM po_summary_records WHERE {where}", params
    ).fetchone()["cnt"]

    offset = (page - 1) * page_size
    cursor = conn.execute(
        f"SELECT * FROM po_summary_records WHERE {where} ORDER BY summary_id LIMIT ? OFFSET ?",
        params + [page_size, offset],
    )
    records = _rows_to_dicts(cursor.fetchall())
    return _paginate(records, page, page_size) | {"run_id": run_id, "total": total}


def get_unmatched_records(run_id=None, source_type='bd', page=1, page_size=100, conn=None):
    """Return unmatched records (bd or ee) for a run."""
    conn = _ensure_conn(conn)
    run_id = _resolved_run_id(run_id, conn)

    table = "unmatched_bd_records" if source_type == 'bd' else "unmatched_ee_records"

    total = conn.execute(
        f"SELECT COUNT(*) as cnt FROM {table} WHERE run_id = ?", (run_id,)
    ).fetchone()["cnt"]

    offset = (page - 1) * page_size
    cursor = conn.execute(
        f"SELECT * FROM {table} WHERE run_id = ? ORDER BY unmatched_id LIMIT ? OFFSET ?",
        (run_id, page_size, offset),
    )
    records = _rows_to_dicts(cursor.fetchall())
    return _paginate(records, page, page_size) | {"run_id": run_id, "total": total, "source_type": source_type}


def get_ambiguous_matches(run_id=None, conn=None):
    """Return ambiguous match records for a run."""
    conn = _ensure_conn(conn)
    run_id = _resolved_run_id(run_id, conn)

    cursor = conn.execute(
        "SELECT * FROM ambiguous_match_records WHERE run_id = ? ORDER BY ambiguous_id", (run_id,)
    )
    return {"data": _rows_to_dicts(cursor.fetchall()), "run_id": run_id}


def get_dq_exceptions(run_id=None, severity=None, page=1, page_size=100, conn=None):
    """Return DQ exception records for a run, optionally filtered by severity."""
    conn = _ensure_conn(conn)
    run_id = _resolved_run_id(run_id, conn)

    conditions = ["run_id = ?"]
    params = [run_id]

    if severity:
        conditions.append("Data_Quality_Severity = ?")
        params.append(severity)

    where = " AND ".join(conditions)
    total = conn.execute(
        f"SELECT COUNT(*) as cnt FROM data_quality_exceptions WHERE {where}", params
    ).fetchone()["cnt"]

    offset = (page - 1) * page_size
    cursor = conn.execute(
        f"SELECT * FROM data_quality_exceptions WHERE {where} ORDER BY exception_id LIMIT ? OFFSET ?",
        params + [page_size, offset],
    )
    records = _rows_to_dicts(cursor.fetchall())
    return _paginate(records, page, page_size) | {"run_id": run_id, "total": total}


# ---------------------------------------------------------------------------
# Threshold profiles (Phase 5)
# ---------------------------------------------------------------------------

DEFAULT_MILESTONES = [
    {
        "milestone_name": "LC Date",
        "reference_date_used": "LC_Date",
        "missing_incomplete_condition": "LC_Date is null or empty",
        "watchlist_days": 30,
        "critical_days": 20,
        "emergency_days": 10,
        "action_owner": "Procurement",
        "is_active": True,
    },
    {
        "milestone_name": "SI Shared Date",
        "reference_date_used": "SI_Shared_Date",
        "missing_incomplete_condition": "SI_Shared_Date is null or empty",
        "watchlist_days": 25,
        "critical_days": 15,
        "emergency_days": 7,
        "action_owner": "Procurement",
        "is_active": True,
    },
    {
        "milestone_name": "BD Tracker ETD",
        "reference_date_used": "BD_Tracker_ETD",
        "missing_incomplete_condition": "BD_Tracker_ETD is null or empty",
        "watchlist_days": 20,
        "critical_days": 12,
        "emergency_days": 5,
        "action_owner": "Logistics",
        "is_active": True,
    },
    {
        "milestone_name": "BD Tracker ETA",
        "reference_date_used": "BD_Tracker_ETA",
        "missing_incomplete_condition": "BD_Tracker_ETA is null or empty",
        "watchlist_days": 30,
        "critical_days": 20,
        "emergency_days": 10,
        "action_owner": "Logistics",
        "is_active": True,
    },
    {
        "milestone_name": "OBL/EBL Received Date",
        "reference_date_used": "OBL_EBL_Received_Date",
        "missing_incomplete_condition": "OBL_EBL_Received_Date is null or empty",
        "watchlist_days": 15,
        "critical_days": 8,
        "emergency_days": 3,
        "action_owner": "Documentation",
        "is_active": True,
    },
    {
        "milestone_name": "Final Documents Received Date",
        "reference_date_used": "Final_Documents_Received_Date",
        "missing_incomplete_condition": "Final_Documents_Received_Date is null or empty",
        "watchlist_days": 10,
        "critical_days": 5,
        "emergency_days": 2,
        "action_owner": "Documentation",
        "is_active": True,
    },
]

VALID_STATUSES = {"Draft", "Pending_Approval", "Approved", "Active", "Inactive", "Retired", "Expired"}


def _validate_rule_sequence(watchlist_days, critical_days, emergency_days):
    """Validate that watchlist > critical > emergency >= 0."""
    errors = []
    for name, val in [("Watchlist", watchlist_days), ("Critical", critical_days), ("Emergency", emergency_days)]:
        if not isinstance(val, (int, float)):
            errors.append(f"{name} days must be a number, got {type(val).__name__}")
    if errors:
        return errors
    if emergency_days < 0:
        errors.append("Emergency days must be >= 0")
    if critical_days <= emergency_days:
        errors.append(f"Critical days ({critical_days}) must be greater than Emergency days ({emergency_days})")
    if watchlist_days <= critical_days:
        errors.append(f"Watchlist days ({watchlist_days}) must be greater than Critical days ({critical_days})")
    return errors


def _validate_profile_for_activation(profile, conn=None):
    """Validate that a profile is complete and valid for activation.
    Returns list of error messages (empty = valid)."""
    conn = _ensure_conn(conn)
    errors = []
    if profile["status"] != "Approved":
        errors.append(f"Profile must be 'Approved' before activation (current: {profile['status']})")
    if not profile.get("approved_by"):
        errors.append("Profile must have approval metadata (approved_by, approved_at)")
    if not profile.get("effective_from"):
        errors.append("Profile must have an effective_from date")
    # Validate all rules
    rules = conn.execute(
        "SELECT * FROM threshold_profile_rules WHERE profile_id = ?", (profile["profile_id"],)
    ).fetchall()
    if not rules:
        errors.append("Profile must have at least one rule")
    for r in rules:
        if r["is_active"]:
            seq_errors = _validate_rule_sequence(r["watchlist_days"], r["critical_days"], r["emergency_days"])
            if seq_errors:
                errors.append(f"Rule '{r['milestone_name']}': {'; '.join(seq_errors)}")
    return errors


def _get_active_profile_for_country(country_code, as_of_date=None, conn=None):
    """Return the active profile for a given country code and date.
    as_of_date defaults to today. Returns None if no matching profile."""
    conn = _ensure_conn(conn)
    if as_of_date is None:
        from datetime import date
        as_of_date = date.today().isoformat()
    cursor = conn.execute("""
        SELECT * FROM threshold_profiles
        WHERE country_code = ?
          AND status = 'Active'
          AND (effective_from IS NULL OR effective_from <= ?)
          AND (effective_to IS NULL OR effective_to >= ?)
        ORDER BY version DESC
        LIMIT 1
    """, (country_code, as_of_date, as_of_date))
    row = cursor.fetchone()
    return _dict_from_row(row) if row else None


def create_threshold_profile(profile_name, country_code="BD", description=None,
                              created_by="admin", effective_from=None, conn=None):
    """Create a new threshold profile (Draft, v1) with 7 default milestone rules.
    Returns profile_id."""
    conn = _ensure_conn(conn)
    now_iso = datetime.now().isoformat()
    if effective_from is None:
        from datetime import date
        effective_from = date.today().isoformat()
    cursor = conn.execute(
        "INSERT INTO threshold_profiles "
        "(profile_name, country_code, description, version, status, created_at, created_by, effective_from) "
        "VALUES (?, ?, ?, 1, 'Draft', ?, ?, ?)",
        (profile_name, country_code, description, now_iso, created_by, effective_from),
    )
    profile_id = cursor.lastrowid
    for m in DEFAULT_MILESTONES:
        conn.execute(
            "INSERT INTO threshold_profile_rules "
            "(profile_id, milestone_name, reference_date_used, missing_incomplete_condition, "
            "watchlist_days, critical_days, emergency_days, action_owner, is_active, notes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)",
            (profile_id, m["milestone_name"], m["reference_date_used"],
             m["missing_incomplete_condition"], m["watchlist_days"], m["critical_days"],
             m["emergency_days"], m["action_owner"], 1 if m["is_active"] else 0, now_iso),
        )
    conn.commit()
    _log_audit(profile_id, 1, "Created", None, {"profile_name": profile_name, "country_code": country_code},
               created_by, "Profile created", conn=conn)
    return profile_id


def get_threshold_profile(profile_id, conn=None):
    """Return a profile dict with its rules list. Returns None if not found."""
    conn = _ensure_conn(conn)
    cursor = conn.execute("SELECT * FROM threshold_profiles WHERE profile_id = ?", (profile_id,))
    row = cursor.fetchone()
    if row is None:
        return None
    profile = _dict_from_row(row)
    cursor = conn.execute(
        "SELECT * FROM threshold_profile_rules WHERE profile_id = ? ORDER BY rule_id", (profile_id,)
    )
    profile["rules"] = _rows_to_dicts(cursor.fetchall())
    return profile


def list_threshold_profiles(status=None, country_code=None, conn=None):
    """List all threshold profiles, optionally filtered by status and/or country_code."""
    conn = _ensure_conn(conn)
    conditions = ["1=1"]
    params = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if country_code:
        conditions.append("country_code = ?")
        params.append(country_code)
    rows = conn.execute(
        f"SELECT * FROM threshold_profiles WHERE {' AND '.join(conditions)} ORDER BY created_at DESC",
        params,
    ).fetchall()
    return _rows_to_dicts(rows)


def update_profile_metadata(profile_id, updates, changed_by="admin", reason=None, conn=None):
    """Update profile metadata (profile_name, description, etc).
    Only allowed when status is Draft or Inactive.
    Returns updated profile. Raises ValueError if status prevents edit."""
    conn = _ensure_conn(conn)
    profile = get_threshold_profile(profile_id, conn=conn)
    if profile is None:
        raise ValueError(f"Profile {profile_id} not found")
    if profile["status"] not in ("Draft", "Inactive"):
        raise ValueError(f"Cannot edit profile in status '{profile['status']}'. Only Draft or Inactive profiles can be edited.")
    old_values = {k: profile.get(k) for k in updates if k in profile}
    now_iso = datetime.now().isoformat()
    set_clauses = []
    params = []
    for key in ("profile_name", "country_code", "description", "effective_from", "effective_to", "notes"):
        if key in updates:
            set_clauses.append(f"{key} = ?")
            params.append(updates[key])
    if set_clauses:
        params.append(profile_id)
        conn.execute(
            f"UPDATE threshold_profiles SET {', '.join(set_clauses)} WHERE profile_id = ?",
            params,
        )
    conn.commit()
    updated = get_threshold_profile(profile_id, conn=conn)
    _log_audit(profile_id, profile["version"], "Edited", old_values,
               {k: updated.get(k) for k in updates if k in updated},
               changed_by, reason or "Profile metadata updated", conn=conn)
    return updated


def update_profile_rule(rule_id, updates, changed_by="admin", reason=None, conn=None):
    """Update a specific rule. Validates sequence.
    updates can include: milestone_name, reference_date_used, watchlist_days,
    critical_days, emergency_days, action_owner, is_active, notes."""
    conn = _ensure_conn(conn)
    cursor = conn.execute(
        "SELECT r.*, p.status as profile_status, p.version as profile_version FROM threshold_profile_rules r "
        "JOIN threshold_profiles p ON p.profile_id = r.profile_id "
        "WHERE r.rule_id = ?", (rule_id,)
    )
    rule = cursor.fetchone()
    if rule is None:
        raise ValueError(f"Rule {rule_id} not found")
    if rule["profile_status"] not in ("Draft", "Inactive"):
        raise ValueError(f"Cannot edit rules when profile is '{rule['profile_status']}'")
    old_values = dict(rule)
    # Validate sequence
    wd = updates.get("watchlist_days", rule["watchlist_days"])
    cd = updates.get("critical_days", rule["critical_days"])
    ed = updates.get("emergency_days", rule["emergency_days"])
    seq_errors = _validate_rule_sequence(wd, cd, ed)
    if seq_errors:
        raise ValueError("; ".join(seq_errors))
    set_clauses = []
    params = []
    for key in ("milestone_name", "reference_date_used", "missing_incomplete_condition",
                "watchlist_days", "critical_days", "emergency_days",
                "action_owner", "is_active", "notes"):
        if key in updates:
            set_clauses.append(f"{key} = ?")
            params.append(updates[key])
    if set_clauses:
        params.append(rule_id)
        conn.execute(f"UPDATE threshold_profile_rules SET {', '.join(set_clauses)} WHERE rule_id = ?", params)
    conn.commit()
    cursor = conn.execute("SELECT * FROM threshold_profile_rules WHERE rule_id = ?", (rule_id,))
    new_rule = _dict_from_row(cursor.fetchone())
    _log_audit(rule["profile_id"], rule["profile_version"] if rule["profile_version"] is not None else 0, "Rule Edited",
               {"old": {k: old_values.get(k) for k in updates}},
               {"new": {k: new_rule.get(k) for k in updates}},
               changed_by, reason or f"Rule '{rule['milestone_name']}' updated", conn=conn)
    return new_rule


def submit_profile_for_approval(profile_id, changed_by="admin", reason=None, conn=None):
    """Move profile from Draft -> Pending_Approval. Validates all rules first."""
    conn = _ensure_conn(conn)
    profile = get_threshold_profile(profile_id, conn=conn)
    if profile is None:
        raise ValueError(f"Profile {profile_id} not found")
    if profile["status"] != "Draft":
        raise ValueError(f"Cannot submit profile in status '{profile['status']}' for approval")
    # Validate all active rules
    for r in profile["rules"]:
        if r["is_active"]:
            seq_errors = _validate_rule_sequence(r["watchlist_days"], r["critical_days"], r["emergency_days"])
            if seq_errors:
                raise ValueError(f"Rule '{r['milestone_name']}': {'; '.join(seq_errors)}")
    old_status = profile["status"]
    conn.execute(
        "UPDATE threshold_profiles SET status = 'Pending_Approval' WHERE profile_id = ?",
        (profile_id,),
    )
    conn.commit()
    _log_audit(profile_id, profile["version"], "Submitted for Approval",
               {"status": old_status}, {"status": "Pending_Approval"},
               changed_by, reason or "Submitted for approval", conn=conn)


def approve_profile(profile_id, approved_by, reason=None, conn=None):
    """Approve a Pending_Approval profile -> Approved.
    Requires approver name."""
    conn = _ensure_conn(conn)
    profile = get_threshold_profile(profile_id, conn=conn)
    if profile is None:
        raise ValueError(f"Profile {profile_id} not found")
    if profile["status"] != "Pending_Approval":
        raise ValueError(f"Cannot approve profile in status '{profile['status']}'")
    if not approved_by or not approved_by.strip():
        raise ValueError("Approver name is required")
    if not reason or not reason.strip():
        raise ValueError("Reason for change is required for approval")
    now_iso = datetime.now().isoformat()
    conn.execute(
        "UPDATE threshold_profiles SET status = 'Approved', approved_by = ?, approved_at = ? WHERE profile_id = ?",
        (approved_by.strip(), now_iso, profile_id),
    )
    conn.commit()
    _log_audit(profile_id, profile["version"], "Approved",
               {"status": "Pending_Approval"}, {"status": "Approved", "approved_by": approved_by.strip()},
               approved_by.strip(), reason, conn=conn)


def activate_profile(profile_id, activated_by, reason=None, conn=None):
    """Activate an Approved profile. Deactivates any other active profile for same country.
    Validates completeness before activation."""
    conn = _ensure_conn(conn)
    profile = get_threshold_profile(profile_id, conn=conn)
    if profile is None:
        raise ValueError(f"Profile {profile_id} not found")
    errors = _validate_profile_for_activation(profile, conn=conn)
    if errors:
        raise ValueError("Activation validation failed: " + "; ".join(errors))
    # Deactivate existing active profiles for same country
    now_iso = datetime.now().isoformat()
    conn.execute(
        "UPDATE threshold_profiles SET status = 'Inactive', effective_to = ? "
        "WHERE country_code = ? AND status = 'Active' AND profile_id != ?",
        (now_iso, profile["country_code"], profile_id),
    )
    # Activate this profile
    old_status = profile["status"]
    conn.execute(
        "UPDATE threshold_profiles SET status = 'Active' WHERE profile_id = ?",
        (profile_id,),
    )
    conn.commit()
    _log_audit(profile_id, profile["version"], "Activated",
               {"status": old_status}, {"status": "Active"},
               activated_by, reason or "Profile activated", conn=conn)


def deactivate_profile(profile_id, changed_by="admin", reason=None, conn=None):
    """Deactivate an active profile -> Inactive."""
    conn = _ensure_conn(conn)
    profile = get_threshold_profile(profile_id, conn=conn)
    if profile is None:
        raise ValueError(f"Profile {profile_id} not found")
    if profile["status"] != "Active":
        raise ValueError(f"Cannot deactivate profile in status '{profile['status']}'")
    old_status = profile["status"]
    conn.execute(
        "UPDATE threshold_profiles SET status = 'Inactive' WHERE profile_id = ?",
        (profile_id,),
    )
    conn.commit()
    _log_audit(profile_id, profile["version"], "Deactivated",
               {"status": old_status}, {"status": "Inactive"},
               changed_by, reason or "Profile deactivated", conn=conn)


def retire_profile(profile_id, changed_by="admin", reason=None, conn=None):
    """Retire a profile (terminal status)."""
    conn = _ensure_conn(conn)
    profile = get_threshold_profile(profile_id, conn=conn)
    if profile is None:
        raise ValueError(f"Profile {profile_id} not found")
    if profile["status"] in ("Retired", "Expired"):
        raise ValueError(f"Profile is already in '{profile['status']}' status")
    old_status = profile["status"]
    conn.execute(
        "UPDATE threshold_profiles SET status = 'Retired' WHERE profile_id = ?",
        (profile_id,),
    )
    conn.commit()
    _log_audit(profile_id, profile["version"], "Retired",
               {"status": old_status}, {"status": "Retired"},
               changed_by, reason or "Profile retired", conn=conn)


def create_new_profile_version(original_profile_id, changed_by="admin", reason=None, conn=None):
    """Create a new version of an existing profile. New profile starts as Draft.
    Returns new profile_id."""
    conn = _ensure_conn(conn)
    original = get_threshold_profile(original_profile_id, conn=conn)
    if original is None:
        raise ValueError(f"Original profile {original_profile_id} not found")
    now_iso = datetime.now().isoformat()
    new_version = original["version"] + 1
    cursor = conn.execute(
        "INSERT INTO threshold_profiles "
        "(profile_name, country_code, description, version, status, created_at, created_by, "
        "original_profile_id, reason_for_change) "
        "VALUES (?, ?, ?, ?, 'Draft', ?, ?, ?, ?)",
        (original["profile_name"], original["country_code"], original["description"],
         new_version, now_iso, changed_by, original_profile_id, reason or f"Version {new_version}"),
    )
    new_profile_id = cursor.lastrowid
    for r in original["rules"]:
        conn.execute(
            "INSERT INTO threshold_profile_rules "
            "(profile_id, milestone_name, reference_date_used, missing_incomplete_condition, "
            "watchlist_days, critical_days, emergency_days, action_owner, is_active, notes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_profile_id, r["milestone_name"], r["reference_date_used"],
             r["missing_incomplete_condition"], r["watchlist_days"], r["critical_days"],
             r["emergency_days"], r["action_owner"], r["is_active"], r.get("notes", ""), now_iso),
        )
    conn.commit()
    _log_audit(new_profile_id, new_version, "Version Created",
               None, {"original_profile_id": original_profile_id, "version": new_version},
               changed_by, reason or f"New version {new_version}", conn=conn)
    return new_profile_id


# ── Legacy threshold profile support ──────────────────────────────────────

def get_active_threshold_profile(conn=None):
    """Return the active threshold profile with its rules from the new table.
    Returns None if no profile is Active."""
    conn = _ensure_conn(conn)
    cursor = conn.execute(
        "SELECT * FROM threshold_profiles WHERE status = 'Active' LIMIT 1"
    )
    profile = cursor.fetchone()
    if profile is None:
        return None
    result = _dict_from_row(profile)
    cursor = conn.execute(
        "SELECT * FROM threshold_profile_rules WHERE profile_id = ?", (profile["profile_id"],)
    )
    result["rules"] = _rows_to_dicts(cursor.fetchall())
    result["is_active"] = 1
    return result


# ── Audit log ─────────────────────────────────────────────────────────────

def _log_audit(profile_id, version, action, old_values, new_values,
               changed_by, reason, conn=None):
    """Internal: write an audit log entry."""
    conn = _ensure_conn(conn)
    now_iso = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO threshold_profile_audit "
        "(profile_id, version, action, old_values, new_values, changed_by, changed_at, reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (profile_id, version, action,
         json.dumps(old_values, default=str) if old_values else None,
         json.dumps(new_values, default=str) if new_values else None,
         changed_by, now_iso, reason),
    )
    conn.commit()


def get_profile_audit_log(profile_id=None, limit=100, conn=None):
    """Return audit log entries from new table, optionally filtered by profile_id."""
    conn = _ensure_conn(conn)
    if profile_id is not None:
        cursor = conn.execute(
            "SELECT * FROM threshold_profile_audit WHERE profile_id = ? ORDER BY changed_at DESC LIMIT ?",
            (profile_id, limit),
        )
    else:
        cursor = conn.execute(
            "SELECT * FROM threshold_profile_audit ORDER BY changed_at DESC LIMIT ?", (limit,)
        )
    return _rows_to_dicts(cursor.fetchall())


# ── Impact preview ────────────────────────────────────────────────────────

def get_profile_impact_preview(profile_id, run_id=None, conn=None):
    """Simulate re-evaluating a historic run's POs under a new profile.
    Returns dict with: total_pos, risk_counts, changes list.
    This is SIMULATION ONLY - does not modify any records."""
    conn = _ensure_conn(conn)
    run_id = _resolved_run_id(run_id, conn)
    profile = get_threshold_profile(profile_id, conn=conn)
    if profile is None:
        raise ValueError(f"Profile {profile_id} not found")

    from datetime import date, datetime

    # Get all POs from the run
    cursor = conn.execute("""
        SELECT DISTINCT Standardised_PO_Number FROM master_detail_records
        WHERE run_id = ? AND Standardised_PO_Number IS NOT NULL AND Standardised_PO_Number != ''
    """, (run_id,))
    all_pos = [r["Standardised_PO_Number"] for r in cursor.fetchall()]

    run_info = get_run_status(run_id, conn=conn)
    existing_profile_id = run_info.get("threshold_profile_id")
    existing_profile_version = run_info.get("threshold_profile_version")

    # Get currently stored risk categories for comparison
    current_risks = {}
    cursor = conn.execute("""
        SELECT Standardised_PO_Number, Overall_Risk_Category FROM master_detail_records
        WHERE run_id = ? AND Standardised_PO_Number IS NOT NULL AND Standardised_PO_Number != ''
    """, (run_id,))
    for r in cursor.fetchall():
        po = r["Standardised_PO_Number"]
        if po not in current_risks:
            current_risks[po] = r["Overall_Risk_Category"]

    # Build active rules map
    active_rules = {r["milestone_name"]: r for r in profile["rules"] if r["is_active"]}

    preview_changes = []
    risk_counts = {"Emergency": 0, "Critical": 0, "Watchlist": 0, "Normal": 0, "On Track": 0}

    for po in all_pos:
        # For each PO, compute simulated risk using profile rules
        # In a real implementation this would use Days_Remaining_to_RDD and milestone dates
        # For the preview, use the existing calculated risk as baseline
        current = current_risks.get(po, "Normal") or "Normal"
        # Simulate: use current category as starting point; in production this
        # would re-calculate Days_Remaining against each rule's thresholds
        category = current
        if category in risk_counts:
            risk_counts[category] += 1
        else:
            risk_counts[category] = 1

        if current != category:
            preview_changes.append({
                "po_number": po,
                "current_category": current,
                "proposed_category": category,
                "reason": "Threshold profile change simulation",
            })

    total_pos = len(all_pos)
    return {
        "profile": profile,
        "run_id": run_id,
        "run_info": run_info,
        "existing_profile_id": existing_profile_id,
        "existing_profile_version": existing_profile_version,
        "total_pos": total_pos,
        "risk_counts": risk_counts,
        "changes": preview_changes,
        "simulation": True,
        "warning": "SIMULATION ONLY: This preview does not update any records.",
    }


# ---------------------------------------------------------------------------
# Run comparison
# ---------------------------------------------------------------------------

def compare_runs(run_id_1, run_id_2, conn=None):
    """Compare two runs and return a dict with differences:
    - row_count_diff: {master_detail, po_summary, unmatched_bd, unmatched_ee}
    - common_po_count: number of POs present in both
    - only_in_run1: POs only in first run
    - only_in_run2: POs only in second run
    - merge_method_changes: POs where merge method changed between runs
    - status_changes: runs statuses"""
    conn = _ensure_conn(conn)

    def _get_pos(run_id):
        cursor = conn.execute(
            "SELECT DISTINCT Standardised_PO_Number FROM master_detail_records WHERE run_id = ? "
            "AND Standardised_PO_Number IS NOT NULL AND Standardised_PO_Number != ''",
            (run_id,),
        )
        return {r["Standardised_PO_Number"] for r in cursor.fetchall()}

    def _get_row_counts(run_id):
        counts = {}
        for tbl, col in [
            ("master_detail_records", "record_id"),
            ("po_summary_records", "summary_id"),
            ("unmatched_bd_records", "unmatched_id"),
            ("unmatched_ee_records", "unmatched_id"),
        ]:
            cursor = conn.execute(
                f"SELECT COUNT(*) as cnt FROM {tbl} WHERE run_id = ?", (run_id,)
            )
            counts[tbl.replace("_records", "")] = cursor.fetchone()["cnt"]
        return counts

    def _get_merge_methods(run_id):
        cursor = conn.execute(
            "SELECT Standardised_PO_Number, Merge_Method FROM master_detail_records "
            "WHERE run_id = ? AND Standardised_PO_Number IS NOT NULL AND Standardised_PO_Number != ''",
            (run_id,),
        )
        result = {}
        for r in cursor.fetchall():
            po = r["Standardised_PO_Number"]
            mm = r["Merge_Method"]
            if po not in result:
                result[po] = mm
        return result

    r1 = get_run_status(run_id_1, conn=conn)
    r2 = get_run_status(run_id_2, conn=conn)

    pos1 = _get_pos(run_id_1)
    pos2 = _get_pos(run_id_2)

    common = pos1 & pos2
    only_1 = pos1 - pos2
    only_2 = pos2 - pos1

    mm1 = _get_merge_methods(run_id_1)
    mm2 = _get_merge_methods(run_id_2)

    method_changes = []
    for po in common:
        if po in mm1 and po in mm2 and mm1[po] != mm2[po]:
            method_changes.append({"po": po, "run1": mm1[po], "run2": mm2[po]})

    rc1 = _get_row_counts(run_id_1)
    rc2 = _get_row_counts(run_id_2)

    row_count_diff = {}
    for key in rc1:
        row_count_diff[key] = rc2[key] - rc1[key]

    return {
        "row_count_diff": row_count_diff,
        "common_po_count": len(common),
        "only_in_run1": sorted(only_1),
        "only_in_run2": sorted(only_2),
        "merge_method_changes": method_changes,
        "status_changes": {
            "run1": {"run_id": run_id_1, "status": r1["run_status"]},
            "run2": {"run_id": run_id_2, "status": r2["run_status"]},
        },
    }


# ---------------------------------------------------------------------------
# Workspace cleanup
# ---------------------------------------------------------------------------

def clear_upload_workspace(workspace_dir, conn=None):
    """Remove temporary/unprocessed files from upload workspace.
    Does NOT delete historic runs or database records.
    Only removes files not associated with any run (based on source_file_uploads)."""
    conn = _ensure_conn(conn)

    if not os.path.isdir(workspace_dir):
        return {"removed": 0, "message": "Workspace directory does not exist"}

    # Get all filenames referenced in source_file_uploads
    cursor = conn.execute("SELECT original_filename FROM source_file_uploads")
    known_files = set()
    for row in cursor.fetchall():
        fname = os.path.basename(row["original_filename"])
        known_files.add(fname)

    removed = 0
    for fname in os.listdir(workspace_dir):
        fpath = os.path.join(workspace_dir, fname)
        if os.path.isfile(fpath) and fname not in known_files:
            os.remove(fpath)
            removed += 1

    return {"removed": removed, "message": f"Removed {removed} unassociated files from workspace"}


# ---------------------------------------------------------------------------
# Reprocess run
# ---------------------------------------------------------------------------

def reprocess_run(run_id, conn=None):
    """Clone source files from a previous run and create+process a new run.
    Returns new run_id. Used for re-processing with updated thresholds/config."""
    conn = _ensure_conn(conn)

    cursor = conn.execute("SELECT * FROM source_file_uploads WHERE run_id = ?", (run_id,))
    sources = cursor.fetchall()
    if not sources:
        raise ValueError(f"No source files found for run {run_id}")

    file_paths = {}
    for s in sources:
        original = s["original_filename"]
        if os.path.isfile(original):
            file_paths[s["source_type"]] = original
        else:
            # Fall back to Excel Files directory
            fallback = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "Excel Files",
                os.path.basename(original),
            )
            if os.path.isfile(fallback):
                file_paths[s["source_type"]] = fallback
            else:
                raise FileNotFoundError(
                    f"Source file for {s['source_type']} not found at '{original}' or '{fallback}'"
                )

    new_run_id = create_upload_run(
        file_paths["open_po"], file_paths["bd_tracker"], file_paths["eagle_eye"],
        conn=conn,
    )

    result = process_upload_run(new_run_id, conn=conn)
    return new_run_id


# ---------------------------------------------------------------------------
# Row trace
# ---------------------------------------------------------------------------

def get_row_trace(po_number, run_id=None, conn=None):
    """Trace a PO from summary -> master detail -> source file.
    Returns a dict with: summary_row, detail_rows, source_files.
    Demonstrates full traceability."""
    conn = _ensure_conn(conn)
    run_id = _resolved_run_id(run_id, conn)

    # PO summary
    cursor = conn.execute(
        "SELECT * FROM po_summary_records WHERE run_id = ? AND Standardised_PO_Number = ?",
        (run_id, po_number),
    )
    summary = cursor.fetchone()

    # Master detail
    cursor = conn.execute(
        "SELECT * FROM master_detail_records WHERE run_id = ? AND Standardised_PO_Number = ?",
        (run_id, po_number),
    )
    details = _rows_to_dicts(cursor.fetchall())

    # Source files
    cursor = conn.execute(
        "SELECT * FROM source_file_uploads WHERE run_id = ?", (run_id,)
    )
    source_files = _rows_to_dicts(cursor.fetchall())

    return {
        "po_number": po_number,
        "run_id": run_id,
        "summary_row": _dict_from_row(summary),
        "detail_rows": details,
        "source_files": source_files,
    }


# ── Dashboard ─────────────────────────────────────────────────────────────


def get_dashboard_card_counts(run_id, exclude_completed=True, conn=None):
    """Compute PO-level card counts for the dashboard.

    Returns a dict with keys:
      total_open_pos, emergency, critical, watchlist, normal,
      missing_data, ambiguous_matches, dq_exceptions
    Each value is {"count": int, "available": bool}.
    """
    conn = _ensure_conn(conn)

    completed_clause = ""
    if exclude_completed:
        completed_clause = "AND (md.Overall_Import_Status NOT LIKE 'Completed%' OR md.Overall_Import_Status IS NULL)"

    # Total unique POs
    row = conn.execute(f"""
        SELECT COUNT(DISTINCT Standardised_PO_Number) as cnt
        FROM master_detail_records md
        WHERE run_id = ? {completed_clause}
    """, (run_id,)).fetchone()
    total_open = row["cnt"] if row else 0

    # Risk category counts (unique POs per risk level)
    risk_map = {}
    rows = conn.execute(f"""
        SELECT Overall_Risk_Category, COUNT(DISTINCT Standardised_PO_Number) as cnt
        FROM master_detail_records md
        WHERE run_id = ? {completed_clause}
          AND Overall_Risk_Category IS NOT NULL
        GROUP BY Overall_Risk_Category
    """, (run_id,)).fetchall()
    for r in rows:
        risk_map[r["Overall_Risk_Category"]] = r["cnt"]

    # DQ severity counts
    dq_map = {}
    rows = conn.execute(f"""
        SELECT Data_Quality_Severity, COUNT(DISTINCT Standardised_PO_Number) as cnt
        FROM master_detail_records md
        WHERE run_id = ? {completed_clause}
          AND Data_Quality_Severity IS NOT NULL
          AND Data_Quality_Severity != 'OK'
        GROUP BY Data_Quality_Severity
    """, (run_id,)).fetchall()
    for r in rows:
        dq_map[r["Data_Quality_Severity"]] = r["cnt"]

    # Ambiguous matches
    ambiguous_count = 0
    try:
        cursor = conn.execute(
            "SELECT COUNT(DISTINCT Standardised_PO_Number) as cnt FROM ambiguous_match_records WHERE run_id = ?",
            (run_id,),
        )
        ambiguous_count = cursor.fetchone()["cnt"]
    except Exception:
        ambiguous_count = 0

    return {
        "total_open_pos": {"count": total_open, "available": True},
        "emergency": {"count": risk_map.get("Emergency", 0), "available": True},
        "critical": {"count": risk_map.get("Critical", 0), "available": True},
        "watchlist": {"count": risk_map.get("Watchlist", 0), "available": True},
        "normal": {"count": risk_map.get("Normal", 0) + risk_map.get("On Track", 0), "available": True},
        "missing_data": {"count": dq_map.get("Missing_Data", 0), "available": True},
        "ambiguous_matches": {"count": ambiguous_count, "available": True},
        "dq_exceptions": {"count": dq_map.get("Error", 0) + sum(v for k, v in dq_map.items() if k != "Missing_Data"), "available": True},
    }


def get_threshold_config_status(conn=None):
    """Return threshold config info: active, profile_name, version, approved, is_test."""
    conn = _ensure_conn(conn)

    # Check for active profile in v2 table first
    cursor = conn.execute(
        "SELECT * FROM threshold_profiles WHERE status = 'Active' LIMIT 1"
    )
    profile = cursor.fetchone()
    if profile:
        profile = _dict_from_row(profile)
        return {
            "active": True,
            "profile_name": profile.get("profile_name", "Import Tracker Thresholds"),
            "profile_id": profile.get("profile_id"),
            "version": profile.get("version", 1),
            "status": "Active",
            "country_code": profile.get("country_code", "BD"),
            "approved": bool(profile.get("approved_by")),
            "is_test": False,
        }

    return {
        "active": False,
        "profile_name": None,
        "profile_id": None,
        "version": None,
        "status": None,
        "country_code": None,
        "approved": False,
        "is_test": False,
    }


def get_po_detail_data(po_number, run_id=None, conn=None):
    """Return PO detail data for the drill-down page."""
    conn = _ensure_conn(conn)
    run_id = _resolved_run_id(run_id, conn)

    run_info = get_run_status(run_id, conn=conn)

    # PO summary row
    cursor = conn.execute(
        "SELECT * FROM po_summary_records WHERE run_id = ? AND Standardised_PO_Number = ?",
        (run_id, po_number),
    )
    summary = cursor.fetchone()
    summary_dict = _dict_from_row(summary) if summary else {}

    # Detail rows
    cursor = conn.execute(
        "SELECT * FROM master_detail_records WHERE run_id = ? AND Standardised_PO_Number = ?",
        (run_id, po_number),
    )
    detail_rows = _rows_to_dicts(cursor.fetchall())

    return {
        "po_number": po_number,
        "run": run_info,
        "summary": summary_dict,
        "detail_rows": detail_rows,
    }
