"""Vorlagen, die jede Schule selbst pflegt: „Verwaltung → Vorlagen“.

* **Laufzettel der Verwaltungsanmeldung** -- die Checkliste als schlichter
  Text, eine Zeile je Punkt (siehe :func:`laufzettel_abschnitte`).
* **Material zum Anmeldespiel** -- ein PDF der Schule (Aufgaben,
  Gesprächsfragen ...), das unverändert zwischen Deckblatt und
  Auswertungsbogen in jedes Protokoll eingebunden wird.

Beides liegt in der Tabelle des Schulprofils und steckt damit in jeder
Datensicherung.
"""

import re
from dataclasses import dataclass, field
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from models import db
from sl_office.school_profile import Schulprofil

LAUFZETTEL_KEY = "vorlage:laufzettel"
MATERIAL_KEY = "vorlage:anmeldespiel"
MAX_MATERIAL_BYTES = 15 * 1024 * 1024
MAX_MATERIAL_SEITEN = 40

#: Der Laufzettel, wie ihn die Serverfassung druckt; {stadt} kommt aus dem
#: Schulprofil. {digital} vermerkt dort eine elektronisch übermittelte
#: Anmeldung -- ohne Elternportal bleibt der Vermerk leer.
LAUFZETTEL_STANDARD = """# Unterlagen
- Anmeldeformular vollständig ausgefüllt {digital}
~ oder
- Abgleich ausgedruckte Daten mit Unterschrift
- Anmeldeschein der Stadt {stadt} ausgefüllt
- Abgleich Anmeldeformular mit Geburtsurkunde
- Masernschutz vorhanden
- Datenschutzerklärung ausgefüllt
- Schweigepflichtsentbindung Kita

# Betreuung
- OGS-Platz gewünscht: Ja | Nein | Anmeldeunterlagen OGS ausgeben
- ÜMI-Platz gewünscht: Ja | Nein | Anmeldeunterlagen ÜMI ausgeben

# Ausgegeben und vorgelegt
- Broschüre zur Grundschule ausgeben
- Erziehungsvertrag liegt vor
"""


@dataclass(frozen=True)
class Kasten:
    """Ein Ankreuzpunkt. ``schluessel`` holt sich einen Hinweis aus den Daten."""
    text: str
    schluessel: str = ""


@dataclass(frozen=True)
class Reihe:
    """Ein Ankreuzpunkt mit Unteroptionen in einer eigenen Zeile darunter."""
    text: str
    optionen: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class Zwischenwort:
    """Ein eingerücktes Wort zwischen zwei Punkten, etwa „oder“."""
    text: str


class VorlagenFehler(ValueError):
    pass


# --- Laufzettel ----------------------------------------------------------------

def laufzettel_text():
    zeile = db.session.get(Schulprofil, LAUFZETTEL_KEY)
    return zeile.wert if zeile is not None and zeile.wert.strip() else LAUFZETTEL_STANDARD


def ist_standard():
    return db.session.get(Schulprofil, LAUFZETTEL_KEY) is None


def laufzettel_speichern(text):
    abschnitte = laufzettel_abschnitte(text)
    if not any(punkte for _, punkte in abschnitte):
        raise VorlagenFehler("Der Laufzettel enthält keinen einzigen Punkt.")
    zeile = db.session.get(Schulprofil, LAUFZETTEL_KEY)
    if zeile is None:
        zeile = Schulprofil(key=LAUFZETTEL_KEY)
        db.session.add(zeile)
    zeile.wert = text.strip() + "\n"


def laufzettel_zuruecksetzen():
    zeile = db.session.get(Schulprofil, LAUFZETTEL_KEY)
    if zeile is not None:
        db.session.delete(zeile)


_PLATZHALTER = re.compile(r"\{(stadt|schule)\}")


def laufzettel_abschnitte(text=None, schule=None):
    """Den Text in Abschnitte zerlegen: ``[(Titel, [Kasten | Reihe | Zwischenwort])]``.

    * ``# Titel``                   -- neuer Abschnitt
    * ``- Text``                    -- Ankreuzpunkt
    * ``- Text: A | B | C``         -- Ankreuzpunkt mit Unteroptionen darunter
    * ``~ Text``                    -- eingerücktes Zwischenwort, etwa „oder“
    * ``{stadt}``, ``{schule}``     -- aus dem Schulprofil
    * ``{digital}``                 -- Vermerk, wenn die Eltern das Formular
      elektronisch übermittelt haben (nur mit Elternportal)

    Andere Zeilen werden als Ankreuzpunkt gelesen, Leerzeilen übergangen.
    """
    text = laufzettel_text() if text is None else text
    schule = schule or {}
    werte = {"stadt": schule.get("town") or "", "schule": schule.get("name") or ""}

    def einsetzen(zeile):
        return re.sub(r"\s{2,}", " ", _PLATZHALTER.sub(lambda m: werte[m.group(1)], zeile)).strip()

    abschnitte = []
    for roh in text.splitlines():
        zeile = roh.strip()
        if not zeile:
            continue
        if zeile.startswith("#"):
            abschnitte.append((einsetzen(zeile.lstrip("#")), []))
            continue
        if not abschnitte:
            abschnitte.append(("", []))
        punkte = abschnitte[-1][1]
        if zeile.startswith("~"):
            punkte.append(Zwischenwort(einsetzen(zeile[1:])))
            continue
        zeile = zeile[1:] if zeile.startswith("-") else zeile
        schluessel = ""
        if "{digital}" in zeile:
            zeile, schluessel = zeile.replace("{digital}", ""), "digital"
        if ":" in zeile and "|" in zeile.split(":", 1)[1]:
            titel, rest = zeile.split(":", 1)
            optionen = tuple(teil.strip() for teil in rest.split("|") if teil.strip())
            punkte.append(Reihe(einsetzen(titel), optionen))
        else:
            punkte.append(Kasten(einsetzen(zeile), schluessel))
    return abschnitte


# --- Material zum Anmeldespiel -------------------------------------------------

def material():
    """Das PDF der Schule als Bytes, sonst ``None``."""
    zeile = db.session.get(Schulprofil, MATERIAL_KEY)
    return zeile.daten if zeile is not None and zeile.daten else None


def material_info():
    daten = material()
    if daten is None:
        return None
    zeile = db.session.get(Schulprofil, MATERIAL_KEY)
    return {"name": zeile.wert, "seiten": len(PdfReader(BytesIO(daten)).pages),
            "groesse": len(daten)}


def material_speichern(daten, dateiname):
    if not daten:
        raise VorlagenFehler("Die Datei ist leer.")
    if len(daten) > MAX_MATERIAL_BYTES:
        raise VorlagenFehler("Die Datei ist größer als 15 MB.")
    if not daten.startswith(b"%PDF"):
        raise VorlagenFehler("Bitte eine PDF-Datei hochladen.")
    try:
        reader = PdfReader(BytesIO(daten))
        if reader.is_encrypted:
            raise VorlagenFehler("Das PDF ist verschlüsselt und lässt sich nicht einbinden.")
        seiten = len(reader.pages)
    except (PdfReadError, ValueError, KeyError, OSError) as exc:
        raise VorlagenFehler("Die PDF-Datei ist beschädigt oder nicht lesbar.") from exc
    if not seiten:
        raise VorlagenFehler("Das PDF hat keine Seiten.")
    if seiten > MAX_MATERIAL_SEITEN:
        raise VorlagenFehler(f"Das PDF hat mehr als {MAX_MATERIAL_SEITEN} Seiten.")
    zeile = db.session.get(Schulprofil, MATERIAL_KEY)
    if zeile is None:
        zeile = Schulprofil(key=MATERIAL_KEY)
        db.session.add(zeile)
    zeile.wert = (dateiname or "Material.pdf")[:200]
    zeile.daten = daten
    return seiten


def material_entfernen():
    zeile = db.session.get(Schulprofil, MATERIAL_KEY)
    if zeile is not None:
        db.session.delete(zeile)
