"""Anchor -> browser backend bridge.

Runs the untouched Phase-3 scripts (clean_open_po / clean_bd_tracker /
clean_eagle_eye / clean_merge / rule_engine) entirely in memory on uploaded
file bytes. This module is Pyodide-safe: it imports no ``frozen`` / ``store`` /
``streamlit`` code, uses only the stdlib + the scripts themselves, and returns
a JSON-serialisable context identical in shape to ``pipeline.run``.

Uploads arrive as bytes and are staged to a temp work dir (``tempfile``)
because ``clean_merge.build_control_sheets`` needs real paths for the Cleaning
Log / Freshness sheets. On Pyodide the temp dir lives on the in-browser virtual
FS, so uploaded data never leaves the device.
"""

import datetime
import json
import os
import sys
import tempfile
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _APP_DIR / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import clean_open_po          # noqa: E402
import clean_bd_tracker        # noqa: E402
import clean_eagle_eye         # noqa: E402
import clean_merge             # noqa: E402
import rule_engine             # noqa: E402

CONTROL_SHEETS = ("Exceptions", "Unmatched BD", "Unmatched EE",
                  "Cleaning Log", "Freshness", "Release", "Reconciliation")

# ---------------------------------------------------------------------------
# JSON-safe coercion
# ---------------------------------------------------------------------------

def _iso(obj):
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    return obj


def _jsonable(value):
    """Deep-convert a context value so ``json.dumps`` never fails."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, set):
        return sorted(_jsonable(v) for v in value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


def context_to_json(context: dict) -> str:
    """Render the processing context as an indented JSON string."""
    return json.dumps(_jsonable(context), ensure_ascii=False, indent=2)


def build_master_xlsx(context: dict) -> bytes:
    """Build a one-sheet .xlsx of the master view (mirrors desktop export)."""
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font

    headers = context.get("master_headers") or []
    rows = context.get("master") or []
    wb = Workbook()
    ws = wb.active
    ws.title = "Master"
    ws.append(["" if v is None else v for v in headers])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in rows:
        ws.append(["" if v is None else v for v in r])
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

def _source_file_item(f, refresh):
    """Mirror pipeline._source_files: loaded_at is the file mtime when known."""
    if f["path"] is None:
        return {"key": f["key"], "filename": "", "loaded_at": None}
    if f.get("mtime"):
        loaded = datetime.datetime.fromtimestamp(f["mtime"])
    else:
        loaded = refresh
    return {"key": f["key"], "filename": f["filename"],
            "loaded_at": loaded.isoformat()}


def _write_source(workdir: Path, filename: str, data: bytes,
                  mtime=None) -> Path:
    safe = "".join(c for c in os.path.basename(filename or "upload.xlsx")
                   if c.isalnum() or c in "._- ")
    safe = safe or "upload.xlsx"
    path = workdir / safe
    path.write_bytes(data)
    if mtime:
        try:
            os.utime(path, (mtime, mtime))
        except (OSError, ValueError):
            pass
    return path


def process_uploads(uploads: dict, threshold_bytes=None,
                    threshold_name="", refresh=None,
                    threshold_mtime=None) -> dict:
    """Run the merge pipeline on uploaded file bytes.

    ``uploads`` maps key -> ``{"name": str, "data": bytes, "mtime": float|None}``
    for open/tracker/ee. ``mtime`` (epoch seconds) drives the Freshness sheet
    and ``loaded_at`` — in the browser it comes from ``File.lastModified``.
    ``threshold_bytes``/``threshold_name`` are the optional Country Thresholds
    file. ``refresh`` pins the pipeline timestamp (used by the parity test).
    Returns the same context dict ``pipeline.run`` returns, with dates
    converted to ISO strings and control-sheet sets to sorted lists.
    """
    refresh = refresh or datetime.datetime.now().replace(microsecond=0)

    with tempfile.TemporaryDirectory(prefix="anchor_web_") as tmp:
        workdir = Path(tmp)
        paths = {}
        meta_files = []
        for key in ("open", "tracker", "ee"):
            item = uploads.get(key)
            if item is None:
                raise ValueError("Missing upload: %s" % key)
            name = item.get("name", "") or ("%s.xlsx" % key)
            path = _write_source(workdir, name, item["data"], item.get("mtime"))
            paths[key] = path
            meta_files.append({"key": key, "filename": name, "path": path,
                               "mtime": item.get("mtime")})

        # -- thresholds (optional) -------------------------------------
        thresholds = None
        threshold_src = "built-in defaults"
        thresholds_filename = ""
        if threshold_bytes is not None:
            th_name = threshold_name or "Country Thresholds.xlsx"
            th_path = _write_source(workdir, th_name, threshold_bytes)
            thresholds = rule_engine.load_country_thresholds(th_path)
            if thresholds is not None:
                threshold_src = str(th_path)
                thresholds_filename = th_name
            meta_files.append({"key": "threshold", "filename": th_name,
                               "path": th_path, "mtime": threshold_mtime})
        else:
            meta_files.append({"key": "threshold", "filename": "",
                               "path": None, "mtime": None})

        # -- clean each source -----------------------------------------
        _, openpo_rows, op_info = clean_open_po.clean_to_rows(paths["open"])
        _, bd_rows, bd_info = clean_bd_tracker.clean_to_rows(paths["tracker"])
        _, ee_rows, ee_info = clean_eagle_eye.clean_to_rows(paths["ee"])

        # -- merge + urgency + control sheets --------------------------
        merged, summary = clean_merge.merge_rows(
            openpo_rows, bd_rows, ee_rows,
            op_info, bd_info, ee_info, thresholds=thresholds)

        control = clean_merge.build_control_sheets(
            op_info, bd_info, ee_info,
            openpo_rows, bd_rows, ee_rows,
            [paths["open"], paths["tracker"], paths["ee"]])
        control["Release"] = clean_merge.build_release_sheet(
            summary, refresh, thresholds, threshold_src)
        control["Reconciliation"] = clean_merge.build_reconciliation(
            merged, summary)

        meta = {
            "version": clean_merge.PIPELINE_VERSION,
            "refreshed_at": refresh.isoformat(),
            "threshold_filename": thresholds_filename,
            "threshold_version": thresholds_filename or "built-in defaults",
            "open_po_count": summary["po_count"],
            "source_files": [
                _source_file_item(f, refresh)
                for f in meta_files
            ],
            "master_headers": clean_merge.MERGE_COLUMNS,
            "bd_headers": clean_bd_tracker.OUT_COLUMNS,
            "ee_headers": clean_eagle_eye.OUT_COLUMNS,
            "op_headers": clean_open_po.OUT_COLUMNS,
        }

        context = {
            "master": merged,
            "master_headers": clean_merge.MERGE_COLUMNS,
            "control": control,
            "ee_rows": ee_rows,
            "bd_rows": bd_rows,
            "op_rows": openpo_rows,
            "summary": summary,
            "meta": meta,
            "thresholds": thresholds,
            "is_restored": False,
        }

    return _jsonable(context)


# ---------------------------------------------------------------------------
# CLI parity harness (python web_main.py <open> <tracker> <ee> [threshold])
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    if len(args) < 3:
        print("usage: python web_main.py <open.xlsx> <tracker.xlsx> <ee.xlsx> [thresholds.xlsx]")
        return 1
    uploads = {
        "open": {"name": os.path.basename(args[0]), "data": open(args[0], "rb").read()},
        "tracker": {"name": os.path.basename(args[1]), "data": open(args[1], "rb").read()},
        "ee": {"name": os.path.basename(args[2]), "data": open(args[2], "rb").read()},
    }
    th = None
    th_name = ""
    th_mtime = None
    if len(args) > 3:
        th = open(args[3], "rb").read()
        th_name = os.path.basename(args[3])
        th_mtime = os.path.getmtime(args[3])
    ctx = process_uploads(uploads, th, th_name, threshold_mtime=th_mtime)
    print(context_to_json(ctx))
    print("\n-- summary --", file=sys.stderr)
    for k in ("rows", "po_count", "not_bd", "not_ee", "not_both"):
        print("  %s: %s" % (k, ctx["summary"].get(k)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
