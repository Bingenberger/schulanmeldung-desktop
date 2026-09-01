"""Prüfläufe über den gespeicherten Datenbestand.

Zwei Dinge lassen sich im laufenden Betrieb nicht zuverlässig verhindern und
müssen deshalb nachträglich prüfbar sein: verwaiste Elternportal-Zeilen aus der
Zeit, als die Löschung eines Kindes sie stehen ließ (SQLite vergibt die
freigewordene Zeilennummer neu, ein später angelegtes Kind konnte sie erben),
und die Kann-Kind-Kennzeichen, die an vielen Stellen fortgeschrieben werden.

Beide Kommandos melden zunächst nur; ``--fix`` bzw. ``--delete`` greift ein.
Ohne Anfrage-Kontext hebt der Jahrgangsfilter aus ``sl_office/school_year`` sich
selbst auf -- die Prüfung sieht deshalb alle Jahrgänge, nicht nur den offenen.
"""

import datetime

import click
from flask.cli import with_appcontext
from sqlalchemy import delete, func, select

from models import Diagnostik, Schueler, db
from sl_office.parent_portal.models import (
    ActivationGrant, AppointmentBooking, ParentAccess, ParentLoginToken, ParentRegistration,
)
from sl_office.services.student_classification import ist_kann_kind, stichtag


def _ja_nein(wert):
    return "Ja" if wert else "Nein"


#: Tabellen, die über ``schueler_id`` an einem Kind hängen.
_STUDENT_TABLES = (ParentAccess, ActivationGrant, ParentRegistration, AppointmentBooking)


def orphaned_portal_records():
    """Verwaiste Zeilen je Tabelle: {Tabellenname: [Zeilen-ID, ...]}."""
    known = select(Schueler.id)
    found = {}
    for model in _STUDENT_TABLES:
        ids = list(db.session.scalars(
            select(model.id).where(model.schueler_id.not_in(known))))
        if ids:
            found[model.__tablename__] = ids
    accesses = select(ParentAccess.id)
    tokens = list(db.session.scalars(
        select(ParentLoginToken.id).where(ParentLoginToken.parent_access_id.not_in(accesses))))
    if tokens:
        found[ParentLoginToken.__tablename__] = tokens
    return found


def delete_orphaned_portal_records():
    """Verwaiste Zeilen entfernen; liefert die Zahl je Tabelle."""
    removed = {}
    known = select(Schueler.id)
    # Anmeldelinks zuerst: sie hängen an den Zugängen, die gleich fallen.
    orphan_accesses = select(ParentAccess.id).where(ParentAccess.schueler_id.not_in(known))
    count = db.session.execute(delete(ParentLoginToken).where(
        ParentLoginToken.parent_access_id.in_(orphan_accesses))).rowcount
    if count:
        removed[ParentLoginToken.__tablename__] = count
    for model in _STUDENT_TABLES:
        count = db.session.execute(
            delete(model).where(model.schueler_id.not_in(known))).rowcount
        if count:
            removed[model.__tablename__] = count
    db.session.commit()
    return removed


# --- Kann-Kinder ------------------------------------------------------------

#: Wie weit ein Geburtsdatum neben seinem Jahrgang liegen darf, bevor es als
#: Zahlendreher gemeldet wird. Ein Jahr deckt Rückstellungen und im Vorjahr
#: nicht eingeschulte Kann-Kinder ab, die regulär im nächsten Jahrgang stehen.
TOLERANZ_JAHRE = 1


def _jahrgangsfenster(jahr, toleranz=TOLERANZ_JAHRE):
    """Von--bis, in dem ein Geburtsdatum für diesen Jahrgang plausibel ist.

    Ohne Toleranz ist das der Zeitraum, aus dem der Jahrgang tatsächlich stammt:
    Muss-Kinder vom 01.10. (Jahr-7) bis zum Stichtag, Kann-Kinder bis zum
    Jahresende.
    """
    return (datetime.date(jahr - 7 - toleranz, 10, 1),
            datetime.date(jahr - 6 + toleranz, 12, 31))


def kann_kind_befunde():
    """Alle Kinder gegen die Stichtagsregel prüfen.

    Liefert ``(falsche_kennzeichen, ohne_geburtsdatum, unplausibel)``. Der erste
    Eintrag lässt sich maschinell richtigstellen, die beiden anderen brauchen
    eine Entscheidung und werden nur gemeldet.
    """
    falsch, ohne_datum, unplausibel = [], [], []
    for kind in db.session.scalars(
            select(Schueler).order_by(Schueler.einschulungsjahr, Schueler.nachname)):
        if kind.geburtsdatum is None:
            ohne_datum.append(kind)
            continue
        erwartet = ist_kann_kind(kind.geburtsdatum, kind.einschulungsjahr)
        if erwartet != bool(kind.kann_kind):
            falsch.append((kind, erwartet))
        von, bis = _jahrgangsfenster(kind.einschulungsjahr)
        if not von <= kind.geburtsdatum <= bis:
            unplausibel.append(kind)
    return falsch, ohne_datum, unplausibel


def vertauschte_geburtsdaten():
    """Kinder, deren Geburtsdatum nach einer Tag/Monat-Vertauschung aussieht.

    Der Importfehler (siehe ``sl_office/students/dates.py``) traf nur Geburtstage
    bis zum 12. eines Monats -- höhere Tage taugen nicht als Monat und blieben
    richtig. Aus den Daten allein ist eine Vertauschung nicht beweisbar, aber
    einzugrenzen: Liegt das gespeicherte Datum außerhalb des Zeitraums, aus dem
    ein Jahrgang stammen kann, das getauschte aber darin, ist der Verdacht hoch.
    Sonst entscheidet, ob der Tausch den Kann-Kind-Status kippen würde -- dann
    hängt eine Einschulungsentscheidung daran.

    Liefert ``[(kind, getauschtes_datum, verdacht, status_kippt), ...]``.
    """
    befunde = []
    for kind in db.session.scalars(
            select(Schueler).order_by(Schueler.einschulungsjahr, Schueler.nachname)):
        if kind.geburtsdatum is None or kind.geburtsdatum.day > 12:
            continue
        try:
            getauscht = kind.geburtsdatum.replace(
                month=kind.geburtsdatum.day, day=kind.geburtsdatum.month)
        except ValueError:
            continue
        # Hier zählt der enge Zeitraum: bei einer Toleranz von einem Jahr fiele
        # der typische Fall (aus 01.10. wurde 10.01.) nicht mehr auf.
        von, bis = _jahrgangsfenster(kind.einschulungsjahr, toleranz=0)
        verdacht = not (von <= kind.geburtsdatum <= bis) and von <= getauscht <= bis
        kippt = (ist_kann_kind(kind.geburtsdatum, kind.einschulungsjahr)
                 != ist_kann_kind(getauscht, kind.einschulungsjahr))
        if verdacht or kippt:
            befunde.append((kind, getauscht, verdacht, kippt))
    return befunde


def kann_kind_kennzeichen_richtigstellen(falsch):
    """Gemeldete Kennzeichen setzen und neue Kann-Kinder zum Schulspiel einladen.

    Eine bereits getroffene Entscheidung gegen das Schulspiel bleibt bestehen;
    umgekehrt wird eine Einladung nicht zurückgenommen, wenn ein Kind seinen
    Kann-Kind-Status verliert -- das Schulspiel kann längst stattgefunden haben.
    """
    for kind, erwartet in falsch:
        kind.kann_kind = erwartet
        if erwartet:
            if kind.diagnostik is None:
                kind.diagnostik = Diagnostik(schulspiel=True)
                db.session.add(kind.diagnostik)
            else:
                kind.diagnostik.schulspiel = True
    db.session.commit()
    return len(falsch)


def register_cli(app):
    @app.cli.command("check-orphans")
    @click.option("--delete", "remove", is_flag=True,
                  help="Gefundene Zeilen löschen statt nur zu melden.")
    @with_appcontext
    def check_orphans(remove):
        """Elternportal-Daten ohne zugehöriges Kind suchen (und auf Wunsch löschen)."""
        found = orphaned_portal_records()
        if not found:
            click.echo("Keine verwaisten Datensätze gefunden.")
            return
        for table, ids in found.items():
            click.echo(f"{table}: {len(ids)} verwaiste Zeile(n) -- ids {ids}")
        if not remove:
            click.echo("\nZum Entfernen: flask --app app check-orphans --delete")
            return
        removed = delete_orphaned_portal_records()
        for table, count in removed.items():
            click.echo(f"gelöscht aus {table}: {count}")

    @app.cli.command("check-kann-kinder")
    @click.option("--fix", "fix", is_flag=True,
                  help="Falsche Kennzeichen richtigstellen statt nur zu melden.")
    @with_appcontext
    def check_kann_kinder(fix):
        """Kann-Kind-Kennzeichen aller Jahrgänge gegen die Stichtagsregel prüfen."""
        falsch, ohne_datum, unplausibel = kann_kind_befunde()
        gesamt = db.session.scalar(select(func.count(Schueler.id)))
        click.echo(f"{gesamt} Kinder geprüft.\n")

        if falsch:
            click.echo(f"Falsches Kann-Kind-Kennzeichen: {len(falsch)}")
            for kind, erwartet in falsch:
                click.echo(f"  id={kind.id:<5} {kind.vorname} {kind.nachname} "
                           f"(geb. {kind.geburtsdatum:%d.%m.%Y}, ESJ {kind.einschulungsjahr}, "
                           f"Stichtag {stichtag(kind.einschulungsjahr):%d.%m.%Y}): "
                           f"gespeichert {_ja_nein(kind.kann_kind)}, "
                           f"richtig {_ja_nein(erwartet)}")
        else:
            click.echo("Kann-Kind-Kennzeichen: alle korrekt.")

        if ohne_datum:
            click.echo(f"\nOhne Geburtsdatum, daher nicht einzuordnen: {len(ohne_datum)}")
            for kind in ohne_datum:
                click.echo(f"  id={kind.id:<5} {kind.vorname} {kind.nachname} "
                           f"(ESJ {kind.einschulungsjahr})")

        if unplausibel:
            click.echo(f"\nGeburtsdatum passt nicht zum Jahrgang, bitte sichten: "
                       f"{len(unplausibel)}")
            for kind in unplausibel:
                click.echo(f"  id={kind.id:<5} {kind.vorname} {kind.nachname} "
                           f"(geb. {kind.geburtsdatum:%d.%m.%Y}, ESJ {kind.einschulungsjahr})")

        verdaechtig = vertauschte_geburtsdaten()
        if verdaechtig:
            click.echo(f"\nGeburtsdatum möglicherweise tag/monat-vertauscht: "
                       f"{len(verdaechtig)}")
            click.echo("  (nur gegen die Liste der Stadt zu klären -- nicht automatisch "
                       "korrigierbar)")
            for kind, getauscht, verdacht, kippt in verdaechtig:
                hinweise = []
                if verdacht:
                    hinweise.append("passt getauscht besser zum Jahrgang")
                if kippt:
                    hinweise.append("Kann-Kind-Status hängt daran")
                click.echo(f"  id={kind.id:<5} {kind.vorname} {kind.nachname} "
                           f"(ESJ {kind.einschulungsjahr}): "
                           f"{kind.geburtsdatum:%d.%m.%Y} oder {getauscht:%d.%m.%Y}? "
                           f"-- {', '.join(hinweise)}")

        if not falsch:
            return
        if not fix:
            click.echo("\nZum Richtigstellen: flask --app app check-kann-kinder --fix")
            return
        click.echo(f"\n{kann_kind_kennzeichen_richtigstellen(falsch)} Kennzeichen "
                   "richtiggestellt.")
