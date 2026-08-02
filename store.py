"""Local persistence for Anchor.

Stores processed outputs and manager state under ``.anchor/`` (gitignored).
We retain processed views, notes, filters and page selection, but never raw
uploaded Excel bytes (uploads live in a transient temp dir and are cleared
after processing). All artefacts are plain JSON + CSV.
"""

import csv
import json
import os
import tempfile
import time
import uuid
from datetime import datetime, date
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
LOCAL_DIR = APP_DIR / ".anchor"
DATA_DIR = LOCAL_DIR / "data"

# Staging root for transient uploads. Each run gets its own UUID subdirectory so
# one run/refresh can never delete another run's in-flight files.
WORK_ROOT = Path(tempfile.gettempdir()) / "anchor_work"
WORK_DIR = WORK_ROOT / uuid.uuid4().hex

# Stale staging subdirs older than this (seconds) may be swept at stage time.
_STALE_SECONDS = 86400  # 24h


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _json_iso(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


def save_json(dst, payload):
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def load_json(src, default=None):
    src = Path(src)
    if not src.exists():
        return default
    try:
        with open(src, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Transient upload staging
# ---------------------------------------------------------------------------

def work_path(original_name: str) -> Path:
    _sweep_stale()
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in os.path.basename(original_name or "")
                   if c.isalnum() or c in "._- ")
    safe = safe or "upload.xlsx"
    return WORK_DIR / safe


def _sweep_stale():
    """Remove staging subdirs from previous (finished) runs."""
    if not WORK_ROOT.exists():
        return
    now = time.time()
    for child in WORK_ROOT.iterdir():
        if not child.is_dir():
            # stray file from an earlier shared-dir version
            try:
                child.unlink()
            except OSError:
                pass
            continue
        try:
            age = now - child.stat().st_mtime
        except OSError:
            continue
        if age > _STALE_SECONDS:
            _rmtree(child)


def _rmtree(d: Path):
    for p in d.rglob("*"):
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            pass
    for p in sorted(d.rglob("*"), reverse=True):
        try:
            if p.is_dir():
                p.rmdir()
        except OSError:
            pass
    try:
        d.rmdir()
    except OSError:
        pass


def clean_work():
    """Remove this run's staging subdir after processing."""
    if WORK_DIR.exists():
        _rmtree(WORK_DIR)


def _slug(name):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)


# ---------------------------------------------------------------------------
# Processed view
# ---------------------------------------------------------------------------

def _write_rows(name, headers, rows):
    dst = DATA_DIR / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        if headers:
            w.writerow(headers)
        for r in rows or ():
            w.writerow(r)


def save_view(context: dict):
    """Persist the processed view produced by pipeline.run()."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    meta = context.get("meta", {})
    payload = {
        "version": meta.get("version", ""),
        "refreshed_at": _json_iso(meta.get("refreshed_at")),
        "threshold_filename": meta.get("threshold_filename", ""),
        "threshold_version": meta.get("threshold_version", ""),
        "open_po_count": meta.get("open_po_count", 0),
        "source_files": meta.get("source_files", []),
        "is_restored": False,
    }
    save_json(LOCAL_DIR / "view_meta.json", payload)

    _write_rows("master.csv", meta.get("master_headers"), context.get("master"))
    _write_rows("bd.csv", meta.get("bd_headers"), context.get("bd_rows"))
    _write_rows("ee.csv", meta.get("ee_headers"), context.get("ee_rows"))
    _write_rows("op.csv", meta.get("op_headers"), context.get("op_rows"))
    for sheet_name in ("Exceptions", "Unmatched BD", "Unmatched EE",
                       "Cleaning Log", "Freshness", "Release", "Reconciliation"):
        item = context.get("control", {}).get(sheet_name)
        if item:
            headers, rows, _ = item
            _write_rows(_slug(sheet_name) + ".csv", headers, rows)
    clean_work()  # never retain uploaded bytes after a run


def read_saved_csv(name):
    dst = DATA_DIR / name
    if not dst.exists():
        return None
    with open(dst, "r", encoding="utf-8") as fh:
        return list(csv.reader(fh))


def load_view_meta():
    return load_json(LOCAL_DIR / "view_meta.json", {})


def has_view() -> bool:
    return (LOCAL_DIR / "view_meta.json").exists()


# ---------------------------------------------------------------------------
# Manager notes (per PO) - device-local only
# ---------------------------------------------------------------------------

NOTES_FILE = LOCAL_DIR / "notes.json"


def load_notes():
    return load_json(NOTES_FILE, {})


def save_notes(notes: dict):
    save_json(NOTES_FILE, notes)


def notes_count():
    return len(load_notes())


# ---------------------------------------------------------------------------
# UI prefs (last page + filters) - survive restarts
# ---------------------------------------------------------------------------

P_PREFS = LOCAL_DIR / "prefs.json"


def load_prefs():
    return load_json(P_PREFS, {})


def save_prefs(prefs: dict):
    save_json(P_PREFS, prefs)


# ---------------------------------------------------------------------------
# Deep clean
# ---------------------------------------------------------------------------

def clear_all(confirmed: bool) -> bool:
    """Remove every locally stored Anchor artefact. Returns True when cleared."""
    if not confirmed:
        return False
    for p in LOCAL_DIR.glob("*"):
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass
    if DATA_DIR.exists():
        for p in DATA_DIR.rglob("*"):
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
    clean_work()
    return True


# ---------------------------------------------------------------------------
# Exports destination
# ---------------------------------------------------------------------------

def export_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR