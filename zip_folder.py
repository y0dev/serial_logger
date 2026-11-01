#!/usr/bin/env python3
"""Zip a folder so it can be sent through filters that block scripts and
executables, and restored on the other side with restore_names.py.

    python zip_folder.py                       # zip the folder containing this script
    python zip_folder.py path/to/folder        # zip another folder
    python zip_folder.py --out C:\\temp\\app.zip
    python zip_folder.py --dry-run             # list what would be zipped
    python zip_folder.py --exclude docs --exclude "build*"   # skip more folders
    python zip_folder.py --no-default-excludes # also zip .git, .venv, ...

What it does (the same rules as `restore_names.py add`, applied inside the zip
so your files on disk are never renamed):
  * every file gets a trailing ".txt" (main.py -> main.py.txt, run.bat -> run.bat.txt);
    files already ending in ".txt" and the SKIP_EXT types (.json .csv .c .h) are stored as-is.
  * restore_names.py.txt is always included in the zip root, so the recipient can
    run `python restore_names.py restore` after renaming it to restore_names.py.
  * cache folders and test folders are skipped (DEFAULT_EXCLUDE_DIRS, plus any --exclude).
    Only folders are skipped: test files such as test_logger.py are still zipped.

The zip is written next to the folder as <folder>_<MM_DD_YYYY_HHmmss>.zip unless --out is given.
"""
import argparse
import fnmatch
import os
import sys
import zipfile
from datetime import datetime

RESTORE_SCRIPT = "restore_names.py.txt"
# Same as restore_names.py: these types are never given a ".txt".
SKIP_EXT = (".json", ".csv", ".c", ".h", ".md", ".log", ".html", ".htm", ".xml", ".yml", ".yaml", ".ini", ".cfg", ".conf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx", ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".ico")
# Folder names or relative paths; wildcards allowed (see restore_names.py).
DEFAULT_EXCLUDE_DIRS = [
    # version control / environments / editors
    ".git", ".venv", "venv", "node_modules", ".idea", ".vscode",
    # caches
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", "cache", "*.egg-info",
    # tests
    "test", "tests", "test_*", "*_test", "*_tests",
]


def archive_name(name: str) -> str:
    """Name a file gets inside the zip (same rule as `restore_names.py add`)."""
    lower = name.lower()
    if lower.endswith(".txt") or lower.endswith(SKIP_EXT):
        return name
    return name + ".txt"


def is_excluded(rel_dir: str, patterns: list[str]) -> bool:
    rel = rel_dir.replace(os.sep, "/")
    base = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(base, p) or fnmatch.fnmatch(rel, p.strip("/")) for p in patterns)


def find_restore_script(root: str) -> str:
    """Locate restore_names.py.txt (in root, beside this script, or as plain .py in root)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(root, RESTORE_SCRIPT), os.path.join(here, RESTORE_SCRIPT),
                 os.path.join(root, "restore_names.py")):
        if os.path.isfile(path):
            return path
    raise FileNotFoundError(f"{RESTORE_SCRIPT} not found in {root} or {here}")


def collect(root: str, exclude: list[str], skip_paths: set[str]) -> list[tuple[str, str]]:
    """Return (source path, path inside zip relative to root) for every file to zip."""
    files = []
    for folder, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs
                         if not is_excluded(os.path.relpath(os.path.join(folder, d), root), exclude))
        for name in sorted(names):
            src = os.path.join(folder, name)
            if os.path.abspath(src) in skip_paths or name.lower().startswith("restore_names"):
                continue  # the output zip itself; restore script is added separately
            rel_dir = os.path.relpath(folder, root)
            arc = archive_name(name) if rel_dir == "." else os.path.join(rel_dir, archive_name(name))
            files.append((src, arc))
    return files


def make_zip(root: str, out: str, exclude: list[str], dry_run: bool = False) -> int:
    root = os.path.abspath(root)
    out = os.path.abspath(out)
    top = os.path.basename(root)
    files = collect(root, exclude, {out})
    files.append((find_restore_script(root), RESTORE_SCRIPT))

    for src, arc in files:
        print(("would add: " if dry_run else "add: ") + arc.replace(os.sep, "/"))
    if dry_run:
        return len(files)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, arc in files:
            zf.write(src, f"{top}/{arc}".replace(os.sep, "/"))
    return len(files)


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    p = argparse.ArgumentParser(description="Zip a folder with .txt added to scripts/executables.")
    p.add_argument("folder", nargs="?", default=here, help="folder to zip (default: this script's folder)")
    p.add_argument("--out", help="zip file to create (default: <folder>_<MM_DD_YYYY_HHmmss>.zip beside the folder)")
    p.add_argument("--exclude", action="append", default=[], metavar="DIR",
                   help="folder name or relative path to skip (repeatable, wildcards allowed)")
    p.add_argument("--no-default-excludes", action="store_true",
                   help="do not skip the default folders (" + ", ".join(DEFAULT_EXCLUDE_DIRS) + ")")
    p.add_argument("--dry-run", action="store_true", help="only list what would be zipped")
    args = p.parse_args()

    root = os.path.abspath(args.folder)
    if not os.path.isdir(root):
        print(f"Not a folder: {root}", file=sys.stderr)
        return 1
    out = args.out or os.path.join(
        os.path.dirname(root), f"{os.path.basename(root)}_{datetime.now():%m_%d_%Y_%H%M%S}.zip")
    exclude = ([] if args.no_default_excludes else DEFAULT_EXCLUDE_DIRS) + args.exclude

    try:
        n = make_zip(root, out, exclude, args.dry_run)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"{'Would zip' if args.dry_run else 'Zipped'} {n} file(s)" + ("" if args.dry_run else f" -> {out}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
