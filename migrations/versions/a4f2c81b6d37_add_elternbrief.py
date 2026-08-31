"""editierbarer Text des Elternanschreibens

Ohne Zeile in dieser Tabelle gilt der im Code hinterlegte Wortlaut der
Schulvorlage; gespeichert wird nur eine abweichende Fassung.
"""
from alembic import op
import sqlalchemy as sa

revision = "a4f2c81b6d37"
down_revision = "f9d1a3c7b204"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "elternbrief",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("titel", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("gruss", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )


def downgrade():
    op.drop_table("elternbrief")
