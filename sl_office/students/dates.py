"""Geburtsdaten aus Tabellenzellen lesen.

``pandas.to_datetime(..., dayfirst=True)`` ist dafür nicht brauchbar: Bei einer
Zeichenkette in ISO-Schreibweise wendet pandas ``dayfirst`` auf die letzten
beiden Bestandteile an und liest ``2021-03-12`` als 3. Dezember. Genau das
passiert, sobald eine echte Datumszelle als Text ankommt -- aus einer
Arbeitsmappe, die mit ``dtype=str`` gelesen wurde, kommt sie als
``"2021-03-12 00:00:00"``. Vertauscht wird dabei nur, was sich vertauschen
lässt: Geburtstage ab dem 13. bleiben unauffällig richtig.

Deshalb wird hier zuerst die Schreibweise erkannt und dann gezielt gelesen.
"""

import datetime
import re

#: ISO-Schreibweise, mit oder ohne angehängte Uhrzeit -- immer Jahr-Monat-Tag.
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]\d.*)?$")
#: Deutsche Schreibweise mit Punkt, Schrägstrich oder Bindestrich: Tag zuerst.
_TAG_ZUERST = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})$")
#: Werte, die pandas für eine leere Zelle liefert.
_LEER = {"", "nan", "nat", "none", "null", "-"}


def parse_birthdate(value):
    """Ein Geburtsdatum aus einer Tabellenzelle lesen.

    Nimmt echte Datumswerte unverändert und liest Zeichenketten nach ihrer
    erkannten Schreibweise. Wirft ``ValueError``, wenn die Zelle leer ist oder
    keiner bekannten Schreibweise folgt -- die Aufrufer zählen solche Zeilen als
    ungültig, statt sie zu raten.
    """
    if value is None:
        raise ValueError("leeres Geburtsdatum")
    # NaN und NaT sind die einzigen Werte, die sich selbst nicht gleichen.
    # pandas liefert sie für eine leere Zelle, und NaT ist eine Unterklasse von
    # ``datetime`` -- ohne diese Prüfung käme es als gültiges Datum durch.
    if value != value:
        raise ValueError("leeres Geburtsdatum")
    # datetime vor date prüfen: datetime ist eine Unterklasse von date.
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    # pandas.Timestamp und numpy.datetime64 bringen ihre eigene Umwandlung mit.
    umwandeln = getattr(value, "to_pydatetime", None)
    if callable(umwandeln):
        return umwandeln().date()
    text = str(value).strip()
    if text.lower() in _LEER:
        raise ValueError("leeres Geburtsdatum")

    treffer = _ISO.match(text)
    if treffer:
        jahr, monat, tag = (int(teil) for teil in treffer.groups())
        return datetime.date(jahr, monat, tag)

    treffer = _TAG_ZUERST.match(text)
    if treffer:
        tag, monat, jahr = (int(teil) for teil in treffer.groups())
        # Zweistellige Jahreszahlen kommen aus alten Ausdrucken; Kinder in
        # diesem Verfahren sind nach 2000 geboren.
        if jahr < 100:
            jahr += 2000
        return datetime.date(jahr, monat, tag)

    raise ValueError(f"unlesbares Geburtsdatum: {text!r}")
