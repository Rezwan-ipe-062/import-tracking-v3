"""
run_poc.py — Entry point for the packaged Import Tracker local POC.

Launches the Flask application with Waitress WSGI server, bound to 127.0.0.1
only. Manages data in %LOCALAPPDATA%\\ImportTracker\\.

Usage:
    python run_poc.py                    # normal launch
    python run_poc.py --port 8080        # custom port
    python run_poc.py --no-browser       # skip auto-open
"""

import os
import sys
import socket
import webbrowser
import argparse
import ctypes
import logging
import threading
from datetime import datetime

# ---- Set up controlled data directories BEFORE importing the app ----

_DATA_ROOT = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "ImportTracker",
)

os.environ.setdefault("IMPORT_TRACKER_DATA_DIR", os.path.join(_DATA_ROOT, "data"))
os.environ.setdefault("IMPORT_TRACKER_ARCHIVE", os.path.join(_DATA_ROOT, "archive"))
os.environ.setdefault("IMPORT_TRACKER_REPORTS", os.path.join(_DATA_ROOT, "reports"))
os.environ.setdefault("IMPORT_TRACKER_LOGS", os.path.join(_DATA_ROOT, "logs"))
os.environ.setdefault("IMPORT_TRACKER_TEMP", os.path.join(_DATA_ROOT, "temp"))

for _dir_key in ("IMPORT_TRACKER_DATA_DIR", "IMPORT_TRACKER_ARCHIVE",
                 "IMPORT_TRACKER_REPORTS", "IMPORT_TRACKER_LOGS",
                 "IMPORT_TRACKER_TEMP"):
    os.makedirs(os.environ[_dir_key], exist_ok=True)

# If no admin secret is explicitly set, generate one for this session
if "IMPORT_TRACKER_ADMIN_SECRET" not in os.environ:
    import uuid
    os.environ["IMPORT_TRACKER_ADMIN_SECRET"] = uuid.uuid4().hex[:16]

_ADMIN_TOKEN = os.environ["IMPORT_TRACKER_ADMIN_SECRET"]

# ---- Now import the app ----

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from web_app import app, ADMIN_SECRET
import pipeline_db
import pipeline_service

# ---- Configure ----

app.config["DEBUG"] = False
app.config["TESTING"] = False
app.config["ENV"] = "production"

SERVER_STARTED = threading.Event()


def _parse_args():
    p = argparse.ArgumentParser(description="Import Tracker Local POC")
    p.add_argument("--port", type=int, default=5000, help="Port to bind (default: 5000)")
    p.add_argument("--no-browser", action="store_true", help="Do not auto-open browser")
    return p.parse_args()


def _find_available_port(start):
    port = start
    while port < start + 100:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
        port += 1
    return None


# ---- Single-instance guard ----

def _acquire_single_instance():
    """Use a Windows named mutex to prevent duplicate instances."""
    mutex_name = "ImportTracker_POC_Local"
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, mutex_name)
    if mutex and kernel32.GetLastError() == 183:
        # ERROR_ALREADY_EXISTS
        print("Import Tracker is already running. Check your system tray or taskbar.")
        print("If the previous window is not visible, restart your computer or")
        print("press Ctrl+C in the existing console window to stop it.")
        return False
    return True


# ---- Waitress runner ----

def _run_waitress(host, port):
    from waitress import serve
    serve(app, host=host, port=port, threads=4, channel_timeout=1200)


# ---- Clean shutdown helpers ----

_server_port = None
_server_host = None
_running = True


def _signal_handler(sig, frame):
    global _running
    print("\nShutting down Import Tracker...")
    _running = False
    os._exit(0)


# ---- Logging ----

_log_file = os.path.join(os.environ["IMPORT_TRACKER_LOGS"], "import_tracker.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

# ---- Main ----

def main():
    global _server_port, _server_host

    args = _parse_args()

    if not _acquire_single_instance():
        return 1

    _server_host = "127.0.0.1"
    _server_port = _find_available_port(args.port)
    if _server_port is None:
        print("ERROR: No available port found (tried ports {}-{}).".format(
            args.port, args.port + 99))
        return 1

    # Print startup banner
    print("=" * 60)
    print("  Import Tracker System — Local POC")
    print("=" * 60)
    print(f"  Pipeline version:  {pipeline_service.PIPELINE_VERSION}")
    print(f"  Data folder:       {_DATA_ROOT}")
    print(f"  Log file:          {_log_file}")
    print()
    print(f"  Open in browser:   http://{_server_host}:{_server_port}")
    print(f"  Admin token:       {_ADMIN_TOKEN}")
    print(f"  Admin login:       http://{_server_host}:{_server_port}/admin/login")
    print()
    print("  Press Ctrl+C to stop the server.")
    print("=" * 60)

    # Register signal handlers for clean shutdown
    import signal
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Auto-open browser
    if not args.no_browser:
        def _open_browser():
            import time
            time.sleep(1.5)
            webbrowser.open(f"http://{_server_host}:{_server_port}")
        threading.Thread(target=_open_browser, daemon=True).start()

    # Start Waitress (blocking)
    _run_waitress(_server_host, _server_port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
