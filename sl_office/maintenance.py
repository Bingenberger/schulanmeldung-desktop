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
import os

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


def _kann_kind_hinweis(kind, neues_datum, jahr):
    """Vermerk, wenn eine Datumskorrektur die Einschulungsentscheidung dreht."""
    vorher = ist_kann_kind(kind.geburtsdatum, jahr)
    nachher = ist_kann_kind(neues_datum, jahr)
    if vorher == nachher:
        return ""
    return f"  [{_ja_nein(vorher)} -> {_ja_nein(nachher)} Kann-Kind]"


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

    @app.cli.command("check-schriften")
    @with_appcontext
    def check_schriften():
        """Zeigen, aus welchen Dateien die Schreiben und Formulare gesetzt werden.

        Alle PDFs der Anwendung entstehen auf dem Server. Fehlt eine Schriftdatei,
        weicht ReportLab stillschweigend auf eine PDF-Standardschrift aus -- die
        Briefe entstehen dann zwar, sehen aber anders aus als beabsichtigt.
        """
        from sl_office.parent_portal.letterhead import _CANDIDATES, _FALLBACK, _font_files

        dateien = _font_files()
        fehlend = []
        for rolle, namen in _CANDIDATES.items():
            pfad = next((dateien[name.lower()] for name in namen
                         if name.lower() in dateien), None)
            if pfad is None:
                fehlend.append(rolle)
                click.echo(f"  {rolle:<9} FEHLT -- ersatzweise {_FALLBACK[rolle]}")
            else:
                mitgeliefert = "Projekt" if "assets/briefkopf" in str(pfad) else "System"
                click.echo(f"  {rolle:<9} {pfad}  [{mitgeliefert}]")
        if fehlend:
            click.echo(f"\n{len(fehlend)} Schriftrolle(n) ohne Datei. Die mitgelieferten "
                       "Dateien liegen in assets/briefkopf -- fehlen sie, ist beim "
                       "Deployment etwas schiefgegangen.")
        else:
            click.echo("\nAlle Schriften vorhanden.")

    @app.cli.command("check-geburtsdaten")
    @click.option("--datei", "datei", required=True,
                  type=click.Path(exists=True, dir_okay=False),
                  help="XLSX-Liste der Stadt für diesen Jahrgang.")
    @click.option("--jahr", "jahr", required=True, type=int,
                  help="Einschulungsjahr, zu dem die Liste gehört.")
    @click.option("--fix", "fix", is_flag=True,
                  help="Abweichende Geburtsdaten übernehmen statt nur zu melden.")
    @click.option("--spalte-vorname", "vorname", default=None,
                  help="Spaltenüberschrift, falls die Erkennung danebenliegt.")
    @click.option("--spalte-nachname", "nachname", default=None)
    @click.option("--spalte-geburtsdatum", "geburtsdatum", default=None)
    @with_appcontext
    def check_geburtsdaten(datei, jahr, fix, vorname, nachname, geburtsdatum):
        """Geburtsdaten eines Jahrgangs gegen die Liste der Stadt abgleichen.

        Ordnet über den Namen zu und ändert ausschließlich das Geburtsdatum --
        es werden keine Kinder angelegt, gelöscht oder anderweitig geändert.
        """
        from sl_office.students.city_import import InvalidWorkbook
        from sl_office.students.reconcile import geburtsdaten_abgleichen

        with open(datei, "rb") as handle:
            payload = handle.read()
        try:
            ergebnis = geburtsdaten_abgleichen(
                payload, jahr,
                vorname=vorname, nachname=nachname, geburtsdatum=geburtsdatum)
        except InvalidWorkbook as fehler:
            raise click.ClickException(str(fehler)) from fehler

        click.echo(f"Jahrgang {jahr} gegen {os.path.basename(datei)} abgeglichen.")
        click.echo(f"Geburtsdatum bereits richtig: {ergebnis.bestaetigt}")

        if ergebnis.vertauscht:
            click.echo(f"\nTag und Monat vertauscht: {len(ergebnis.vertauscht)}")
            for kind, neu in ergebnis.vertauscht:
                click.echo(f"  id={kind.id:<5} {kind.vorname} {kind.nachname}: "
                           f"{kind.geburtsdatum:%d.%m.%Y} -> {neu:%d.%m.%Y}"
                           f"{_kann_kind_hinweis(kind, neu, jahr)}")

        if ergebnis.abweichend:
            click.echo(f"\nAnderweitig abweichendes Geburtsdatum: {len(ergebnis.abweichend)}")
            click.echo("  (keine Vertauschung -- bitte einzeln prüfen, ob der Name "
                       "wirklich dasselbe Kind meint)")
            for kind, neu in ergebnis.abweichend:
                click.echo(f"  id={kind.id:<5} {kind.vorname} {kind.nachname}: "
                           f"{kind.geburtsdatum:%d.%m.%Y} -> {neu:%d.%m.%Y}"
                           f"{_kann_kind_hinweis(kind, neu, jahr)}")

        if ergebnis.mehrdeutig:
            click.echo(f"\nName mehrfach im Jahrgang, nicht zuzuordnen: "
                       f"{len(ergebnis.mehrdeutig)}")
            for vor, nach, kinder, _datum in ergebnis.mehrdeutig:
                click.echo(f"  {vor} {nach} -- ids {[kind.id for kind in kinder]}")

        if ergebnis.ohne_treffer:
            click.echo(f"\nIn der Liste, aber nicht im Bestand: {len(ergebnis.ohne_treffer)}")
            for vor, nach, datum in ergebnis.ohne_treffer:
                click.echo(f"  {vor} {nach} (geb. {datum:%d.%m.%Y})")

        if ergebnis.nicht_in_datei:
            click.echo(f"\nIm Bestand, aber nicht in der Liste: "
                       f"{len(ergebnis.nicht_in_datei)}")
            click.echo("  (normal für Kinder, die sich selbst angemeldet haben)")
            for kind in ergebnis.nicht_in_datei:
                click.echo(f"  id={kind.id:<5} {kind.vorname} {kind.nachname} "
                           f"(geb. {kind.geburtsdatum:%d.%m.%Y})"
                           if kind.geburtsdatum else
                           f"  id={kind.id:<5} {kind.vorname} {kind.nachname}")

        if ergebnis.unlesbar:
            click.echo(f"\nZeilen ohne verwertbaren Namen oder Datum übersprungen: "
                       f"{ergebnis.unlesbar}")

        if not ergebnis.korrekturen:
            click.echo("\nKeine Geburtsdaten zu ändern.")
            return
        if not fix:
            click.echo(f"\nZum Übernehmen: flask --app app check-geburtsdaten "
                       f"--datei {datei} --jahr {jahr} --fix")
            return
        click.echo(f"\n{ergebnis.anwenden()} Geburtsdaten übernommen, "
                   "Kann-Kind-Kennzeichen neu bestimmt.")

    @app.cli.command("check-dubletten")
    @click.option("--datei", "datei", required=True,
                  type=click.Path(exists=True, dir_okay=False),
                  help="XLSX-Liste der Stadt für diesen Jahrgang.")
    @click.option("--jahr", "jahr", required=True, type=int,
                  help="Einschulungsjahr, zu dem die Liste gehört.")
    @click.option("--fix", "fix", is_flag=True,
                  help="Lösbare Dubletten zusammenführen statt nur zu melden.")
    @click.option("--spalte-vorname", "vorname", default=None,
                  help="Spaltenüberschrift, falls die Erkennung danebenliegt.")
    @click.option("--spalte-nachname", "nachname", default=None)
    @click.option("--spalte-geburtsdatum", "geburtsdatum", default=None)
    @with_appcontext
    def check_dubletten(datei, jahr, fix, vorname, nachname, geburtsdatum):
        """Doppelt angelegte Kinder eines Jahrgangs zusammenführen.

        Der bearbeitete Datensatz bleibt und bekommt das Geburtsdatum aus der
        Liste; der unbearbeitete entfällt. Sind beide bearbeitet, wird nur
        gemeldet.
        """
        from sl_office.students.city_import import InvalidWorkbook
        from sl_office.students.duplicates import dubletten, zusammenfuehren
        from sl_office.students.reconcile import geburtsdaten_abgleichen

        with open(datei, "rb") as handle:
            payload = handle.read()
        try:
            abgleich = geburtsdaten_abgleichen(
                payload, jahr,
                vorname=vorname, nachname=nachname, geburtsdatum=geburtsdatum)
        except InvalidWorkbook as fehler:
            raise click.ClickException(str(fehler)) from fehler

        befunde = dubletten(abgleich)
        if not befunde:
            click.echo(f"Jahrgang {jahr}: keine doppelt angelegten Kinder gefunden.")
            return

        loesbar = [befund for befund in befunde if befund.loesbar]
        click.echo(f"Jahrgang {jahr}: {len(befunde)} Name(n) doppelt im Bestand, "
                   f"davon {len(loesbar)} automatisch auflösbar.\n")
        for befund in befunde:
            click.echo(f"{befund.name} -- laut Liste geb. {befund.datum:%d.%m.%Y}")
            for kind in befund.kinder:
                spuren = befund.spuren[kind.id]
                rolle = ""
                if befund.loesbar:
                    rolle = " BLEIBT" if kind is befund.behalten else " entfällt"
                datum = f"{kind.geburtsdatum:%d.%m.%Y}" if kind.geburtsdatum else "ohne Datum"
                click.echo(f"  id={kind.id:<5} geb. {datum}"
                           f"{' (richtig)' if kind.geburtsdatum == befund.datum else ''}"
                           f"{rolle}")
                genannt = befund.verweise[kind.id]
                if genannt:
                    spuren = spuren + [f"als Freund genannt ({genannt}x, wird umgehängt)"]
                click.echo(f"        {', '.join(spuren) if spuren else 'keine Bearbeitung'}")
            if befund.konflikt:
                click.echo(f"  -> nicht automatisch lösbar: {befund.konflikt}")
            elif befund.datum_zu_berichtigen:
                click.echo(f"  -> Geburtsdatum wird auf {befund.datum:%d.%m.%Y} berichtigt"
                           f"{_kann_kind_hinweis(befund.behalten, befund.datum, jahr)}")
            click.echo("")

        if not loesbar:
            return
        if not fix:
            click.echo(f"Zum Zusammenführen: flask --app app check-dubletten "
                       f"--datei {datei} --jahr {jahr} --fix")
            return
        entfernt = zusammenfuehren(loesbar, app.config["UPLOAD_FOLDER"])
        click.echo(f"{entfernt} Doppeleintrag/-einträge entfernt, Geburtsdaten berichtigt, "
                   "Kann-Kind-Kennzeichen neu bestimmt.")
        offen = len(befunde) - len(loesbar)
        if offen:
            click.echo(f"{offen} Fall/Fälle bleiben zur Prüfung von Hand offen.")
