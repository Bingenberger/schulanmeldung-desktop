"""Erinnerungsstempel an der Terminbuchung.

Ein gesetzter Wert heißt: für diese Buchung ist die Erinnerung raus. Ohne die
Spalte müsste der Erinnerungsdienst raten, was er beim letzten Lauf zugestellt
hat, und würde nach jedem Neustart doppelt schreiben.
"""

import sqlalchemy as sa
from alembic import op

revision = "b2e9147ac36d"
down_revision = "a4f2c81b6d37"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("appointment_booking") as batch:
        batch.add_column(sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table("appointment_booking") as batch:
        batch.drop_column("reminder_sent_at")
