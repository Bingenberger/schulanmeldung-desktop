"""Die Rückmeldung an die Eltern: ihr persönlicher Ablauf für den Tag.

Verschickt wird erst nach der Einteilung, denn vorher steht die Reihenfolge der
Stationen nicht fest. Jede Familie bekommt nur die Punkte, die sie angekreuzt
hat -- eine vollständige Programmübersicht wäre für die meisten falsch.
"""

from models import Schueler, db
from sl_office.open_day.service import (
    ablaufplan, plan_vermerken, stationsplan, zeitspanne)
from sl_office.parent_portal.letters import german_date
from sl_office.parent_portal.mail_service import build_message, send_message


def _anrede(eintrag):
    name = (eintrag.name or "").strip()
    return f"Guten Tag {name}," if name else "Guten Tag,"


def plan_text(eintrag, event, plan=None, schule=""):
    """Der Nachrichtentext einer Familie."""
    student = db.session.get(Schueler, eintrag.schueler_id)
    kind = student.vorname if student else "Ihr Kind"
    zeilen = [_anrede(eintrag), ""]

    if not eintrag.teilnahme:
        zeilen += [
            f"Sie haben uns mitgeteilt, dass Sie am {event.titel} am "
            f"{german_date(event.datum)} nicht teilnehmen können. Vielen Dank für "
            "Ihre Rückmeldung.",
            "",
            f"Wir freuen uns darauf, Sie und {kind} bei der Schulanmeldung "
            "kennenzulernen.",
        ]
        return _abschluss(zeilen, event, schule)

    zeilen.append(f"schön, dass Sie mit {kind} zum {event.titel} am "
                  f"{german_date(event.datum)} kommen.")
    if event.ort:
        zeilen.append(f"Treffpunkt ist {event.ort}.")
    zeilen.append("")

    geplant = ablaufplan(eintrag, plan)
    if geplant:
        zeilen.append(f"Sie sind der Gruppe {eintrag.gruppe} zugeteilt. "
                      "Ihr persönlicher Ablauf:")
        zeilen.append("")
        for station, label in geplant:
            ort = f" ({station.ort})" if station.ort else ""
            zeilen.append(f"  {zeitspanne(station)}   {label}{ort}")
        zeilen.append("")
        zeilen.append("Bitte finden Sie sich einige Minuten vor dem ersten Punkt ein.")
    else:
        zeilen.append("Sie haben keinen der angebotenen Programmpunkte ausgewählt. "
                      "Schauen Sie sich gerne in Ruhe bei uns um -- wir freuen uns "
                      "auf Ihren Besuch.")
    return _abschluss(zeilen, event, schule)


def _abschluss(zeilen, event, schule):
    if event.hinweise:
        zeilen += ["", event.hinweise.strip()]
    zeilen += ["", "Mit freundlichen Grüßen", schule or "Ihre Schule", ""]
    return "\n".join(zeilen)


def send_plan(app, eintrag, event=None, plan=None):
    """Einer Familie ihren Ablaufplan zustellen.

    Vermerkt wird der Versand hier nicht -- darum kümmert sich
    :func:`send_plans`, das jede Familie einzeln festschreibt.
    """
    event = event or eintrag.event
    if not (eintrag.email or "").strip():
        return False
    schule = app.config.get("SCHOOL_NAME", "")
    betreff = (f"{event.titel} am {german_date(event.datum)}"
               if eintrag.teilnahme else f"Ihre Absage zum {event.titel}")
    return send_message(app, build_message(
        app, eintrag.email, betreff,
        plan_text(eintrag, event, plan=plan, schule=schule)))


def send_plans(app, eintraege, event, nur_offene=True):
    """Stapelversand; liefert ``(zugestellt, uebersprungen)``.

    ``nur_offene`` beschränkt auf Familien, die noch nichts bekommen haben oder
    deren Plan sich seit dem Versand geändert hat -- so lässt sich der Knopf
    gefahrlos zweimal drücken.
    """
    plan = stationsplan(event)
    zugestellt = uebersprungen = 0
    for eintrag in eintraege:
        if nur_offene and eintrag.plan_gesendet_am is not None and not eintrag.plan_veraltet:
            uebersprungen += 1
            continue
        if eintrag.teilnahme and eintrag.wuensche and eintrag.gruppe is None:
            # Ohne Gruppe gäbe es keinen Ablauf zu berichten.
            uebersprungen += 1
            continue
        # Jede Familie einzeln quittieren: eine klemmende Adresse darf die
        # übrigen nicht aufhalten, und der nächste Lauf macht dort weiter.
        try:
            send_plan(app, eintrag, event, plan)
        except Exception:
            db.session.rollback()
            app.logger.exception("Ablaufplan nicht versendet",
                                 extra={"registration_id": eintrag.id})
            uebersprungen += 1
            continue
        plan_vermerken(eintrag)
        db.session.commit()
        zugestellt += 1
    return zugestellt, uebersprungen
