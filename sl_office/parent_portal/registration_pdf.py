"""Die Angaben aus dem digitalen Anmeldeformular auf die amtliche Vorlage drucken.

``Schulanmeldung.pdf`` ist ein fertig gesetztes Dokument ohne Formularfelder.
Die Werte werden darum als zweite Ebene darübergelegt: reportlab zeichnet sie
auf leere Seiten gleicher Größe, pypdf legt beide übereinander. Herausgegeben
wird also die amtliche Vorlage, nur eben ausgefüllt.

Die Koordinaten stammen aus der Vorlage selbst -- jede Beschriftung wurde mit
ihrer Grundlinie ausgelesen, die Werte stehen auf derselben Höhe rechts davon.
Wird die Vorlage neu gesetzt, sind :data:`PLACEMENTS` und :data:`CHOICES` die
einzigen Stellen, die nachgeführt werden müssen.
"""

from __future__ import annotations

import datetime
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from sl_office.parent_portal import letterhead
from sl_office.services.pdf_forms import page_size, stack

TEMPLATE = Path(__file__).resolve().parents[2] / "Schulanmeldung.pdf"

#: Linke Wertspalte -- hinter der längsten Beschriftung der linken Spalte.
VALUE_X = 220.0
#: Rechte Wertspalte der zweispaltigen Zeilen (Geburtsort, Muttersprache …).
VALUE_X_RIGHT = 413.0
VALUE_SIZE = 10.5
#: Die Vorlage setzt ihre Ankreuzfelder als Buchstabe "O" in 12 pt.
MARK_SIZE = 10.0
CIRCLE_WIDTH = 9.3


class Text:
    """Ein Wert an fester Stelle. ``build`` liefert den Text aus den Daten."""

    def __init__(self, page, x, y, build, width=None, lines=1):
        self.page, self.x, self.y = page, x, y
        self.build, self.width, self.lines = build, width, lines


class Choice:
    """Ein Ankreuzfeld: gesetzt, wenn ``field`` genau ``value`` enthält."""

    def __init__(self, page, x, y, field, value):
        self.page, self.x, self.y = page, x, y
        self.field, self.value = field, value


def _field(name):
    return lambda data: (data.get(name) or "").strip()


def _joined(*names, separator=" "):
    def build(data):
        parts = [(data.get(name) or "").strip() for name in names]
        return separator.join(part for part in parts if part)
    return build


def _child_name(data):
    name = _joined("kind_nachname", "kind_vorname", separator=", ")(data)
    return name


def _german_date(name):
    def build(data):
        raw = (data.get(name) or "").strip()
        try:
            return datetime.date.fromisoformat(raw).strftime("%d.%m.%Y")
        except ValueError:
            # Die Eltern haben ein Datumsfeld ausgefüllt; steht dort etwas
            # anderes, wird es unverändert übernommen statt verschluckt.
            return raw
    return build


def _kita_period(data):
    von, bis = (data.get("kita_von") or "").strip(), (data.get("kita_bis") or "").strip()
    if von and bis:
        return f"{von} bis {bis}"
    return von or bis


#: Freitextwerte. Reihenfolge wie in der Vorlage, von oben nach unten.
PLACEMENTS = (
    # --- Seite 1: Daten des Kindes ---
    Text(1, VALUE_X, 573.5, _child_name),
    Text(1, VALUE_X, 547.6, _german_date("kind_geburtsdatum")),
    Text(1, VALUE_X_RIGHT, 547.6, _field("kind_geburtsort")),
    Text(1, VALUE_X, 490.1, _field("kind_strasse")),
    Text(1, VALUE_X, 464.1, _joined("kind_plz", "kind_ort")),
    Text(1, VALUE_X, 438.2, _field("kind_staatsangehoerigkeit")),
    Text(1, VALUE_X_RIGHT, 438.2, _field("kind_muttersprache")),
    # Steht hinter dem Ankreuzfeld "O andere:" der Konfessionsliste.
    Text(1, 462.0, 377.4, _field("kind_konfession_andere"), width=90),
    # --- Seite 1: erste sorgeberechtigte Person ---
    Text(1, VALUE_X, 272.2, _field("sorgeberechtigt_1_name")),
    Text(1, VALUE_X, 246.3, _field("sorgeberechtigt_1_strasse")),
    Text(1, VALUE_X, 220.3, _joined("sorgeberechtigt_1_plz", "sorgeberechtigt_1_ort")),
    Text(1, VALUE_X, 194.4, _field("sorgeberechtigt_1_staatsangehoerigkeit")),
    Text(1, VALUE_X_RIGHT, 194.4, _field("sorgeberechtigt_1_muttersprache")),
    Text(1, VALUE_X, 168.4, _field("sorgeberechtigt_1_zuzugsjahr")),
    Text(1, VALUE_X_RIGHT, 168.4, _field("sorgeberechtigt_1_geburtsland")),
    Text(1, VALUE_X, 122.2, _field("sorgeberechtigt_1_email")),
    Text(1, VALUE_X, 96.3, _field("sorgeberechtigt_1_telefon")),
    Text(1, VALUE_X, 70.3, _field("sorgeberechtigt_1_notfalltelefon")),
    # --- Seite 2: zweite sorgeberechtigte Person ---
    Text(2, VALUE_X, 730.4, _field("sorgeberechtigt_2_name")),
    Text(2, VALUE_X, 704.5, _field("sorgeberechtigt_2_strasse")),
    Text(2, VALUE_X, 678.5, _joined("sorgeberechtigt_2_plz", "sorgeberechtigt_2_ort")),
    Text(2, VALUE_X, 652.6, _field("sorgeberechtigt_2_staatsangehoerigkeit")),
    Text(2, VALUE_X_RIGHT, 652.6, _field("sorgeberechtigt_2_muttersprache")),
    Text(2, VALUE_X, 626.6, _field("sorgeberechtigt_2_zuzugsjahr")),
    Text(2, VALUE_X_RIGHT, 626.6, _field("sorgeberechtigt_2_geburtsland")),
    Text(2, VALUE_X, 574.7, _field("sorgeberechtigt_2_email")),
    Text(2, VALUE_X, 548.8, _field("sorgeberechtigt_2_telefon")),
    Text(2, VALUE_X, 522.8, _field("sorgeberechtigt_2_notfalltelefon")),
    # --- Seite 2: weitere Angaben ---
    # Der Kasten reicht bis zur Zeile "weitere Notfallnummer" bei y=381.9.
    Text(2, VALUE_X, 458.2, _field("notfallinformationen"), width=330, lines=5),
    Text(2, VALUE_X, 381.9, _field("weitere_notfallnummer")),
    Text(2, 458.0, 312.3, _field("besuchte_kita_andere"), width=90),
    Text(2, VALUE_X, 292.1, _kita_period),
    Text(2, VALUE_X, 266.9, _field("familiensprache")),
    Text(2, VALUE_X, 156.4, _field("ort_datum")),
)

#: Ankreuzfelder. Die Werte sind wortgleich mit den Auswahllisten des
#: digitalen Formulars (:mod:`sl_office.parent_portal.registration_form`).
CHOICES = (
    Choice(1, 196.4, 412.2, "kind_geschlecht", "weiblich"),
    Choice(1, 267.4, 412.2, "kind_geschlecht", "männlich"),
    Choice(1, 338.2, 412.2, "kind_geschlecht", "divers"),
    Choice(1, 196.4, 392.1, "kind_konfession", "katholisch"),
    Choice(1, 267.4, 392.1, "kind_konfession", "evangelisch"),
    Choice(1, 338.2, 392.1, "kind_konfession", "islamisch"),
    Choice(1, 409.1, 392.1, "kind_konfession", "ohne Bekenntnis"),
    Choice(1, 196.4, 377.4, "kind_konfession", "orthodox"),
    Choice(1, 267.4, 377.4, "kind_konfession", "jüdisch"),
    Choice(1, 338.2, 377.4, "kind_konfession", "alevitisch"),
    Choice(1, 409.1, 377.4, "kind_konfession", "andere"),
    Choice(1, 196.4, 357.3, "religion_abgemeldet", "nein"),
    Choice(1, 267.4, 357.3, "religion_abgemeldet", "ja"),
    Choice(1, 196.6, 148.2, "sorgeberechtigt_1_sorgeberechtigt", "ja"),
    Choice(1, 232.0, 148.2, "sorgeberechtigt_1_sorgeberechtigt", "nein"),
    Choice(2, 196.6, 600.7, "sorgeberechtigt_2_sorgeberechtigt", "ja"),
    Choice(2, 232.0, 600.7, "sorgeberechtigt_2_sorgeberechtigt", "nein"),
    Choice(2, 196.6, 347.1, "weitere_sorgeberechtigte", "ja"),
    Choice(2, 232.0, 347.1, "weitere_sorgeberechtigte", "nein"),
    Choice(2, 196.6, 326.9, "besuchte_kita", "Kita St. Matthäus"),
    Choice(2, 302.9, 326.9, "besuchte_kita", "Kita Pappelweg"),
    Choice(2, 444.7, 326.9, "besuchte_kita", "Kita Weidenstraße"),
    Choice(2, 196.6, 312.3, "besuchte_kita", "Wilde 13"),
    Choice(2, 302.9, 312.3, "besuchte_kita", "Kita Sanddornstraße"),
    Choice(2, 444.7, 312.3, "besuchte_kita", "Andere"),
    # "Die Anmeldedaten wurden vorab digital übermittelt" -- hier immer der Fall.
    Choice(2, 329.1, 182.4, "_digital_uebermittelt", "ja"),
)


def with_master_data(data, student):
    """Leere Felder aus den Stammdaten der Schule auffüllen.

    Nur wo die Eltern nichts eingetragen haben; ihre eigene Angabe hat immer
    Vorrang. Dieselbe Regel gilt im Elternportal für die Vorbelegung.
    """
    merged = dict(data or {})
    known = {
        "kind_nachname": student.nachname,
        "kind_vorname": student.vorname,
        "kind_geburtsdatum": student.geburtsdatum.isoformat() if student.geburtsdatum else "",
        "kind_strasse": student.strasse or "",
        "kind_plz": student.plz or "",
        "kind_ort": student.ort or "",
    }
    for name, value in known.items():
        if value and not (merged.get(name) or "").strip():
            merged[name] = value
    merged["_digital_uebermittelt"] = "ja"
    return merged


def _wrap(text, font, size, width, limit):
    """Text auf ``limit`` Zeilen der Breite ``width`` umbrechen."""
    zeilen, aktuell = [], ""
    for wort in text.split():
        versuch = f"{aktuell} {wort}".strip()
        if aktuell and stringWidth(versuch, font, size) > width:
            zeilen.append(aktuell)
            aktuell = wort
            if len(zeilen) == limit:
                break
        else:
            aktuell = versuch
    if aktuell and len(zeilen) < limit:
        zeilen.append(aktuell)
    return zeilen


def _shrink_to_fit(text, font, size, width):
    """Größe so weit verkleinern, dass der Wert in seine Spalte passt."""
    while size > 6.5 and stringWidth(text, font, size) > width:
        size -= 0.5
    return size


def _overlay(data, page_count, page_size):
    """Die Ebene mit den Werten -- eine leere Seite je Seite der Vorlage."""
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=page_size)
    schrift = letterhead.fonts()["body"]

    for number in range(1, page_count + 1):
        pdf.setFillColorRGB(0, 0, 0)
        for platz in (p for p in PLACEMENTS if p.page == number):
            text = platz.build(data)
            if not text:
                continue
            breite = platz.width or (page_size[0] - platz.x - 45)
            if platz.lines > 1:
                pdf.setFont(schrift, VALUE_SIZE)
                for index, zeile in enumerate(_wrap(text, schrift, VALUE_SIZE, breite, platz.lines)):
                    pdf.drawString(platz.x, platz.y - index * (VALUE_SIZE + 2.0), zeile)
            else:
                pdf.setFont(schrift, _shrink_to_fit(text, schrift, VALUE_SIZE, breite))
                pdf.drawString(platz.x, platz.y, text)

        pdf.setFont(schrift, MARK_SIZE)
        for feld in (c for c in CHOICES if c.page == number):
            if (data.get(feld.field) or "").strip() != feld.value:
                continue
            # Das Kreuz mittig in den vorgedruckten Kreis setzen.
            versatz = (CIRCLE_WIDTH - stringWidth("X", schrift, MARK_SIZE)) / 2
            pdf.drawString(feld.x + versatz, feld.y + 0.5, "X")
        pdf.showPage()

    pdf.save()
    return buffer.getvalue()


def build_many(items, title="Anmeldeformulare"):
    """Mehrere Anmeldungen in einem PDF; ``items`` sind (Daten, Kind)-Paare."""
    vorlage = PdfReader(str(TEMPLATE))
    anzahl, groesse = len(vorlage.pages), page_size(vorlage)
    ebenen = (_overlay(with_master_data(daten, student), anzahl, groesse)
              for daten, student in items)
    return stack(TEMPLATE, ebenen, title=title)


def build(data, student):
    """Die ausgefüllte Anmeldung als PDF-Bytes."""
    return build_many([(data, student)],
                      title=f"Schulanmeldung {student.vorname} {student.nachname}")
