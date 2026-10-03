"""Vorlagen, die jede Schule selbst pflegt: „Verwaltung → Vorlagen“.

Der **Laufzettel der Anmeldung** ist eine Checkliste als schlichter Text,
eine Zeile je Punkt (siehe :func:`laufzettel_abschnitte`). Er liegt in der
Tabelle des Schulprofils und steckt damit in jeder Datensicherung.
"""

import re
from dataclasses import dataclass, field

from models import db
from sl_office.school_profile import Schulprofil

LAUFZETTEL_KEY = "vorlage:laufzettel"

#: Vorbelegter Laufzettel; {stadt} kommt aus dem Schulprofil.
LAUFZETTEL_STANDARD = """# Unterlagen
- Anmeldeformular vollständig ausgefüllt
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
    """Ein Ankreuzpunkt."""
    text: str


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
        # Ein Vermerk aus der Zeit mit Elternportal; heute ohne Bedeutung.
        zeile = zeile.replace("{digital}", "")
        if ":" in zeile and "|" in zeile.split(":", 1)[1]:
            titel, rest = zeile.split(":", 1)
            optionen = tuple(teil.strip() for teil in rest.split("|") if teil.strip())
            punkte.append(Reihe(einsetzen(titel), optionen))
        else:
            punkte.append(Kasten(einsetzen(zeile)))
    return abschnitte
