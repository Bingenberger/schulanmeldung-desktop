"""SL-Office als eigenständige Anwendung starten.

Startet einen Webserver nur für diesen Rechner (127.0.0.1), bringt die
Datenbank auf den neuesten Stand, legt einmal am Tag eine Sicherung an und
öffnet die Oberfläche im Browser. Ein kleines Fenster zeigt, dass SL-Office
läuft, und beendet es wieder.

Auf Wunsch -- Häkchen im Fenster oder ``SL_OFFICE_NETZWERK=1`` -- lauscht der
Server stattdessen auf allen Schnittstellen, und andere Rechner im lokalen
Netz erreichen SL-Office über die Adresse dieses Rechners.

    python desktop.py                 # Entwicklung
    SL-Office.exe                     # gebaute Windows-Fassung (siehe packaging/)
    SL-Office.app                     # gebaute macOS-Fassung

Wird SL-Office ein zweites Mal gestartet, öffnet sich nur der Browser auf der
schon laufenden Instanz.
"""

import datetime
import ipaddress
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
#: Darauf lauscht der Server, wenn SL-Office im lokalen Netz bereitsteht.
NETZ_HOST = "0.0.0.0"  # noqa: S104 -- nur auf ausdrücklichen Wunsch, siehe netzwerk_gewuenscht
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


def _einstellungsdatei(data_dir):
    return data_dir / "einstellungen.json"


def netzwerk_festgelegt():
    """``True``/``False``, wenn ``SL_OFFICE_NETZWERK`` die Wahl vorgibt, sonst ``None``."""
    wert = os.getenv("SL_OFFICE_NETZWERK")
    if wert is None or not wert.strip():
        return None
    return wert.strip().lower() in {"1", "true", "yes", "on", "ja"}


def netzwerk_gewuenscht(data_dir):
    """Soll SL-Office auch von anderen Rechnern im lokalen Netz erreichbar sein?"""
    festgelegt = netzwerk_festgelegt()
    if festgelegt is not None:
        return festgelegt
    try:
        daten = json.loads(_einstellungsdatei(data_dir).read_text(encoding="utf-8"))
        return daten.get("netzwerk") is True
    except (OSError, ValueError, AttributeError):
        return False


def netzwerk_merken(data_dir, an):
    """Die Wahl aus dem Steuerfenster für den nächsten Start festhalten."""
    _einstellungsdatei(data_dir).write_text(json.dumps({"netzwerk": bool(an)}), encoding="utf-8")


def netzadresse():
    """Adresse dieses Rechners im lokalen Netz, sonst ``None``."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            # Schickt nichts; das System wählt nur die Schnittstelle ins Netz aus.
            probe.connect(("10.255.255.255", 1))
            adresse = probe.getsockname()[0]
        except OSError:
            try:
                adresse = socket.gethostbyname(socket.gethostname())
            except OSError:
                return None
    return None if adresse.startswith("127.") else adresse


def aus_lokalem_netz(adresse):
    """Kommt die Anfrage von diesem Rechner oder aus einem privaten Netz?"""
    try:
        return ipaddress.ip_address(adresse).is_private
    except ValueError:
        return False


def nur_lokales_netz(wsgi_app):
    """Anfragen von außerhalb privater Netze abweisen.

    Im Netzbetrieb lauscht der Server auf allen Schnittstellen; hängt der
    Rechner zugleich direkt am Internet, bleibt SL-Office von dort verschlossen.
    """
    def anwendung(environ, start_response):
        if not aus_lokalem_netz(environ.get("REMOTE_ADDR", "")):
            start_response("403 Forbidden", [("Content-Type", "text/plain; charset=utf-8")])
            return ["SL-Office ist nur im lokalen Netz erreichbar.".encode("utf-8")]
        return wsgi_app(environ, start_response)
    return anwendung


def freier_port(host=HOST):
    for port in PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
            except OSError:
                continue
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
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


def server_starten(app, port, netzwerk=False):
    from waitress import create_server

    server = create_server(app, host=NETZ_HOST if netzwerk else HOST, port=port, threads=8,
                           ident=APP_NAME)
    thread = threading.Thread(target=server.run, name="waitress", daemon=True)
    thread.start()
    return server


def server_anhalten(server):
    """Nicht mehr lauschen, offene Verbindungen schließen, Arbeitsfäden beenden.

    Abgebaut wird im Faden des Servers selbst: von außen geschlossene Sockets
    brächten dessen Schleife durcheinander. Danach ist der Port sofort wieder
    frei -- nötig, um zwischen diesem Rechner und dem lokalen Netz umzuschalten.
    """
    from waitress import wasyncore

    fertig = threading.Event()

    def abbauen():
        try:
            wasyncore.close_all(server._map, ignore_all=True)
        finally:
            fertig.set()

    server.trigger.pull_trigger(abbauen)
    fertig.wait(5)
    server.task_dispatcher.shutdown()


def _warten_bis_bereit(url, sekunden=15):
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        try:
            with urllib.request.urlopen(url + "/lebenszeichen", timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


NETZ_HINWEIS = (
    "Andere Rechner im selben Netz erreichen dann die Anmeldeseite von SL-Office. "
    "Die Verbindung ist nicht verschlüsselt.\n\n"
    "Bitte nur in einem vertrauenswürdigen Netz einschalten, etwa im Verwaltungsnetz "
    "der Schule -- nicht im Schüler- oder Gäste-WLAN. Fragt die Firewall des Rechners "
    "nach, muss der Zugriff für private Netze erlaubt werden."
)


def _adresszeilen(url, port, data_dir, netzwerk):
    zeilen = [f"Adresse: {url}"]
    if netzwerk:
        adresse = netzadresse()
        zeilen.append(f"Im Netz: http://{adresse}:{port}" if adresse
                      else "Im Netz: keine Netzwerkverbindung gefunden")
    zeilen.append(f"Daten: {data_dir}")
    return "\n".join(zeilen)


def fenster(url, port, data_dir, beenden, netzwerk, netzwerk_schalten):
    """Das kleine Steuerfenster. Ohne Tk (z. B. auf einem Server) wird gewartet.

    ``netzwerk_schalten(an)`` stellt den Server um und meldet, ob das gelang.
    """
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
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
    adressen = tk.StringVar(value=_adresszeilen(url, port, data_dir, netzwerk))
    ttk.Label(rahmen, textvariable=adressen, justify="left").grid(
        column=0, row=1, columnspan=3, sticky="w", pady=(4, 8))

    im_netz = tk.BooleanVar(value=netzwerk)

    def netz_umschalten():
        an = im_netz.get()
        if an and not messagebox.askokcancel(
                APP_NAME, "SL-Office im lokalen Netz bereitstellen?\n\n" + NETZ_HINWEIS,
                parent=root):
            im_netz.set(False)
            return
        if not netzwerk_schalten(an):
            im_netz.set(not an)
            messagebox.showerror(
                APP_NAME, "Die Umstellung ist nicht gelungen; SL-Office läuft wie bisher weiter. "
                          "Näheres steht im Protokoll (sl-office.log) im Datenordner.", parent=root)
            return
        adressen.set(_adresszeilen(url, port, data_dir, an))

    ttk.Checkbutton(
        rahmen, text="Im lokalen Netz bereitstellen", variable=im_netz, command=netz_umschalten,
        # Gibt die Umgebungsvariable die Wahl vor, gilt sie auch beim nächsten Start.
        state="disabled" if netzwerk_festgelegt() is not None else "normal",
    ).grid(column=0, row=2, columnspan=3, sticky="w", pady=(0, 12))

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
        column=0, row=3, padx=(0, 6))
    ttk.Button(rahmen, text="Datenordner", command=ordner_oeffnen).grid(column=1, row=3, padx=6)
    ttk.Button(rahmen, text="Beenden", command=schliessen).grid(column=2, row=3, padx=(6, 0))
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

    app.wsgi_app = nur_lokales_netz(app.wsgi_app)
    netzwerk = netzwerk_gewuenscht(data_dir)
    port = freier_port(NETZ_HOST if netzwerk else HOST)
    url = f"http://{HOST}:{port}"
    try:
        server = server_starten(app, port, netzwerk)
    except OSError:      # die Netzfreigabe darf den Start nicht verhindern
        if not netzwerk:
            raise
        log.exception("Start im lokalen Netz fehlgeschlagen; SL-Office läuft nur auf diesem Rechner")
        netzwerk = False
        server = server_starten(app, port)
    laufend = {"server": server, "netzwerk": netzwerk}
    _instanzdatei(data_dir).write_text(json.dumps({"port": port, "pid": os.getpid()}),
                                       encoding="utf-8")
    log.info("SL-Office gestartet unter %s, Daten in %s", url, data_dir)
    if netzwerk:
        log.info("Im lokalen Netz bereitgestellt unter http://%s:%s", netzadresse() or "?", port)

    def netzwerk_schalten(an):
        """Den Server auf demselben Port neu starten; Anmeldungen bleiben bestehen."""
        server_anhalten(laufend["server"])
        try:
            laufend["server"] = server_starten(app, port, an)
        except OSError:
            log.exception("Umstellung der Netzfreigabe fehlgeschlagen")
            laufend["server"] = server_starten(app, port, laufend["netzwerk"])
            return False
        laufend["netzwerk"] = an
        netzwerk_merken(data_dir, an)
        if an:
            log.info("Im lokalen Netz bereitgestellt unter http://%s:%s", netzadresse() or "?", port)
        else:
            log.info("Netzfreigabe beendet; SL-Office läuft nur auf diesem Rechner")
        return True

    def beenden():
        log.info("SL-Office wird beendet")
        try:
            _instanzdatei(data_dir).unlink(missing_ok=True)
        finally:
            server_anhalten(laufend["server"])

    if _warten_bis_bereit(url):
        webbrowser.open(url)
    fenster(url, port, str(data_dir), beenden, netzwerk, netzwerk_schalten)
    return 0


if __name__ == "__main__":
    sys.exit(main())
