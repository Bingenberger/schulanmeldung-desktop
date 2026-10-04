"""SL-Office als eigenständige Anwendung starten.

Startet einen Webserver nur für diesen Rechner (127.0.0.1), bringt die
Datenbank auf den neuesten Stand, legt einmal am Tag eine Sicherung an und
öffnet die Oberfläche im Browser. Ein kleines Fenster zeigt, dass SL-Office
läuft, und beendet es wieder.

    python desktop.py                 # Entwicklung
    SL-Office.exe                     # gebaute Windows-Fassung (siehe packaging/)
    SL-Office.app                     # gebaute macOS-Fassung

Wird SL-Office ein zweites Mal gestartet, öffnet sich nur der Browser auf der
schon laufenden Instanz.
"""

import datetime
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

os.environ.setdefault("SL_OFFICE_ENV", "desktop")

from config import desktop_data_dir  # noqa: E402

APP_NAME = "SL-Office"
HOST = "127.0.0.1"
PORTS = range(5050, 5060)
#: Antwort der Lebenszeichen-Adresse, an der eine laufende Instanz erkannt wird.
LEBENSZEICHEN = "SL-Office"
#: So viele tägliche Sicherungen bleiben stehen.
SICHERUNGEN_BEHALTEN = 14
AUTO_PREFIX = "auto-"

log = logging.getLogger("sl_office.desktop")


def resource_dir():
    """Ordner mit Vorlagen, statischen Dateien und Migrationen."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _logging_einrichten(data_dir):
    """Protokoll in den Datenordner; darf mehrfach aufgerufen werden.

    Alembic richtet beim Migrieren das Logging nach ``alembic.ini`` neu ein und
    schaltet dabei bestehende Logger ab -- danach wird hier nachgezogen.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, RotatingFileHandler):
            root.removeHandler(handler)
    handler = RotatingFileHandler(data_dir / "sl-office.log", maxBytes=1_000_000, backupCount=3,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    for name in ("sl_office", "sl_office.desktop", "waitress", "app"):
        logging.getLogger(name).disabled = False


def _instanzdatei(data_dir):
    return data_dir / "laufende-instanz.json"


def laufende_instanz(data_dir):
    """Adresse einer schon laufenden SL-Office-Instanz, sonst ``None``."""
    try:
        daten = json.loads(_instanzdatei(data_dir).read_text(encoding="utf-8"))
        url = f"http://{HOST}:{int(daten['port'])}"
        with urllib.request.urlopen(url + "/lebenszeichen", timeout=2) as antwort:
            if antwort.read().decode("utf-8", "replace").strip() == LEBENSZEICHEN:
                return url
    except (OSError, ValueError, KeyError):
        pass
    return None


def freier_port():
    for port in PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((HOST, port))
            except OSError:
                continue
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


def datenbank_aktualisieren(app):
    """Alle Migrationen einspielen; eine neue Installation bekommt so ihr Schema."""
    from flask_migrate import upgrade

    with app.app_context():
        upgrade(directory=str(resource_dir() / "migrations"))


def taegliche_sicherung(app):
    """Einmal am Tag beim Start die Datenbank sichern; ältere Sicherungen ausdünnen."""
    from sl_office.admin import backup_service

    heute = AUTO_PREFIX + datetime.date.today().isoformat()
    vorhandene = backup_service.list_backups(app)
    if any(eintrag["name"].startswith(heute) for eintrag in vorhandene):
        return None
    with app.app_context():
        ziel = backup_service.create_backup(app, include_uploads=True, prefix=AUTO_PREFIX)
    # Nur die automatischen ausdünnen; von Hand angelegte bleiben unberührt.
    automatisch = [e for e in backup_service.list_backups(app)
                   if e["name"].startswith(AUTO_PREFIX)]
    for alt in automatisch[SICHERUNGEN_BEHALTEN:]:
        try:
            backup_service.delete_backup(app, alt["name"])
        except backup_service.BackupError:
            continue
    return ziel


def server_starten(app, port):
    from waitress import create_server

    server = create_server(app, host=HOST, port=port, threads=8, ident=APP_NAME)
    thread = threading.Thread(target=server.run, name="waitress", daemon=True)
    thread.start()
    return server


def _warten_bis_bereit(url, sekunden=15):
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        try:
            with urllib.request.urlopen(url + "/lebenszeichen", timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def fenster(url, data_dir, beenden):
    """Das kleine Steuerfenster. Ohne Tk (z. B. auf einem Server) wird gewartet."""
    try:
        import tkinter as tk
        from tkinter import ttk
        root = tk.Tk()
    except Exception:      # kein Tk oder keine Anzeige, etwa auf einem Server
        log.info("Kein Fenster möglich; SL-Office läuft, bis der Prozess beendet wird.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            beenden()
        return

    root.title(APP_NAME)
    root.resizable(False, False)
    rahmen = ttk.Frame(root, padding=16)
    rahmen.grid()
    schrift = "Segoe UI" if os.name == "nt" else "TkDefaultFont"
    ttk.Label(rahmen, text="SL-Office läuft.", font=(schrift, 12, "bold")).grid(
        column=0, row=0, columnspan=3, sticky="w")
    ttk.Label(rahmen, text=f"Adresse: {url}\nDaten: {data_dir}", justify="left").grid(
        column=0, row=1, columnspan=3, sticky="w", pady=(4, 12))

    def ordner_oeffnen():
        if os.name == "nt":
            os.startfile(data_dir)  # noqa: S606 -- Windows-Explorer, fester Pfad
        elif sys.platform == "darwin":
            subprocess.Popen(["open", data_dir])  # noqa: S603, S607 -- Finder, fester Pfad
        else:
            webbrowser.open(Path(data_dir).as_uri())

    def schliessen():
        beenden()
        root.destroy()

    ttk.Button(rahmen, text="Im Browser öffnen", command=lambda: webbrowser.open(url)).grid(
        column=0, row=2, padx=(0, 6))
    ttk.Button(rahmen, text="Datenordner", command=ordner_oeffnen).grid(column=1, row=2, padx=6)
    ttk.Button(rahmen, text="Beenden", command=schliessen).grid(column=2, row=2, padx=(6, 0))
    root.protocol("WM_DELETE_WINDOW", schliessen)
    if sys.platform == "darwin":
        # Cmd+Q und "Beenden" im Dock sollen genauso aufräumen wie der Knopf.
        root.createcommand("::tk::mac::Quit", schliessen)
    root.mainloop()


def main():
    data_dir = desktop_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    _logging_einrichten(data_dir)

    url = laufende_instanz(data_dir)
    if url:
        log.info("SL-Office läuft bereits unter %s", url)
        webbrowser.open(url)
        return 0

    from app import app

    datenbank_aktualisieren(app)
    _logging_einrichten(data_dir)
    try:
        ziel = taegliche_sicherung(app)
        if ziel:
            log.info("Tägliche Sicherung angelegt: %s", ziel)
    except Exception:      # eine fehlgeschlagene Sicherung darf den Start nicht verhindern
        log.exception("Tägliche Sicherung fehlgeschlagen")

    port = freier_port()
    url = f"http://{HOST}:{port}"
    server = server_starten(app, port)
    _instanzdatei(data_dir).write_text(json.dumps({"port": port, "pid": os.getpid()}),
                                       encoding="utf-8")
    log.info("SL-Office gestartet unter %s, Daten in %s", url, data_dir)

    def beenden():
        log.info("SL-Office wird beendet")
        try:
            _instanzdatei(data_dir).unlink(missing_ok=True)
        finally:
            server.close()

    if _warten_bis_bereit(url):
        webbrowser.open(url)
    fenster(url, str(data_dir), beenden)
    return 0


if __name__ == "__main__":
    sys.exit(main())
