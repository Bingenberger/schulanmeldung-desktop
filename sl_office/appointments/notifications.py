"""Terminmails: Bestätigung an die Eltern, Hinweis an die Schule, Erinnerung.

Alle drei Nachrichten entstehen hier, verschickt werden sie über den SMTP-Adapter
in :mod:`sl_office.parent_portal.mail_service`. Der Versand läuft grundsätzlich
*nach* dem Commit der Buchung: eine nicht zustellbare Mail darf einen bereits
vergebenen Termin nicht wieder zurücknehmen.
"""

import datetime

from flask import url_for
from sqlalchemy import select

from models import Schueler, db
from sl_office.appointments import calendar
from sl_office.appointments.service import naive_utc, slot_label
from sl_office.parent_portal.mail_service import build_message, send_message, staff_recipient
from sl_office import school_profile
from sl_office.parent_portal.models import (
    AppointmentBooking, AppointmentEvent, AppointmentSlot, ParentAccess, utcnow,
)

ICS_FILENAME = "Anmeldetermin.ics"


def _school_name(app):
    return school_profile.get("SCHOOL_NAME")


def _portal_link(app):
    """Link in den Elternbereich, sofern er sich bauen lässt.

    Im Erinnerungsdienst gibt es keine Anfrage; ohne ``PUBLIC_BASE_URL`` bleibt
    der Link darum weg, statt den Versand scheitern zu lassen.
    """
    try:
        return url_for("parent_portal.start", _external=True)
    except RuntimeError:
        return ""


def _attach_calendar(message, booking, slot, event, student, app):
    payload = calendar.build_calendar([calendar.parent_entry(
        booking, slot, event, student,
        school_name=_school_name(app),
        instructions=event.parent_instructions or "",
    )], name="Schulanmeldung")
    message.add_attachment(
        payload, maintype="text", subtype="calendar", filename=ICS_FILENAME,
        params={"method": "PUBLISH", "charset": "UTF-8"},
    )
    return message


def _appointment_block(slot, event, app):
    """Die immer gleichen Eckdaten eines Termins als Textabsatz."""
    lines = [slot_label(slot, event)]
    place = slot.location or _school_name(app)
    if place:
        lines.append(f"Ort: {place}")
    return "\n".join(lines)


def _parent_body(app, booking, slot, event, student, opening, closing_hint=True):
    parts = [
        "Guten Tag,",
        "",
        opening,
        "",
        _appointment_block(slot, event, app),
    ]
    if event.parent_instructions:
        parts += ["", event.parent_instructions.strip()]
    parts += ["", f"Der Termin liegt der Mail als Datei {ICS_FILENAME} bei und lässt sich "
                  "damit direkt in Ihren Kalender übernehmen."]
    link = _portal_link(app)
    if link and closing_hint:
        parts += ["", "Im Elternbereich können Sie den Termin einsehen und ihn bis "
                      f"{event.cancellation_deadline_hours} Stunden vorher stornieren:", link]
    elif link:
        parts += ["", f"Elternbereich: {link}"]
    parts += ["", "Mit freundlichen Grüßen", _school_name(app) or "Ihre Schule", ""]
    return "\n".join(parts)


def send_parent_confirmation(app, recipient, booking, slot, event, student):
    """Buchungsbestätigung mit Kalenderdatei."""
    message = build_message(
        app, recipient,
        f"Ihr Anmeldetermin für {student.vorname} {student.nachname}",
        _parent_body(
            app, booking, slot, event, student,
            f"der Anmeldetermin für {student.vorname} {student.nachname} ist verbindlich gebucht:",
        ),
    )
    _attach_calendar(message, booking, slot, event, student, app)
    return send_message(app, message)


def send_parent_reminder(app, recipient, booking, slot, event, student):
    """Erinnerung im vereinbarten Vorlauf vor dem Termin."""
    message = build_message(
        app, recipient,
        f"Erinnerung: Anmeldetermin für {student.vorname} {student.nachname}",
        _parent_body(
            app, booking, slot, event, student,
            f"wir erinnern Sie an den Anmeldetermin für {student.vorname} {student.nachname}:",
            closing_hint=False,
        ),
    )
    _attach_calendar(message, booking, slot, event, student, app)
    return send_message(app, message)


def send_staff_notice(app, booking, slot, event, student, booked_by=""):
    """Hinweis an die Schule, dass Eltern einen Termin gebucht haben."""
    recipient = staff_recipient(app)
    if not recipient:
        app.logger.info("Kein Empfänger für Terminbenachrichtigungen konfiguriert")
        return False
    lines = [
        f"{student.nachname}, {student.vorname} hat einen Anmeldetermin gebucht.",
        "",
        _appointment_block(slot, event, app),
        f"Veranstaltung: {event.title}",
    ]
    if booked_by:
        lines.append(f"Gebucht von: {booked_by}")
    lines += ["", "Diese Nachricht wurde automatisch von SL-Office erzeugt.", ""]
    message = build_message(
        app, recipient,
        f"Neue Terminbuchung: {student.nachname}, {student.vorname}",
        "\n".join(lines),
    )
    return send_message(app, message)


def confirm_booking(app, booking_id):
    """Nach einer Elternbuchung: Bestätigung an die Familie, Hinweis an die Schule.

    Wird nach dem Commit aufgerufen und schluckt keine Fehler -- der Aufrufer
    entscheidet, ob ein fehlgeschlagener Versand die Antwort beeinflusst.
    """
    row = db.session.execute(
        select(AppointmentBooking, AppointmentSlot, AppointmentEvent, Schueler)
        .join(AppointmentSlot, AppointmentSlot.id == AppointmentBooking.slot_id)
        .join(AppointmentEvent, AppointmentEvent.id == AppointmentBooking.event_id)
        .join(Schueler, Schueler.id == AppointmentBooking.schueler_id)
        .where(AppointmentBooking.id == booking_id)
    ).first()
    if row is None:
        return False
    booking, slot, event, student = row
    access = db.session.get(ParentAccess, booking.parent_access_id) if booking.parent_access_id else None
    sent = False
    if access and access.email_normalized:
        sent = send_parent_confirmation(app, access.email_normalized, booking, slot, event, student)
        # Wer kurzfristig bucht, hat die Eckdaten gerade erst gelesen: die
        # Bestätigung ersetzt dann die Erinnerung, die sonst sofort folgen würde.
        if sent and _within_reminder_window(app, slot):
            booking.reminder_sent_at = utcnow()
            db.session.commit()
    send_staff_notice(app, booking, slot, event, student,
                      booked_by=access.display_name if access else "")
    return sent


def _within_reminder_window(app, slot, now=None):
    hours = app.config.get("APPOINTMENT_REMINDER_HOURS", 24)
    now = naive_utc(now or utcnow())
    return naive_utc(slot.starts_at) <= now + datetime.timedelta(hours=hours)


def due_reminders(app, now=None):
    """Bestätigte Termine im Erinnerungsfenster, für die noch nichts raus ist."""
    now = naive_utc(now or utcnow())
    horizon = now + datetime.timedelta(hours=app.config.get("APPOINTMENT_REMINDER_HOURS", 24))
    return db.session.execute(
        select(AppointmentBooking, AppointmentSlot, AppointmentEvent, Schueler)
        .join(AppointmentSlot, AppointmentSlot.id == AppointmentBooking.slot_id)
        .join(AppointmentEvent, AppointmentEvent.id == AppointmentBooking.event_id)
        .join(Schueler, Schueler.id == AppointmentBooking.schueler_id)
        .where(
            AppointmentBooking.status == "confirmed",
            AppointmentBooking.reminder_sent_at.is_(None),
            # Vergangene Termine bleiben liegen: nach einem Ausfall des Dienstes
            # wäre eine Erinnerung im Nachhinein nur noch Verwirrung.
            AppointmentSlot.starts_at > now,
            AppointmentSlot.starts_at <= horizon,
        )
        .order_by(AppointmentSlot.starts_at)
    ).all()


def _reminder_recipients(student_id):
    """Alle aktiven Zugänge des Kindes -- getrennt lebende Eltern haben zwei."""
    return list(db.session.scalars(
        select(ParentAccess).where(
            ParentAccess.schueler_id == student_id,
            ParentAccess.status == "active",
        ).order_by(ParentAccess.id)
    ))


def send_due_reminders(app, now=None):
    """Fällige Erinnerungen zustellen; liefert (verschickt, übersprungen).

    Jede Buchung wird einzeln quittiert, damit ein Fehler bei einer Familie die
    übrigen nicht aufhält und der nächste Lauf genau dort weitermacht.
    """
    sent = skipped = 0
    for booking, slot, event, student in due_reminders(app, now):
        recipients = _reminder_recipients(booking.schueler_id)
        if not recipients:
            # Von der Schule vergebener Termin ohne Elternzugang: nichts zu tun,
            # aber vermerken, damit der Fall nicht bei jedem Lauf wieder auftaucht.
            booking.reminder_sent_at = utcnow()
            db.session.commit()
            skipped += 1
            continue
        try:
            for access in recipients:
                send_parent_reminder(app, access.email_normalized, booking, slot, event, student)
        except Exception:
            db.session.rollback()
            app.logger.exception("Terminerinnerung fehlgeschlagen", extra={"booking": booking.id})
            continue
        booking.reminder_sent_at = utcnow()
        db.session.commit()
        sent += 1
    return sent, skipped
