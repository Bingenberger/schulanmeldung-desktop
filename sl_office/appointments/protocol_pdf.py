"""Das Protokoll des Anmeldespiels.

Jede Schule führt ihr Anmeldespiel anders. Das Protokoll eines Kindes setzt
sich darum aus drei Teilen zusammen:

1. ein **Deckblatt** im Briefkopf der Schule mit Termin, Name, Anschrift,
   Geburtsdatum, Kita und Kann-Kind-Vermerk,
2. das **Material der Schule** -- Aufgaben, Gesprächsfragen, Beobachtungs-
   hilfen --, so wie es unter „Verwaltung → Vorlagen“ als PDF hinterlegt ist
   (:mod:`sl_office.vorlagen`); ohne hinterlegtes Material entfällt der Teil,
3. ein **Auswertungsbogen** aus den Kriterien der Pädagogischen Diagnostik
   (:mod:`sl_office.criteria`): jedes Kriterium mit Ankreuzfeldern, so dass
   sich das Ergebnis danach eins zu eins in die Anwendung übertragen lässt.

Gedruckt wird beidseitig; jedes Kind beginnt auf einem eigenen Blatt.
"""

from io import BytesIO

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from sqlalchemy import select

from models import Schueler, db
from sl_office import features, vorlagen
from sl_office.appointments.admin_protocol_pdf import (
    KASTEN_SEITE, TEXT_GROESSE, ZEILE, _abschnitt, _kasten_zeichnen, _kopfzeile, _notizen,
)
from sl_office.appointments.service import active_booking_for_student, slot_label
from sl_office.criteria import service as criteria
from sl_office.parent_portal import letterhead, registration_form
from sl_office.parent_portal.letterhead import INK, MARGIN_X, RULE, TEXT_W, Flow
from sl_office.parent_portal.models import AppointmentBooking, AppointmentSlot, ParentRegistration

TITEL = "Protokoll Anmeldespiel"
AUSWERTUNG = "Auswertung Anmeldespiel"
SKALA = ("++", "+", "o", "–")
#: Wo die Ankreuzfelder eines Kriteriums beginnen; links davon steht sein Name.
SPALTE = 7.2 * cm


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


def kita_aus_anmeldung(schueler_ids=None):
    """``{schueler_id: Einrichtung}`` aus den übermittelten Anmeldeformularen.

    Einmal für alle Kinder zusammen, damit ein Sammeldruck nicht je Bogen
    erneut fragt.
    """
    abfrage = select(ParentRegistration)
    if schueler_ids is not None:
        abfrage = abfrage.where(ParentRegistration.schueler_id.in_(schueler_ids))
    gefunden = {}
    for anmeldung in db.session.scalars(abfrage):
        kita = registration_form.kita_angabe(anmeldung.data or {})
        if kita:
            gefunden[anmeldung.schueler_id] = kita
    return gefunden


def werte(student, appointment=None, kita=None):
    """Was auf dem Protokoll dieses Kindes steht.

    Die Kita nennen die Eltern im Anmeldeformular selbst; das Stammdatenfeld
    der Schule tritt nur ein, wenn dort nichts steht.
    """
    return {
        "termin": _termin_text(appointment),
        "vorname": (student.vorname or "").strip(),
        "nachname": (student.nachname or "").strip(),
        "adresse": _adresse(student),
        "geburtstag": (student.geburtsdatum.strftime("%d.%m.%Y")
                       if student.geburtsdatum else ""),
        "kita": (kita or "").strip() or (student.kita or "").strip(),
    }


def _ankreuzen(flow, x, optionen, gesetzt=None):
    """Kästchen mit Beschriftung nebeneinander; bricht um, wenn der Platz endet.

    ``gesetzt`` ist die Option, die vorab angekreuzt wird.
    """
    rechts = MARGIN_X + TEXT_W
    start = x
    flow.pdf.setFont(flow.font["body"], TEXT_GROESSE - 0.5)
    for option in optionen:
        breite = KASTEN_SEITE + 0.22 * cm + stringWidth(option, flow.font["body"], TEXT_GROESSE - 0.5)
        if x > start and x + breite > rechts:
            flow.need(ZEILE)
            flow.y -= ZEILE
            x = start
        _kasten_zeichnen(flow, x, flow.y + 0.04 * cm)
        if option == gesetzt:
            flow.pdf.setFont(flow.font["bold"], TEXT_GROESSE)
            flow.pdf.drawCentredString(x + KASTEN_SEITE / 2, flow.y + 0.08 * cm, "X")
            flow.pdf.setFont(flow.font["body"], TEXT_GROESSE - 0.5)
        flow.pdf.setFillColor(INK)
        flow.pdf.drawString(x + KASTEN_SEITE + 0.22 * cm, flow.y + 0.11 * cm, option)
        x += breite + 0.7 * cm


def _beschriftung(flow, text):
    """Name eines Kriteriums links; zu lange Namen werden kleiner gesetzt."""
    groesse = TEXT_GROESSE
    while groesse > 8 and stringWidth(text, flow.font["body"], groesse) > SPALTE - 0.4 * cm:
        groesse -= 0.5
    flow.pdf.setFillColor(INK)
    flow.pdf.setFont(flow.font["body"], groesse)
    flow.pdf.drawString(MARGIN_X, flow.y + 0.11 * cm, text)


def _schreiblinie(flow, x):
    flow.pdf.setStrokeColor(RULE)
    flow.pdf.setLineWidth(0.6)
    flow.pdf.line(x, flow.y + 0.02 * cm, MARGIN_X + TEXT_W, flow.y + 0.02 * cm)


def _kriterium(flow, kriterium):
    flow.need(ZEILE * 1.2)
    flow.y -= ZEILE
    _beschriftung(flow, kriterium.bezeichnung)
    x = MARGIN_X + SPALTE
    if kriterium.typ == "skala":
        _ankreuzen(flow, x, SKALA)
    elif kriterium.typ == "janein":
        _ankreuzen(flow, x, ("ja",))
    elif kriterium.typ in ("auswahl", "mehrfach"):
        _ankreuzen(flow, x, kriterium.optionsliste)
    else:
        _schreiblinie(flow, x)


def _deckblatt(flow, daten, kann_kind):
    flow.title_bar(TITEL)
    _kopfzeile(flow, "Anmeldetermin", daten["termin"])
    _kopfzeile(flow, "Name des Kindes", f"{daten['vorname']} {daten['nachname']}".strip())
    _kopfzeile(flow, "Anschrift", daten["adresse"])
    _kopfzeile(flow, "Geburtsdatum", daten["geburtstag"])
    _kopfzeile(flow, "Kita", daten["kita"])
    flow.y -= ZEILE
    _beschriftung(flow, "Kann-Kind")
    _ankreuzen(flow, MARGIN_X + SPALTE, ("Ja", "Nein"), "Ja" if kann_kind else "Nein")
    flow.y -= ZEILE
    _beschriftung(flow, "Durchführende Lehrkraft")
    _schreiblinie(flow, MARGIN_X + SPALTE)
    _notizen(flow, "Gespräch mit den Eltern", 8)


def _auswertung(flow, daten, module):
    flow.title_bar(AUSWERTUNG)
    _kopfzeile(flow, "Name des Kindes", f"{daten['vorname']} {daten['nachname']}".strip())
    if module["diagnostik"]:
        for gruppe, liste in criteria.gruppiert(criteria.kriterien("diagnostik")):
            _abschnitt(flow, gruppe or "Beobachtungen")
            for kriterium in liste:
                _kriterium(flow, kriterium)
        _abschnitt(flow, "Gesamteindruck")
        for text in ("kognitiv", "Verhalten"):
            flow.need(ZEILE)
            flow.y -= ZEILE
            _beschriftung(flow, text)
            _ankreuzen(flow, MARGIN_X + SPALTE, SKALA)
    weiteres = [(text, ("ja", "nein")) for schalter, text in (
        ("schulspiel", "Einladung zum Schulspiel"),
        ("aosf", "Verdacht auf AO-SF"),
        ("rueckstellung", "Rückstellung empfehlen"),
    ) if module[schalter]]
    if weiteres:
        _abschnitt(flow, "Weiteres Vorgehen")
        for text, optionen in weiteres:
            flow.need(ZEILE)
            flow.y -= ZEILE
            _beschriftung(flow, text)
            _ankreuzen(flow, MARGIN_X + SPALTE, optionen)
    # So viele Schreiblinien, wie noch auf die Seite passen -- eine
    # Folgeseite nur für zwei Notizzeilen wäre Papierverschwendung.
    platz = int((flow.y - flow._bottom - 0.9 * cm) / ZEILE)
    _notizen(flow, "Sonstige Beobachtungen", max(2, min(6, platz)))


def _seiten(zeichnen, school):
    puffer = BytesIO()
    pdf = canvas.Canvas(puffer, pagesize=A4)
    flow = Flow(pdf, school)
    zeichnen(flow)
    flow.finish()
    pdf.save()
    return PdfReader(BytesIO(puffer.getvalue()))


def build_many(eintraege, title="Protokolle Anmeldespiel", school=None):
    """Ein PDF aus mehreren Protokollen; ``eintraege`` sind (Kind, Termin)-Paare."""
    eintraege = list(eintraege)
    school = school if school is not None else letterhead.branding()
    module = features.module_states()
    material = vorlagen.material()
    material_reader = PdfReader(BytesIO(material)) if material else None
    aus_anmeldung = kita_aus_anmeldung([student.id for student, _ in eintraege])

    writer = PdfWriter()
    for student, appointment in eintraege:
        daten = werte(student, appointment, aus_anmeldung.get(student.id))
        anfang = len(writer.pages)
        writer.append(_seiten(lambda flow: _deckblatt(flow, daten, student.kann_kind), school))
        if material_reader is not None:
            writer.append(material_reader)
        writer.append(_seiten(lambda flow: _auswertung(flow, daten, module), school))
        # Beidseitiger Druck: jedes Kind beginnt auf einem eigenen Blatt.
        if (len(writer.pages) - anfang) % 2:
            writer.add_blank_page(*A4)
    writer.add_metadata({"/Title": title, "/Producer": "SL-Office"})
    try:
        writer.compress_identical_objects()
    except AttributeError:      # ältere pypdf-Fassung: dann eben größer
        pass
    ergebnis = BytesIO()
    writer.write(ergebnis)
    return ergebnis.getvalue()


def build(student, appointment=None, school=None):
    """Das Protokoll eines einzelnen Kindes."""
    return build_many([(student, appointment)], school=school,
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
