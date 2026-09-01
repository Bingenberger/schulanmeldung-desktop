# Betrieb und Deployment

Die Anwendung läuft als systemd-Dienst hinter nginx. Änderungen kommen per
`git push` auf den Server.

```
Browser ──HTTPS──▶ nginx ──HTTP──▶ gunicorn (127.0.0.1:5000) ──▶ SL-Office
                                                                    │
Arbeitsrechner ──git push──▶ ~/sl-office.git ──post-receive──▶ Arbeitsverzeichnis
```

| Datei | Zweck |
| --- | --- |
| `sl-office.service` | Dienstdefinition, wird nach `/etc/systemd/system/` kopiert |
| `sl-office-reminders.service` / `.timer` | stündlicher Lauf der Terminerinnerungen |
| `deploy/nginx/sl-office.conf` | Vorlage für den nginx-Server-Block |
| `deploy/sudoers-sl-office` | erlaubt dem Deployment den Neustart des Dienstes |
| `deploy/setup-git-deploy.sh` | legt das nackte Repository an und installiert den Hook |
| `deploy/post-receive` | der Hook selbst: Dateien, Migrationen, Tests, Neustart |
| `.env` | Zugangsdaten und Betriebsart, **nicht** im Repository |

## 1. Konfiguration: `.env`

`.env.example` als Vorlage kopieren und ausfüllen. Erst diese Datei schaltet
die Anwendung in den Produktionsbetrieb; ohne sie läuft sie im
Entwicklungsprofil mit unsicheren Vorgaben.

```bash
cp .env.example .env
chmod 600 .env
openssl rand -hex 32          # Ergebnis als SL_OFFICE_SECRET_KEY eintragen
```

Drei Werte sind für den Betrieb hinter nginx entscheidend:

```ini
SL_OFFICE_ENV=production
SL_OFFICE_SECRET_KEY=<32+ zufällige Zeichen>
SL_OFFICE_DATABASE_URL=sqlite:////home/emrichschule/SL-Office/instance/database.db
# Ein vorgeschalteter Proxy: nur damit wertet die Anwendung X-Forwarded-* aus.
SL_OFFICE_TRUSTED_PROXIES=1
```

`SL_OFFICE_TRUSTED_PROXIES` ist nicht optional: Ohne diesen Wert hält die
Anwendung jede Anfrage für unverschlüsselt und baut die Aktivierungslinks der
Elternbriefe als `http://` mit dem internen Hostnamen. Steht die Anwendung
einmal *nicht* hinter einem Proxy, muss der Wert wieder auf `0` — sonst könnte
ein Client die Kopfzeilen selbst mitschicken und sich eine fremde
Absenderadresse geben.

## 2. nginx

```bash
sudo cp deploy/nginx/sl-office.conf /etc/nginx/sites-available/sl-office
sudo sed -i 's/<DOMAIN>/anmeldung.ggs-niederkassel.de/g' /etc/nginx/sites-available/sl-office
sudo ln -s /etc/nginx/sites-available/sl-office /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

Zertifikat danach mit certbot holen; der HTTP-Block hält
`/.well-known/acme-challenge/` dafür offen:

```bash
sudo certbot --nginx -d anmeldung.ggs-niederkassel.de
```

Zwei Punkte in der Vorlage lohnen einen Blick:

* **`client_max_body_size 12m`** muss zu `SL_OFFICE_MAX_UPLOAD_BYTES` (Vorgabe
  10 MB) passen. Ist der nginx-Wert kleiner, bricht nginx den Upload ab, bevor
  die Anwendung eine verständliche Meldung zeigen kann.
* **Sicherheits-Kopfzeilen setzt die Anwendung selbst** (`security.py`, mit
  HSTS im Produktionsprofil). In nginx gehören sie deshalb nicht noch einmal
  hinein, sonst werden sie doppelt ausgeliefert.

## 3. Dienst

```bash
sudo cp sl-office.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart sl-office
sudo systemctl status sl-office
```

Die Dienstdefinition bindet gunicorn an `127.0.0.1:5000`. Vorher war es
`0.0.0.0:5000`, die Anwendung war also unverschlüsselt aus dem ganzen Netz
erreichbar. **Deshalb zuerst nginx einrichten, dann den Dienst umstellen** —
danach ist der direkte Zugriff auf Port 5000 nicht mehr möglich.

## 4. Terminerinnerungen

Bucht eine Familie im Elternbereich einen Termin, verschickt die Anwendung
sofort zwei Mails: die Bestätigung mit Kalenderdatei an die Eltern und einen
Hinweis an die Schule. Beides passiert in der Anfrage selbst und braucht
nichts weiter als einen erreichbaren Mailserver.

Die Erinnerung 24 Stunden vor dem Termin dagegen hat keine Anfrage, an der sie
hängen könnte. Sie läuft als eigener Aufruf:

```bash
venv/bin/flask --app app appointment-reminders
```

Den startet ein Timer stündlich. Ein Scheduler innerhalb der Anwendung ginge
nicht: gunicorn arbeitet mit drei Prozessen, jeder würde denselben Termin
erinnern.

```bash
sudo cp sl-office-reminders.service sl-office-reminders.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sl-office-reminders.timer
systemctl list-timers sl-office-reminders.timer     # nächster Lauf
journalctl -u sl-office-reminders -n 20             # was zuletzt raus ging
```

Drei Werte in der `.env` steuern den Versand:

```ini
# Wer erfährt von neuen Buchungen? Leer = SL_OFFICE_SCHOOL_CONTACT_MAIL,
# beides leer = kein Hinweis an die Schule.
SL_OFFICE_NOTIFY_MAIL=sekretariat@example.org
# Vorlauf der Erinnerung in Stunden.
SL_OFFICE_REMINDER_HOURS=24
# Ohne Anfrage kennt der Erinnerungsdienst die eigene Adresse nicht; ohne
# diesen Wert enthält die Erinnerung keinen Link in den Elternbereich.
SL_OFFICE_PUBLIC_BASE_URL=https://anmeldung.example.org
```

Verschickt wird jede Erinnerung genau einmal: die Buchung merkt sich den
Zeitpunkt in `reminder_sent_at`. Wer kurzfristig bucht, bekommt nur die
Bestätigung — sie enthält dieselben Angaben. Termine, die die Schule ohne
Elternzugang vergeben hat, werden übersprungen und dabei als erledigt
vermerkt, damit sie nicht bei jedem Lauf erneut auftauchen. Ein verpasster
Lauf ist unkritisch: `Persistent=true` holt ihn nach, und die noch offenen
Termine stehen weiterhin in der Warteschlange.

## 5. Git-Deployment einrichten

Einmalig auf dem Server:

```bash
./deploy/setup-git-deploy.sh
sudo visudo -c -f deploy/sudoers-sl-office        # Syntax prüfen
sudo install -m 0440 deploy/sudoers-sl-office /etc/sudoers.d/sl-office
```

Das Skript legt `~/sl-office.git` an (ein nacktes Repository, ohne
Arbeitsverzeichnis) und installiert den Hook. Der sudoers-Eintrag erlaubt dem
Anwendungsbenutzer genau drei Befehle für genau diesen Dienst — kein
allgemeines sudo-Recht.

Auf dem Arbeitsrechner:

```bash
git clone ssh://emrichschule@<server>:/home/emrichschule/sl-office.git SL-Office
cd SL-Office
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env        # eigene Werte für die lokale Arbeit
```

## 6. Der Arbeitsablauf

```bash
git add -A
git commit -m "Was geändert wurde"
git push                    # bzw.: git push server main
```

Der Hook läuft dann auf dem Server durch:

1. **Dateien aktualisieren** – Auschecken ins Arbeitsverzeichnis. `git clean`
   entfernt dabei nicht mehr vorhandene Dateien. `uploads/`, `instance/`,
   `backups/`, `venv/` und `.env` sind ignoriert und bleiben unberührt; eine
   *nicht* ignorierte Datei, die nur auf dem Server liegt, wird gelöscht.
2. **Abhängigkeiten** aus `requirements.txt` nachziehen.
3. **Datenbank sichern** nach `backups/database_<Zeit>_vor_<Commit>.db` —
   über die SQLite-Backup-API, also auch bei laufendem Dienst konsistent. Die
   letzten 30 Sicherungen bleiben erhalten.
4. **Migrationen** ausführen (`flask db upgrade`).
5. **Tests** ausführen.
6. **Dienst neu starten** und prüfen, ob er läuft.

Schlägt ein Schritt fehl, bricht der Push mit Fehlermeldung ab. Der Neustart
kommt zuletzt: Scheitern Migrationen oder Tests, läuft der alte Stand weiter.
Die Dateien im Arbeitsverzeichnis sind dann allerdings schon aktualisiert —
der nächste Push mit der Korrektur bringt beides wieder zusammen.

Nur der Branch `main` wird ausgerollt. Andere Branches lassen sich also
gefahrlos auf den Server schieben.

## 7. Zurückrollen

```bash
# Auf dem Arbeitsrechner: den letzten Commit rückgängig machen und pushen
git revert HEAD
git push
```

Reicht das nicht, weil eine Migration Daten verändert hat, hilft die Sicherung
von Schritt 3:

```bash
sudo systemctl stop sl-office
cp backups/database_<Zeitpunkt>_vor_<Commit>.db instance/database.db
git --git-dir=$HOME/sl-office.git --work-tree=$PWD checkout -f <alter-commit>
sudo systemctl start sl-office
```

## 8. Nachsehen, wenn etwas klemmt

```bash
sudo systemctl status sl-office
sudo journalctl -u sl-office -n 100 --no-pager   # Anwendungs- und gunicorn-Log
sudo tail -f /var/log/nginx/sl-office.error.log
```

| Symptom | Übliche Ursache |
| --- | --- |
| 502 Bad Gateway | Dienst läuft nicht — `journalctl` ansehen |
| Links im Elternbrief sind `http://` oder haben den falschen Host | `SL_OFFICE_TRUSTED_PROXIES=1` fehlt in `.env` |
| 413 beim Hochladen | `client_max_body_size` kleiner als `SL_OFFICE_MAX_UPLOAD_BYTES` |
| Push bricht bei „Dienst neu starten" ab | sudoers-Eintrag fehlt |
| Push bricht bei „Migrationen" ab | siehe `docs/DATENBANKMIGRATIONEN.md` |
