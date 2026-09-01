"""Aufräumarbeiten an Datenbeständen, die eine frühere Programmfassung hinterlassen hat.

Bis die Löschung eines Kindes auch seine Elternportal-Daten mitnahm, blieben
diese Zeilen als Waisen zurück. Weil SQLite die freigewordene Zeilennummer neu
vergibt, konnte ein später angelegtes Kind sie erben. Der Fehler ist behoben --
die Zeilen, die schon liegen geblieben sind, muss aber jemand wegräumen.
"""

import click
from flask.cli import with_appcontext
from sqlalchemy import delete, select

from models import Schueler, db
from sl_office.parent_portal.models import (
    ActivationGrant, AppointmentBooking, ParentAccess, ParentLoginToken, ParentRegistration,
)

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
