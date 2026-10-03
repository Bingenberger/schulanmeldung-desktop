# SL-Office (Desktop-Fassung)

SL-Office begleitet die Schuleinschreibung an einer Grundschule – von der
Anmeldung bis zur Klassenbildung: Stammdaten, Pädagogische Diagnostik,
Schulspiel, schulärztliche Untersuchung, AO-SF und Rückstellung, Förderkurse
und Betreuung, Terminvergabe für das Anmeldegespräch und Elternbriefe.

Diese Fassung läuft als eigenständige Windows-Anwendung auf einem Rechner der
Schule und lässt sich von jeder Schule selbst einrichten: Schulprofil, Module,
Kriterien und Vorlagen werden in der Anwendung gepflegt, nicht im Quelltext.

> Entstanden ist sie aus [`schulanmeldung`](https://github.com/Bingenberger/schulanmeldung),
> der Serverfassung der Gemeinschaftsgrundschule Niederkassel, die dort
> unverändert weitergeführt wird. Gegenüber der Serverfassung fehlen
> Elternportal, Tag der offenen Tür, Mailversand und das Bedrucken von
> Protokollbögen; die Anmeldung kommt ohne zweiten Faktor aus.

## Installieren und starten

1. `SL-Office-Setup-<Version>.exe` ausführen – Administratorrechte sind nicht
   nötig. Das Setup steht unter *Releases* bzw. als Artefakt des Workflows
   „Windows-Build“.
2. SL-Office starten. Es öffnet sich im Browser; ein kleines Fenster zeigt, dass
   es läuft, und beendet es wieder. Der eingebaute Webserver ist nur von diesem
   Rechner aus erreichbar (`127.0.0.1`, Port 5050–5059).
3. Beim ersten Start das Administrationskonto anlegen.
4. Unter *Verwaltung* die Schule einrichten:
   - **Schulprofil** – Name, Anschrift, Kontakt, Schulleitung, Logo, Unterschrift
     (erscheinen im Briefkopf aller Schreiben)
   - **Module** – abschalten, was die Schule nicht nutzt
   - **Kriterien** – Beobachtungspunkte für Diagnostik, Schulspiel und Schularzt
   - **Vorlagen** – Checkliste des Laufzettels Anmeldung
   - **Benutzer** – Konten für Kolleginnen und Kollegen

Angemeldet wird mit Benutzername und Passwort. Wer trotzdem einen zweiten
Faktor möchte, setzt die Umgebungsvariable `SL_OFFICE_TWO_FACTOR=1`.

## Funktionsumfang

- Schülerliste mit Filtern, Schülerakte, Anlegen und Bearbeiten
- Import aus Excel-Listen und aus der Liste der Stadt (XLSX oder CSV, mit
  gemerkter Spaltenzuordnung)
- Pädagogische Diagnostik, Schulspiel und Schularzt mit frei anlegbaren
  Kriterien (Skala, Ankreuzfeld, Auswahl, Mehrfachauswahl, Freitext, Datum);
  Kita-Bericht, Freundeswünsche
- AO-SF-Verfahren und Rückstellung mit hochgeladenen Dokumenten; Berichte
  lassen sich direkt in der Schülerakte ansehen
- Terminplanung für das Anmeldegespräch: Gesprächstage, Zeitfenster, Vergabe an
  die Kinder, Liste der Kinder ohne Termin, Kalenderexport
- Elternbrief in zwei Fassungen: mit vergebenem Termin oder mit der Bitte, einen
  Termin zu vereinbaren – je Kind wird beim Druck die passende gesetzt
- Laufzettel Anmeldung im Briefkopf der Schule, einzeln oder für alle Kinder
  nach Termin sortiert
- Klassenbildung mit Zuweisung und Klassenmappe als PDF; Förderkurse und
  Betreuung, auch im Stapel; Karteikarten, Förderkursliste, Klassenlisten als
  Excel
- Einschulungsjahre: alle Daten sind auf einen Jahrgang eingegrenzt, ältere
  Jahrgänge lassen sich lesend ansehen
- Rollen `Administrator`, `Schulleitung`, `Foerderlehrkraft`, `Sekretariat`

Protokollbögen für Anmeldespiel oder Gespräch druckt SL-Office bewusst nicht –
das handhabt jede Schule anders.

## Daten und Datensicherung

Alles liegt in `%APPDATA%\SL-Office`:

| Pfad | Inhalt |
| --- | --- |
| `sl-office.db` | Datenbank (SQLite) |
| `uploads\` | hochgeladene Dokumente |
| `Datensicherungen\` | automatische Sicherung je Tag, die letzten 14 bleiben |
| `sl-office.log` | Protokoll |

Ein anderer Ort, etwa ein Netzlaufwerk, lässt sich mit der Umgebungsvariablen
`SL_OFFICE_DATA_DIR` festlegen. Aktualisieren oder Deinstallieren lässt die
Daten unberührt; nach einem Update bringt SL-Office die Datenbank beim Start
selbst auf den neuen Stand.

**Sichern:** zusätzlich zu den täglichen Sicherungen jederzeit von Hand unter
*Verwaltung → Datensicherung*; dort lässt sich die Datenbank auch
herunterladen und extern ablegen. Die Daten betreffen Kinder – Sicherungen
gehören auf einen geschützten Datenträger.

**Zurückspielen:** unter *Verwaltung → Datensicherung* hat jede Sicherung den
Knopf „Zurückspielen“ (Datenbank und Dokumente); eine heruntergeladene
Datenbankdatei lässt sich dort hochladen. Vorher wird der aktuelle Stand als
`vor-wiederherstellung-…` gesichert, eine ältere Sicherung wird auf den Stand
der Programmversion gebracht, danach melden sich alle neu an. Nur die
Administration darf zurückspielen.

## Entwicklung

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt -r requirements-desktop.txt
./venv/bin/python desktop.py               # wie die Windows-Fassung, mit Steuerfenster
```

`desktop.py` legt die Daten unter Linux in `~/.local/share/sl-office` ab (oder
in `SL_OFFICE_DATA_DIR`), wendet die Migrationen an und führt beim ersten Start
durch die Einrichtung.

Tests:

```bash
./venv/bin/python -m unittest discover -s tests -t . -q
```

Das Schema wird ausschließlich über Alembic-Migrationen geändert:

```bash
./venv/bin/flask --app app db migrate -m "Beschreibung"   # neue Migration
./venv/bin/flask --app app db upgrade                     # anwenden
```

Hinweise zu älteren Datenbanken aus der Zeit vor den Migrationen stehen in
[docs/DATENBANKMIGRATIONEN.md](docs/DATENBANKMIGRATIONEN.md).

## Windows-Setup bauen

Der Workflow `.github/workflows/windows-build.yml` läuft bei jedem Push: Tests
unter Linux, dann unter Windows Bau mit PyInstaller (`packaging/sl-office.spec`),
ein Probestart der gebauten `SL-Office.exe` und das Setup mit Inno Setup
(`packaging/sl-office.iss`). Das Setup hängt als Artefakt am Workflow-Lauf.

**Neue Version veröffentlichen:** auf `main` einen Tag setzen und pushen, etwa

```bash
git tag v1.0.0
git push origin v1.0.0
```

Der Workflow legt dann ein Release mit `SL-Office-Setup-1.0.0.exe` an.

Von Hand unter Windows:

```bat
pip install -r requirements.txt -r requirements-desktop.txt
pyinstaller packaging\sl-office.spec --noconfirm
iscc /DAppVersion=1.0.0 packaging\sl-office.iss
```

Das Setup ist nicht signiert; Windows SmartScreen warnt deshalb beim ersten
Start („Weitere Informationen“ → „Trotzdem ausführen“).

## Aufbau

| Pfad | Inhalt |
| --- | --- |
| `desktop.py` | Starter der Windows-Fassung: lokaler Server, Migrationen, Sicherung, Steuerfenster |
| `app.py` | Anwendungsfabrik `create_app`, historische Routen (Diagnostik, Klassen, Exporte) |
| `config.py` | Konfigurationsprofile `desktop`, `development`, `testing`, `production` |
| `models.py` | Schüler, Diagnostik, AO-SF, Rückstellung, Benutzer, Einschulungsjahr |
| `forms.py` | WTForms-Formulare |
| `security.py` | CSRF-Schutz und Sicherheitskopfzeilen |
| `document_service.py` | Auflösung hochgeladener Dateien über ihren Fachdatensatz |
| `sl_office/auth/` | Anmeldung, Ersteinrichtung, optionaler zweiter Faktor |
| `sl_office/students/` | Import, Auswahl, Anlegen und Bearbeiten von Schülern |
| `sl_office/admin/` | Verwaltung einschließlich Datensicherung und Zurückspielen |
| `sl_office/appointments/` | Gesprächstage, Zeitfenster, Vergabe, Laufzettel |
| `sl_office/briefe/` | Elternbriefe und Briefkopf |
| `sl_office/criteria/` | frei anlegbare Kriterien der Bögen |
| `sl_office/features.py` | abschaltbare Module |
| `sl_office/school_profile.py`, `sl_office/vorlagen.py` | Schulprofil und Laufzettel-Vorlage |
| `sl_office/school_year.py` | Eingrenzung aller Abfragen auf ein Einschulungsjahr |
| `sl_office/authorization.py`, `sl_office/audit.py` | Rollenprüfung, Protokollierung |
| `templates/`, `static/`, `assets/` | Oberfläche, mitgelieferte Bibliotheken, Schriften |
| `packaging/` | PyInstaller-Bauplan und Inno-Setup-Skript |
| `migrations/` | Alembic-Migrationen |
| `tests/` | Tests (unittest) |
| `deploy/`, `docs/` | Betrieb als Server und dessen Dokumentation (aus der Serverfassung übernommen) |

## Betrieb als Server

Der Code kann weiterhin auch als Webanwendung hinter nginx laufen
(`SL_OFFICE_ENV=production`, Vorlage in `.env.example`, Skripte in `deploy/`).
Anleitung und Sicherheitskonzept in `docs/` stammen aus der Serverfassung;
Abschnitte zu Elternportal, Mailversand und Erinnerungen gelten hier nicht.
Für den Einsatz an einer einzelnen Schule ist die Windows-Fassung der
vorgesehene Weg.

## Datenschutz

SL-Office verarbeitet personenbezogene Daten von Kindern. Die Desktop-Fassung
ist nur vom eigenen Rechner aus erreichbar; Benutzerkonten mit Rollen,
serverseitige Rechteprüfung und Protokollierung bleiben erhalten. Rechner,
Windows-Benutzerkonto und Sicherungen sind entsprechend zu schützen.

Nicht ins Repository gehören (und sind in `.gitignore` ausgenommen): `.env`,
`instance/`, `uploads/`, `backups/` sowie alle Datenbankdateien.

## Offen vor einer Weitergabe an andere Schulen

- Lizenz der Hausschrift `FrenteH1` klären
- Setup signieren, damit SmartScreen nicht warnt
