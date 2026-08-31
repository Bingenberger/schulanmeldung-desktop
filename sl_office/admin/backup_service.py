"""Backups of the database and the uploaded documents.

The database is copied through SQLite's online backup API rather than by
copying the file: a plain file copy taken while a write is in flight can yield
an unusable database.
"""

import datetime
import os
import re
import shutil
import sqlite3
from io import BytesIO
from pathlib import Path

#: Directory name pattern, also used to validate names coming from the browser.
NAME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}$")


class BackupError(RuntimeError):
    pass


def _database_path(app):
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri.startswith("sqlite:///"):
        raise BackupError("Die Sicherung unterstützt derzeit nur SQLite-Datenbanken.")
    path = uri[len("sqlite:///"):]
    if path == ":memory:":
        raise BackupError("Eine In-Memory-Datenbank kann nicht gesichert werden.")
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path(app.instance_path) / candidate
    return candidate


def backup_root(app):
    root = Path(app.config.get("BACKUP_FOLDER") or (Path(app.root_path) / "backups"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _consistent_copy(source: Path, target: Path):
    """Copy a live SQLite database safely."""
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as origin, \
            sqlite3.connect(target) as copy:
        origin.backup(copy)


def database_snapshot(app):
    """Return a consistent copy of the database as bytes, for download."""
    source = _database_path(app)
    if not source.exists():
        raise BackupError("Die Datenbankdatei wurde nicht gefunden.")
    staging = backup_root(app) / f".download-{os.getpid()}.db"
    try:
        _consistent_copy(source, staging)
        return BytesIO(staging.read_bytes())
    finally:
        staging.unlink(missing_ok=True)


def create_backup(app, include_uploads=True):
    """Write a timestamped folder with the database and the uploads."""
    name = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    target = backup_root(app) / name
    suffix = 1
    while target.exists():
        suffix += 1
        target = backup_root(app) / f"{name}-{suffix}"
    target.mkdir(parents=True)
    try:
        _consistent_copy(_database_path(app), target / "database.db")
        uploads = Path(app.config.get("UPLOAD_FOLDER", ""))
        if include_uploads and uploads.is_dir():
            shutil.copytree(uploads, target / "uploads")
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def _folder_size(path: Path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def list_backups(app):
    """Existing backups, newest first."""
    root = backup_root(app)
    entries = []
    for item in root.iterdir():
        if not item.is_dir() or item.name.startswith("."):
            continue
        entries.append({
            "name": item.name,
            "created": datetime.datetime.fromtimestamp(item.stat().st_mtime),
            "size": _folder_size(item),
            "has_uploads": (item / "uploads").is_dir(),
            "has_database": (item / "database.db").is_file(),
        })
    return sorted(entries, key=lambda entry: entry["created"], reverse=True)


def delete_backup(app, name):
    """Remove one backup folder, refusing anything outside the backup root."""
    root = backup_root(app).resolve()
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise BackupError("Ungültiger Name der Sicherung.")
    target = (root / name).resolve()
    if target.parent != root or not target.is_dir():
        raise BackupError("Diese Sicherung wurde nicht gefunden.")
    shutil.rmtree(target)


def human_size(number):
    for unit in ("B", "KB", "MB", "GB"):
        if number < 1024 or unit == "GB":
            return f"{number:.0f} {unit}" if unit == "B" else f"{number:.1f} {unit}"
        number /= 1024
