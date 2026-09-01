"""Hinweis an die Schule, wenn ein abgesendetes Anmeldeformular noch geändert wird.

Beide Sorgeberechtigten bearbeiten denselben Datensatz. Ändert eine Person etwas,
nachdem die Schule den Vorgang schon gelesen hat, ist das eine Nachricht wert --
sonst prüft die Schule am Ende einen anderen Stand als den, den sie kennt.
"""

from flask import url_for

from models import Schueler, db
from sl_office.parent_portal.mail_service import build_message, send_message, staff_recipient
from sl_office.parent_portal.models import ParentRegistration, utcnow


def _detail_link(registration_id):
    """Direktlink in die Verwaltung, sofern er sich bauen lässt."""
    try:
        return url_for("admin.registration_detail", registration_id=registration_id, _external=True)
    except RuntimeError:
        return ""


def notify_registration_change(app, registration_id, changed_by=""):
    """Die Schule einmal über eine Änderung nach der Abgabe unterrichten.

    Gemeldet wird nur die erste Änderung. Erst wenn die Schule den Vorgang
    wieder aufgreift und den Status anfasst, ist der nächste Hinweis fällig --
    sonst käme bei jedem einzeln gespeicherten Schritt eine Mail.
    """
    registration = db.session.get(ParentRegistration, registration_id)
    if registration is None or registration.change_notified_at is not None:
        return False
    recipient = staff_recipient(app)
    if not recipient:
        app.logger.info("Kein Empfänger für Anmeldebenachrichtigungen konfiguriert")
        return False
    student = db.session.get(Schueler, registration.schueler_id)
    lines = [
        f"Das Anmeldeformular von {student.nachname}, {student.vorname} wurde nach dem "
        "Absenden noch einmal geändert.",
        "",
        f"Stand: Version {registration.version}",
    ]
    if changed_by:
        lines.append(f"Geändert von: {changed_by}")
    lines += ["", "Der Vorgang steht wieder auf „Übermittelt“ und wartet damit auf eine "
                  "erneute Durchsicht."]
    link = _detail_link(registration.id)
    if link:
        lines += ["", link]
    lines += ["", "Diese Nachricht wurde automatisch von SL-Office erzeugt.", ""]
    sent = send_message(app, build_message(
        app, recipient,
        f"Anmeldung geändert: {student.nachname}, {student.vorname}",
        "\n".join(lines),
    ))
    registration.change_notified_at = utcnow()
    db.session.commit()
    return sent
