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


def create_backup(app, include_uploads=True, prefix=""):
    """Write a timestamped folder with the database and the uploads.

    ``prefix`` marks automatic backups (``auto-``) so that pruning them never
    touches the ones made by hand.
    """
    name = prefix + datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
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
    shutil.rmtree(_backup_folder(app, name))


def human_size(number):
    for unit in ("B", "KB", "MB", "GB"):
        if number < 1024 or unit == "GB":
            return f"{number:.0f} {unit}" if unit == "B" else f"{number:.1f} {unit}"
        number /= 1024


# --- Wiederherstellen -------------------------------------------------------------

#: Vor jedem Zurückspielen wird der aktuelle Stand so gesichert; ein Versehen
#: lässt sich damit seinerseits wieder zurücknehmen.
BEFORE_RESTORE_PREFIX = "vor-wiederherstellung-"
#: Tabellen, an denen eine SL-Office-Datenbank zu erkennen ist.
REQUIRED_TABLES = {"schueler", "user"}


def _backup_folder(app, name):
    """Den Ordner einer vorhandenen Sicherung, nur innerhalb des Sicherungsordners."""
    root = backup_root(app).resolve()
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise BackupError("Ungültiger Name der Sicherung.")
    target = (root / name).resolve()
    if target.parent != root or not target.is_dir():
        raise BackupError("Diese Sicherung wurde nicht gefunden.")
    return target


def check_database(path: Path):
    """Prüft, ob ``path`` eine unbeschädigte SL-Office-Datenbank ist."""
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("Die Datenbank in der Sicherung ist beschädigt.")
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
    except sqlite3.DatabaseError as exc:
        raise BackupError("Die Datei ist keine SL-Office-Datenbank.") from exc
    if not REQUIRED_TABLES <= tables:
        raise BackupError("Die Datei ist keine SL-Office-Datenbank.")
    return "alembic_version" in tables


def _bring_schema_up_to_date(app, versioned):
    """Eine ältere Sicherung auf den Stand dieser Programmversion bringen."""
    from models import db

    if versioned:
        from flask_migrate import upgrade
        upgrade(directory=str(Path(app.root_path) / "migrations"))
    else:
        # Ohne Versionsvermerk (mit create_all angelegt): fehlende Tabellen ergänzen.
        db.create_all()


def _copy_into_live_database(app, source: Path):
    """Den Inhalt von ``source`` über die Online-Backup-API in die laufende Datenbank schreiben.

    So wird die Datei nicht ersetzt, während die Anwendung sie geöffnet hat;
    offene Verbindungen werden vorher geschlossen.
    """
    from models import db

    target = _database_path(app)
    db.session.remove()
    db.engine.dispose()
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as origin, \
            sqlite3.connect(target) as destination:
        origin.backup(destination)
    db.engine.dispose()


def _replace_uploads(app, source: Path):
    uploads = Path(app.config.get("UPLOAD_FOLDER", ""))
    if not uploads:
        return
    if uploads.exists():
        shutil.rmtree(uploads)
    shutil.copytree(source, uploads)


def restore(app, database: Path, uploads: Path = None):
    """Datenbank (und, falls vorhanden, Dokumente) zurückspielen.

    Liefert den Ordner der Sicherung, die vom bisherigen Stand angelegt wurde.
    """
    versioned = check_database(database)
    safety = create_backup(app, include_uploads=True, prefix=BEFORE_RESTORE_PREFIX)
    _copy_into_live_database(app, database)
    if uploads is not None and uploads.is_dir():
        _replace_uploads(app, uploads)
    _bring_schema_up_to_date(app, versioned)
    return safety


def restore_backup(app, name):
    """Eine Sicherung aus der Liste zurückspielen."""
    folder = _backup_folder(app, name)
    database = folder / "database.db"
    if not database.is_file():
        raise BackupError("Diese Sicherung enthält keine Datenbank.")
    return restore(app, database, folder / "uploads")


def restore_upload(app, payload: bytes):
    """Eine heruntergeladene Datenbankdatei zurückspielen; Dokumente bleiben unberührt."""
    if not payload.startswith(b"SQLite format 3\x00"):
        raise BackupError("Die Datei ist keine SL-Office-Datenbank.")
    staging = backup_root(app) / f".upload-{os.getpid()}.db"
    try:
        staging.write_bytes(payload)
        return restore(app, staging)
    finally:
        staging.unlink(missing_ok=True)
