"""Der Zeitraum eines Gesprächsfensters ist nur unter den lebenden einmalig.

Ein gelöschtes Fenster, an dem noch stornierte Buchungen hängen, wird nicht
entfernt, sondern auf ``cancelled`` gesetzt -- der Fremdschlüssel der Buchungen
schützt es mit ``ON DELETE RESTRICT``. Mit der bisherigen Eindeutigkeit über
alle Zeilen belegte es damit seine Uhrzeit für immer: ein neues Fenster zur
selben Zeit ließ sich nicht mehr anlegen.

Die Regel im Code kannte diese Ausnahme längst (``_assert_not_duplicate``
übergeht stornierte Fenster); die Datenbank zieht hier nach.
"""

import sqlalchemy as sa
from alembic import op

revision = "e1b6d05a7c94"
down_revision = "d7a3c92f4b81"
branch_labels = None
depends_on = None

_COLUMNS = ["event_id", "starts_at", "ends_at"]


def upgrade():
    with op.batch_alter_table("appointment_slot") as batch:
        batch.drop_constraint("uq_slot_event_period", type_="unique")
    op.create_index(
        "uq_slot_event_period_active", "appointment_slot", _COLUMNS, unique=True,
        sqlite_where=sa.text("status != 'cancelled'"),
        postgresql_where=sa.text("status <> 'cancelled'"),
    )


def downgrade():
    op.drop_index("uq_slot_event_period_active", table_name="appointment_slot")
    with op.batch_alter_table("appointment_slot") as batch:
        batch.create_unique_constraint("uq_slot_event_period", _COLUMNS)
