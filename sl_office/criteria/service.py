"""Kriterien lesen, Werte erfassen und auswerten.

Werte liegen als Text in ``kriterium_wert``; hier werden sie je Feldtyp
gelesen, geprüft und für Anzeige und Druck aufbereitet.

* ``skala``    -- "0" bis "3" (– bis ++)
* ``janein``   -- "1" oder "0"
* ``auswahl``  -- der gewählte Eintrag
* ``mehrfach`` -- die gewählten Einträge, je Zeile einer
* ``text``     -- Freitext
* ``datum``    -- ISO-Datum
"""

import datetime

from sqlalchemy import select

from models import Diagnostik, SchulaerztlicheUntersuchung, SchulspielDiagnostik, db
from sl_office.criteria.catalog import BOEGEN, SKALA_TEXT, STANDARD, TYPEN
from sl_office.criteria.models import Kriterium, KriteriumWert

#: Tabellen, in denen die Serverfassung die Werte fest gespeichert hat.
ALTTABELLEN = {
    "diagnostik": Diagnostik,
    "schulspiel": SchulspielDiagnostik,
    "schularzt": SchulaerztlicheUntersuchung,
}


class Eingabefehler(ValueError):
    """Pflichtangaben fehlen oder Werte passen nicht zum Feldtyp."""

    def __init__(self, fehler):
        super().__init__("; ".join(fehler))
        self.fehler = fehler


def feldname(kriterium):
    return f"k{kriterium.id}"


# --- Katalog -----------------------------------------------------------------

def ensure_catalog(bogen):
    """Den vorbelegten Katalog anlegen, wenn der Bogen noch keine Kriterien hat.

    Dabei werden Werte, die noch in den alten festen Spalten stehen, einmalig
    übernommen -- so bleiben Daten aus der Serverfassung erhalten.
    """
    if bogen not in BOEGEN:
        raise KeyError(bogen)
    if db.session.scalar(select(Kriterium.id).where(Kriterium.bogen == bogen).limit(1)):
        return False
    angelegt = []
    for index, eintrag in enumerate(STANDARD[bogen]):
        kriterium = Kriterium(bogen=bogen, reihenfolge=(index + 1) * 10, **eintrag)
        db.session.add(kriterium)
        angelegt.append(kriterium)
    db.session.flush()
    _uebernehmen(bogen, angelegt)
    # Sofort festschreiben: sonst legte jede Anfrage den Katalog neu an, und
    # die Feldnamen (k<ID>) eines angezeigten Formulars passten beim
    # Absenden nicht mehr. Aufgerufen wird das vor jeder anderen Änderung.
    db.session.commit()
    return True


def _uebernehmen(bogen, kriterien):
    from sl_office.school_year import UNSCOPED

    tabelle = ALTTABELLEN[bogen]
    zeilen = db.session.scalars(
        select(tabelle).execution_options(**{UNSCOPED: True})).all()
    for zeile in zeilen:
        for kriterium in kriterien:
            if not kriterium.altfeld or not hasattr(zeile, kriterium.altfeld):
                continue
            wert = _alt_als_text(kriterium, getattr(zeile, kriterium.altfeld))
            if wert:
                db.session.add(KriteriumWert(kriterium_id=kriterium.id,
                                             schueler_id=zeile.schueler_id, wert=wert))


def _alt_als_text(kriterium, wert):
    if wert is None:
        return ""
    if kriterium.typ == "janein":
        return "1" if wert else ""
    if kriterium.typ == "datum":
        return wert.isoformat() if hasattr(wert, "isoformat") else str(wert)
    if kriterium.typ == "mehrfach":
        return "\n".join(teil.strip() for teil in str(wert).split(",") if teil.strip())
    return str(wert).strip()


def kriterien(bogen, nur_aktive=True):
    ensure_catalog(bogen)
    abfrage = select(Kriterium).where(Kriterium.bogen == bogen)
    if nur_aktive:
        abfrage = abfrage.where(Kriterium.aktiv.is_(True))
    return db.session.scalars(abfrage.order_by(Kriterium.reihenfolge, Kriterium.id)).all()


def gruppiert(liste):
    """[(Gruppe, [Kriterien])] in Katalogreihenfolge."""
    gruppen = []
    for kriterium in liste:
        if not gruppen or gruppen[-1][0] != kriterium.gruppe:
            gruppen.append((kriterium.gruppe, []))
        gruppen[-1][1].append(kriterium)
    return gruppen


# --- Werte -------------------------------------------------------------------

def _rohwerte(schueler_id, bogen):
    zeilen = db.session.execute(
        select(KriteriumWert.kriterium_id, KriteriumWert.wert)
        .join(Kriterium, Kriterium.id == KriteriumWert.kriterium_id)
        .where(KriteriumWert.schueler_id == schueler_id, Kriterium.bogen == bogen))
    return {kid: wert for kid, wert in zeilen}


def lesen(kriterium, text):
    """Gespeicherten Text in den passenden Python-Wert wandeln."""
    if text is None or text == "":
        return [] if kriterium.typ == "mehrfach" else None
    if kriterium.typ == "skala":
        try:
            return int(text)
        except ValueError:
            return None
    if kriterium.typ == "janein":
        return text == "1"
    if kriterium.typ == "mehrfach":
        return [teil for teil in text.splitlines() if teil]
    if kriterium.typ == "datum":
        try:
            return datetime.date.fromisoformat(text)
        except ValueError:
            return None
    return text


def werte(schueler_id, bogen):
    """{Kriterium-ID: Wert} aller erfassten Kriterien des Kindes."""
    ensure_catalog(bogen)
    roh = _rohwerte(schueler_id, bogen)
    alle = {k.id: k for k in kriterien(bogen, nur_aktive=False)}
    return {kid: lesen(alle[kid], text) for kid, text in roh.items() if kid in alle}


def _aus_formular(kriterium, formular):
    name = feldname(kriterium)
    if kriterium.typ == "mehrfach":
        erlaubt = kriterium.optionsliste
        gewaehlt = [wert for wert in formular.getlist(name) if wert in erlaubt]
        return "\n".join(gewaehlt), None
    roh = (formular.get(name) or "").strip()
    if kriterium.typ == "janein":
        return ("1" if roh in ("1", "on", "y", "true") else "0"), None
    if not roh:
        return "", None
    if kriterium.typ == "skala":
        if roh not in {"0", "1", "2", "3"}:
            return "", f"{kriterium.bezeichnung}: ungültiger Wert"
        return roh, None
    if kriterium.typ == "auswahl":
        if roh not in kriterium.optionsliste:
            return "", f"{kriterium.bezeichnung}: ungültige Auswahl"
        return roh, None
    if kriterium.typ == "datum":
        try:
            return datetime.date.fromisoformat(roh).isoformat(), None
        except ValueError:
            return "", f"{kriterium.bezeichnung}: kein gültiges Datum"
    return roh[:2000], None


def pruefen(bogen, formular):
    """{Kriterium-ID: Text} aus dem Formular; wirft :class:`Eingabefehler`."""
    gelesen, fehler = {}, []
    for kriterium in kriterien(bogen):
        text, problem = _aus_formular(kriterium, formular)
        if problem:
            fehler.append(problem)
        elif kriterium.pflicht and kriterium.typ != "janein" and not text:
            fehler.append(f"{kriterium.bezeichnung}: bitte angeben")
        gelesen[kriterium.id] = text
    if fehler:
        raise Eingabefehler(fehler)
    return gelesen


def speichern(schueler_id, bogen, formular):
    """Werte aus dem Formular übernehmen. Nur aktive Kriterien werden berührt;
    Werte abgeschalteter Kriterien bleiben stehen."""
    gelesen = pruefen(bogen, formular)
    vorhanden = {zeile.kriterium_id: zeile for zeile in db.session.scalars(
        select(KriteriumWert).where(KriteriumWert.schueler_id == schueler_id,
                                    KriteriumWert.kriterium_id.in_(list(gelesen))))}
    for kid, text in gelesen.items():
        zeile = vorhanden.get(kid)
        if not text:
            if zeile is not None:
                db.session.delete(zeile)
        elif zeile is None:
            db.session.add(KriteriumWert(kriterium_id=kid, schueler_id=schueler_id, wert=text))
        else:
            zeile.wert = text
    return gelesen


def formular_werte(bogen, formular):
    """Werte aus einem abgeschickten Formular, um es nach Fehlern neu zu füllen."""
    ergebnis = {}
    for kriterium in kriterien(bogen):
        text, _ = _aus_formular(kriterium, formular)
        ergebnis[kriterium.id] = lesen(kriterium, text)
    return ergebnis


# --- Auswertung --------------------------------------------------------------

def anzeigetext(kriterium, wert):
    if wert is None or wert == []:
        return ""
    if kriterium.typ == "skala":
        return SKALA_TEXT.get(wert, "")
    if kriterium.typ == "janein":
        return "ja" if wert else "nein"
    if kriterium.typ == "mehrfach":
        return ", ".join(wert)
    if kriterium.typ == "datum":
        return wert.strftime("%d.%m.%Y")
    return str(wert)


def eintraege(schueler_id, bogen):
    """[(Kriterium, Wert)] für Anzeige und Druck.

    Aktive Kriterien immer, abgeschaltete nur, wenn für das Kind ein Wert
    erfasst ist -- so verschwindet nichts, was schon eingetragen wurde.
    """
    vorhanden = werte(schueler_id, bogen)
    return [(k, vorhanden.get(k.id)) for k in kriterien(bogen, nur_aktive=False)
            if k.aktiv or k.id in vorhanden]


def eintraege_gruppiert(schueler_id, bogen):
    """[(Gruppe, [(Kriterium, Wert)])] in Katalogreihenfolge."""
    gruppen = []
    for kriterium, wert in eintraege(schueler_id, bogen):
        if not gruppen or gruppen[-1][0] != kriterium.gruppe:
            gruppen.append((kriterium.gruppe, []))
        gruppen[-1][1].append((kriterium, wert))
    return gruppen


def wertung(schueler_id, bogen, vorhanden=None):
    """(Summe, Anzahl bewerteter Kriterien, Höchstwert) der Skalen in der Wertung."""
    vorhanden = werte(schueler_id, bogen) if vorhanden is None else vorhanden
    gewertet = [k for k in kriterien(bogen) if k.typ == "skala" and k.in_wertung]
    summe = sum(vorhanden[k.id] for k in gewertet if vorhanden.get(k.id) is not None)
    anzahl = sum(1 for k in gewertet if vorhanden.get(k.id) is not None)
    return summe, anzahl, 3 * len(gewertet)


def tendenz(summe, anzahl):
    """Durchschnitt als Skalenwert, wie bisher: ab 2,5 ++, ab 1,5 +, ab 0,5 o."""
    if not anzahl:
        return None
    schnitt = summe / anzahl
    if schnitt >= 2.5:
        return 3
    if schnitt >= 1.5:
        return 2
    if schnitt >= 0.5:
        return 1
    return 0


def foerderhinweise(schueler_id, bogen="schularzt"):
    """Kurzbezeichnungen der angekreuzten bzw. gewählten Förderhinweise."""
    hinweise = []
    for kriterium, wert in eintraege(schueler_id, bogen):
        if not kriterium.foerderhinweis:
            continue
        if kriterium.typ == "janein" and wert:
            hinweise.append(kriterium.kurzname)
        elif kriterium.typ == "mehrfach" and wert:
            hinweise.extend(wert)
        elif kriterium.typ == "auswahl" and wert:
            hinweise.append(f"{kriterium.kurzname}: {wert}")
    return hinweise


def hat_werte(kriterium_id):
    return db.session.scalar(select(KriteriumWert.id)
                             .where(KriteriumWert.kriterium_id == kriterium_id).limit(1)) is not None


__all__ = ["BOEGEN", "TYPEN", "Eingabefehler", "ensure_catalog", "kriterien", "gruppiert",
           "werte", "speichern", "pruefen", "formular_werte", "eintraege", "wertung", "tendenz",
           "foerderhinweise", "anzeigetext", "feldname", "hat_werte"]
