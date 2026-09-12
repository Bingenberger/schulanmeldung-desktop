"""Der Stand des Anmeldeverfahrens, wie Eltern ihn sehen.

Die Schritte stehen an verschiedenen Stellen der Anwendung: der Zugang im
Elternportal, der Termin in der Terminverwaltung, das Formular in der
Anmeldung, der Tag der offenen Tür in seinem eigenen Modul. Für die Eltern ist
das ein Vorgang, und sie sollen auf einen Blick sehen, was erledigt ist und was
noch von ihnen erwartet wird.

Ein Schritt hat genau einen von vier Zuständen:

``erledigt``  getan, hier ist nichts mehr zu tun
``laeuft``    begonnen, aber noch nicht abgeschlossen
``offen``     die Eltern sind am Zug
``wartet``    hängt an der Schule oder an einem Datum
"""

import datetime

from sqlalchemy import func, select

from models import db
from sl_office.parent_portal.models import ParentAccess

ZUSTAENDE = {
    "erledigt": ("Erledigt", "success", "bi-check-circle-fill"),
    "laeuft": ("In Bearbeitung", "primary", "bi-pencil-square"),
    "offen": ("Noch offen", "warning", "bi-exclamation-circle-fill"),
    "wartet": ("Wir melden uns", "secondary", "bi-hourglass-split"),
}


def _schritt(titel, zustand, text, link=None, link_text=""):
    return {"titel": titel, "zustand": zustand, "text": text,
            "link": link, "link_text": link_text}


def _zugang(student_id):
    aktive = db.session.scalar(select(func.count(ParentAccess.id)).where(
        ParentAccess.schueler_id == student_id, ParentAccess.status == "active")) or 0
    if aktive >= 2:
        return _schritt("Zugang eingerichtet", "erledigt",
                        "Beide Zugänge sind eingerichtet.")
    return _schritt(
        "Zugang eingerichtet", "erledigt",
        "Ihr Zugang ist eingerichtet. Der zweite Zugang aus dem Anmeldeschreiben "
        "ist noch offen – geben Sie ihn gerne an die zweite erziehungsberechtigte "
        "Person weiter.")


def _termin(appointment, slot_label, jetzt):
    if appointment is None:
        return _schritt("Anmeldetermin wählen", "offen",
                        "Sie haben noch keinen Termin für das Anmeldegespräch.",
                        "parent_portal.appointments", "Termin auswählen")
    _booking, slot, event = appointment
    beschriftung = slot_label(slot, event)
    if slot.starts_at < jetzt:
        return _schritt("Anmeldetermin wählen", "erledigt", f"Ihr Termin: {beschriftung}")
    return _schritt("Anmeldetermin wählen", "erledigt",
                    f"Ihr Termin: {beschriftung}", "parent_portal.dashboard", "")


def _formular(registration):
    if registration is None or not (registration.data or {}):
        return _schritt("Anmeldeformular ausfüllen", "offen",
                        "Das Formular ist noch nicht begonnen.",
                        "parent_portal.registration", "Formular öffnen")
    if registration.status == "draft":
        return _schritt("Anmeldeformular ausfüllen", "laeuft",
                        "Ihre Angaben sind gespeichert, das Formular ist aber noch "
                        "nicht abgesendet.",
                        "parent_portal.registration", "Weiter ausfüllen")
    abgegeben = (f"Am {registration.submitted_at.strftime('%d.%m.%Y')} übermittelt."
                 if registration.submitted_at else "Übermittelt.")
    if registration.status == "submitted":
        return _schritt("Anmeldeformular ausfüllen", "erledigt",
                        f"{abgegeben} Wir sehen es uns an.",
                        "parent_portal.registration", "Angaben ansehen")
    if registration.status == "in_review":
        return _schritt("Anmeldeformular ausfüllen", "erledigt",
                        f"{abgegeben} Ihre Unterlagen werden gerade geprüft.",
                        "parent_portal.registration", "Angaben ansehen")
    return _schritt("Anmeldeformular ausfüllen", "erledigt",
                    f"{abgegeben} Die Prüfung ist abgeschlossen.",
                    "parent_portal.registration", "Angaben ansehen")


def _gespraech(appointment, registration, jetzt):
    if appointment is None:
        return _schritt("Anmeldegespräch", "wartet",
                        "Sobald Ihr Termin feststeht, sehen Sie ihn hier.")
    _booking, slot, _event = appointment
    if slot.starts_at < jetzt:
        if registration is not None and registration.status == "completed":
            return _schritt("Anmeldegespräch", "erledigt",
                            "Das Gespräch hat stattgefunden, die Anmeldung ist abgeschlossen.")
        return _schritt("Anmeldegespräch", "erledigt", "Das Gespräch hat stattgefunden.")
    tage = (slot.starts_at.date() - jetzt.date()).days
    wann = "heute" if tage == 0 else "morgen" if tage == 1 else f"in {tage} Tagen"
    return _schritt("Anmeldegespräch", "wartet",
                    f"Ihr Gespräch findet {wann} statt. Bitte bringen Sie Ihr Kind mit.")


def _offener_tag(event, eintrag):
    if event is None:
        return None
    if eintrag is None:
        zustand, text = "offen", "Bitte sagen Sie uns kurz Bescheid, ob Sie kommen."
    elif not eintrag.teilnahme:
        zustand, text = "erledigt", "Sie haben abgesagt. Danke für die Rückmeldung."
    elif eintrag.gruppe:
        zustand, text = "erledigt", f"Sie sind angemeldet – Gruppe {eintrag.gruppe}."
    else:
        zustand, text = "erledigt", "Sie sind angemeldet. Den Ablauf schicken wir Ihnen zu."
    return _schritt(f"{event.titel} am {event.datum.strftime('%d.%m.%Y')}", zustand, text,
                    "parent_portal.open_day",
                    "Jetzt zurückmelden" if eintrag is None else "Rückmeldung ändern")


def prozessschritte(student, appointment, registration, slot_label,
                    open_day_event=None, open_day_entry=None, jetzt=None):
    """Die Schritte des Verfahrens in der Reihenfolge, in der sie anstehen."""
    jetzt = jetzt or datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    schritte = [_zugang(student.id)]
    tag = _offener_tag(open_day_event, open_day_entry)
    if tag is not None:
        schritte.append(tag)
    schritte += [
        _termin(appointment, slot_label, jetzt),
        _formular(registration),
        _gespraech(appointment, registration, jetzt),
    ]
    return schritte


def fortschritt(schritte):
    """Anteil der erledigten Schritte in Prozent, für den Balken."""
    if not schritte:
        return 0
    erledigt = sum(1 for schritt in schritte if schritt["zustand"] == "erledigt")
    return round(100 * erledigt / len(schritte))
