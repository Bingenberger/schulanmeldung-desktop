"""simplify appointment slots: drop resources, record booking source

The school runs a single kind of appointment (Anmeldegespräch). Parallel
interviews are expressed through the slot capacity instead of resources, so the
resource table and the slot's resource_id disappear and the "no overlap" rule
now applies per event.
"""
from alembic import op
import sqlalchemy as sa

revision = "b7c4e91a2d05"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("appointment_booking") as batch_op:
        batch_op.add_column(sa.Column("source", sa.String(length=10), nullable=False, server_default="parent"))
        batch_op.create_check_constraint("ck_appointment_booking_source", "source IN ('parent','staff')")

    # Collapse any pre-existing per-resource parallelism into capacity so no
    # appointment is lost, then drop the resource dimension entirely.
    connection = op.get_bind()
    duplicates = connection.execute(sa.text(
        "SELECT event_id, starts_at, COUNT(*) AS n, MIN(id) AS keep_id, SUM(capacity) AS total "
        "FROM appointment_slot GROUP BY event_id, starts_at HAVING COUNT(*) > 1"
    )).fetchall()
    for row in duplicates:
        connection.execute(
            sa.text("UPDATE appointment_slot SET capacity = :total WHERE id = :keep_id"),
            {"total": row.total, "keep_id": row.keep_id},
        )
        connection.execute(
            sa.text(
                "UPDATE appointment_booking SET slot_id = :keep_id WHERE slot_id IN "
                "(SELECT id FROM appointment_slot WHERE event_id = :event_id "
                " AND starts_at = :starts_at AND id != :keep_id)"
            ),
            {"keep_id": row.keep_id, "event_id": row.event_id, "starts_at": row.starts_at},
        )
        connection.execute(
            sa.text(
                "DELETE FROM appointment_slot WHERE event_id = :event_id "
                "AND starts_at = :starts_at AND id != :keep_id"
            ),
            {"event_id": row.event_id, "starts_at": row.starts_at, "keep_id": row.keep_id},
        )

    with op.batch_alter_table("appointment_slot") as batch_op:
        batch_op.drop_constraint("uq_slot_resource_period", type_="unique")
        batch_op.drop_index("ix_appointment_slot_resource_id")
        batch_op.drop_column("resource_id")
        batch_op.create_unique_constraint("uq_slot_event_start", ["event_id", "starts_at"])

    op.drop_table("appointment_resource")


def downgrade():
    op.create_table(
        "appointment_resource",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("public_label", sa.String(length=100), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    # Every slot needs a resource again; a single placeholder carries them all.
    connection = op.get_bind()
    connection.execute(sa.text(
        "INSERT INTO appointment_resource (id, name, active) VALUES (1, 'Anmeldegespräch', 1)"
    ))
    with op.batch_alter_table("appointment_slot") as batch_op:
        batch_op.drop_constraint("uq_slot_event_start", type_="unique")
        batch_op.add_column(sa.Column("resource_id", sa.Integer(), nullable=False, server_default="1"))
        batch_op.create_index("ix_appointment_slot_resource_id", ["resource_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_appointment_slot_resource", "appointment_resource", ["resource_id"], ["id"], ondelete="RESTRICT"
        )
        batch_op.create_unique_constraint("uq_slot_resource_period", ["resource_id", "starts_at", "ends_at"])

    with op.batch_alter_table("appointment_booking") as batch_op:
        batch_op.drop_constraint("ck_appointment_booking_source", type_="check")
        batch_op.drop_column("source")
