# SL-Office (Desktop-Fassung)

SL-Office begleitet die Schuleinschreibung an einer Grundschule – von der
Anmeldung bis zur Klassenbildung: Stammdaten, Pädagogische Diagnostik,
Schulspiel, schulärztliche Untersuchung, AO-SF und Rückstellung, Förderkurse
und Betreuung, Terminvergabe für das Anmeldegespräch und Elternbriefe.

Diese Fassung läuft als eigenständige Anwendung unter Windows oder macOS auf
einem Rechner der Schule und lässt sich von jeder Schule selbst einrichten: Schulprofil, Module,
Kriterien und Vorlagen werden in der Anwendung gepflegt, nicht im Quelltext.

> Entstanden ist sie aus [`schulanmeldung`](https://github.com/Bingenberger/schulanmeldung),
> der Serverfassung der Gemeinschaftsgrundschule Niederkassel, die dort
> unverändert weitergeführt wird. Gegenüber der Serverfassung fehlen
> Elternportal, Tag der offenen Tür, Mailversand und das Bedrucken von
> Protokollbögen; die Anmeldung kommt ohne zweiten Faktor aus.

## Installieren und starten

1. **Windows:** `SL-Office-Setup-<Version>.exe` ausführen – Administratorrechte
   sind nicht nötig.
   **macOS:** `SL-Office-<Version>-macOS-<arm64|x86_64>.dmg` öffnen und SL-Office
   in den Ordner „Programme“ ziehen (`arm64` für Macs mit Apple-Chip, `x86_64`
   für Macs mit Intel-Prozessor).
   Beides steht unter *Releases* bzw. als Artefakt des Workflows „Desktop-Build“.
2. SL-Office starten. Es öffnet sich im Browser; ein kleines Fenster zeigt, dass
   es läuft, und beendet es wieder. Der eingebaute Webserver ist nur von diesem
   Rechner aus erreichbar (`127.0.0.1`, Port 5050–5059) – es sei denn, SL-Office
   wird [im lokalen Netz bereitgestellt](#im-lokalen-netz-bereitstellen).
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

## Im lokalen Netz bereitstellen

Sollen mehrere Rechner mit denselben Daten arbeiten, läuft SL-Office auf einem
davon und die anderen greifen im Browser darauf zu. Dazu im Steuerfenster
**„Im lokalen Netz bereitstellen“** ankreuzen. Das Fenster zeigt dann unter
„Im Netz“ die Adresse für die anderen Rechner, etwa `http://192.168.1.23:5050`.
Die Wahl bleibt bis zum Abschalten bestehen, auch über einen Neustart hinweg;
wer gerade angemeldet ist, bleibt es beim Umschalten.

- Die Firewall fragt beim ersten Einschalten nach: unter Windows den Zugriff für
  **private Netzwerke** zulassen (dafür können Administratorrechte nötig sein),
  unter macOS eingehende Verbindungen **erlauben**.
- SL-Office muss auf dem bereitstellenden Rechner laufen, solange die anderen
  damit arbeiten. Eine feste Adresse (DHCP-Reservierung im Router) erspart, dass
  sich die Adresse ändert.
- Die Verbindung ist **nicht verschlüsselt** (HTTP). Nur in einem
  vertrauenswürdigen Netz einschalten, etwa dem Verwaltungsnetz – nicht im
  Schüler- oder Gäste-WLAN. Empfohlen ist dann der zweite Faktor
  (`SL_OFFICE_TWO_FACTOR=1`).
- Anfragen von außerhalb privater Netze weist SL-Office ab, und das erste
  Administrationskonto lässt sich nur am Rechner selbst anlegen.

Ohne Steuerfenster oder fest vorgegeben: die Umgebungsvariable
`SL_OFFICE_NETZWERK=1` schaltet die Bereitstellung ein, `0` aus; das Häkchen
ist dann gesperrt.

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

Alles liegt in `%APPDATA%\SL-Office`, unter macOS in
`~/Library/Application Support/SL-Office`:

| Pfad | Inhalt |
| --- | --- |
| `sl-office.db` | Datenbank (SQLite) |
| `uploads\` | hochgeladene Dokumente |
| `Datensicherungen\` | automatische Sicherung je Tag, die letzten 14 bleiben |
| `sl-office.log` | Protokoll |
| `einstellungen.json` | Wahl aus dem Steuerfenster (Bereitstellung im Netz) |

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
./venv/bin/python desktop.py               # wie die gebaute Fassung, mit Steuerfenster
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

## Windows-Setup und macOS-Image bauen

Der Workflow `.github/workflows/desktop-build.yml` läuft bei jedem Push: Tests
unter Linux, dann Bau mit PyInstaller (`packaging/sl-office.spec`) und ein
Probestart der gebauten Fassung – unter Windows mit anschließendem Setup aus
Inno Setup (`packaging/sl-office.iss`), unter macOS mit einem Disk-Image
(`packaging/macos-dmg.sh`), je eines für Apple-Chip und Intel. Setup und
Disk-Images hängen als Artefakte am Workflow-Lauf.

**Neue Version veröffentlichen:** auf `main` einen Tag setzen und pushen, etwa

```bash
git tag v1.0.0
git push origin v1.0.0
```

Der Workflow legt dann ein Release mit `SL-Office-Setup-1.0.0.exe` und den
beiden Disk-Images an.

Von Hand unter Windows:

```bat
pip install -r requirements.txt -r requirements-desktop.txt
pyinstaller packaging\sl-office.spec --noconfirm
iscc /DAppVersion=1.0.0 packaging\sl-office.iss
```

Von Hand unter macOS (baut für den Prozessor des eigenen Rechners):

```bash
pip install -r requirements.txt -r requirements-desktop.txt
SL_OFFICE_VERSION=1.0.0 pyinstaller packaging/sl-office.spec --noconfirm
packaging/macos-dmg.sh 1.0.0
```

Das Setup ist nicht signiert; Windows SmartScreen warnt deshalb beim ersten
Start („Weitere Informationen“ → „Trotzdem ausführen“). Auch die macOS-Fassung
ist weder signiert noch notarisiert: macOS verweigert den ersten Start, bis
SL-Office unter *Systemeinstellungen → Datenschutz & Sicherheit* mit „Dennoch
öffnen“ freigegeben wird.

## Aufbau

| Pfad | Inhalt |
| --- | --- |
| `desktop.py` | Starter der Desktop-Fassung: lokaler Server, Migrationen, Sicherung, Steuerfenster |
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
| `packaging/` | PyInstaller-Bauplan, Inno-Setup-Skript, Disk-Image für macOS |
| `migrations/` | Alembic-Migrationen |
| `tests/` | Tests (unittest) |
| `deploy/`, `docs/` | Betrieb als Server und dessen Dokumentation (aus der Serverfassung übernommen) |

## Betrieb als Server

Der Code kann weiterhin auch als Webanwendung hinter nginx laufen
(`SL_OFFICE_ENV=production`, Vorlage in `.env.example`, Skripte in `deploy/`).
Anleitung und Sicherheitskonzept in `docs/` stammen aus der Serverfassung;
Abschnitte zu Elternportal, Mailversand und Erinnerungen gelten hier nicht.
Für den Einsatz an einer einzelnen Schule ist die Desktop-Fassung der
vorgesehene Weg.

## Datenschutz

SL-Office verarbeitet personenbezogene Daten von Kindern. Die Desktop-Fassung
ist nur vom eigenen Rechner aus erreichbar, solange sie nicht ausdrücklich im
lokalen Netz bereitgestellt wird; Benutzerkonten mit Rollen,
serverseitige Rechteprüfung und Protokollierung bleiben erhalten. Rechner,
Benutzerkonto am Rechner und Sicherungen sind entsprechend zu schützen.

Nicht ins Repository gehören (und sind in `.gitignore` ausgenommen): `.env`,
`instance/`, `uploads/`, `backups/` sowie alle Datenbankdateien.

## Offen vor einer Weitergabe an andere Schulen

- Setup signieren, damit SmartScreen nicht warnt
- macOS-Fassung signieren und notarisieren (Apple-Developer-Konto nötig)
