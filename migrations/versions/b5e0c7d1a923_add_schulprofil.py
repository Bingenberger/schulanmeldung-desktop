"""Schulprofil: Angaben zur Schule in der Anwendung statt in Umgebungsvariablen

Eine Zeile je Angabe; Logo und Unterschrift stehen als Bytes in ``daten``.
Fehlt eine Zeile, gilt weiter der Wert aus der Konfiguration.
"""
from alembic import op
import sqlalchemy as sa

revision = "b5e0c7d1a923"
down_revision = "79fdf743c733"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "schulprofil",
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("wert", sa.Text(), nullable=False),
        sa.Column("daten", sa.LargeBinary(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade():
    op.drop_table("schulprofil")
