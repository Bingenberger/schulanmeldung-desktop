"""Anmeldezeitraum und Gesprächstage trennen.

Bisher legte ein einziger Zeitraum beides fest: wann Eltern buchen dürfen und
an welchen Tagen Gesprächsfenster liegen dürfen. In der Praxis fällt das
auseinander -- gebucht wird wochenlang vorher, gesprochen an wenigen Tagen.

Die bestehenden Werte beschreiben tatsächlich die Gesprächstage, denn an ihnen
hingen die Spalten des Planers. Sie werden darum als Gesprächstage übernommen;
der Buchungszeitraum bleibt zunächst stehen und will von der Schule neu
gesetzt werden.

Die Umrechnung läuft bewusst in Python: die Zeitstempel liegen in UTC, das
Datum eines Gesprächstags gilt aber in der Zeitzone der Veranstaltung, und ein
später Termin fiele mit einer reinen SQL-Umwandlung auf den falschen Tag.
"""

import datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op

revision = "d7a3c92f4b81"
down_revision = "c4f7b1908e52"
branch_labels = None
depends_on = None

_events = sa.table(
    "appointment_event",
    sa.column("id", sa.Integer),
    sa.column("timezone", sa.String),
    sa.column("booking_opens_at", sa.DateTime),
    sa.column("booking_closes_at", sa.DateTime),
    sa.column("slot_days_from", sa.Date),
    sa.column("slot_days_until", sa.Date),
)


def _local_date(value, zone):
    if value is None:
        return None
    aware = value.replace(tzinfo=datetime.UTC) if value.tzinfo is None else value
    return aware.astimezone(ZoneInfo(zone or "Europe/Berlin")).date()


def upgrade():
    with op.batch_alter_table("appointment_event") as batch:
        batch.add_column(sa.Column("slot_days_from", sa.Date(), nullable=True))
        batch.add_column(sa.Column("slot_days_until", sa.Date(), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(sa.select(
        _events.c.id, _events.c.timezone,
        _events.c.booking_opens_at, _events.c.booking_closes_at,
    )).fetchall()
    for event_id, zone, opens, closes in rows:
        if opens is None and closes is None:
            continue
        connection.execute(_events.update().where(_events.c.id == event_id).values(
            slot_days_from=_local_date(opens, zone),
            slot_days_until=_local_date(closes, zone),
        ))


def downgrade():
    with op.batch_alter_table("appointment_event") as batch:
        batch.drop_column("slot_days_until")
        batch.drop_column("slot_days_from")
