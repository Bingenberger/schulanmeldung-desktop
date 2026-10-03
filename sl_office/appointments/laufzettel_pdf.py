"""Der Laufzettel für die Anmeldung.

Der Bogen wird selbst gesetzt -- mit demselben Briefkopf wie die
Elternschreiben. Er besteht nur aus einer Checkliste, und die gewinnt deutlich,
wenn sie echte Ankreuzfelder, Gruppen und Schreiblinien bekommt.

Die Checkliste pflegt jede Schule unter „Verwaltung → Vorlagen“
(:mod:`sl_office.vorlagen`). Die Seite richtet sich von selbst nach der Liste.
"""

from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from sqlalchemy import select

from models import Schueler, db
from sl_office.appointments.models import AppointmentBooking, AppointmentSlot
from sl_office.appointments.service import active_booking_for_student, slot_label
from sl_office.briefe.letterhead import (
    HEADING_RULE, INK, MARGIN_X, MUTED, RULE, TEXT_W, Flow,
)
from sl_office.vorlagen import Reihe, Zwischenwort, laufzettel_abschnitte


TITEL = "Laufzettel Anmeldung"
TEXT_GROESSE = 11.0
#: Zeilenhöhe der Ankreuzpunkte. Zusammen mit den Abschnittsabständen ist sie
#: so bemessen, dass die Liste samt Notizblock und Unterschrift auf **eine**
#: Seite passt -- der Briefkopf lässt auf der ersten Seite nur bis 2,6 cm über
#: den Rand Platz. Wer Punkte ergänzt, prüft das am besten im Ausdruck nach.
ZEILE = 0.62 * cm
KASTEN_SEITE = 0.34 * cm
#: Höhe des Blocks für handschriftliche Notizen.
NOTIZ_ZEILEN = 5


def _kasten_zeichnen(flow, x, y):
    flow.pdf.setStrokeColor(INK)
    flow.pdf.setLineWidth(0.8)
    flow.pdf.rect(x, y, KASTEN_SEITE, KASTEN_SEITE, stroke=1, fill=0)


def _punkt(flow, text, hinweis="", einzug=0.0):
    """Ein Ankreuzfeld mit Beschriftung; der Hinweis steht klein dahinter."""
    flow.need(ZEILE)
    flow.y -= ZEILE
    _kasten_zeichnen(flow, MARGIN_X + einzug, flow.y + 0.04 * cm)
    flow.pdf.setFillColor(INK)
    flow.pdf.setFont(flow.font["body"], TEXT_GROESSE)
    x = MARGIN_X + einzug + KASTEN_SEITE + 0.28 * cm
    flow.pdf.drawString(x, flow.y + 0.11 * cm, text)
    if hinweis:
        flow.pdf.setFillColor(MUTED)
        flow.pdf.setFont(flow.font["italic"], 8.5)
        flow.pdf.drawString(x + stringWidth(text, flow.font["body"], TEXT_GROESSE) + 0.3 * cm,
                            flow.y + 0.11 * cm, hinweis)
        flow.pdf.setFillColor(INK)


def _optionen(flow, optionen, einzug):
    """Mehrere kleine Ankreuzfelder nebeneinander."""
    flow.need(ZEILE)
    flow.y -= ZEILE
    x = MARGIN_X + einzug
    flow.pdf.setFont(flow.font["body"], TEXT_GROESSE - 0.5)
    for option in optionen:
        _kasten_zeichnen(flow, x, flow.y + 0.04 * cm)
        flow.pdf.setFillColor(INK)
        flow.pdf.drawString(x + KASTEN_SEITE + 0.22 * cm, flow.y + 0.11 * cm, option)
        x += KASTEN_SEITE + 0.22 * cm + stringWidth(
            option, flow.font["body"], TEXT_GROESSE - 0.5) + 0.9 * cm


def _abschnitt(flow, titel):
    flow.need(1.4 * cm)
    flow.y -= 0.58 * cm
    flow.pdf.setFillColor(INK)
    flow.pdf.setFont(flow.font["bold"], 11.5)
    flow.pdf.drawString(MARGIN_X, flow.y, titel)
    flow.pdf.setStrokeColor(HEADING_RULE)
    flow.pdf.setLineWidth(1.0)
    flow.pdf.line(MARGIN_X, flow.y - 0.14 * cm, MARGIN_X + TEXT_W, flow.y - 0.14 * cm)
    flow.y -= 0.18 * cm


def _kopfzeile(flow, beschriftung, wert):
    """Beschriftung klein darüber, Wert groß darunter, abgeschlossen mit einer Linie."""
    wert = wert or "—"
    flow.y -= 0.72 * cm
    flow.pdf.setFillColor(MUTED)
    flow.pdf.setFont(flow.font["body"], 9.5)
    flow.pdf.drawString(MARGIN_X, flow.y + 0.42 * cm, beschriftung.upper())
    flow.pdf.setFillColor(INK)
    groesse = 13.0
    while groesse > 9.0 and stringWidth(wert, flow.font["bold"], groesse) > TEXT_W:
        groesse -= 0.5
    flow.pdf.setFont(flow.font["bold"], groesse)
    flow.pdf.drawString(MARGIN_X, flow.y - 0.06 * cm, wert or "—")
    flow.pdf.setStrokeColor(RULE)
    flow.pdf.setLineWidth(0.7)
    flow.pdf.line(MARGIN_X, flow.y - 0.3 * cm, MARGIN_X + TEXT_W, flow.y - 0.3 * cm)
    flow.y -= 0.3 * cm


def _notizen(flow, titel, zeilen):
    flow.need(0.7 * cm + zeilen * ZEILE)
    flow.y -= 0.58 * cm
    flow.pdf.setFillColor(INK)
    flow.pdf.setFont(flow.font["bold"], 11.5)
    flow.pdf.drawString(MARGIN_X, flow.y, titel)
    flow.pdf.setStrokeColor(RULE)
    flow.pdf.setLineWidth(0.6)
    for _ in range(zeilen):
        flow.y -= ZEILE
        flow.pdf.line(MARGIN_X, flow.y, MARGIN_X + TEXT_W, flow.y)


def _unterschrift(flow, beschriftung):
    flow.need(1.7 * cm)
    flow.y -= 1.25 * cm
    flow.pdf.setStrokeColor(INK)
    flow.pdf.setLineWidth(0.8)
    breite = 8.0 * cm
    flow.pdf.line(MARGIN_X, flow.y, MARGIN_X + breite, flow.y)
    flow.pdf.setFillColor(MUTED)
    flow.pdf.setFont(flow.font["body"], 9.5)
    flow.pdf.drawString(MARGIN_X, flow.y - 0.4 * cm, beschriftung)


def _termin_text(appointment):
    if appointment is None:
        return ""
    _buchung, slot, event = appointment
    return slot_label(slot, event)


def _bogen(flow, student, appointment):
    flow.title_bar(TITEL)
    _kopfzeile(flow, "Name des Kindes", f"{student.vorname} {student.nachname}".strip())
    _kopfzeile(flow, "Anmeldetermin", _termin_text(appointment))
    for titel, punkte in laufzettel_abschnitte(schule=flow.school):
        if titel:
            _abschnitt(flow, titel)
        for punkt in punkte:
            if isinstance(punkt, Zwischenwort):
                flow.y -= 0.42 * cm
                flow.pdf.setFillColor(MUTED)
                flow.pdf.setFont(flow.font["italic"], 10)
                flow.pdf.drawString(MARGIN_X + KASTEN_SEITE + 0.28 * cm, flow.y, punkt.text)
                flow.pdf.setFillColor(INK)
            elif isinstance(punkt, Reihe):
                _punkt(flow, punkt.text)
                _optionen(flow, punkt.optionen, einzug=KASTEN_SEITE + 0.3 * cm)
            else:
                _punkt(flow, punkt.text)
    _notizen(flow, "Weitere Beratungspunkte", NOTIZ_ZEILEN)
    _unterschrift(flow, "Unterschrift Mitarbeiter:in Schule")


def build_many(eintraege, school, title="Laufzettel Anmeldung", doppelseitig=True):
    """Je Kind ein Bogen; ``eintraege`` sind (Kind, Termin)-Paare.

    ``doppelseitig`` hängt an jeden Bogen eine leere Seite, damit beim
    beidseitigen Druck jedes Kind auf einem eigenen Blatt steht.
    """
    puffer = BytesIO()
    pdf = canvas.Canvas(puffer, pagesize=A4)
    pdf.setTitle(title)
    pdf.setAuthor(school.get("name", ""))
    for student, appointment in eintraege:
        flow = Flow(pdf, school)
        _bogen(flow, student, appointment)
        flow.finish()
        if doppelseitig:
            pdf.showPage()
    pdf.save()
    return puffer.getvalue()


def build(student, school, appointment=None):
    """Ein einzelner Bogen -- ohne Leerseite, er wird ja allein gedruckt."""
    return build_many([(student, appointment)], school,
                      title=f"Laufzettel Anmeldung {student.vorname} {student.nachname}",
                      doppelseitig=False)


def fuer_veranstaltung(event):
    """Alle Kinder mit Termin dieser Veranstaltung, nach Terminzeit sortiert.

    Kinder ohne Termin stehen am Ende: Auch sie brauchen einen Laufzettel, wenn
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
