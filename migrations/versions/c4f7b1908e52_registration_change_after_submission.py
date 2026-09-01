"""Wer hat abgesendet, und wurde die Schule über eine spätere Änderung informiert.

Beide Sorgeberechtigten bearbeiten dasselbe Formular. Ohne diese beiden Spalten
sieht das zweite Elternteil nicht, von wem der abgesendete Stand stammt, und
die Schule erfährt nicht, dass sich nach ihrer Prüfung noch etwas geändert hat.
"""

import sqlalchemy as sa
from alembic import op

revision = "c4f7b1908e52"
down_revision = "b2e9147ac36d"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("parent_registration") as batch:
        batch.add_column(sa.Column("submitted_by_access_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("change_notified_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(
            "fk_parent_registration_submitted_by", "parent_access",
            ["submitted_by_access_id"], ["id"], ondelete="SET NULL",
        )


def downgrade():
    with op.batch_alter_table("parent_registration") as batch:
        batch.drop_constraint("fk_parent_registration_submitted_by", type_="foreignkey")
        batch.drop_column("change_notified_at")
        batch.drop_column("submitted_by_access_id")
