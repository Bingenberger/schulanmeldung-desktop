# Datenbankmigrationen

SL-Office verwendet Flask-Migrate/Alembic. Das Schema wird außerhalb der
Testumgebung nicht mehr beim Anwendungsstart mit `db.create_all()` verändert.

## Neue leere Installation

Nach Setzen von `SL_OFFICE_ENV`, `SL_OFFICE_SECRET_KEY` und
`SL_OFFICE_DATABASE_URL`:

```bash
./venv/bin/flask --app app db upgrade
```

## Bestehende Installation

Die vorhandene `instance/database.db` darf **nicht** einfach mit `db upgrade`
behandelt werden, weil ihre Tabellen bereits existieren. Der sichere Ablauf ist:

1. Anwendung stoppen und konsistente Sicherung von Datenbank und Uploads erstellen.
2. Sicherung testweise wiederherstellen.
3. `scripts/schema_inventory.py` auf Original und Sicherung ausführen.
4. Historische Schemaabweichungen prüfen.
5. Erst nach dokumentierter Prüfung die Bestandsdatenbank auf die Baseline stempeln:
   `flask --app app db stamp ed2b53c79584`.
6. Danach `flask --app app db upgrade` ausführen.
7. Anwendung und Datensatzsummen prüfen.

Das Stempeln verändert nur Alembics Versionsmarkierung und darf erst erfolgen,
wenn das reale Schema fachlich mit der Baseline abgeglichen wurde.

## Bekannte Bestandsabweichung

`schueler.geschlecht` ist in der bestehenden SQLite-Datenbank als `TEXT`
angelegt, im aktuellen Modell und in der Baseline als `VARCHAR(10)`. SQLite
behandelt beide affin weitgehend gleich. Diese Abweichung wird nicht automatisch
am Produktivbestand geändert. Vor PostgreSQL ist eine validierende Migration mit
Prüfung aller vorhandenen Werte (`m`, `w`, `d`, `u`) erforderlich.

Abweichende Spaltenreihenfolgen in `diagnostik` und
`schulaerztliche_untersuchung` sind semantisch ohne Bedeutung.

## Neue Schemaänderung

```bash
./venv/bin/flask --app app db migrate -m "kurze beschreibung"
./venv/bin/flask --app app db upgrade
```

Jede generierte Migration muss gelesen und auf einer leeren Datenbank sowie auf
einer anonymisierten Kopie des Bestands getestet werden. Downgrades dürfen nicht
als Ersatz für Backups betrachtet werden.
