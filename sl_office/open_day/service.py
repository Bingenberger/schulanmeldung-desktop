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
    OpenDayEvent, OpenDayRegistration, OpenDayStation, OpenDayZuteilung,
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
        "ohne_platz": len(ohne_platz(event)),
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
    db.session.flush()
    zuteilungen_pruefen(event)
    return len(offen)


def ablaufplan(eintrag, plan=None):
    """Die Stationen dieser Familie in der Reihenfolge ihrer Gruppe.

    Liefert ``[(station, label, platz), ...]``; ``platz`` ist ``None``, solange
    die Station sich nicht aufteilt oder noch niemand zugeteilt wurde. Ohne
    Gruppe oder ohne Wünsche ist der Plan leer -- die Familie kommt, nimmt aber
    an keiner Station teil.
    """
    if eintrag.gruppe is None or not eintrag.wuensche:
        return []
    plan = plan if plan is not None else stationsplan(eintrag.event)
    zugeteilt = zuteilung_je_station(eintrag)
    geplant = []
    for art in GRUPPEN_ABLAUF[eintrag.gruppe]:
        if art not in eintrag.wuensche:
            continue
        station = plan.get((eintrag.gruppe, art))
        if station is None:
            continue
        zeile = zugeteilt.get(station.id)
        geplant.append((station, STATION_LABELS[art], zeile.platz if zeile else None))
    return geplant


def plan_vermerken(eintrag):
    """Festhalten, welcher Stand verschickt wurde."""
    eintrag.plan_gesendet_am = utcnow()
    eintrag.plan_signatur = eintrag.signatur


# --- Feineinteilung auf die Plätze einer Station ---------------------------

def zuteilung_je_station(eintrag):
    """``{station_id: zuteilung}`` dieser Familie."""
    return {zeile.station_id: zeile for zeile in eintrag.zuteilungen}


def _passende_stationen(eintrag, plan):
    """Die Stationen mit Plätzen, auf die diese Familie verteilt gehört."""
    if eintrag.gruppe is None or not eintrag.teilnahme:
        return []
    return [station for art in eintrag.wuensche
            if (station := plan.get((eintrag.gruppe, art))) is not None and station.plaetze]


def _zuteilungen_bereinigen(eintrag, gueltige_stationen):
    """Zuteilungen wegräumen, die nicht mehr passen.

    Nötig, sobald eine Familie die Gruppe wechselt oder einen Programmpunkt
    abwählt: Ihr Platz gehört dann zur Station der alten Gruppe und wäre eine
    Einladung in eine Klasse, die zu dieser Zeit gar nicht besucht wird.
    """
    erlaubt = {station.id for station in gueltige_stationen}
    for zeile in list(eintrag.zuteilungen):
        if zeile.station_id not in erlaubt or zeile.platz_id not in {
                platz.id for station in gueltige_stationen for platz in station.plaetze}:
            eintrag.zuteilungen.remove(zeile)
            db.session.delete(zeile)


def zuteilungen_pruefen(event):
    """Nach jeder Änderung an Gruppe oder Wünschen die Plätze nachziehen.

    Wird von allen Stellen aufgerufen, die daran drehen -- der Einteilung, der
    Handkorrektur und dem Elternformular -- damit niemand mit einer Einladung
    in eine Klasse dasteht, die seine Gruppe zu der Zeit nicht besucht.
    """
    plan = stationsplan(event)
    for eintrag, _ in anmeldungen(event):
        _zuteilungen_bereinigen(eintrag, _passende_stationen(eintrag, plan))


def _platzbelegung(eintraege):
    """Wie viele Familien je Platz bereits eingeteilt sind."""
    belegt = {}
    for eintrag in eintraege:
        for zeile in eintrag.zuteilungen:
            belegt[zeile.platz_id] = belegt.get(zeile.platz_id, 0) + 1
    return belegt


def _freiester_platz(station, belegt):
    """Der Platz mit dem meisten Luft; volle Plätze bleiben außen vor.

    Bemisst sich am Füllgrad, damit eine kleine Klasse neben einer großen
    nicht überläuft. Ohne angegebene Kapazität zählt die reine Anzahl.
    """
    offen = [platz for platz in station.plaetze
             if platz.kapazitaet is None or belegt.get(platz.id, 0) < platz.kapazitaet]
    if not offen:
        return None
    return min(offen, key=lambda platz: (
        belegt.get(platz.id, 0) / platz.kapazitaet if platz.kapazitaet else 0,
        belegt.get(platz.id, 0),
        platz.bezeichnung,
    ))


def plaetze_zuteilen(event, neu_verteilen=False):
    """Die eingeteilten Familien auf die Plätze ihrer Stationen verteilen.

    Liefert ``(zugeteilt, ohne_platz)``. ``ohne_platz`` zählt die Fälle, in
    denen alle Plätze einer Station belegt waren -- dann fehlt Kapazität, und
    das muss jemand sehen, statt dass still jemand unter den Tisch fällt.
    """
    plan = stationsplan(event)
    eintraege = [eintrag for eintrag, _ in anmeldungen(event, nur_teilnehmer=True)]
    for eintrag in eintraege:
        _zuteilungen_bereinigen(eintrag, _passende_stationen(eintrag, plan))
        if neu_verteilen:
            for zeile in list(eintrag.zuteilungen):
                eintrag.zuteilungen.remove(zeile)
                db.session.delete(zeile)
    db.session.flush()

    belegt = _platzbelegung(eintraege)
    zugeteilt = ohne_platz = 0
    for eintrag in eintraege:
        vorhanden = zuteilung_je_station(eintrag)
        for station in _passende_stationen(eintrag, plan):
            if station.id in vorhanden:
                continue
            platz = _freiester_platz(station, belegt)
            if platz is None:
                ohne_platz += 1
                continue
            eintrag.zuteilungen.append(OpenDayZuteilung(station_id=station.id, platz_id=platz.id))
            belegt[platz.id] = belegt.get(platz.id, 0) + 1
            zugeteilt += 1
    return zugeteilt, ohne_platz


def platz_setzen(eintrag, station, platz):
    """Eine Familie von Hand auf einen Platz setzen (oder ``None``: herunternehmen)."""
    vorhanden = zuteilung_je_station(eintrag).get(station.id)
    if vorhanden is not None:
        eintrag.zuteilungen.remove(vorhanden)
        db.session.delete(vorhanden)
    if platz is not None:
        eintrag.zuteilungen.append(OpenDayZuteilung(station_id=station.id, platz_id=platz.id))


def platzbelegung(event):
    """``{platz_id: [(eintrag, kind), ...]}`` -- wer wo hospitiert."""
    belegung = {}
    for eintrag, kind in anmeldungen(event, nur_teilnehmer=True):
        for zeile in eintrag.zuteilungen:
            belegung.setdefault(zeile.platz_id, []).append((eintrag, kind))
    for familien in belegung.values():
        familien.sort(key=lambda paar: (paar[1].nachname, paar[1].vorname))
    return belegung


def ohne_platz(event):
    """Familien, denen an einer Station mit Plätzen noch keiner zugewiesen ist."""
    plan = stationsplan(event)
    offen = []
    for eintrag, kind in anmeldungen(event, nur_teilnehmer=True):
        vorhanden = zuteilung_je_station(eintrag)
        for station in _passende_stationen(eintrag, plan):
            if station.id not in vorhanden:
                offen.append((eintrag, kind, station))
    return offen


def uhrzeit(wert):
    return wert.strftime("%H:%M") if isinstance(wert, datetime.time) else ""


def zeitspanne(station):
    return f"{uhrzeit(station.beginn)}–{uhrzeit(station.ende)} Uhr"
