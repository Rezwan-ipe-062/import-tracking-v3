"""Parity harness: web_main.process_uploads must equal pipeline.run output.

Runs both engines on the same staged upload files and asserts the JSON-safe
contexts match field-for-field (dates normalised to ISO strings). This guards
the browser build against silent drift from the desktop/Streamlit app.

Run:  python -m tests.parity  <open.xlsx> <tracker.xlsx> <ee.xlsx> [thresholds.xlsx]
"""

import datetime
import json
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import pipeline            # noqa: E402
import web_main            # noqa: E402


def _iso(v):
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    return v


def _normalise(obj):
    if isinstance(obj, dict):
        return {k: _normalise(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_normalise(v) for v in obj]
    if isinstance(obj, set):
        return sorted(_normalise(v) for v in obj)
    return _iso(obj)


# Columns whose values are stamped with datetime.now() inside the Phase-3
# scripts, so they legitimately differ by a second between two runs.
_TIME_COLS = {
    "Cleaning Log": 6,   # "Cleaned At"
    "Exceptions": 6,     # "Refresh Date"
    "Freshness": 3,      # "Master Refresh"
}


def _masked_control(control):
    out = {}
    for name, (headers, rows, _) in (control or {}).items():
        cols = _TIME_COLS.get(name)
        new_rows = []
        for r in rows:
            r = list(r)
            if cols is not None and cols < len(r):
                r[cols] = "<time>"
            new_rows.append(r)
        # Release sheet: the Value column carries the run refresh timestamp and
        # the staged temp path (Thresholds source) — both run-local.
        if name == "Release":
            new_rows = [r[:] for r in new_rows]
            for r in new_rows:
                if r and len(r) > 1 and r[0] == "Master Refresh":
                    r[1] = "<time>"
                if r and len(r) > 1 and r[0] == "Thresholds source":
                    r[1] = "<path>"
        out[name] = (headers, new_rows, [])
    return out


def _load_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def run_parity(open_path, tracker_path, ee_path, threshold_path=None):
    # Run the reference pipeline first so we can pin web_main to the same
    # refresh timestamp and file mtimes (the Freshness sheet and meta depend
    # on them).
    src = {
        "open": open_path,
        "tracker": tracker_path,
        "ee": ee_path,
    }
    pipe_ctx = pipeline.run(src, threshold_path)
    refresh = pipe_ctx["meta"]["refreshed_at"]
    if isinstance(refresh, str):
        refresh = datetime.datetime.fromisoformat(refresh)

    def _item(p):
        return {"name": p.name, "data": _load_bytes(p), "mtime": p.stat().st_mtime}

    uploads = {
        "open": _item(open_path),
        "tracker": _item(tracker_path),
        "ee": _item(ee_path),
    }
    th = None
    th_name = ""
    th_mtime = None
    if threshold_path:
        th = _load_bytes(threshold_path)
        th_name = threshold_path.name
        th_mtime = threshold_path.stat().st_mtime

    web_ctx = web_main.process_uploads(uploads, th, th_name, refresh=refresh,
                                       threshold_mtime=th_mtime)

    # Normalise both to the same JSON-safe form.
    web_norm = _normalise(web_ctx)
    pipe_norm = _normalise(pipe_ctx)
    web_norm["control"] = _masked_control(web_norm.get("control"))
    pipe_norm["control"] = _masked_control(pipe_norm.get("control"))
    # meta.refreshed_at is pinned; loaded_at derives from file mtime — equal.
    diffs = []
    for key in ("master", "master_headers", "control", "ee_rows", "bd_rows",
                "op_rows", "summary", "meta"):
        if web_norm.get(key) != pipe_norm.get(key):
            diffs.append(key)

    # thresholds: pipeline returns dict-of-dicts; web returns same after json.
    if web_norm.get("thresholds") != pipe_norm.get("thresholds"):
        diffs.append("thresholds")

    return web_ctx, pipe_ctx, diffs


def main():
    args = sys.argv[1:]
    if len(args) < 3:
        print("usage: python -m tests.parity <open.xlsx> <tracker.xlsx> <ee.xlsx> [thresholds.xlsx]")
        return 1
    paths = [Path(a) for a in args]
    th = paths[3] if len(paths) > 3 else None

    web_ctx, pipe_ctx, diffs = run_parity(paths[0], paths[1], paths[2], th)

    print("web     master rows: %d" % len(web_ctx["master"]))
    print("pipeline master rows: %d" % len(pipe_ctx["master"]))
    print("web     summary: rows=%s pos=%s" % (
        web_ctx["summary"].get("rows"), web_ctx["summary"].get("po_count")))
    print("pipeline summary: rows=%s pos=%s" % (
        pipe_ctx["summary"].get("rows"), pipe_ctx["summary"].get("po_count")))

    if diffs:
        print("MISMATCH in: %s" % ", ".join(diffs))
        return 1
    print("PARITY OK — all fields match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
