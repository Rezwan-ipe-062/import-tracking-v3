"""
build_poc.py — PyInstaller build script for Import Tracker local POC.

Produces single-file executables in ..\\release\\ImportTracker\\:
  - ImportTracker.exe (the main application)
  - backup_restore.exe (backup/restore companion)

Uses C:\\Temp\\pyi_build\\ for build workdirs to avoid Windows MAX_PATH issues.

Usage:
    python build_poc.py                    # full build
    python build_poc.py --clean            # clean build artifacts first
    python build_poc.py --no-backup        # skip building backup_restore.exe
"""

import os
import sys
import shutil
import subprocess
import argparse

import PyInstaller.__main__

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.normpath(os.path.join(_SCRIPT_DIR, ".."))
_RELEASE_DIR = os.path.join(_PROJECT_ROOT, "release")
_POC_DIR = os.path.join(_RELEASE_DIR, "ImportTracker")
_ICON_PATH = os.path.join(_SCRIPT_DIR, "static", "assets", "icons", "favicon.ico")
_BUILD_TEMP = r"C:\Temp\pyi_build"


def _ensure_icon():
    """Ensure a .ico file exists for the executable."""
    if os.path.isfile(_ICON_PATH):
        return _ICON_PATH
    png_path = os.path.join(os.environ.get("USERPROFILE", "C:\\Users\\Default"),
                            "Downloads", "old-computer.png")
    if os.path.isfile(png_path):
        try:
            from PIL import Image
            img = Image.open(png_path)
            img.save(_ICON_PATH, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
            return _ICON_PATH
        except ImportError:
            pass
    return None


def _build_one(name, script_rel, extra_hidden=None):
    """Build a single .exe with PyInstaller."""
    icon = _ensure_icon()
    script = os.path.join(_SCRIPT_DIR, script_rel)

    # Use absolute paths for --add-data (resolved at build time)
    data_dirs = []
    if name == "ImportTracker":
        data_dirs = [
            f"{os.path.join(_SCRIPT_DIR, 'templates')}{os.pathsep}templates",
            f"{os.path.join(_SCRIPT_DIR, 'static')}{os.pathsep}static",
        ]

    hidden = [
        "pipeline_db", "pipeline_service",
        "clean_open_po", "clean_bd_tracker", "clean_eagle_eye",
        "merge_import_master",
        "waitress", "pandas", "openpyxl",
    ]
    if extra_hidden:
        hidden.extend(extra_hidden)

    args = [
        script,
        "--name", name,
        "--onefile",
        "--console",
        "--distpath", _POC_DIR,
        "--workpath", os.path.join(_BUILD_TEMP, "work_" + name),
        "--specpath", os.path.join(_BUILD_TEMP, "spec_" + name),
        "--noconfirm",
    ]
    for h in hidden:
        args.append("--hidden-import")
        args.append(h)
    for d in data_dirs:
        args.append("--add-data")
        args.append(d)
    if icon:
        args.append("--icon")
        args.append(icon)

    # Ensure build dirs exist
    os.makedirs(os.path.join(_BUILD_TEMP, "work_" + name, name), exist_ok=True)
    os.makedirs(os.path.join(_BUILD_TEMP, "spec_" + name), exist_ok=True)

    print(f"Building {name}.exe ...")
    PyInstaller.__main__.run(args)
    print(f"{name}.exe build complete.")


def _clean():
    if os.path.isdir(_BUILD_TEMP):
        shutil.rmtree(_BUILD_TEMP)
        print(f"Cleaned: {_BUILD_TEMP}")
    for f in os.listdir(_POC_DIR):
        p = os.path.join(_POC_DIR, f)
        if os.path.isfile(p) and f.endswith(".exe"):
            os.unlink(p)
            print(f"Removed: {f}")


def _write_release_readme():
    readme_path = os.path.join(_POC_DIR, "README.txt")
    content = """Import Tracker System -- Local POC
==================================

START THE APPLICATION:
    Double-click ImportTracker.exe
    Your browser will open at http://127.0.0.1:5000

BACKUP YOUR DATA:
    Double-click backup_restore.exe backup
    Or use the Backup & Restore page in the application (System menu)

RESTORE FROM BACKUP:
    Double-click backup_restore.exe restore C:\\\path\\\to\\\backup.zip

STOP THE APPLICATION:
    Press Ctrl+C in the console window, or close the console window.

DATA FOLDER:
    All data is stored in: %LOCALAPPDATA%\\ImportTracker\\
    This includes the database, archives, and generated reports.

TIP: Create a desktop shortcut to ImportTracker.exe for quick access.
"""
    with open(readme_path, "w") as f:
        f.write(content)


def main():
    p = argparse.ArgumentParser(description="Build Import Tracker POC package")
    p.add_argument("--clean", action="store_true", help="Clean build artifacts")
    p.add_argument("--no-backup", action="store_true",
                   help="Skip building backup_restore.exe")
    args = p.parse_args()

    os.makedirs(_POC_DIR, exist_ok=True)

    if args.clean:
        _clean()

    _build_one("ImportTracker", "run_poc.py")

    if not args.no_backup:
        _build_one("backup_restore", "backup_restore.py")

    _write_release_readme()

    print("\n" + "=" * 60)
    print("  Build Complete")
    print("=" * 60)
    print(f"  Application:  {os.path.join(_POC_DIR, 'ImportTracker.exe')}")
    if not args.no_backup:
        print(f"  Backup tool:  {os.path.join(_POC_DIR, 'backup_restore.exe')}")
    print()
    print("  To deploy:")
    print("    1. Copy the entire 'ImportTracker' folder to the target PC")
    print("    2. Create a desktop shortcut to ImportTracker.exe")
    print("    3. Double-click ImportTracker.exe to start")
    print("=" * 60)


if __name__ == "__main__":
    main()
