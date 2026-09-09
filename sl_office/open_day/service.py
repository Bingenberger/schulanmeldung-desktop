"""Einteilung in die beiden Gruppen und der persönliche Ablaufplan.

Nicht jede Familie will alles sehen. Die Einteilung muss deshalb nicht nur die
Gruppen gleich groß machen, sondern jede *Station* für sich ausgleichen: Eine
Führung, zu der in Gruppe 1 dreißig und in Gruppe 2 fünf Familien kommen, wäre
gleichmäßig eingeteilt und trotzdem unbrauchbar.
"""

import datetime

from sqlalchemy import select

from models import Schueler, db
from sl_office.open_day.models import (
    GRUPPEN, GRUPPEN_ABLAUF, STANDARDZEITEN, STATIONEN, STATION_ARTEN, STATION_LABELS,
    OpenDayEvent, OpenDayRegistration, OpenDayStation,
)
from sl_office.parent_portal.models import utcnow


def veroeffentlichtes_event(jahr):
    """Die für Eltern sichtbare Veranstaltung des Jahrgangs, sonst ``None``."""
    return db.session.scalar(select(OpenDayEvent).where(
        OpenDayEvent.school_year == jahr, OpenDayEvent.status == "published"))


def events_des_jahrgangs(jahr):
    return list(db.session.scalars(select(OpenDayEvent)
                                   .where(OpenDayEvent.school_year == jahr)
                                   .order_by(OpenDayEvent.datum.desc())))


def standard_stationen(event):
    """Die sechs Stationen mit den Vorgabezeiten anlegen."""
    for gruppe in GRUPPEN:
        for art in GRUPPEN_ABLAUF[gruppe]:
            beginn, ende = STANDARDZEITEN[(gruppe, art)]
            event.stationen.append(OpenDayStation(gruppe=gruppe, art=art,
                                                  beginn=beginn, ende=ende))
    return event.stationen


def stationsplan(event):
    """``{(gruppe, art): station}`` -- der Fahrplan der Veranstaltung."""
    return {(station.gruppe, station.art): station for station in event.stationen}


def anmeldungen(event, nur_teilnehmer=False):
    """Alle Rückmeldungen mit dem zugehörigen Kind, nach Nachnamen sortiert."""
    abfrage = (select(OpenDayRegistration, Schueler)
               .join(Schueler, Schueler.id == OpenDayRegistration.schueler_id)
               .where(OpenDayRegistration.event_id == event.id)
               .order_by(Schueler.nachname, Schueler.vorname))
    if nur_teilnehmer:
        abfrage = abfrage.where(OpenDayRegistration.teilnahme.is_(True))
    return list(db.session.execute(abfrage).all())


def zaehlung(event):
    """Kennzahlen für die Auswertung."""
    paare = anmeldungen(event)
    teilnehmer = [eintrag for eintrag, _ in paare if eintrag.teilnahme]
    werte = {
        "rueckmeldungen": len(paare),
        "zusagen": len(teilnehmer),
        "absagen": len(paare) - len(teilnehmer),
        "ohne_gruppe": sum(1 for eintrag in teilnehmer if eintrag.gruppe is None),
        "plan_offen": sum(1 for eintrag in teilnehmer
                          if eintrag.gruppe and eintrag.plan_gesendet_am is None),
        "plan_veraltet": sum(1 for eintrag in teilnehmer if eintrag.plan_veraltet),
    }
    for art, _ in STATIONEN:
        werte[art] = {gruppe: sum(1 for eintrag in teilnehmer
                                  if art in eintrag.wuensche and eintrag.gruppe == gruppe)
                      for gruppe in GRUPPEN}
        werte[art]["gesamt"] = sum(1 for eintrag in teilnehmer if art in eintrag.wuensche)
    return werte


def _belegung(eintraege):
    """Wie viele Familien je Gruppe und Station bereits eingeteilt sind."""
    return {(gruppe, art): sum(1 for eintrag in eintraege
                               if eintrag.gruppe == gruppe and art in eintrag.wuensche)
            for gruppe in GRUPPEN for art in STATION_ARTEN}


def einteilen(event, neu_verteilen=False):
    """Die Teilnehmenden auf die beiden Gruppen verteilen.

    Vergeben wird der Reihe nach: Jede Familie kommt in die Gruppe, in der ihre
    gewünschten Stationen bislang am schwächsten besetzt sind. Damit gleichen
    sich alle drei Stationen gleichzeitig aus, ohne dass eine davon bevorzugt
    werden müsste.

    Ohne ``neu_verteilen`` bleiben bereits eingeteilte Familien, wo sie sind --
    eine schon verschickte Rückmeldung soll nicht ohne Not hinfällig werden.
    Die neu Hinzugekommenen füllen dann die dünner besetzte Seite auf.
    """
    eintraege = [eintrag for eintrag, _ in anmeldungen(event, nur_teilnehmer=True)]
    for eintrag in eintraege:
        if not eintrag.wuensche:
            # Wer nichts sehen möchte, braucht keine Gruppe -- er kommt einfach.
            eintrag.gruppe = None
        elif neu_verteilen:
            eintrag.gruppe = None

    offen = [eintrag for eintrag in eintraege if eintrag.gruppe is None and eintrag.wuensche]
    # Die anspruchsvollsten Familien zuerst: wer alle drei Stationen mitnimmt,
    # bindet am meisten Platz und soll nicht am Ende auf eine volle Seite fallen.
    offen.sort(key=lambda eintrag: (-len(eintrag.wuensche), eintrag.schueler_id))

    belegt = _belegung(eintraege)
    for eintrag in offen:
        gruppe = min(GRUPPEN, key=lambda nummer: (
            max(belegt[(nummer, art)] for art in eintrag.wuensche),
            sum(belegt[(nummer, art)] for art in eintrag.wuensche),
            nummer,
        ))
        eintrag.gruppe = gruppe
        for art in eintrag.wuensche:
            belegt[(gruppe, art)] += 1
    return len(offen)


def ablaufplan(eintrag, plan=None):
    """Die Stationen dieser Familie in der Reihenfolge ihrer Gruppe.

    Liefert ``[(station, label), ...]``. Ohne Gruppe oder ohne Wünsche ist der
    Plan leer -- die Familie kommt, nimmt aber an keiner Station teil.
    """
    if eintrag.gruppe is None or not eintrag.wuensche:
        return []
    plan = plan if plan is not None else stationsplan(eintrag.event)
    geplant = []
    for art in GRUPPEN_ABLAUF[eintrag.gruppe]:
        if art not in eintrag.wuensche:
            continue
        station = plan.get((eintrag.gruppe, art))
        if station is not None:
            geplant.append((station, STATION_LABELS[art]))
    return geplant


def plan_vermerken(eintrag):
    """Festhalten, welcher Stand verschickt wurde."""
    eintrag.plan_gesendet_am = utcnow()
    eintrag.plan_signatur = eintrag.signatur


def uhrzeit(wert):
    return wert.strftime("%H:%M") if isinstance(wert, datetime.time) else ""


def zeitspanne(station):
    return f"{uhrzeit(station.beginn)}–{uhrzeit(station.ende)} Uhr"
