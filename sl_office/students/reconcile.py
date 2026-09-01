"""Geburtsdaten gegen die Liste der Stadt abgleichen.

Bis zur Korrektur in :mod:`sl_office.students.dates` hat der Import bei
Geburtstagen bis zum 12. eines Monats Tag und Monat vertauscht. Aus den
gespeicherten Daten allein ist das nicht mehr aufzulösen -- wohl aber gegen die
Originaldatei, sofern sie noch vorliegt.

Der Abgleich ordnet ausschließlich über den Namen zu (das Geburtsdatum ist ja
gerade das Strittige) und rührt nur das Geburtsdatum an. Es werden weder Kinder
angelegt noch gelöscht, und keine andere Spalte wird überschrieben.
"""

import re
import unicodedata
from io import BytesIO

import pandas as pd

from models import Schueler, db
from sl_office.services.student_classification import recalculate_kann_kind
from sl_office.students.city_import import InvalidWorkbook, _clean, _validate_xlsx, suggest_mapping
from sl_office.students.dates import parse_birthdate

#: Spalten, ohne die kein Abgleich möglich ist.
PFLICHTSPALTEN = ("nachname", "vorname", "geburtsdatum")


def _namensschluessel(vorname, nachname):
    """Vergleichsform eines Namens: ohne Groß/Klein, Akzente und Mehrfach-Leerzeichen.

    Die Liste der Stadt und der eigene Bestand stammen aus derselben Quelle,
    weichen aber in Schreibweise und Zeichensatz gelegentlich ab.
    """
    text = f"{vorname} {nachname}".casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(zeichen for zeichen in text if not unicodedata.combining(zeichen))
    return re.sub(r"[\s.-]+", " ", text).strip()


def _ist_vertauschung(gespeichert, aus_datei):
    return (gespeichert.year == aus_datei.year
            and gespeichert.day == aus_datei.month
            and gespeichert.month == aus_datei.day
            and gespeichert != aus_datei)


class Abgleich:
    """Ergebnis eines Prüflaufs; ``anwenden()`` schreibt die Korrekturen."""

    def __init__(self):
        self.vertauscht = []      # [(kind, datum_aus_datei)]
        self.abweichend = []      # [(kind, datum_aus_datei)] -- keine Vertauschung
        self.bestaetigt = 0       # Datum stimmt bereits überein
        self.ohne_treffer = []    # [(vorname, nachname, datum)] -- Zeile ohne Kind
        self.mehrdeutig = []      # [(vorname, nachname, [kind, ...], datum_aus_datei)]
        self.unlesbar = 0         # Zeilen ohne verwertbaren Namen oder Datum
        self.nicht_in_datei = []  # [kind] -- Kind des Jahrgangs fehlt in der Liste

    @property
    def korrekturen(self):
        return self.vertauscht + self.abweichend

    def anwenden(self):
        """Geburtsdaten setzen und die Kann-Kind-Kennzeichen nachziehen."""
        for kind, neues_datum in self.korrekturen:
            kind.geburtsdatum = neues_datum
        if self.korrekturen:
            recalculate_kann_kind()
            db.session.commit()
        return len(self.korrekturen)


def _spaltenzuordnung(headers, vorgaben):
    mapping = suggest_mapping(headers)
    mapping.update({feld: spalte for feld, spalte in vorgaben.items() if spalte})
    fehlend = [feld for feld in PFLICHTSPALTEN if not mapping.get(feld)]
    if fehlend:
        raise InvalidWorkbook(
            f"Keine Spalte gefunden für: {', '.join(fehlend)}. "
            f"Vorhandene Spalten: {', '.join(headers)}")
    unbekannt = [mapping[feld] for feld in PFLICHTSPALTEN if mapping[feld] not in headers]
    if unbekannt:
        raise InvalidWorkbook(f"Diese Spalten gibt es in der Datei nicht: {', '.join(unbekannt)}")
    return mapping


def geburtsdaten_abgleichen(payload, jahr, **spalten):
    """Die Arbeitsmappe gegen den Jahrgang ``jahr`` prüfen.

    ``spalten`` erlaubt es, die automatische Spaltenerkennung je Pflichtfeld zu
    überschreiben (``nachname=``, ``vorname=``, ``geburtsdatum=``).
    """
    _validate_xlsx(payload)
    # Ohne ``dtype=str``: echte Datumszellen bleiben Datumswerte und müssen gar
    # nicht erst aus Text zurückgelesen werden.
    frame = pd.read_excel(BytesIO(payload), engine="openpyxl")
    if frame.empty:
        raise InvalidWorkbook("Die Arbeitsmappe enthält keine Datenzeilen.")
    frame.columns = [str(spalte).strip() for spalte in frame.columns]
    mapping = _spaltenzuordnung(list(frame.columns), spalten)

    bestand = {}
    for kind in Schueler.query.filter_by(einschulungsjahr=jahr).all():
        bestand.setdefault(_namensschluessel(kind.vorname, kind.nachname), []).append(kind)

    ergebnis = Abgleich()
    getroffen = set()
    for _, zeile in frame.iterrows():
        vorname = _clean(zeile[mapping["vorname"]])
        nachname = _clean(zeile[mapping["nachname"]])
        try:
            aus_datei = parse_birthdate(zeile[mapping["geburtsdatum"]])
        except (TypeError, ValueError):
            aus_datei = None
        if not vorname or not nachname or aus_datei is None:
            ergebnis.unlesbar += 1
            continue

        schluessel = _namensschluessel(vorname, nachname)
        kinder = bestand.get(schluessel, [])
        if not kinder:
            ergebnis.ohne_treffer.append((vorname, nachname, aus_datei))
            continue
        if len(kinder) > 1:
            ergebnis.mehrdeutig.append((vorname, nachname, kinder, aus_datei))
            continue

        kind = kinder[0]
        getroffen.add(kind.id)
        if kind.geburtsdatum == aus_datei:
            ergebnis.bestaetigt += 1
        elif kind.geburtsdatum is not None and _ist_vertauschung(kind.geburtsdatum, aus_datei):
            ergebnis.vertauscht.append((kind, aus_datei))
        else:
            ergebnis.abweichend.append((kind, aus_datei))

    mehrdeutige_ids = {kind.id for _, _, kinder, _ in ergebnis.mehrdeutig for kind in kinder}
    ergebnis.nicht_in_datei = [
        kind for kinder in bestand.values() for kind in kinder
        if kind.id not in getroffen and kind.id not in mehrdeutige_ids
    ]
    return ergebnis
