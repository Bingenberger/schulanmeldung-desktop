"""Feldkatalog des Anmeldeformulars.

Eine Quelle für alles, was mit dem Formular zu tun hat: Reihenfolge, Beschriftung,
Eingabeart, Pflichtangaben und die Aufteilung in Schritte stehen nur hier. Die
Route, die Formularseite und die Ansicht in der Verwaltung lesen daraus.

Der Katalog bildet das Papierformular ``Schulanmeldung.pdf`` ab. Nicht übernommen
sind die drei Felder, die es nur auf Papier gibt: die beiden Unterschriftszeilen
(online treten die Bestätigungen im letzten Schritt an ihre Stelle) und die Frage
"Die Anmeldedaten wurden vorab digital übermittelt" -- wer dieses Formular
absendet, hat sie beantwortet.
"""

import re
from dataclasses import dataclass, field as dataclass_field
from datetime import date

GESCHLECHT = ("weiblich", "männlich", "divers")
KONFESSION = ("katholisch", "evangelisch", "islamisch", "orthodox", "jüdisch",
              "alevitisch", "ohne Bekenntnis", "andere")
JA_NEIN = ("ja", "nein")
KITAS = ("Kita St. Matthäus", "Kita Pappelweg", "Kita Weidenstraße", "Wilde 13",
         "Kita Sanddornstraße", "Andere")


#: Monat und Jahr, mit oder ohne Trennzeichen. Der zweite Zweig lässt einen
#: einstelligen Monat zu, dann aber mit Trennzeichen -- "82023" wäre sonst
#: nicht zu entscheiden.
MONTH_YEAR_PATTERN = r"\d{2}[./-]?\d{4}|\d[./-]\d{4}"


@dataclass(frozen=True)
class Field:
    """Ein Eingabefeld. ``kind`` bestimmt, wie die Seite es darstellt."""

    name: str
    label: str
    kind: str = "text"
    required: bool = False
    options: tuple = ()
    hint: str = ""
    autocomplete: str = "off"
    maxlength: int = 200
    width: int = 6  # Spalten im 12er-Raster
    group: str = ""
    min: str = ""
    max: str = ""
    placeholder: str = ""
    pattern: str = ""
    inputmode: str = ""


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    intro: str = ""
    fields: tuple = dataclass_field(default_factory=tuple)
    optional: bool = False  # darf im Ganzen übersprungen werden


def _guardian(number, required_contact):
    """Die elf Felder je sorgeberechtigter Person, wie im Papierformular."""
    prefix = f"sorgeberechtigt_{number}_"
    person = "Zur Person"
    return (
        Field(prefix + "name", "Name, Vorname", required=required_contact, group=person,
              autocomplete="name" if number == 1 else "off"),
        Field(prefix + "sorgeberechtigt", "Sorgeberechtigt", kind="select", options=JA_NEIN,
              group=person, width=3),
        Field(prefix + "staatsangehoerigkeit", "Staatsangehörigkeit", group=person),
        Field(prefix + "muttersprache", "Muttersprache", group=person),
        Field(prefix + "zuzugsjahr", "Zuzugsjahr nach Deutschland", kind="number", group=person,
              width=3, min="1900", max=str(date.today().year), maxlength=4),
        Field(prefix + "geburtsland", "Geburtsland", group=person),
        Field(prefix + "strasse", "Straße und Hausnummer", group="Anschrift",
              autocomplete="street-address" if number == 1 else "off"),
        Field(prefix + "plz", "Postleitzahl", group="Anschrift", width=2, maxlength=10),
        Field(prefix + "ort", "Ort", group="Anschrift", width=4),
        Field(prefix + "email", "E-Mail-Adresse", kind="email", required=required_contact,
              group="Kontakt", maxlength=320, autocomplete="email" if number == 1 else "off"),
        Field(prefix + "telefon", "Telefonnummer", kind="tel", group="Kontakt", maxlength=40,
              autocomplete="tel" if number == 1 else "off"),
        Field(prefix + "notfalltelefon", "Telefonnummer (Notfall)", kind="tel", group="Kontakt",
              maxlength=40),
    )


STEPS = (
    Step("kind", "Daten des Kindes",
         "Diese Angaben brauchen wir in jedem Fall.",
         (
             Field("kind_nachname", "Nachname", required=True),
             Field("kind_vorname", "Vorname", required=True),
             Field("kind_geburtsdatum", "Geburtsdatum", kind="date", required=True, width=4),
             Field("kind_geburtsort", "Geburtsort", width=4),
             Field("kind_geschlecht", "Geschlecht", kind="select", options=GESCHLECHT, width=4),
         )),
    Step("herkunft", "Anschrift und Herkunft des Kindes",
         "Wohnt Ihr Kind unter einer anderen Anschrift als Sie, tragen Sie hier "
         "die Anschrift des Kindes ein.",
         (
             Field("kind_strasse", "Straße und Hausnummer"),
             Field("kind_plz", "Postleitzahl", width=2, maxlength=10),
             Field("kind_ort", "Ort", width=4),
             Field("kind_staatsangehoerigkeit", "Staatsangehörigkeit"),
             Field("kind_muttersprache", "Muttersprache"),
         )),
    Step("religion", "Konfession und Religionsunterricht", "",
         (
             Field("kind_konfession", "Konfession", kind="select", options=KONFESSION),
             Field("kind_konfession_andere", "Falls andere: welche?"),
             Field("religion_abgemeldet", "Vom Religionsunterricht abgemeldet?", kind="select",
                   options=JA_NEIN, width=12,
                   hint="Kinder, die vom Religionsunterricht abgemeldet sind, werden in anderen "
                        "Lerngruppen betreut. Sie erhalten kein alternatives Unterrichtsangebot."),
         )),
    Step("sorge1", "Erste sorgeberechtigte Person",
         "Name und E-Mail-Adresse brauchen wir, um Sie erreichen zu können.",
         _guardian(1, required_contact=True)),
    Step("sorge2", "Zweite sorgeberechtigte Person",
         "Bei alleinigem Sorgerecht ist eine entsprechende amtliche Bescheinigung bei der "
         "Schulanmeldung vorzulegen. Gibt es keine zweite sorgeberechtigte Person, "
         "überspringen Sie diesen Schritt.",
         _guardian(2, required_contact=False), optional=True),
    Step("weiteres", "Weitere Angaben", "",
         (
             Field("notfallinformationen", "Notfallinformationen", kind="textarea", width=12,
                   maxlength=2000,
                   hint="Chronische Erkrankungen, Allergien, Unverträglichkeiten und Ähnliches."),
             Field("weitere_notfallnummer", "Weitere Notfallnummer", kind="tel", maxlength=40),
             Field("weitere_sorgeberechtigte", "Gibt es weitere sorgeberechtigte Personen?",
                   kind="select", options=JA_NEIN),
             Field("familiensprache", "Familiensprache"),
             # Monat und Jahr als Text mit Muster: <input type="month"> kennt
             # nicht jeder Browser und liefert dann uneinheitliche Werte.
             Field("besuchte_kita", "Einrichtung", kind="select", options=KITAS,
                   group="Besuchte Kita"),
             Field("besuchte_kita_andere", "Falls andere: welche?", group="Besuchte Kita"),
             # Der Schrägstrich darf entfallen: das Ziffernfeld eines
             # Mobiltelefons hat keine Taste dafür. Gespeichert wird trotzdem
             # einheitlich MM/JJJJ -- siehe normalise().
             Field("kita_von", "Besucht seit", group="Besuchte Kita", width=3,
                   placeholder="MM/JJJJ", pattern=MONTH_YEAR_PATTERN, inputmode="numeric",
                   maxlength=7, hint="Monat und Jahr, z. B. 08/2023 oder 082023."),
             Field("kita_bis", "Besucht bis", group="Besuchte Kita", width=3,
                   placeholder="MM/JJJJ", pattern=MONTH_YEAR_PATTERN, inputmode="numeric",
                   maxlength=7, hint="Voraussichtliches Ende, meist der Sommer vor der Einschulung."),
         )),
    Step("abschluss", "Prüfen und absenden",
         "Bitte sehen Sie Ihre Angaben noch einmal durch. Über „Ändern“ kommen Sie "
         "zurück zum jeweiligen Schritt.",
         (
             Field("ort_datum", "Ort, Datum", maxlength=120),
             Field("datenschutz_bestaetigt", "Ich bestätige die Datenschutzinformationen und "
                   "stimme der Verarbeitung für die Schulanmeldung zu.", kind="checkbox",
                   required=True, width=12),
             Field("angaben_richtig", "Ich versichere, dass meine Angaben vollständig und "
                   "richtig sind.", kind="checkbox", required=True, width=12),
         )),
)

#: Alle Feldnamen in Formularreihenfolge.
FORM_FIELDS = tuple(field.name for step in STEPS for field in step.fields)
FIELDS_BY_NAME = {field.name: field for step in STEPS for field in step.fields}
#: Die letzte Seite fasst zusammen, statt neue Personendaten zu erfragen.
SUMMARY_STEP = STEPS[-1]


def step_by_key(key):
    for step in STEPS:
        if step.key == key:
            return step
    return None


def step_number(key):
    for index, step in enumerate(STEPS, start=1):
        if step.key == key:
            return index
    return 0


def neighbours(key):
    """(vorheriger, nächster) Schritt -- ``None`` am Anfang bzw. Ende."""
    index = step_number(key) - 1
    previous = STEPS[index - 1] if index > 0 else None
    following = STEPS[index + 1] if 0 <= index < len(STEPS) - 1 else None
    return previous, following


_MONTH_YEAR = re.compile(r"^\s*(\d{1,2})\s*[./-]?\s*(\d{4})\s*$")


def _month_year(value):
    """"082023", "8.2023" oder "08 / 2023" werden zu "08/2023".

    Was nicht passt, bleibt unverändert stehen: das Formular weist Eingaben
    nicht zurück, die Schule sieht sie beim Anmeldegespräch ohnehin durch.
    """
    match = _MONTH_YEAR.match(value)
    if not match:
        return value
    monat, jahr = match.groups()
    return f"{int(monat):02d}/{jahr}"


#: Felder, deren Eingabe vor dem Speichern vereinheitlicht wird.
NORMALISERS = {"kita_von": _month_year, "kita_bis": _month_year}


def normalise(name, value):
    """Eine Eingabe in der Schreibweise ablegen, die das Formular ausgibt."""
    value = (value or "").strip()
    cleaner = NORMALISERS.get(name)
    return cleaner(value) if cleaner else value


def missing(data):
    """Fehlende Pflichtangaben als (Schritt, Feld)-Paare, in Formularreihenfolge."""
    return [(step, field) for step in STEPS for field in step.fields
            if field.required and not (data.get(field.name) or "").strip()]


def resume_step(data):
    """Schritt, bei dem das Ausfüllen fortgesetzt wird.

    Solange Pflichtangaben fehlen, ist das der erste Schritt, in dem etwas
    fehlt; danach die Zusammenfassung. So landet niemand auf einer Seite, die
    er längst ausgefüllt hat, und ein leeres Formular beginnt vorne.
    """
    gaps = missing(data)
    return gaps[0][0].key if gaps else SUMMARY_STEP.key


def summary(data):
    """Ausgefüllte Angaben je Schritt für die Zusammenfassung und die Verwaltung."""
    blocks = []
    for step in STEPS:
        entries = [(field, data[field.name]) for field in step.fields
                   if (data.get(field.name) or "").strip()]
        if entries:
            blocks.append((step, entries))
    return blocks
