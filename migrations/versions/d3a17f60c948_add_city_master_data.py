"""add address and guardian names from the municipal enrolment list

The city of Niederkassel reports the child's address and the names of the
guardians. Those fields feed the parent letter, so they live on the student.
"""
from alembic import op
import sqlalchemy as sa

revision = "d3a17f60c948"
down_revision = "c8d5f02b3e16"
branch_labels = None
depends_on = None

COLUMNS = (
    ("strasse", sa.String(length=200)),
    ("plz", sa.String(length=10)),
    ("ort", sa.String(length=120)),
    ("erzb_1_name", sa.String(length=200)),
    ("erzb_2_name", sa.String(length=200)),
)


def upgrade():
    with op.batch_alter_table("schueler") as batch_op:
        for name, type_ in COLUMNS:
            batch_op.add_column(sa.Column(name, type_, nullable=True))


def downgrade():
    with op.batch_alter_table("schueler") as batch_op:
        for name, _ in reversed(COLUMNS):
            batch_op.drop_column(name)
