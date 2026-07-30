"""
test_phase2.py — Automated tests for Phase 2 of the Syngenta Bangladesh Import Tracker pipeline.

Tests all service-layer functions: run lifecycle, duplicate detection,
threshold profiles, record traceability, run comparison, and workspace cleanup.
"""

import json
import os
import shutil
import sys
import tempfile
import traceback
import uuid
from datetime import datetime

import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from pipeline_db import get_connection, init_database, ColumnMapper
from pipeline_service import (
    PIPELINE_VERSION,
    compute_file_hash,
    check_duplicate_upload,
    create_upload_run,
    process_upload_run,
    get_run_status,
    list_upload_runs,
    get_latest_successful_run,
    get_master_detail,
    get_po_summary,
    get_unmatched_records,
    get_ambiguous_matches,
    get_dq_exceptions,
    get_active_threshold_profile,
    create_threshold_profile,
    get_threshold_profile,
    update_profile_rule,
    submit_profile_for_approval,
    approve_profile,
    activate_profile,
    get_profile_audit_log,
    compare_runs,
    clear_upload_workspace,
    get_row_trace,
    validate_upload_files,
)

EXCEL_DIR = os.path.join(_SCRIPT_DIR, "..", "Excel Files")
OPEN_PO_PATH = os.path.join(EXCEL_DIR, "Open PO 23rd July.xlsx")
BD_TRACKER_PATH = os.path.join(EXCEL_DIR, "BD TRACKER - 2026 v1.xlsx")
EAGLE_EYE_PATH = os.path.join(EXCEL_DIR, "Eagle eye.xlsx")

RESULTS = []


def check(condition, message):
    RESULTS.append((condition, message))
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {message}")
    return condition


def make_temp_db():
    tmp = tempfile.mkdtemp(prefix="phase2_test_")
    db_path = os.path.join(tmp, "test_import_tracker.db")
    conn = get_connection(db_path)
    init_database(conn)
    return db_path, conn, tmp


def close_and_clean(conn, tmp_dir):
    """Close connection (with checkpoint) and remove temp directory."""
    try:
        if conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
    except Exception:
        pass
    if tmp_dir:
        for _ in range(3):
            try:
                shutil.rmtree(tmp_dir, ignore_errors=False)
                break
            except Exception:
                import time
                time.sleep(0.5)


def check_source_files():
    missing = []
    for label, path in [("Open PO", OPEN_PO_PATH), ("BD Tracker", BD_TRACKER_PATH),
                         ("Eagle Eye", EAGLE_EYE_PATH)]:
        if not os.path.isfile(path):
            missing.append(f"{label}: {path}")
    return missing


# ======================================================================
# TEST 1: Duplicate detection
# ======================================================================

def test_duplicate_detection():
    print("\n" + "=" * 60)
    print("TEST 1: test_duplicate_detection")
    print("=" * 60)

    db_path, conn, tmp_dir = make_temp_db()

    run_id_1 = create_upload_run(OPEN_PO_PATH, BD_TRACKER_PATH, EAGLE_EYE_PATH, conn=conn)
    check(run_id_1 is not None, f"First run created: {run_id_1}")

    result = process_upload_run(run_id_1, conn=conn)
    check(result["status"] in ("Completed", "Completed_With_Exceptions"),
          f"First run processed with status: {result['status']}")

    hashes = {
        "open_po": compute_file_hash(OPEN_PO_PATH),
        "bd_tracker": compute_file_hash(BD_TRACKER_PATH),
        "eagle_eye": compute_file_hash(EAGLE_EYE_PATH),
    }
    is_dup, dup_id, dup_status = check_duplicate_upload(hashes, conn=conn)
    check(is_dup, f"Duplicate detected (dup_run_id={dup_id}, status={dup_status})")

    run_id_2 = create_upload_run(OPEN_PO_PATH, BD_TRACKER_PATH, EAGLE_EYE_PATH, conn=conn)
    check(run_id_2 is not None, f"Second (duplicate) run created: {run_id_2}")
    check(run_id_2 != run_id_1, "Second run has different run_id from first")

    status2 = get_run_status(run_id_2, conn=conn)
    check(status2["run_status"] == "Completed",
          f"Duplicate run auto-completed: {status2['run_status']}")
    check(status2["rejected_duplicate"] == 1,
          "Duplicate run marked as rejected_duplicate=1")

    runs = list_upload_runs(conn=conn)
    check(len(runs) >= 2, f"Both runs exist in DB ({len(runs)} runs found)")

    close_and_clean(conn, tmp_dir)


# ======================================================================
# TEST 2: Failed run does not affect latest
# ======================================================================

def test_failed_run_does_not_affect_latest():
    print("\n" + "=" * 60)
    print("TEST 2: test_failed_run_does_not_affect_latest")
    print("=" * 60)

    db_path, conn, tmp_dir = make_temp_db()

    run_id_ok = create_upload_run(OPEN_PO_PATH, BD_TRACKER_PATH, EAGLE_EYE_PATH, conn=conn)
    result_ok = process_upload_run(run_id_ok, conn=conn)
    check(result_ok["status"] in ("Completed", "Completed_With_Exceptions"),
          f"Successful run status: {result_ok['status']}")

    md_ok = get_master_detail(run_id=run_id_ok, page=1, page_size=1, conn=conn)
    check(md_ok["total"] > 0, f"Successful run has {md_ok['total']} master detail records")

    try:
        process_upload_run("nonexistent-run-id", conn=conn)
        check(False, "Expected ValueError for nonexistent run")
    except ValueError as e:
        check("not found" in str(e), f"Correct error for nonexistent run: {e}")

    bad_run_id = "bad-run-" + str(uuid.uuid4())[:8]
    now_iso = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO upload_runs (run_id, run_status, created_at, pipeline_version) "
        "VALUES (?, 'Uploaded', ?, ?)",
        (bad_run_id, now_iso, PIPELINE_VERSION),
    )
    conn.execute(
        "INSERT INTO source_file_uploads (run_id, source_type, original_filename, file_hash, upload_timestamp) "
        "VALUES (?, 'open_po', ?, 'badhash', ?)",
        (bad_run_id, os.path.join(tmp_dir, "nonexistent.xlsx"), now_iso),
    )
    conn.execute(
        "INSERT INTO source_file_uploads (run_id, source_type, original_filename, file_hash, upload_timestamp) "
        "VALUES (?, 'bd_tracker', ?, 'badhash', ?)",
        (bad_run_id, os.path.join(tmp_dir, "nonexistent_bd.xlsx"), now_iso),
    )
    conn.execute(
        "INSERT INTO source_file_uploads (run_id, source_type, original_filename, file_hash, upload_timestamp) "
        "VALUES (?, 'eagle_eye', ?, 'badhash', ?)",
        (bad_run_id, os.path.join(tmp_dir, "nonexistent_ee.xlsx"), now_iso),
    )
    conn.commit()

    bad_result = process_upload_run(bad_run_id, conn=conn)
    check(bad_result["status"] == "Failed",
          f"Bad run status is Failed: {bad_result['status']}")
    check(len(bad_result["errors"]) > 0, "Failed run has error messages")

    latest = get_latest_successful_run(conn=conn)
    check(latest is not None, "Latest successful run exists")
    check(latest["run_id"] == run_id_ok,
          f"Latest successful run is still run_id_ok: {latest['run_id']} == {run_id_ok}")

    md_after = get_master_detail(run_id=run_id_ok, page=1, page_size=1, conn=conn)
    check(md_after["total"] == md_ok["total"],
          f"Master detail count unchanged: {md_after['total']} == {md_ok['total']}")

    close_and_clean(conn, tmp_dir)


# ======================================================================
# TEST 3: Clear workspace does not delete history
# ======================================================================

def test_clear_workspace_does_not_delete_history():
    print("\n" + "=" * 60)
    print("TEST 3: test_clear_workspace_does_not_delete_history")
    print("=" * 60)

    # Use separate directories for DB and workspace to avoid conflict
    db_root = tempfile.mkdtemp(prefix="phase2_db_")
    ws_root = tempfile.mkdtemp(prefix="phase2_ws_")
    db_path = os.path.join(db_root, "test_import_tracker.db")
    conn = get_connection(db_path)
    init_database(conn)

    known_file = os.path.join(ws_root, "known_file.txt")
    with open(known_file, "w") as f:
        f.write("known content")
    unknown_file = os.path.join(ws_root, "unknown_file.txt")
    with open(unknown_file, "w") as f:
        f.write("unknown content")

    now_iso = datetime.now().isoformat()
    run_id = "ws-test-run"
    conn.execute(
        "INSERT INTO upload_runs (run_id, run_status, created_at, pipeline_version) "
        "VALUES (?, 'Completed', ?, ?)",
        (run_id, now_iso, PIPELINE_VERSION),
    )
    conn.execute(
        "INSERT INTO source_file_uploads (run_id, source_type, original_filename, file_hash, upload_timestamp) "
        "VALUES (?, 'open_po', ?, 'hash1', ?)",
        (run_id, known_file, now_iso),
    )
    conn.commit()

    result = clear_upload_workspace(ws_root, conn=conn)
    check(result["removed"] == 1, f"One unknown file removed (got {result['removed']})")
    check(os.path.isfile(known_file), "Known file still exists after cleanup")
    check(not os.path.isfile(unknown_file), "Unknown file was removed")

    runs = list_upload_runs(conn=conn)
    check(len(runs) == 1, f"Run records still exist ({len(runs)} runs)")

    status = get_run_status(run_id, conn=conn)
    check(status["run_status"] == "Completed", "Historic run status intact")

    close_and_clean(conn, db_root)
    shutil.rmtree(ws_root, ignore_errors=True)


# ======================================================================
# TEST 4: Record traceability
# ======================================================================

def test_record_traceability():
    print("\n" + "=" * 60)
    print("TEST 4: test_record_traceability")
    print("=" * 60)

    db_path, conn, tmp_dir = make_temp_db()

    run_id = create_upload_run(OPEN_PO_PATH, BD_TRACKER_PATH, EAGLE_EYE_PATH, conn=conn)
    check(run_id is not None, f"Run created: {run_id}")
    process_upload_run(run_id, conn=conn)

    summary = get_po_summary(run_id=run_id, page=1, page_size=5000, exclude_completed=False, conn=conn)
    check(summary["total"] > 0, f"PO summary has {summary['total']} records")
    if summary["total"] == 0:
        close_and_clean(conn, tmp_dir)
        return

    first_po = summary["data"][0]["Standardised_PO_Number"]
    check(first_po is not None and str(first_po).strip() != "",
          f"First PO is non-empty: {first_po}")

    trace = get_row_trace(first_po, run_id=run_id, conn=conn)
    check(trace["summary_row"] is not None, f"Summary row found for PO {first_po}")
    check(len(trace["detail_rows"]) > 0, f"Detail rows found for PO {first_po}")
    check(len(trace["source_files"]) == 3,
          f"Three source files found (got {len(trace['source_files'])})")

    check(trace["summary_row"]["run_id"] == run_id,
          f"Summary run_id matches: {trace['summary_row']['run_id']} == {run_id}")
    for detail in trace["detail_rows"]:
        check(detail["run_id"] == run_id,
              f"Detail record run_id matches: {detail['run_id']} == {run_id}")

    for sf in trace["source_files"]:
        check(sf["run_id"] == run_id, f"Source file run_id matches: {sf['run_id']}")
        check(sf["source_type"] in ("open_po", "bd_tracker", "eagle_eye"),
              f"Source type valid: {sf['source_type']}")

    close_and_clean(conn, tmp_dir)


# ======================================================================
# TEST 5: Run ID on all outputs
# ======================================================================

def test_run_id_on_all_outputs():
    print("\n" + "=" * 60)
    print("TEST 5: test_run_id_on_all_outputs")
    print("=" * 60)

    db_path, conn, tmp_dir = make_temp_db()

    run_id = create_upload_run(OPEN_PO_PATH, BD_TRACKER_PATH, EAGLE_EYE_PATH, conn=conn)
    check(run_id is not None, f"Run created: {run_id}")
    process_upload_run(run_id, conn=conn)

    tables = [
        ("master_detail_records", "record_id"),
        ("po_summary_records", "summary_id"),
        ("unmatched_bd_records", "unmatched_id"),
        ("unmatched_ee_records", "unmatched_id"),
        ("ambiguous_match_records", "ambiguous_id"),
        ("data_quality_exceptions", "exception_id"),
    ]

    any_data = False
    for table, id_col in tables:
        cursor = conn.execute(
            f"SELECT COUNT(*) as cnt FROM {table} WHERE run_id = ?", (run_id,)
        )
        row = cursor.fetchone()
        if row and row["cnt"] > 0:
            any_data = True
            check(True, f"Table {table} has {row['cnt']} records for run_id={run_id}")

        cursor2 = conn.execute(
            f"SELECT COUNT(*) as cnt FROM {table} WHERE run_id != ?", (run_id,)
        )
        row2 = cursor2.fetchone()
        check(row2["cnt"] == 0,
              f"No {table} records with wrong run_id (found {row2['cnt']})")

    check(any_data, "At least one table has data for this run")

    close_and_clean(conn, tmp_dir)


# ======================================================================
# TEST 6: Threshold audit
# ======================================================================

def test_threshold_audit():
    print("\n" + "=" * 60)
    print("TEST 6: test_threshold_audit")
    print("=" * 60)

    db_path, conn, tmp_dir = make_temp_db()

    run_id = create_upload_run(OPEN_PO_PATH, BD_TRACKER_PATH, EAGLE_EYE_PATH, conn=conn)
    process_upload_run(run_id, conn=conn)

    profile_id = create_threshold_profile("Test Profile 1", conn=conn)
    check(profile_id is not None and profile_id > 0, f"Profile created: id={profile_id}")

    active = get_active_threshold_profile(conn=conn)
    check(active is None, "New profile is not active (no active profile found)")

    profile = get_threshold_profile(profile_id, conn=conn)
    target_rule = next((r for r in profile["rules"] if r["milestone_name"] == "BD Tracker ETA"), None)
    if target_rule:
        update_profile_rule(
            target_rule["rule_id"],
            {"watchlist_days": 90, "critical_days": 60, "emergency_days": 30},
            changed_by="test_user",
            reason="Test rule update",
            conn=conn,
        )

    submit_profile_for_approval(profile_id, changed_by="test_approver", reason="Ready for review", conn=conn)
    approve_profile(profile_id, "test_approver", reason="Approved for testing", conn=conn)
    activate_profile(profile_id, "test_approver", reason="Activating for testing", conn=conn)

    active = get_active_threshold_profile(conn=conn)
    check(active is not None, "Active profile found after activation")
    check(active["profile_id"] == profile_id,
          f"Active profile id matches: {active['profile_id']} == {profile_id}")
    check(active["approved_by"] == "test_approver",
          f"Approved by: {active['approved_by']}")
    check(len(active["rules"]) > 0,
          f"Active profile has {len(active['rules'])} rules")

    audit_entries = get_profile_audit_log(profile_id=profile_id, conn=conn)
    check(len(audit_entries) >= 2,
          f"Audit log has {len(audit_entries)} entries (expected >= 2)")

    actions = [e["action"] for e in audit_entries]
    check("Rule Edited" in actions, "Audit log has 'Rule Edited' entry")
    check("Activated" in actions, "Audit log has 'Activated' entry")

    md = get_master_detail(run_id=run_id, page=1, page_size=5000, conn=conn)
    if md["total"] > 0:
        risk_statuses = set(str(r.get("Risk_Calculation_Status", ""))
                            for r in md["data"])
        check("Rules not configured" in risk_statuses,
              f"Previous run has 'Rules not configured': {risk_statuses}")

    close_and_clean(conn, tmp_dir)


# ======================================================================
# TEST 7: Rerun creates new run
# ======================================================================

def test_rerun_creates_new_run():
    print("\n" + "=" * 60)
    print("TEST 7: test_rerun_creates_new_run")
    print("=" * 60)

    db_path, conn, tmp_dir = make_temp_db()

    # Run 1
    run_id_1 = create_upload_run(OPEN_PO_PATH, BD_TRACKER_PATH, EAGLE_EYE_PATH, conn=conn)
    result1 = process_upload_run(run_id_1, conn=conn)
    check(result1["status"] in ("Completed", "Completed_With_Exceptions"),
          f"First run status: {result1['status']}")

    # Run 2 — bypass duplicate check by directly creating the run
    run_id_2 = str(uuid.uuid4())
    now_iso = datetime.now().isoformat()
    hashes = {
        "open_po": compute_file_hash(OPEN_PO_PATH),
        "bd_tracker": compute_file_hash(BD_TRACKER_PATH),
        "eagle_eye": compute_file_hash(EAGLE_EYE_PATH),
    }
    conn.execute(
        "INSERT INTO upload_runs (run_id, run_status, created_at, pipeline_version) "
        "VALUES (?, 'Uploaded', ?, ?)",
        (run_id_2, now_iso, PIPELINE_VERSION),
    )
    for st, p, h in [
        ("open_po", OPEN_PO_PATH, hashes["open_po"]),
        ("bd_tracker", BD_TRACKER_PATH, hashes["bd_tracker"]),
        ("eagle_eye", EAGLE_EYE_PATH, hashes["eagle_eye"]),
    ]:
        conn.execute(
            "INSERT INTO source_file_uploads (run_id, source_type, original_filename, file_hash, upload_timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id_2, st, p, h, now_iso),
        )
    conn.commit()

    result2 = process_upload_run(run_id_2, conn=conn)
    check(result2["status"] in ("Completed", "Completed_With_Exceptions"),
          f"Second run status: {result2['status']}")

    check(run_id_2 != run_id_1, "Two runs have different run_ids")

    runs = list_upload_runs(conn=conn)
    run_ids = [r["run_id"] for r in runs]
    check(run_id_1 in run_ids, "First run exists in DB")
    check(run_id_2 in run_ids, "Second run exists in DB")

    latest = get_latest_successful_run(conn=conn)
    check(latest is not None, "Latest successful run exists")
    check(latest["run_id"] == run_id_2,
          f"Latest successful run is the second one: {latest['run_id']} == {run_id_2}")

    close_and_clean(conn, tmp_dir)


# ======================================================================
# TEST 8: Compare runs
# ======================================================================

def test_compare_runs():
    print("\n" + "=" * 60)
    print("TEST 8: test_compare_runs")
    print("=" * 60)

    db_path, conn, tmp_dir = make_temp_db()

    # Run 1
    run_id_1 = create_upload_run(OPEN_PO_PATH, BD_TRACKER_PATH, EAGLE_EYE_PATH, conn=conn)
    result1 = process_upload_run(run_id_1, conn=conn)
    check(result1["status"] in ("Completed", "Completed_With_Exceptions"),
          f"First run status: {result1['status']}")

    # Run 2 — bypass duplicate check
    run_id_2 = str(uuid.uuid4())
    now_iso = datetime.now().isoformat()
    hashes = {
        "open_po": compute_file_hash(OPEN_PO_PATH),
        "bd_tracker": compute_file_hash(BD_TRACKER_PATH),
        "eagle_eye": compute_file_hash(EAGLE_EYE_PATH),
    }
    conn.execute(
        "INSERT INTO upload_runs (run_id, run_status, created_at, pipeline_version) "
        "VALUES (?, 'Uploaded', ?, ?)",
        (run_id_2, now_iso, PIPELINE_VERSION),
    )
    for st, p, h in [
        ("open_po", OPEN_PO_PATH, hashes["open_po"]),
        ("bd_tracker", BD_TRACKER_PATH, hashes["bd_tracker"]),
        ("eagle_eye", EAGLE_EYE_PATH, hashes["eagle_eye"]),
    ]:
        conn.execute(
            "INSERT INTO source_file_uploads (run_id, source_type, original_filename, file_hash, upload_timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id_2, st, p, h, now_iso),
        )
    conn.commit()

    result2 = process_upload_run(run_id_2, conn=conn)
    check(result2["status"] in ("Completed", "Completed_With_Exceptions"),
          f"Second run status: {result2['status']}")

    comp = compare_runs(run_id_1, run_id_2, conn=conn)

    check("row_count_diff" in comp, "Comparison has row_count_diff")
    check("common_po_count" in comp, "Comparison has common_po_count")
    check("only_in_run1" in comp, "Comparison has only_in_run1")
    check("only_in_run2" in comp, "Comparison has only_in_run2")
    check("merge_method_changes" in comp, "Comparison has merge_method_changes")
    check("status_changes" in comp, "Comparison has status_changes")

    check(comp["common_po_count"] >= 0,
          f"Common PO count: {comp['common_po_count']}")

    s1 = comp["status_changes"]["run1"]["status"]
    s2 = comp["status_changes"]["run2"]["status"]
    check(s1 in ("Completed", "Completed_With_Exceptions"),
          f"Run1 status valid: {s1}")
    check(s2 in ("Completed", "Completed_With_Exceptions"),
          f"Run2 status valid: {s2}")

    close_and_clean(conn, tmp_dir)


# ======================================================================
# Additional coverage: validation
# ======================================================================

def test_validate_upload_files():
    print("\n" + "=" * 60)
    print("TEST 9: test_validate_upload_files")
    print("=" * 60)

    db_path, conn, tmp_dir = make_temp_db()

    # Valid files
    v = validate_upload_files(OPEN_PO_PATH, BD_TRACKER_PATH, EAGLE_EYE_PATH, conn=conn)
    check(v["valid"], "All valid files pass validation")
    check(len(v["errors"]) == 0, "No validation errors for valid files")

    # Missing file
    bad = os.path.join(tmp_dir, "nonexistent.xlsx")
    v2 = validate_upload_files(bad, BD_TRACKER_PATH, EAGLE_EYE_PATH, conn=conn)
    check(not v2["valid"], "Missing file fails validation")
    check(len(v2["errors"]) > 0, "Errors reported for missing file")

    close_and_clean(conn, tmp_dir)


# ======================================================================
# Additional coverage: compute_file_hash
# ======================================================================

def test_compute_file_hash():
    print("\n" + "=" * 60)
    print("TEST 10: test_compute_file_hash")
    print("=" * 60)

    h = compute_file_hash(OPEN_PO_PATH)
    check(len(h) == 64, f"SHA-256 hash is 64 hex chars (got {len(h)})")

    h2 = compute_file_hash(OPEN_PO_PATH)
    check(h == h2, "Same file produces same hash")

    conn, tmp_dir = None, None
    print(f"  Hash: {h[:16]}...")


# ======================================================================
# Main
# ======================================================================

def main():
    global RESULTS
    RESULTS = []

    missing = check_source_files()
    if missing:
        print("ERROR: Missing source files:")
        for m in missing:
            print(f"  {m}")
        print(f"\nPlace the Excel files in: {EXCEL_DIR}")
        sys.exit(1)

    print("=" * 60)
    print("PHASE 2 IMPORT TRACKER PIPELINE TESTS")
    print(f"Python: {sys.version.split()[0]}")
    print(f"pandas: {pd.__version__}")
    print(f"Pipeline version: {PIPELINE_VERSION}")
    print(f"Source files:")
    print(f"  Open PO:   {OPEN_PO_PATH}")
    print(f"  BD Tracker: {BD_TRACKER_PATH}")
    print(f"  Eagle Eye:  {EAGLE_EYE_PATH}")
    print("=" * 60)

    tests = [
        ("test_compute_file_hash", test_compute_file_hash),
        ("test_validate_upload_files", test_validate_upload_files),
        ("test_duplicate_detection", test_duplicate_detection),
        ("test_failed_run_does_not_affect_latest", test_failed_run_does_not_affect_latest),
        ("test_clear_workspace_does_not_delete_history", test_clear_workspace_does_not_delete_history),
        ("test_record_traceability", test_record_traceability),
        ("test_run_id_on_all_outputs", test_run_id_on_all_outputs),
        ("test_threshold_audit", test_threshold_audit),
        ("test_rerun_creates_new_run", test_rerun_creates_new_run),
        ("test_compare_runs", test_compare_runs),
    ]

    for name, func in tests:
        try:
            func()
        except Exception as e:
            RESULTS.append((False, f"{name} raised exception: {e}"))
            print(f"\n  [FAIL] {name} raised exception: {e}")
            traceback.print_exc()

    passed = sum(1 for c, _ in RESULTS if c)
    failed = sum(1 for c, _ in RESULTS if not c)

    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"  Total assertions: {len(RESULTS)}")
    print(f"  Passed:           {passed}")
    print(f"  Failed:           {failed}")
    if len(RESULTS) > 0:
        print(f"  Pass rate:        {passed / len(RESULTS) * 100:.1f}%")
    print("=" * 60)

    if failed > 0:
        print("\nFailed assertions:")
        for c, m in RESULTS:
            if not c:
                print(f"  [FAIL] {m}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
