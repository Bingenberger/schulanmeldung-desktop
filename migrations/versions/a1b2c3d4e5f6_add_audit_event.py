"""add audit event table"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "9b8c1d2e3f4a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "audit_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("object_type", sa.String(length=80), nullable=False),
        sa.Column("object_id", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("audit_event") as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_event_occurred_at"), ["occurred_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_event_action"), ["action"], unique=False)


def downgrade():
    with op.batch_alter_table("audit_event") as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_event_action"))
        batch_op.drop_index(batch_op.f("ix_audit_event_occurred_at"))
    op.drop_table("audit_event")
