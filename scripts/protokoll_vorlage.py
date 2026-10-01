"""Die leere Druckvorlage ``Protokoll_Anmeldespiel.pdf`` aus der ODT erzeugen.

Das Protokoll wird wie die Schulanmeldung gedruckt: Die Vorlage liegt als
fertiges PDF im Projekt, die Werte legt :mod:`sl_office.appointments.protocol_pdf`
als zweite Ebene darüber. Dieses Skript stellt die Vorlage her und misst
zugleich nach, wo die Seriendruckfelder sitzen -- beides nur nötig, wenn die
ODT geändert wurde.

    venv/bin/python scripts/protokoll_vorlage.py

Es braucht LibreOffice (``soffice``) und läuft deshalb auf dem Arbeitsplatz,
nicht auf dem Server. Das Ergebnis gehört ins Projekt eingecheckt.

Die Schriften des Briefkopfs müssen dabei **systemweit** installiert sein:
LibreOffice kennt ``assets/briefkopf`` nicht und ersetzt eine fehlende Schrift
stillschweigend durch eine andere. Das Skript prüft deshalb vorher nach und
kontrolliert hinterher, was tatsächlich in der Vorlage steckt.
"""

import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[1]
ODT = WURZEL / "Protokoll_Anmeldespiel.odt"
PDF = WURZEL / "Protokoll_Anmeldespiel.pdf"

#: Schriften, die der Briefkopf der Vorlage braucht. Fehlt eine davon, setzt
#: LibreOffice das Dokument mit einer Ersatzschrift -- und das Motto sieht
#: hinterher falsch aus, ohne dass jemand gewarnt würde.
GEFORDERTE_SCHRIFTEN = ("FrenteH1", "Calligraffiti", "Calibri Light")

#: Reihenfolge der Seriendruckfelder; der Index steckt im Messmarker.
FELDER = ("Datum", "Uhrzeit", "Vorname", "Nachname", "Straße", "Geburtstag")
FELD = re.compile(
    r'<text:database-display[^>]*text:column-name="([^"]+)"[^>]*>.*?</text:database-display>')


def _umschreiben(ziel, ersetzer):
    """Die ODT kopieren und dabei jedes Seriendruckfeld ersetzen."""
    with zipfile.ZipFile(ODT) as ein, zipfile.ZipFile(ziel, "w", zipfile.ZIP_DEFLATED) as aus:
        for eintrag in ein.infolist():
            daten = ein.read(eintrag.filename)
            if eintrag.filename == "content.xml":
                daten = FELD.sub(lambda treffer: ersetzer(treffer.group(1)),
                                 daten.decode("utf-8")).encode("utf-8")
            aus.writestr(eintrag, daten)


def _schriften_pruefen():
    """Vorab melden, welche Schrift LibreOffice nicht finden wird."""
    fehlend = []
    for name in GEFORDERTE_SCHRIFTEN:
        try:
            treffer = subprocess.run(["fc-match", name], check=True, capture_output=True,
                                     text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            print("  fc-match nicht verfügbar – Schriften nicht prüfbar.")
            return
        # fc-match liefert immer etwas; entscheidend ist, ob es die Gesuchte ist.
        gefunden = treffer.split('"')[1] if '"' in treffer else ""
        passt = gefunden.replace(" ", "").lower().startswith(name.replace(" ", "").lower()[:6])
        print(f"  {name:<14} {'->  ' + gefunden if passt else 'FEHLT (ersetzt durch ' + gefunden + ')'}")
        if not passt:
            fehlend.append(name)
    if fehlend:
        print(f"\n  Diese Schriften müssen systemweit installiert sein: {', '.join(fehlend)}.")
        print("  LibreOffice liest assets/briefkopf nicht. Ohne sie wird der Briefkopf der")
        print("  Vorlage falsch gesetzt -- und das steckt danach fest im PDF.")
        if not sys.stdin.isatty():
            print("  (keine Eingabe möglich -- es wird trotzdem gesetzt)")
        elif input("  Trotzdem fortfahren? [j/N] ").strip().lower() not in ("j", "ja"):
            sys.exit("Abgebrochen.")


def _eingebettete_schriften(pdf):
    """Was in der erzeugten Vorlage tatsächlich steckt."""
    from pypdf import PdfReader

    gefunden = set()
    for seite in PdfReader(str(pdf)).pages:
        schriften = (seite.get("/Resources", {}).get("/Font") or {})
        for verweis in schriften.values():
            name = verweis.get_object().get("/BaseFont")
            if name:
                gefunden.add(str(name).split("+")[-1])
    return sorted(gefunden)


def _nach_pdf(odt, ordner):
    subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(ordner),
                    str(odt)], check=True, capture_output=True, timeout=300)
    return Path(ordner) / (Path(odt).stem + ".pdf")


def _vermessen(pdf):
    """Grundlinien der Marker ausgeben, als Vorlage für PLACEMENTS."""
    from pypdf import PdfReader

    for nummer, seite in enumerate(PdfReader(str(pdf)).pages, 1):
        funde = []
        seite.extract_text(visitor_text=lambda text, cm, tm, fd, size: funde.append(
            (tm[4], tm[5], text)) if "QQ" in text else None)
        for x, y, text in funde:
            for index in re.findall(r"QQ(\d)QQ", text):
                print(f"  Seite {nummer}: {FELDER[int(index)]:<12} x={x:7.1f} y={y:7.1f}")


def main():
    if not ODT.exists():
        sys.exit(f"{ODT.name} fehlt.")
    print("Schriften des Briefkopfs:")
    _schriften_pruefen()
    with tempfile.TemporaryDirectory() as ordner:
        marker = Path(ordner) / "marker.odt"
        leer = Path(ordner) / "leer.odt"
        _umschreiben(marker, lambda name: f"QQ{FELDER.index(name)}QQ")
        _umschreiben(leer, lambda name: "")
        print("\nSeriendruckfelder:")
        _vermessen(_nach_pdf(marker, ordner))
        shutil.copy(_nach_pdf(leer, ordner), PDF)
    print("\nIn der Vorlage eingebettet:")
    for name in _eingebettete_schriften(PDF):
        print(f"  {name}")
    print(f"\n{PDF.name} neu erzeugt. Sitzen die Werte schief, sind die Grundlinien oben "
          "in protocol_pdf.PLACEMENTS nachzutragen.")
    print("Die Datei unterscheidet sich auch bei unveränderter ODT -- LibreOffice stempelt "
          "Zeitpunkt und Dokument-ID hinein. Ein Diff heißt also nicht, dass sich etwas "
          "am Formular geändert hat.")


if __name__ == "__main__":
    main()
