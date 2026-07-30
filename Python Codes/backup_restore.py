"""
backup_restore.py — CLI companion for Import Tracker backup and restore.

Usage:
    # Create a backup:
    python backup_restore.py backup [--output C:\\path\\to\\backup.zip]

    # Restore from a backup:
    python backup_restore.py restore C:\\path\\to\\backup.zip [--force]

The backup includes: SQLite database (plus WAL/shm), archived source files,
generated reports, and threshold configuration. Logs and temp files are excluded.
"""

import argparse
import os
import sys
import zipfile
from datetime import datetime

# Resolve the data directory the same way run_poc.py does
_DATA_ROOT = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "ImportTracker",
)

_DB_PATH = os.path.join(_DATA_ROOT, "data", "import_tracker.db")
_ARCHIVE_DIR = os.path.join(_DATA_ROOT, "archive")
_REPORTS_DIR = os.path.join(_DATA_ROOT, "reports")
_LOGS_DIR = os.path.join(_DATA_ROOT, "logs")
_TEMP_DIR = os.path.join(_DATA_ROOT, "temp")

_EXCLUDED_DIRS = {os.path.normcase(_LOGS_DIR), os.path.normcase(_TEMP_DIR)}


def _find_data_root():
    """Find the ImportTracker data root from environment or default."""
    data_dir_env = os.environ.get("IMPORT_TRACKER_DATA_DIR")
    if data_dir_env:
        return os.path.dirname(data_dir_env)
    archive_env = os.environ.get("IMPORT_TRACKER_ARCHIVE")
    if archive_env:
        return os.path.dirname(archive_env)
    return _DATA_ROOT


def cmd_backup(args):
    data_root = _find_data_root()
    db_path = os.path.join(data_root, "data", "import_tracker.db")
    archive_dir = os.path.join(data_root, "archive")
    reports_dir = os.path.join(data_root, "reports")

    # Determine output path
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.output:
        out_path = args.output
        if not out_path.endswith(".zip"):
            out_path += ".zip"
    else:
        out_path = os.path.join(data_root, "reports", f"import_tracker_backup_{ts}.zip")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    print(f"Creating backup: {out_path}")
    added = []

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Database
        if os.path.isfile(db_path):
            zf.write(db_path, "data/import_tracker.db")
            added.append(f"  data/import_tracker.db ({os.path.getsize(db_path)} bytes)")
        for wal_ext in ("-wal", "-shm"):
            wal_path = db_path + wal_ext
            if os.path.isfile(wal_path):
                zf.write(wal_path, f"data/import_tracker.db{wal_ext}")
                added.append(f"  data/import_tracker.db{wal_ext}")

        # Archives
        if os.path.isdir(archive_dir):
            for entry in sorted(os.listdir(archive_dir)):
                entry_path = os.path.join(archive_dir, entry)
                if os.path.isdir(entry_path):
                    for root, dirs, files in os.walk(entry_path):
                        for f in sorted(files):
                            fp = os.path.join(root, f)
                            arcname = os.path.relpath(fp, os.path.dirname(archive_dir))
                            zf.write(fp, arcname)
                            added.append(f"  {arcname}")

        # Reports
        if os.path.isdir(reports_dir):
            for f in sorted(os.listdir(reports_dir)):
                fp = os.path.join(reports_dir, f)
                if os.path.isfile(fp) and not f.endswith(".zip"):
                    arcname = os.path.relpath(fp, os.path.dirname(archive_dir))
                    zf.write(fp, arcname)
                    added.append(f"  {arcname}")

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(f"Backup created: {out_path} ({size_mb:.1f} MB)")
    print("Contents:")
    for a in added:
        print(a)
    print("\nBackup complete. Copy this file to your company-approved shared drive or")
    print("OneDrive for Business folder for safekeeping.")
    return 0


def cmd_restore(args):
    if not os.path.isfile(args.backup_zip):
        print(f"ERROR: Backup file not found: {args.backup_zip}")
        return 1

    if not args.force:
        print("WARNING: This will REPLACE all current data (database, archives, reports).")
        print(f"  Data folder: {_find_data_root()}")
        ans = input("Are you sure you want to continue? (yes/no): ").strip().lower()
        if ans != "yes":
            print("Restore cancelled.")
            return 1

    data_root = _find_data_root()
    print(f"Restoring from: {args.backup_zip}")
    print(f"  Target: {data_root}")

    with zipfile.ZipFile(args.backup_zip, "r") as zf:
        for member in zf.namelist():
            # Resolve target path safely
            dest = os.path.normpath(os.path.join(data_root, member))
            if not dest.startswith(os.path.normpath(data_root)):
                print(f"  SKIPPED (path traversal): {member}")
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            zf.extract(member, data_root)
            print(f"  Extracted: {member}")

    print("\nRestore complete. Current data has been replaced.")
    print("Restart the Import Tracker application for changes to take effect.")
    return 0


def main():
    p = argparse.ArgumentParser(description="Import Tracker Backup & Restore")
    sub = p.add_subparsers(dest="command", required=True)

    # backup
    bp = sub.add_parser("backup", help="Create a backup ZIP")
    bp.add_argument("--output", "-o", help="Output path for backup ZIP")

    # restore
    rp = sub.add_parser("restore", help="Restore from a backup ZIP")
    rp.add_argument("backup_zip", help="Path to the backup ZIP file")
    rp.add_argument("--force", "-f", action="store_true",
                    help="Skip confirmation prompt")

    args = p.parse_args()
    if args.command == "backup":
        return cmd_backup(args)
    elif args.command == "restore":
        return cmd_restore(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
