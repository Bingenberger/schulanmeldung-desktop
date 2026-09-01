"""Einordnung der Kinder nach den Stichtagsregeln des Schuljahres."""

import datetime

from models import Diagnostik, GlobalSettings, Schueler, db

#: Ein Kind wird schulpflichtig, wenn es bis zu diesem Tag des Einschulungs-
#: jahres sechs Jahre alt ist (§ 35 SchulG NRW). Wer spaeter Geburtstag hat,
#: ist ein Kann-Kind und kann nur auf Antrag eingeschult werden.
STICHTAG_MONAT, STICHTAG_TAG = 9, 30

#: Kennzeichnet "kein Jahrgang ermittelt" -- ``None`` waere hier mehrdeutig.
_UNGESETZT = object()


def stichtag(einschulungsjahr):
    """Letztes Geburtsdatum, mit dem ein Kind in diesem Jahrgang Muss-Kind ist."""
    return datetime.date(einschulungsjahr - 6, STICHTAG_MONAT, STICHTAG_TAG)


def ist_kann_kind(geburtsdatum, einschulungsjahr):
    """Kann-Kind, wenn der sechste Geburtstag nach dem Stichtag liegt."""
    if geburtsdatum is None or einschulungsjahr is None:
        return False
    return geburtsdatum > stichtag(einschulungsjahr)


def eingestelltes_einschulungsjahr(settings=None):
    """Jahrgang, an dem gerade gearbeitet wird.

    Erste Wahl ist der in der Sitzung gewaehlte bzw. als aktuell markierte
    Jahrgang -- danach richten sich auch neu angelegte Kinder. Die Einstellung
    unter *Einstellungen* greift nur, solange noch kein Jahrgang gepflegt ist.
    """
    # Erst hier importiert: ``school_year`` haengt am Modell, und ein Import auf
    # Modulebene wuerde beide Richtungen verknoten.
    from sl_office import school_year

    year = school_year.active_year()
    if year is None:
        row = school_year.current_year_row()
        year = row.jahr if row else None
    if year is None:
        settings = settings or GlobalSettings.query.first()
        year = settings.einschulungsjahr if settings else None
    return year


def recalculate_kann_kind(student=None, settings=None):
    """Kann-Kind-Kennzeichen neu bestimmen, ohne die Transaktion abzuschliessen.

    Gerechnet wird gegen das Einschulungsjahr des Kindes selbst. Ein frueherer
    Jahrgang behaelt dadurch seine Einordnung, auch wenn die Schule laengst am
    naechsten arbeitet. Nur wo noch kein Jahrgang am Kind steht, entscheidet der
    eingestellte Jahrgang.

    Liefert die Zahl der geprueften Kinder.
    """
    # Ein noch nicht gespeichertes Kind bekommt sein Einschulungsjahr erst beim
    # Flush (``before_insert`` in sl_office/school_year.py). Ohne diesen Schritt
    # haengt das Ergebnis davon ab, ob zufaellig ein Autoflush dazwischenfaellt.
    if db.session.new:
        db.session.flush()

    students = [student] if student is not None else Schueler.query.all()
    fallback = _UNGESETZT
    for current in students:
        year = current.einschulungsjahr
        if year is None:
            if fallback is _UNGESETZT:
                fallback = eingestelltes_einschulungsjahr(settings)
            year = fallback
        war_kann_kind = bool(current.kann_kind)
        current.kann_kind = ist_kann_kind(current.geburtsdatum, year)
        if current.kann_kind and not war_kann_kind:
            # Nur beim erstmaligen Erkennen einladen. Eine spaeter getroffene
            # Entscheidung gegen das Schulspiel bliebe sonst nicht bestehen:
            # jeder Import und jede Stammdatenaenderung holte sie zurueck.
            if current.diagnostik is None:
                current.diagnostik = Diagnostik(schulspiel=True)
                db.session.add(current.diagnostik)
            else:
                current.diagnostik.schulspiel = True
    return len(students)
