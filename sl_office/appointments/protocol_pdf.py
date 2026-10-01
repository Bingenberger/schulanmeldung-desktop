"""Das Protokoll des Anmeldespiels mit den Anmeldedaten füllen.

``Protokoll_Anmeldespiel.odt`` ist die Vorlage der Schule; sie trägt
Seriendruckfelder für Termin, Namen, Anschrift und Geburtstag. Gedruckt wird
sie wie die Schulanmeldung: Die leere Fassung liegt als
``Protokoll_Anmeldespiel.pdf`` im Projekt, die Werte legt dieses Modul als
zweite Ebene darüber.

Die Grundlinien in :data:`PLACEMENTS` stammen aus der Vorlage selbst. Wird die
ODT geändert, erzeugt ``scripts/protokoll_vorlage.py`` das PDF neu und misst
die Felder nach -- dann sind die Werte hier nachzutragen.
"""

from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from sqlalchemy import select

from models import Schueler, db
from sl_office.appointments.service import active_booking_for_student, slot_label
from sl_office.parent_portal import letterhead
from sl_office.parent_portal.models import AppointmentBooking, AppointmentSlot
from sl_office.services.pdf_forms import page_size, stack

TEMPLATE = Path(__file__).resolve().parents[2] / "Protokoll_Anmeldespiel.pdf"

WERT_GROESSE = 12.0
#: Die Vorlage setzt ihre Ankreuzfelder als "O" in 12 pt.
MARKE_GROESSE = 10.0
KREIS_BREITE = 9.3

#: Feld -> (x, Grundlinie, nutzbare Breite). Alles steht auf Seite 1.
PLACEMENTS = {
    "termin": (42.6, 651.8, 219.0),
    "vorname": (42.6, 607.4, 219.0),
    "nachname": (297.7, 607.4, 220.0),
    "adresse": (42.6, 562.9, 219.0),
    "geburtstag": (297.7, 562.9, 108.0),
    "kita": (42.6, 518.5, 219.0),
}
#: Die vorgedruckten Kreise hinter "Kann-Kind".
KANN_KIND = {True: (410.9, 562.9), False: (446.3, 562.9)}


def _adresse(student):
    """Straße und Ort in einer Zeile -- die Vorlage hat nur eine."""
    ort = " ".join(teil for teil in ((student.plz or "").strip(),
                                     (student.ort or "").strip()) if teil)
    return ", ".join(teil for teil in ((student.strasse or "").strip(), ort) if teil)


def _termin_text(appointment):
    if appointment is None:
        return ""
    _buchung, slot, event = appointment
    return slot_label(slot, event)


def werte(student, appointment=None):
    """Was auf dem Protokoll dieses Kindes steht."""
    return {
        "termin": _termin_text(appointment),
        "vorname": (student.vorname or "").strip(),
        "nachname": (student.nachname or "").strip(),
        "adresse": _adresse(student),
        "geburtstag": (student.geburtsdatum.strftime("%d.%m.%Y")
                       if student.geburtsdatum else ""),
        "kita": (student.kita or "").strip(),
    }


def _passend(text, schrift, breite):
    """Schriftgröße, bei der der Wert in seine Spalte passt."""
    groesse = WERT_GROESSE
    while groesse > 7.0 and stringWidth(text, schrift, groesse) > breite:
        groesse -= 0.5
    return groesse


def _overlay(daten, kann_kind, seitenzahl, groesse):
    """Die Werteebene: beschriftete erste Seite, der Rest bleibt leer."""
    puffer = BytesIO()
    pdf = canvas.Canvas(puffer, pagesize=groesse)
    schrift = letterhead.fonts()["body"]
    pdf.setFillColorRGB(0, 0, 0)
    for feld, (x, y, breite) in PLACEMENTS.items():
        text = daten.get(feld) or ""
        if not text:
            continue
        pdf.setFont(schrift, _passend(text, schrift, breite))
        pdf.drawString(x, y, text)
    if kann_kind is not None:
        x, y = KANN_KIND[bool(kann_kind)]
        pdf.setFont(schrift, MARKE_GROESSE)
        versatz = (KREIS_BREITE - stringWidth("X", schrift, MARKE_GROESSE)) / 2
        pdf.drawString(x + versatz, y + 0.5, "X")
    pdf.showPage()
    for _ in range(seitenzahl - 1):
        pdf.showPage()
    pdf.save()
    return puffer.getvalue()


def _ebenen(eintraege, seitenzahl, groesse):
    for student, appointment in eintraege:
        yield _overlay(werte(student, appointment), student.kann_kind, seitenzahl, groesse)


def build_many(eintraege, title="Protokolle Anmeldespiel"):
    """Ein PDF aus mehreren Protokollen; ``eintraege`` sind (Kind, Termin)-Paare."""
    vorlage = PdfReader(str(TEMPLATE))
    return stack(TEMPLATE, _ebenen(list(eintraege), len(vorlage.pages), page_size(vorlage)),
                 title=title)


def build(student, appointment=None):
    """Das Protokoll eines einzelnen Kindes."""
    return build_many([(student, appointment)],
                      title=f"Protokoll Anmeldespiel {student.vorname} {student.nachname}")


def fuer_veranstaltung(event):
    """Alle Kinder mit Termin dieser Veranstaltung, nach Terminzeit sortiert.

    Kinder ohne Termin stehen am Ende: Auch sie brauchen ein Protokoll, wenn
    sie kurzfristig erscheinen, aber einsortieren lassen sie sich nicht.
    """
    gebucht = db.session.execute(
        select(Schueler, AppointmentSlot)
        .join(AppointmentBooking, AppointmentBooking.schueler_id == Schueler.id)
        .join(AppointmentSlot, AppointmentSlot.id == AppointmentBooking.slot_id)
        .where(AppointmentBooking.event_id == event.id,
               AppointmentBooking.status == "confirmed")
        .order_by(AppointmentSlot.starts_at, Schueler.nachname, Schueler.vorname)).all()
    eintraege = [(student, (None, slot, event)) for student, slot in gebucht]
    versorgt = {student.id for student, _ in eintraege}
    uebrige = select(Schueler).order_by(Schueler.nachname, Schueler.vorname)
    if versorgt:
        uebrige = uebrige.where(Schueler.id.not_in(versorgt))
    eintraege += [(student, active_booking_for_student(student.id))
                  for student in db.session.scalars(uebrige)]
    return eintraege
