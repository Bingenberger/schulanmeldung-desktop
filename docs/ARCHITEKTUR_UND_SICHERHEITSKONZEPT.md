# Architektur- und Sicherheitskonzept für SL-Office

**Status:** Zielkonzept vor Implementierungsbeginn  
**Stand:** 26. August 2026  
**Geltungsbereich:** bestehende interne Anwendung, neues Elternportal, Terminbuchung und digitale Schulanmeldung

## 1. Ziel und Leitentscheidungen

SL-Office soll von einer lokal gewachsenen Flask-Anwendung zu einer wartbaren und über das Internet sicher betreibbaren Fachanwendung weiterentwickelt werden. Bestehende Schülerdaten und Dokumente werden kontrolliert migriert; ein unverbundener vollständiger Neubau ist nicht vorgesehen.

Verbindliche Leitentscheidungen:

1. Interne Verwaltung und Elternportal verwenden dieselbe fachliche Anwendung und Datenbasis, werden aber durch getrennte Flask-Blueprints, Authentifizierungsverfahren, Berechtigungen und URL-Bereiche isoliert.
2. Elternangaben verändern interne Schülerstammdaten niemals unmittelbar. Sie werden als versionierte Anmeldung eingereicht, intern geprüft und erst dann übernommen.
3. Ein Besitzlink aus einem Brief ist nur ein Aktivierungsnachweis, kein dauerhaftes Kennwort. Dauerhafte Elternzugänge werden an bestätigte E-Mail-Adressen gebunden.
4. Pro Kind sind höchstens zwei aktive Elternzugänge zulässig. Die Zahl der Sorge- und Kontaktpersonen im Anmeldeformular bleibt davon unabhängig.
5. Eltern sehen ausschließlich explizit veröffentlichte Statusmeldungen. Interne Statuswerte oder Bemerkungen werden nicht automatisch nach außen abgebildet.
6. Autorisierung wird serverseitig für jede Aktion geprüft. Ausgeblendete Schaltflächen sind kein Zugriffsschutz.
7. Dokumente liegen außerhalb öffentlich auslieferbarer Verzeichnisse und werden nur über eine berechtigungsgeprüfte Download-Funktion bereitgestellt.
8. Datenbankänderungen erfolgen ausschließlich über versionierte Migrationen. Produktiv wird `db.create_all()` nicht als Migrationsmechanismus verwendet.
9. Sicherheitsrelevante und fachlich wichtige Änderungen werden nachvollziehbar protokolliert.
10. Der Netzbetrieb erfolgt ausschließlich hinter einem gehärteten HTTPS-Reverse-Proxy.

## 2. Aktueller Stand

Die Anwendung besteht gegenwärtig im Wesentlichen aus:

- einer monolithischen `app.py` mit Routen, Geschäftslogik, Exporten und Dateioperationen,
- SQLAlchemy-Modellen in `models.py`,
- WTForms-Formularen in `forms.py`,
- serverseitigen Jinja-Templates,
- einer SQLite-Datenbank unter `instance/database.db`,
- personenbezogenen PDF-Dateien im Projektordner `uploads`,
- Gunicorn als Anwendungsserver.

Festgestellte Risiken, die vor einer Internetfreigabe behoben werden müssen:

- fest im Quelltext hinterlegter Flask-Secret-Key,
- sehr weitreichende Rechte für beliebige angemeldete Benutzer,
- Dokumentabruf ohne dokumentbezogene Berechtigungsprüfung,
- Dateinamen mit Schüler-ID und ursprünglichem Namen,
- fehlende zentrale Upload-Größen- und Inhaltskontrolle,
- fehlende Anmeldebegrenzung und Mehrfaktoroption für interne Konten,
- keine geregelten Datenbankmigrationen,
- praktisch keine automatisierten Tests,
- interne Fehlerdetails können in Rückmeldungen erscheinen,
- externe CDN-Abhängigkeiten für Bootstrap und Icons,
- keine dokumentierte Aufbewahrungs-, Lösch- oder Backupstrategie,
- keine zentrale Auditierung.

## 3. Zielarchitektur

### 3.1 Anwendungsschnitt

Die Anwendung bleibt zunächst ein modularer Monolith. Das ist für Teamgröße und Fachdomäne einfacher zu betreiben als verteilte Dienste, erlaubt aber klare Grenzen.

```text
Browser intern ──HTTPS──┐
                       ├── Reverse Proxy ── Flask/Gunicorn ── PostgreSQL
Browser Eltern ──HTTPS─┘                         │
                                                ├── geschützter Dokumentenspeicher
                                                ├── Mail-Provider
                                                └── Hintergrundaufträge
```

Empfohlene Paketstruktur:

```text
sl_office/
  __init__.py              # Application Factory
  config.py                # Development/Test/Production
  extensions.py            # db, login, csrf, limiter, migrations
  auth/                     # interne Anmeldung und Kontosicherheit
  students/                 # Schülerstammdaten
  diagnostics/              # Diagnostik, Schulspiel, Kita, Schularzt
  procedures/               # AO-SF und Rückstellung
  classes/                  # Klassen, Betreuung, Förderkurse, Freunde
  appointments/             # Terminplanung und Buchung
  admissions/               # digitale Anmeldung und interne Prüfung
  parent_portal/            # Aktivierung, Elternsitzung, Statusansicht
  documents/                # Upload, Download, Prüfung, Metadaten
  notifications/            # E-Mail-Vorlagen und Versandaufträge
  audit/                    # Audit-Ereignisse
  exports/                  # PDF- und Excel-Erzeugung
  templates/
  static/
migrations/
tests/
```

Blueprints dürfen nicht direkt in fremde Tabellen schreiben. Fachliche Änderungen laufen über Services, die Validierung, Berechtigungen, Transaktionen und Audit-Einträge bündeln. Datenbankmodelle enthalten Beziehungen und lokale Invarianten, jedoch keine Request- oder E-Mail-Logik.

### 3.2 Technische Komponenten

- **Flask Application Factory:** ermöglicht getrennte Konfigurationen und isolierte Tests.
- **SQLAlchemy + Alembic/Flask-Migrate:** versionierte Schemaänderungen.
- **PostgreSQL im Netzbetrieb:** bessere Nebenläufigkeit, Constraints, Sicherung und Betriebskontrolle als SQLite. SQLite darf lokal während der Übergangsphase bestehen bleiben.
- **Serverseitige Templates:** für das Elternportal ausreichend und sicher überschaubar; keine SPA ist erforderlich.
- **Hintergrundaufträge:** Mailversand und größere Exporte dürfen Requests nicht blockieren. Zu Beginn reicht eine transaktionale Outbox mit separatem Worker; später ist ein Queue-System möglich.
- **Geschützter Dateispeicher:** lokales, nicht öffentliches Volume oder S3-kompatibler Speicher. Der Datenbankeintrag ist maßgeblich, nicht der ursprüngliche Dateiname.

## 4. Fachliches Datenmodell

Alle Tabellen erhalten mindestens eine technische ID, Erstellungs- und Änderungszeit. Änderbare Kerndaten erhalten bei Bedarf eine Versionsnummer für konkurrierende Bearbeitungen.

### 4.1 Bestehender Kern

- `Student`: interne Schülerakte und schulisch geprüfte Stammdaten
- `InternalUser`, `Role`, `Permission`: interne Konten und Berechtigungen
- Diagnostik-, Schulspiel-, Kita-, Schularzt-, AO-SF- und Rückstellungsdatensätze
- Klassen-, Betreuungs-, Förderkurs- und Freundschaftszuordnungen
- `SchoolYear` statt globaler, nur einmal vorhandener Einstellung

Bestehende Status-Strings werden schrittweise in definierte Statuswerte mit geprüften Übergängen überführt. One-to-one-Beziehungen erhalten eindeutige Datenbank-Constraints auf `student_id`.

### 4.2 Elternzugang

#### `ParentAccess`

- Zuordnung zu genau einem Kind
- normalisierte, bestätigte E-Mail-Adresse
- Anzeigename der zugreifenden Person
- Status: `pending`, `active`, `locked`, `revoked`
- Zeitpunkt der E-Mail-Bestätigung und letzten Nutzung
- fortlaufende Sicherheitsversion zum Widerruf aller vorhandenen Sitzungen
- optionaler Bezug zu einer im Formular erfassten Person

Invariante: maximal zwei nicht widerrufene Elternzugänge pro Kind. Diese Regel wird im Service und, soweit möglich, durch Datenbanklogik abgesichert.

#### `ActivationGrant`

- Zuordnung zum Kind
- ausschließlich gehashter Aktivierungscode
- Ablaufzeit, Nutzungszahl und Widerrufszeitpunkt
- Zweck, etwa Erstaktivierung oder zweiter Zugang
- Ersteller und Erstellungszeit

Der Brief enthält mindestens 128 Bit kryptografisch zufällige Entropie, als QR-Code und manuell eingebbaren Code. Der Klartext wird nach der Ausgabe nicht gespeichert und nicht protokolliert. Ein Grant ist zeitlich begrenzt und nach erfolgreicher Aktivierung verbraucht.

#### `ParentLoginToken`

- ausschließlich gehashter, einmal verwendbarer Token
- Elternzugang, Zweck, Ablaufzeit und Verbrauchszeit

Ein per E-Mail versandter Anmeldelink ist kurzlebig und einmalig. Nach Einlösung entsteht eine sichere, zeitlich begrenzte Sitzung. Eine E-Mail-Adresse kann nur verwendet werden, wenn sie zuvor über den Briefcode aktiviert oder intern verifiziert wurde. Wiederanfragen liefern immer dieselbe neutrale Antwort, damit keine Adressen oder Kinderkonten ermittelt werden können.

### 4.3 Terminmodell

#### `AppointmentEvent`

Rahmen einer Terminaktion, beispielsweise „Schulanmeldung 2027/2028“, mit Buchungsbeginn, Buchungsende, Stornierungsfrist, Zeitzone und Elternhinweisen.

#### `AppointmentResource`

Parallel nutzbarer Platz, Raum oder Mitarbeitenden-Slot. Ressourcen können intern benannt werden; Eltern sehen nur freigegebene Bezeichnungen.

#### `AppointmentSlot`

- Veranstaltung, Ressource, Beginn und Ende
- Kapazität, Status und optionaler Ort
- Sperrgrund nur intern

Mehrere Ressourcen oder eine Kapazität größer eins ermöglichen parallele Terminfenster. Überlappungen derselben Ressource werden verhindert, außer sie sind ausdrücklich als Kapazitätsmodell vorgesehen.

#### `AppointmentBooking`

- Kind, Slot und buchender Elternzugang
- Status: `reserved`, `confirmed`, `cancelled`, `attended`, `no_show`
- Buchungs-, Änderungs- und Stornierungszeitpunkte
- optionaler interner Kommentar

Ein Kind hat innerhalb einer Veranstaltung höchstens eine aktive Buchung. Die Vergabe erfolgt atomar in einer Datenbanktransaktion; Kapazität wird nie nur in der Oberfläche geprüft. Eine sehr kurze Reservierung während des Abschlusses ist möglich, muss aber automatisch ablaufen.

### 4.4 Digitale Schulanmeldung

#### `AdmissionApplication`

- Zuordnung zum Kind und Schuljahr
- Status: `draft`, `submitted`, `under_review`, `changes_requested`, `accepted`, `rejected`, `withdrawn`
- Formularversion
- Einreichungs-, Prüf- und Übernahmezeitpunkte
- Bearbeiter sowie Version für Konflikterkennung

Pro Schuljahr existiert höchstens eine aktive Anmeldung je Kind. Nach Einreichung wird die Version unveränderlich eingefroren. Korrekturen erzeugen eine neue Version oder einen gezielten Änderungsauftrag; sie überschreiben nicht unbemerkt die eingereichte Fassung.

#### Inhaltliche Teilbereiche

- `AdmissionChildData`: Name, Geburtsdatum/-ort, Adresse, Staatsangehörigkeit, Mutter- und Familiensprache, Geschlecht
- `AdmissionGuardian`: beliebig viele erfasste Personen mit Anschrift, Sprachen, Zuzugsjahr, Geburtsland, Kontaktdaten und Sorgeberechtigungsangabe
- `AdmissionEmergencyContact`: Notfallnummern und Kontaktbezug
- `AdmissionHealthData`: chronische Erkrankungen, Allergien und Unverträglichkeiten; besonders restriktiv berechtigt
- `AdmissionDaycare`: Kita, Freitext-Kita und Besuchszeitraum
- `AdmissionReligion`: Konfession und Abmeldung vom Religionsunterricht
- `AdmissionDeclaration`: bestätigte Erklärungen, Datenschutzinformation, Formularversion und Zeitpunkte

Die aktuelle PDF bleibt zunächst das verbindlich zu unterschreibende Dokument. Aus den geprüften Daten kann für den Vor-Ort-Termin eine vorausgefüllte PDF erzeugt werden. Ort, Datum, Unterschriften und geprüfte Nachweise werden intern dokumentiert.

### 4.5 Elternstatus

#### `PortalStatusUpdate`

- Zuordnung zum Kind beziehungsweise zur Anmeldung
- öffentlicher Statuscode
- freigegebener Text aus kontrollierter Vorlage
- Veröffentlichungs- und optionales Ablaufdatum
- veröffentlichender interner Nutzer

Es gibt keine automatische Ausgabe interner Freitexte. Zulässige öffentliche Zustände sind beispielsweise:

- Anmeldung noch nicht begonnen
- Formular in Bearbeitung
- Formular eingereicht
- Termin gebucht
- persönliche Anmeldung erfolgt
- Unterlagen werden geprüft
- Rückmeldung oder Ergänzung erforderlich
- Verfahren abgeschlossen

Diagnostikwerte, AO-SF-Informationen, medizinische Einzelheiten, Freundschafts- und Negativwünsche sowie interne Bemerkungen sind niemals Teil dieser Projektion.

### 4.6 Dokumente, Benachrichtigungen und Audit

#### `Document`

- zufälliger Speicherbezeichner ohne Personenbezug
- ursprünglicher Dateiname nur als Metadatum
- Besitzerobjekt und Dokumenttyp
- MIME-Typ, Größe, kryptografischer Hash
- Prüfstatus, Ersteller und Aufbewahrungsfrist

#### `Notification`

- Empfänger, Vorlagenschlüssel, Status und Versandzeitpunkte
- keine Zugangstoken oder unnötigen Gesundheitsdaten im dauerhaft gespeicherten Inhalt
- idempotenter Versand zur Vermeidung doppelter Nachrichten

#### `AuditEvent`

- Zeitpunkt, Akteurstyp und pseudonymisierte Akteur-ID
- Aktion, betroffenes Objekt und Ergebnis
- Request-Korrelations-ID
- sicherheitsrelevante Metadaten, aber keine Formularinhalte, Tokens oder Passwörter

Audit-Einträge werden mindestens für Anmeldung, fehlgeschlagene Zugriffe, Aktivierung, Tokenwiderruf, Dokumentabruf, Export, Statusfreigabe, Terminänderung, Einreichung, Prüfung und Löschung erzeugt.

## 5. Rollen und Berechtigungen

Die bestehenden Rollen bleiben zunächst erhalten. Rechte werden als benannte Berechtigungen zentral definiert und nicht in einzelnen Routen als lose Rollenlisten verteilt.

| Bereich | Administration | Schulleitung | Sekretariat | Förderlehrkraft | Elternzugang |
|---|---:|---:|---:|---:|---:|
| Benutzer und Rollen verwalten | ja | nein | nein | nein | nein |
| Systemeinstellungen | ja | eingeschränkt | nein | nein | nein |
| Schülerstammdaten lesen | ja | ja | ja | erforderlich | eigenes Kind, Portalansicht |
| Schülerstammdaten ändern | ja | ja | ja | nein | nur Anmeldungsentwurf |
| Anmeldungen prüfen/übernehmen | ja | ja | ja | nein | einreichen/einsehen |
| Termine administrieren | ja | ja | ja | optional lesend | freie Slots/eigene Buchung |
| Diagnostik bearbeiten | ja | ja | nein | ja | nein |
| Gesundheitsdaten lesen | ja | ja | erforderlich | erforderlich | eigene eingereichte Angaben |
| AO-SF/Rückstellung bearbeiten | ja | ja | definierte Teilrechte | definierte Teilrechte | nur freigegebener Status |
| Klassenbildung bearbeiten | ja | ja | optional | optional | nein |
| sensible Dokumente abrufen | nach Dokumenttyp | nach Dokumenttyp | nach Dokumenttyp | nach Dokumenttyp | nur ausdrücklich freigegebene eigene Dokumente |
| Gesamtexporte | ja | ja | explizit | nein | nein |
| Audit einsehen | ja | eingeschränkt | nein | nein | nein |

„Erforderlich“ und „optional“ müssen vor Implementierung je Arbeitsablauf mit der Schule festgelegt werden. Standard ist kein Zugriff. Exporte erhalten eigene Rechte, da sie große Mengen personenbezogener Daten offenlegen.

## 6. Sicherheitskonzept

### 6.1 Interne Authentifizierung

- Passwörter mit aktuellem, konfigurierbarem Passwort-Hashverfahren; bestehende Hashes werden bei erfolgreicher Anmeldung transparent angehoben.
- Mindestlänge und Sperre bekannter kompromittierter Passwörter statt komplizierter Zeichenvorschriften.
- generische Fehlermeldungen und Rate-Limits pro Konto und Quelladresse.
- temporäre Sperrung mit sicherem Wiederherstellungsprozess.
- verpflichtende Mehrfaktorauthentifizierung mindestens für Administration und Schulleitung; TOTP oder WebAuthn, keine SMS als bevorzugter Faktor.
- Sitzungswechsel nach Anmeldung und Rechteänderung; globaler Sitzungswiderruf möglich.
- keine Standardkonten oder Initialpasswörter im Deployment.

### 6.2 Elternauthentifizierung

- Briefcode und E-Mail-Token werden mit `secrets` erzeugt und nur gehasht gespeichert.
- Aktivierungs- und Anmeldelinks sind einmalig, kurzlebig und widerrufbar.
- Browser-Sitzungen verwenden `Secure`, `HttpOnly` und einen angemessenen `SameSite`-Wert.
- Sensible Änderungen wie zweite E-Mail-Adresse, neue Zugangsaktivierung oder Datenübermittlung verlangen eine frische Bestätigung.
- Maximal zwei aktive Zugänge werden transaktional durchgesetzt.
- Bei vermutetem Missbrauch kann die Schule einzelne oder alle Elternzugänge eines Kindes widerrufen.
- E-Mail-Wiederherstellung offenbart nicht, ob eine Adresse vorhanden ist.
- Zugangslinks werden weder in Analysewerkzeuge noch in Referrer, Proxy-Logs oder Audit-Metadaten übernommen. Nach Einlösung erfolgt eine Weiterleitung auf eine tokenfreie URL.

### 6.3 Webschutz

- HTTPS mit HSTS; HTTP wird dauerhaft umgeleitet.
- CSRF-Schutz für alle zustandsändernden Browseranfragen, einschließlich JSON-Endpunkten.
- sichere Session-Cookies, kurze Inaktivitätszeit und absolute Sitzungsdauer.
- restriktive Content-Security-Policy; Bootstrap und Icons werden lokal ausgeliefert.
- Header mindestens: `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, geeignete `Permissions-Policy`, Frame-Schutz über CSP.
- keine offenen Weiterleitungen; Redirect-Ziele werden als interne Pfade validiert.
- Request-Größenlimits und Timeouts auf Proxy- und Anwendungsebene.
- sichere Fehlerseiten ohne Stacktraces, SQL- oder Dateipfade; Details nur im geschützten Serverlog.
- Rate-Limits für Login, Aktivierung, Linkanforderung, Terminbuchung und Formulareinreichung.

### 6.4 Datei- und Export-Sicherheit

- Uploads liegen außerhalb des Quellbaums und sind nicht direkt durch den Webserver erreichbar.
- erlaubte Dokumenttypen werden per Dateisignatur und MIME-Erkennung geprüft, nicht nur anhand der Endung.
- Größen- und Seitenlimits; aktive Inhalte und ungewöhnliche PDF-Strukturen werden abgewiesen oder quarantänisiert.
- Virenprüfung vor Freigabe, sobald Eltern selbst Dateien hochladen dürfen.
- zufällige Speichernamen; Pfade werden nie aus Nutzereingaben zusammengesetzt.
- Download prüft Objektberechtigung und Dokumenttyp und setzt `Content-Disposition`, `nosniff` sowie private Cache-Regeln.
- Excel-/CSV-Exporte schützen vor Formelinjektion.
- PDF-Erzeugung escaped oder validiert alle dynamischen Inhalte.
- Export und Massendownload werden auditiert.

### 6.5 Datenschutz und Datenminimierung

- Vor Produktivstart sind Zweck, Rechtsgrundlage, Informationspflichten, Aufbewahrungsfristen und Betroffenenprozesse mit dem Datenschutzbeauftragten festzulegen.
- Gesundheitsdaten und Angaben zum Sorgerecht erhalten gesonderte Berechtigungen und Protokollierung.
- E-Mails enthalten keine sensiblen Formularinhalte; sie verweisen nur auf das Portal.
- Nicht mehr benötigte Entwürfe, abgelaufene Tokens und temporäre Dateien werden automatisiert gelöscht.
- Protokolle enthalten keine Passwörter, Tokens, vollständigen Formulardaten oder Dokumente.
- Produktivdaten dürfen nicht ungefiltert in Entwicklung oder Tests kopiert werden.
- Auskunft, Berichtigung, Sperrung und regelkonforme Löschung werden als administrierte Abläufe vorgesehen.

### 6.6 Betrieb

- Geheimnisse ausschließlich über geschützte Umgebungs-/Secret-Konfiguration; Schlüsselrotation ist dokumentiert.
- separate Datenbankrolle mit minimalen Rechten.
- verschlüsselte, automatisierte Backups von Datenbank und Dokumenten; regelmäßige Wiederherstellungstests.
- Sicherheitsupdates mit festem Rhythmus und Abhängigkeitsprüfung.
- zentralisierte strukturierte Logs, Alarmierung bei Fehlerraten, Login-Auffälligkeiten und Versandproblemen.
- Health- und Readiness-Prüfungen ohne Ausgabe sensibler Details.
- Produktionsmodus ohne Flask-Debugger.
- Reverse Proxy mit TLS, Größenlimits, Timeouts und vertrauenswürdiger Proxy-Konfiguration.
- mindestens getrennte Entwicklungs-, Test- und Produktionsumgebungen.

## 7. Elternabläufe

### 7.1 Erster Zugang

1. Verwaltung legt das Kind an oder importiert es.
2. Das System erzeugt einen einmalig ausgebbaren Aktivierungsbrief mit QR- und Kurzcode.
3. Die Person öffnet den Link, gibt beziehungsweise bestätigt ihre E-Mail-Adresse und erhält eine Bestätigungsmail.
4. Nach Bestätigung entsteht Elternzugang 1 und eine sichere Sitzung.
5. Ein zweiter Grant kann durch die Schule oder nach einem kontrollierten ersten Prozess für Elternzugang 2 erstellt werden.
6. Jeder Zugang arbeitet unabhängig; Widerruf eines Zugangs beendet nicht automatisch den anderen.

### 7.2 Erneuter Zugang

1. Eltern geben auf einer neutralen Seite ihre E-Mail-Adresse an.
2. Die Oberfläche bestätigt die Anfrage unabhängig vom Kontobestand.
3. Bei vorhandener bestätigter Zuordnung wird ein kurzlebiger Einmallink versendet.
4. Nach Einlösung wird der Token verbraucht und aus der URL entfernt.

### 7.3 Anmeldung und Termin

1. Eltern vervollständigen das mehrseitige, mobil nutzbare Formular; Fortschritt wird gespeichert.
2. Pflichtfelder und Abhängigkeiten werden serverseitig validiert.
3. Eltern sehen eine vollständige Zusammenfassung und bestätigen Erklärungen.
4. Die Einreichung wird eingefroren und quittiert.
5. Ein freier Termin wird atomar gebucht; Änderung und Stornierung folgen den Fristen.
6. Intern erscheint die Anmeldung in einer Prüfliste mit Feld-für-Feld-Übernahme.
7. Rückfragen werden als strukturierter Änderungsauftrag gestellt.
8. Beim Termin werden Identität, Nachweise und Unterschriften geprüft.
9. Nur ein bewusst freigegebener Status wird im Portal angezeigt.

Die konkrete Reihenfolge „erst Termin, dann Formular“ wird unterstützt. Technisch sollten beide Bereiche nach Aktivierung zugänglich bleiben, damit das Formular später vervollständigt werden kann.

### 7.4 Gleichzeitige Bearbeitung durch zwei Elternteile

Zwei Zugänge dürfen nicht unbemerkt dieselbe Fassung überschreiben. Jede Speicherung übermittelt eine Versionsnummer. Bei einem Konflikt wird die neuere Fassung angezeigt und eine bewusste Zusammenführung verlangt. Nach Einreichung sind direkte Änderungen gesperrt; Ergänzungen erfolgen über einen neuen Entwurf oder Änderungsauftrag.

## 8. Migration ohne Datenverlust

### Phase A: Inventur und Sicherung

- verbindlich bestimmen, welche der beiden vorhandenen SQLite-Dateien produktiv ist,
- konsistente Sicherung von Datenbank und Uploads erstellen und testweise wiederherstellen,
- Schema, Datensatzanzahlen, verwaiste Dokumente und Dubletten automatisiert inventarisieren,
- sensible Beispieldaten für Tests anonymisieren,
- Quellcode in eine geregelte Versionsverwaltung aufnehmen.

### Phase B: Sicherheitsfundament im Bestand

- Application Factory und umgebungsbasierte Konfiguration,
- Secret-Key ersetzen und bestehende Sitzungen bewusst ungültig machen,
- CSRF, sichere Cookies, Security-Header, Größenlimits und zentrale Fehlerbehandlung,
- Berechtigungssystem und geschützte Dokumentauslieferung,
- strukturierte Logs und Audit-Grundlage,
- erste kritische Tests.

### Phase C: Modularisierung

- Modelle zunächst kompatibel übernehmen,
- Routen fachbereichsweise in Blueprints verschieben,
- Datei-, Export- und Geschäftslogik in Services extrahieren,
- Verhalten mit Charakterisierungstests absichern,
- Alembic-Baseline auf dem bestehenden Schema einführen.

### Phase D: Neue Datenbasis

- neue Termin-, Elternportal-, Anmelde-, Dokument- und Audit-Tabellen ergänzen,
- Constraints und Indizes anlegen,
- bestehende Dateien in geschützten Speicher migrieren,
- optional kontrollierte Migration von SQLite nach PostgreSQL mit Summen- und Hashvergleich.

### Phase E: Neue Funktionen

1. interne Terminverwaltung,
2. Aktivierungsbrief und Elternzugänge,
3. Terminbuchung,
4. digitales Formular und Zwischenspeicherung,
5. Einreichungs- und Prüfworkflow,
6. öffentliche Statusprojektion,
7. vorausgefüllte Anmelde-PDF,
8. E-Mail-Outbox und Betriebsüberwachung.

### Phase F: Internetfreigabe

- unabhängige Sicherheitsprüfung der exponierten Oberflächen,
- Berechtigungstests jeder Rolle und jedes Dokumenttyps,
- Last- und Nebenläufigkeitstest der Terminbuchung,
- Backup-Wiederherstellung und Notfallablauf testen,
- Datenschutzfreigabe, Auftragsverarbeitung und Löschkonzept abschließen,
- gestufter Pilot mit wenigen Einladungen, anschließend allgemeine Freigabe.

Jede Phase muss rückrollbar sein. Datenmigrationen werden zuerst an einer Kopie durchgeführt; irreversible Schritte benötigen eine geprüfte Sicherung und ein Abnahmeprotokoll.

## 9. Teststrategie und Abnahmekriterien

### 9.1 Automatisierte Tests

- Unit-Tests für Statusübergänge, Kapazitäten, Fristen und Berechtigungen
- Integrations-Tests für Datenbanktransaktionen, Uploads, Mail-Outbox und Exporte
- Request-Tests für jede Route als anonym, jede interne Rolle und beide Elternzugänge
- Negativtests für fremde Kinder, fremde Dokumente, abgelaufene/wiederverwendete Tokens und CSRF
- Nebenläufigkeitstest: letzter Terminplatz kann nur einmal vergeben werden
- Migrationstests auf einer anonymisierten Kopie des bestehenden Schemas
- Browser-End-to-End-Tests für Aktivierung, Formular, Buchung, Prüfung und Status

### 9.2 Mindestabnahme vor Netzbetrieb

- kein Geheimnis und kein Standardpasswort im Repository,
- keine sensible Route ohne explizite Berechtigung,
- Elternzugang kann ausschließlich das zugeordnete Kind sehen,
- zweiter Zugang möglich, dritter wird auch bei Parallelaufrufen verhindert,
- verbrauchter, abgelaufener oder widerrufener Token ist wirkungslos,
- doppelte Überbuchung ist technisch ausgeschlossen,
- Dokumente sind nicht über erratbare Pfade erreichbar,
- interne Felder erscheinen in keiner Elternantwort oder Fehlermeldung,
- Backup lässt sich in einer leeren Umgebung wiederherstellen,
- zentrale Abläufe sind automatisiert getestet und dokumentiert.

## 10. Noch fachlich zu entscheiden

Diese Entscheidungen blockieren das Sicherheitsfundament nicht, müssen aber vor dem jeweiligen Modul feststehen:

1. Welche konkreten Teilrechte benötigen Sekretariat und Förderlehrkräfte für Gesundheits-, AO-SF- und Rückstellungsdaten?
2. Wer darf den zweiten Elternzugang erzeugen: ausschließlich die Schule oder auch Zugang 1 mit nachgelagerter Prüfung?
3. Dürfen beide Elternzugänge alle eingereichten Daten sehen, insbesondere Kontaktdaten und Gesundheitsangaben der jeweils anderen Person?
4. Bis wann dürfen Eltern Termine selbst ändern oder stornieren?
5. Welche Statusmeldungen und Rückfragen dürfen per E-Mail angekündigt werden?
6. Welche Daten und Dokumente sind wie lange aufzubewahren?
7. Soll die papiergebundene Unterschrift dauerhaft maßgeblich bleiben oder später ein rechtlich geprüftes digitales Verfahren folgen?
8. Soll ein Termin eine Ressource mit Kapazität oder mehrere namentlich getrennte Ressourcen anzeigen?

Bis zur Entscheidung gelten die datenschutzfreundlichen Standardannahmen: Schule aktiviert Zugang 2, Eltern sehen keine privaten Daten des jeweils anderen Zugangs, Änderungen nach Einreichung benötigen interne Freigabe, und E-Mails enthalten keine Fachdaten.

## 11. Unmittelbar nächster Umsetzungsschritt

Als erstes Umsetzungspaket wird ausschließlich das Fundament bearbeitet:

1. reproduzierbare Tests und Bestandsinventur,
2. Application Factory und Konfigurationsklassen,
3. Umgebungsvariablen und Secret-Prüfung,
4. Alembic-Baseline,
5. zentrales Berechtigungsgerüst,
6. geschützter Dokumentservice,
7. Security-Header, Cookie- und Request-Limits,
8. Charakterisierungstests für die wichtigsten bestehenden Abläufe.

Erst wenn dieses Paket auf einer gesicherten Datenkopie geprüft ist, beginnt die fachliche Implementierung von Terminverwaltung und Elternportal.
