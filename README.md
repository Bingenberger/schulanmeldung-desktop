# SL-Office (Desktop-Fassung)

> Dieses Repository ist die frei konfigurierbare Fassung von
> [`schulanmeldung`](https://github.com/Bingenberger/schulanmeldung), gedacht als
> eigenständige Windows-Anwendung für beliebige Grundschulen. Die Serverfassung
> der Gemeinschaftsgrundschule Niederkassel wird dort unverändert weitergeführt.
>
> Unterschiede bisher:
>
> - **Kein Elternportal, kein Tag der offenen Tür:** beides ist ausgebaut. Die
>   Elternbriefe laden ohne Zugangslinks zur Terminvereinbarung ein, Termine
>   vergibt die Schule.
> - **Schulprofil in der Anwendung:** Name, Anschrift, Schulleitung, Logo und
>   Unterschrift pflegt jede Schule unter *Verwaltung → Schulprofil*.
> - **Module:** unter *Verwaltung → Module* schaltet jede Schule ab, was sie
>   nicht nutzt (z. B. Schulspiel, AO-SF, Förderkurse).
> - **Frei anlegbare Kriterien:** die Beobachtungspunkte von Pädagogischer
>   Diagnostik, Schulspiel und Schularzt legt jede Schule unter
>   *Verwaltung → Kriterien* selbst an (Skala, Ankreuzfeld, Auswahl,
>   Mehrfachauswahl, Freitext, Datum). Vorbelegt ist der bisherige Katalog;
>   Werte aus den alten festen Spalten werden dabei übernommen.
> - **Vorlagen:** unter *Verwaltung → Vorlagen* pflegt jede Schule die
>   Checkliste des Laufzettels Anmeldung. Protokollbögen druckt die Anwendung
>   nicht – das handhabt jede Schule anders. Die Liste der Stadt darf auch eine
>   CSV-Datei sein, die Spaltenzuordnung wird für das nächste Jahr gemerkt.
>
> Fehlerbehebungen aus der Serverfassung lassen sich über den Remote `upstream`
> mit `git cherry-pick` übernehmen.

## Windows-Fassung

**Installieren:** `SL-Office-Setup-<Version>.exe` ausführen (keine
Administratorrechte nötig). SL-Office startet einen Webserver nur für diesen
Rechner und öffnet sich im Browser; ein kleines Fenster zeigt, dass es läuft,
und beendet es wieder. Beim ersten Start legt man das Administrationskonto an,
danach geht es über *Verwaltung* weiter: Schulprofil, Module, Kriterien,
Vorlagen, Benutzer. Angemeldet wird mit Benutzername und Passwort; einen
zweiten Faktor braucht die lokale Installation nicht
(`SL_OFFICE_TWO_FACTOR=1` schaltet ihn wieder ein).

**Daten:** alles liegt in `%APPDATA%\SL-Office` – Datenbank
(`sl-office.db`), hochgeladene Dateien, das Protokoll und unter
`Datensicherungen` eine automatische Sicherung je Tag (die letzten 14 bleiben).
Ein anderer Ort, etwa ein Netzlaufwerk, lässt sich mit der Umgebungsvariablen
`SL_OFFICE_DATA_DIR` festlegen. Deinstallieren oder Aktualisieren lässt die
Daten unberührt.

**Zurückspielen:** unter *Verwaltung → Datensicherung* hat jede Sicherung den
Knopf „Zurückspielen“ (Datenbank und Dokumente); eine über „Datenbank
herunterladen“ gesicherte Datei lässt sich dort hochladen. Vorher wird der
aktuelle Stand als `vor-wiederherstellung-…` gesichert, eine ältere Sicherung
wird auf den Stand der Programmversion gebracht, danach melden sich alle neu
an. Nur die Administration darf zurückspielen.

**Bauen:** der Workflow `.github/workflows/windows-build.yml` testet, baut mit
PyInstaller (`packaging/sl-office.spec`), startet die gebaute Fassung einmal
probehalber und erzeugt das Setup mit Inno Setup (`packaging/sl-office.iss`).
Das Setup hängt als Artefakt am Workflow-Lauf; ein Tag `v1.2.3` legt ein
Release an. Von Hand unter Windows:

```bat
pip install -r requirements.txt -r requirements-desktop.txt
pyinstaller packaging\sl-office.spec --noconfirm
iscc /DAppVersion=1.0.0 packaging\sl-office.iss
```

Zum Ausprobieren ohne Paket genügt `python desktop.py`.


Fachanwendung für die Schuleinschreibung an der Gemeinschaftsgrundschule
Niederkassel. SL-Office begleitet ein Kind von der Anmeldung bis zur
Klassenbildung: Stammdaten, Diagnostik, Schulanmeldespiel, schulärztliche
Untersuchung, AO-SF und Rückstellung, Förderkurse und Betreuung, dazu die
Elternkommunikation mit Anmeldeformular und Terminbuchung.

Die Anwendung ist eine Flask-Anwendung mit serverseitigen Jinja-Templates,
SQLAlchemy-Modellen und SQLite als Datenbank. Sie läuft als systemd-Dienst
hinter nginx; Änderungen kommen per `git push` auf den Server.

## Funktionsumfang

Anmeldung mit Benutzername und Passwort (auf dem Server zusätzlich mit
Zwei-Faktor-Bestätigung).

- Schülerliste mit Filtern, Einzelansicht, Anlegen und Bearbeiten
- Import aus Excel-Listen und aus den Listen der Stadt (XLSX oder CSV)
- Pädagogische Diagnostik, Schulspiel, Schularzt mit frei anlegbaren
  Kriterien; Kita-Bericht, Freundeswünsche
- AO-SF-Verfahren und Rückstellung, jeweils mit Dokumenten
- Klassenbildung mit Zuweisung und Klassenmappe als PDF
- Förderkurse einzeln und im Stapel, Betreuung im Stapel
- PDF-Karteikarten, Klassenmappe, Förderkursliste; Klassenlisten als Excel
- Einschulungsjahre: alle Daten sind auf ein Schuljahr eingegrenzt, ältere
  Jahrgänge lassen sich lesend ansehen
- Terminplanung für das Anmeldegespräch: Gesprächstage, Zeitfenster,
  Vergabe an die Kinder, Liste der Kinder ohne Termin, Kalenderexport
- Elternbrief in zwei Fassungen: mit vergebenem Termin oder mit der Bitte,
  einen Termin zu vereinbaren — je Kind wird beim Druck die passende gesetzt
- Laufzettel Anmeldung, im Briefkopf der Schule gesetzt, mit Name und Termin —
  einzeln oder für alle Kinder nach Termin sortiert
- Administration: Benutzer, Einstellungen, Schulprofil, Module, Kriterien,
  Vorlagen, Datensicherung mit Zurückspielen

## Aufbau

| Pfad | Inhalt |
| --- | --- |
| `app.py` | Anwendungsfabrik `create_app`, historische Routen (Diagnostik, Klassen, Exporte) |
| `models.py` | Schüler, Diagnostik, AO-SF, Rückstellung, Benutzer, Einschulungsjahr |
| `forms.py` | WTForms-Formulare der internen Oberfläche |
| `config.py` | Konfigurationsprofile `development`, `testing`, `production`, `desktop` |
| `desktop.py` | Starter der Windows-Fassung |
| `security.py` | CSRF-Schutz und Sicherheitskopfzeilen |
| `document_service.py` | Auflösung hochgeladener Dateien über ihren Fachdatensatz |
| `sl_office/auth/` | Anmeldung, Zwei-Faktor-Verfahren, Wiederherstellungscodes |
| `sl_office/students/` | Import, Auswahl, Anlegen und Bearbeiten von Schülern |
| `sl_office/admin/` | Administration einschließlich Datensicherung |
| `sl_office/appointments/` | Terminserien, Zeitfenster, Vergabe, Laufzettel |
| `sl_office/briefe/` | Elternbriefe und Briefkopf |
| `sl_office/criteria/` | frei anlegbare Kriterien der Bögen |
| `sl_office/features.py` | abschaltbare Module |
| `sl_office/school_profile.py`, `sl_office/vorlagen.py` | Schulprofil und Laufzettel-Vorlage |
| `sl_office/school_year.py` | zentrale Eingrenzung aller Abfragen auf ein Einschulungsjahr |
| `sl_office/authorization.py` | `role_required` für die vier Rollen |
| `sl_office/audit.py` | Protokollierung sicherheitsrelevanter Vorgänge |
| `templates/`, `static/`, `assets/` | Oberfläche, mitgelieferte Bibliotheken, Schriften |
| `packaging/` | PyInstaller-Bauplan und Inno-Setup-Skript |
| `migrations/` | Alembic-Migrationen |
| `tests/` | Tests (unittest) |
| `deploy/` | nginx-Vorlage, Git-Hook, sudoers-Regel |
| `docs/` | Architektur- und Sicherheitskonzept, Betrieb, Migrationen (Serverfassung) |

Rollen: `Administrator`, `Schulleitung`, `Foerderlehrkraft`, `Sekretariat`.

## Einrichtung zur Entwicklung

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/flask --app app db upgrade      # Schema anlegen
./venv/bin/python create_admin.py          # Erstzugang admin / admin123
./venv/bin/python app.py                   # http://127.0.0.1:5001
```

Ohne `.env` läuft die Anwendung im Entwicklungsprofil: SQLite unter
`instance/database.db`, ein selbst erzeugter Schlüssel in
`instance/.development-secret-key`, Uploads im Projektordner `uploads/`.
Das Standardkennwort des Erstzugangs ist sofort zu ändern.

## Konfiguration

`.env.example` nach `.env` kopieren und ausfüllen; die Datei gehört nicht ins
Repository und sollte `chmod 600` haben. Erst `SL_OFFICE_ENV=production`
schaltet in den Produktionsbetrieb, der `SL_OFFICE_SECRET_KEY` und
`SL_OFFICE_DATABASE_URL` zwingend verlangt.

Wichtige Werte:

| Variable | Bedeutung |
| --- | --- |
| `SL_OFFICE_ENV` | `development`, `testing` oder `production` |
| `SL_OFFICE_SECRET_KEY` | mindestens 32 zufällige Zeichen (`openssl rand -hex 32`) |
| `SL_OFFICE_DATABASE_URL` | Datenbank, im Betrieb der absolute SQLite-Pfad |
| `SL_OFFICE_UPLOAD_FOLDER` | Ablage der Dokumente außerhalb des Webverzeichnisses |
| `SL_OFFICE_TRUSTED_PROXIES` | `1` hinter nginx, sonst `0`; ohne den Wert entstehen Aktivierungslinks als `http://` mit internem Hostnamen |
| `SL_OFFICE_MAIL_*` | Mailversand der Elternzugänge und Terminmails |
| `SL_OFFICE_NOTIFY_MAIL` | erfährt von neuen Terminbuchungen; leer = `SL_OFFICE_SCHOOL_CONTACT_MAIL` |
| `SL_OFFICE_REMINDER_HOURS` | Vorlauf der Terminerinnerung, Vorgabe 24 |
| `SL_OFFICE_PUBLIC_BASE_URL` | öffentliche Adresse; der Erinnerungsdienst baut damit den Portallink |
| `SL_OFFICE_SCHOOL_*` | Briefkopf der Elternschreiben; Vorgaben stehen in `config.py` |

## Datenbank

Das Schema wird ausschließlich über Alembic-Migrationen geändert; im Betrieb
gibt es kein `db.create_all()`.

```bash
./venv/bin/flask --app app db upgrade                 # anwenden
./venv/bin/flask --app app db migrate -m "Beschreibung"   # neue Migration
```

Eine bestehende Datenbank aus der Zeit vor den Migrationen darf nicht ohne
Weiteres mit `db upgrade` behandelt werden. Der geprüfte Ablauf steht in
[docs/DATENBANKMIGRATIONEN.md](docs/DATENBANKMIGRATIONEN.md).

Sicherungen erstellt `backup_db.py` über die Backup-API von SQLite, also auch
im laufenden Betrieb konsistent. Die letzten 30 Stände bleiben unter
`backups/` liegen; das Deployment sichert vor jeder Migration automatisch.

```bash
./venv/bin/python backup_db.py "vor_umbau"
```

## Tests

```bash
./venv/bin/python -m unittest discover -s tests -t . -q
```

Dieselbe Zeile läuft im Deployment; schlagen die Tests fehl, wird nicht
neu gestartet.

## Betrieb

```
Browser ──HTTPS──▶ nginx ──HTTP──▶ gunicorn (127.0.0.1:5000) ──▶ SL-Office
                                                                    │
Arbeitsrechner ──git push──▶ ~/sl-office.git ──post-receive──▶ Arbeitsverzeichnis
```

Der Dienst wird einmalig mit `./install_service.sh` eingerichtet
(`sl-office.service` nach `/etc/systemd/system/`), das Git-Deployment mit
`deploy/setup-git-deploy.sh`. Danach genügt ein `git push`: der Hook spielt die
Dateien ein, installiert Abhängigkeiten, sichert die Datenbank, wendet
Migrationen an, führt die Tests aus und startet den Dienst neu.

Vollständige Anleitung einschließlich nginx-Block, Zurückrollen und
Fehlersuche: [docs/BETRIEB_UND_DEPLOYMENT.md](docs/BETRIEB_UND_DEPLOYMENT.md).

## Sicherheit und Datenschutz

Die Anwendung verarbeitet personenbezogene Daten von Kindern. Grundsätze und
Maßnahmen — getrennte Blueprints für innen und außen, serverseitige
Rechteprüfung jeder Aktion, berechtigungsgeprüfter Dokumentabruf, Elternangaben
nur als geprüfte Einreichung, Protokollierung, Betrieb ausschließlich hinter
HTTPS — beschreibt
[docs/ARCHITEKTUR_UND_SICHERHEITSKONZEPT.md](docs/ARCHITEKTUR_UND_SICHERHEITSKONZEPT.md).

Nicht ins Repository gehören und sind in `.gitignore` ausgenommen: `.env`,
`instance/`, `uploads/`, `backups/` sowie alle Datenbankdateien.
