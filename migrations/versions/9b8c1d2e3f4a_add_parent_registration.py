"""add digital parent registration form"""
from alembic import op
import sqlalchemy as sa

revision = "9b8c1d2e3f4a"
down_revision = "87518c6eebb8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "parent_registration",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("schueler_id", sa.Integer(), nullable=False),
        sa.Column("created_by_access_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('draft','submitted','in_review','completed')", name="ck_parent_registration_status"),
        sa.ForeignKeyConstraint(["created_by_access_id"], ["parent_access.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["schueler_id"], ["schueler.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("schueler_id"),
    )
    with op.batch_alter_table("parent_registration") as batch_op:
        batch_op.create_index(batch_op.f("ix_parent_registration_status"), ["status"], unique=False)


def downgrade():
    with op.batch_alter_table("parent_registration") as batch_op:
        batch_op.drop_index(batch_op.f("ix_parent_registration_status"))
    op.drop_table("parent_registration")
