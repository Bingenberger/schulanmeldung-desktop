"""Migration: adds klasse column to schueler, anzahl_klassen to global_settings"""
import sqlite3, os

db_path = os.path.join(os.path.dirname(__file__), 'instance', 'database.db')
conn = sqlite3.connect(db_path)
cur = conn.cursor()

existing = [row[1] for row in cur.execute("PRAGMA table_info(schueler)").fetchall()]
if 'klasse' not in existing:
    cur.execute("ALTER TABLE schueler ADD COLUMN klasse VARCHAR(10)")
    print("Added schueler.klasse")
else:
    print("schueler.klasse already exists")

existing_gs = [row[1] for row in cur.execute("PRAGMA table_info(global_settings)").fetchall()]
if 'anzahl_klassen' not in existing_gs:
    cur.execute("ALTER TABLE global_settings ADD COLUMN anzahl_klassen INTEGER DEFAULT 3")
    print("Added global_settings.anzahl_klassen")
else:
    print("global_settings.anzahl_klassen already exists")

conn.commit()
conn.close()
print("Migration abgeschlossen.")
