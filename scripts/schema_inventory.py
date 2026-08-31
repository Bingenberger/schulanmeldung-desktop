"""Read-only inventory for a SQLite database; never prints row contents."""

import argparse
import sqlite3
from pathlib import Path


def inventory(path):
    database = Path(path).resolve()
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        print(f"database: {database}")
        print(f"integrity: {integrity}")
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
            count = connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
            print(f"{table}: rows={count}, columns={len(columns)}")
            for column in columns:
                print(f"  {column[1]} {column[2]} nullable={not bool(column[3])}")
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", default="instance/database.db")
    inventory(parser.parse_args().database)
