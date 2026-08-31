"""Konsistente Sicherung der SQLite-Datenbank.

Nutzt die Backup-API von SQLite statt einer Dateikopie: die liefert auch dann
einen in sich stimmigen Stand, wenn der Dienst gerade schreibt. Wird vor jeder
Migration aus dem Deployment aufgerufen.
"""

import datetime
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
#: Ältere Sicherungen bleiben liegen, aber nicht unbegrenzt viele.
KEEP = 30


def database_path():
    for candidate in (os.path.join(BASE_DIR, "instance", "database.db"),
                      os.path.join(BASE_DIR, "database.db")):
        if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
            return candidate
    return None


def prune(directory, keep=KEEP):
    backups = sorted(name for name in os.listdir(directory) if name.endswith(".db"))
    for name in backups[:-keep] if len(backups) > keep else []:
        os.remove(os.path.join(directory, name))


def backup_db(label=""):
    source_path = database_path()
    if source_path is None:
        print("Keine Datenbank gefunden - nichts zu sichern.")
        return None

    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{label}" if label else ""
    target = os.path.join(BACKUP_DIR, f"database_{stamp}{suffix}.db")

    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    copy = sqlite3.connect(target)
    try:
        with copy:
            source.backup(copy)
    finally:
        copy.close()
        source.close()
    prune(BACKUP_DIR)
    print(f"Sicherung erstellt: {target}")
    return target


if __name__ == "__main__":
    backup_db(sys.argv[1] if len(sys.argv) > 1 else "")
