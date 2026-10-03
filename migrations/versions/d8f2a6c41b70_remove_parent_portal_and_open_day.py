"""Elternportal und Tag der offenen Tür entfernen

Die Desktop-Fassung hat weder Elternportal noch Tag der offenen Tür. Entfernt
werden deren Tabellen, die Spalten der Terminverwaltung, die nur der Buchung
durch Eltern dienten, und die zugehörigen Einträge im Schulprofil.

Die Daten lassen sich nicht wiederherstellen; ``downgrade`` legt nur die
Struktur wieder an, damit ältere Programmstände starten.
"""
from alembic import op
import sqlalchemy as sa

revision = "d8f2a6c41b70"
down_revision = "c3d9e4f2a817"
branch_labels = None
depends_on = None

#: In Abhängigkeitsreihenfolge: was auf andere zeigt, fällt zuerst.
TABLES = (
    "open_day_zuteilung", "open_day_registration", "open_day_platz", "open_day_station",
    "open_day_event", "parent_login_token", "activation_grant", "parent_registration",
)
EVENT_COLUMNS = ("booking_opens_at", "booking_closes_at", "cancellation_deadline_hours",
                 "parent_instructions")
BOOKING_COLUMNS = ("parent_access_id", "reminder_sent_at")


def _existing_tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    present = _existing_tables()
    for table in TABLES:
        if table in present:
            op.drop_table(table)

    booking = _columns("appointment_booking")
    with op.batch_alter_table("appointment_booking", recreate="always") as batch:
        for column in BOOKING_COLUMNS:
            if column in booking:
                batch.drop_column(column)
    event = _columns("appointment_event")
    with op.batch_alter_table("appointment_event") as batch:
        for column in EVENT_COLUMNS:
            if column in event:
                batch.drop_column(column)

    if "parent_access" in present:
        op.drop_table("parent_access")

    op.execute("DELETE FROM schulprofil WHERE key IN "
               "('modul:tag_der_offenen_tuer', 'vorlage:anmeldespiel')")


def downgrade():
    op.create_table(
        "parent_access",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("schueler_id", sa.Integer(), nullable=False),
        sa.Column("email_normalized", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200)),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("security_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["schueler_id"], ["schueler.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("appointment_event") as batch:
        batch.add_column(sa.Column("booking_opens_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("booking_closes_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("cancellation_deadline_hours", sa.Integer(),
                                   nullable=False, server_default="24"))
        batch.add_column(sa.Column("parent_instructions", sa.Text()))
    with op.batch_alter_table("appointment_booking") as batch:
        batch.add_column(sa.Column("parent_access_id", sa.Integer()))
        batch.add_column(sa.Column("reminder_sent_at", sa.DateTime(timezone=True)))
