"""allow overlapping appointment slots

The school runs staggered interviews of different lengths, so two windows may
share time. Only an exactly identical period stays forbidden -- that case is
expressed through the slot capacity instead.
"""
from alembic import op
import sqlalchemy as sa

revision = "c8d5f02b3e16"
down_revision = "b7c4e91a2d05"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("appointment_slot") as batch_op:
        batch_op.drop_constraint("uq_slot_event_start", type_="unique")
        batch_op.create_unique_constraint("uq_slot_event_period", ["event_id", "starts_at", "ends_at"])


def downgrade():
    # Overlapping windows cannot coexist under the old rule; collapse each
    # start time down to its longest window so the constraint can be restored.
    connection = op.get_bind()
    clashes = connection.execute(sa.text(
        "SELECT event_id, starts_at, MAX(ends_at) AS keep_end FROM appointment_slot "
        "GROUP BY event_id, starts_at HAVING COUNT(*) > 1"
    )).fetchall()
    for row in clashes:
        keep_id = connection.execute(sa.text(
            "SELECT id FROM appointment_slot WHERE event_id = :event_id "
            "AND starts_at = :starts_at AND ends_at = :keep_end LIMIT 1"
        ), dict(row._mapping)).scalar()
        connection.execute(sa.text(
            "UPDATE appointment_booking SET slot_id = :keep_id WHERE slot_id IN "
            "(SELECT id FROM appointment_slot WHERE event_id = :event_id "
            " AND starts_at = :starts_at AND id != :keep_id)"
        ), {**dict(row._mapping), "keep_id": keep_id})
        connection.execute(sa.text(
            "DELETE FROM appointment_slot WHERE event_id = :event_id "
            "AND starts_at = :starts_at AND id != :keep_id"
        ), {**dict(row._mapping), "keep_id": keep_id})

    with op.batch_alter_table("appointment_slot") as batch_op:
        batch_op.drop_constraint("uq_slot_event_period", type_="unique")
        batch_op.create_unique_constraint("uq_slot_event_start", ["event_id", "starts_at"])
